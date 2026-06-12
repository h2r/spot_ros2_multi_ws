#!/usr/bin/env python3
"""Multi-operator command aggregator.

Star topology: every operator client publishes ghost_msgs/OperatorInput to
/operators/input and this node is the sole subscriber. Each tick it fuses
the inputs on every channel through a swappable strategy, clamps the result,
and publishes it to that channel's robot topic — operators never command the
robots directly. It also publishes ghost_msgs/UiState on /ui_state, the
curated view that front-ends render; clients subscribe to that, never to the
raw input firehose.
"""

import math

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import Twist
from ghost_msgs.msg import ChannelState, OperatorInput, OperatorState, UiState

from ghost_aggregator.strategies import Contribution, make_strategy


class OperatorAggregator(Node):
    def __init__(self):
        super().__init__("operator_aggregator")

        self.declare_parameter("robots", ["spot", "spot2"])
        self.declare_parameter("tick_rate", 10.0)
        # Inputs older than this carry no weight; matches the web clients'
        # 10 Hz publish rate with room for a few dropped messages.
        self.declare_parameter("input_timeout", 0.5)
        # Operators silent this long disappear from /ui_state entirely.
        self.declare_parameter("operator_forget_after", 5.0)
        # Same caps spot_sync_drive_velocity uses.
        self.declare_parameter("max_linear_speed", 0.3)
        self.declare_parameter("max_angular_speed", 0.2)
        # After a channel goes idle, publish this many explicit zero-twists,
        # then go silent so an idle channel never fights another commander.
        self.declare_parameter("idle_zero_ticks", 3)
        self.declare_parameter("strategy", "passthrough")
        self.declare_parameter("selected_operator", "")  # used by "select"

        robots = self.get_parameter("robots").value
        self.cmd_publishers = {
            f"{robot}/drive": self.create_publisher(Twist, f"/{robot}/cmd_vel", 1)
            for robot in robots
        }
        self.ui_publisher = self.create_publisher(UiState, "/ui_state", 1)
        self.input_subscriber = self.create_subscription(
            OperatorInput, "/operators/input", self.on_input, 10
        )

        # channel -> operator_id -> (last OperatorInput, arrival time in seconds)
        self.inputs = {channel: {} for channel in self.cmd_publishers}
        self.idle_zeros_left = {channel: 0 for channel in self.cmd_publishers}
        self.warned_channels = set()

        tick_rate = self.get_parameter("tick_rate").value
        self.timer = self.create_timer(1.0 / tick_rate, self.tick)
        self.get_logger().info(
            "aggregating channels {} with strategy '{}'".format(
                list(self.cmd_publishers), self.get_parameter("strategy").value
            )
        )

    def now_seconds(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def on_input(self, msg: OperatorInput):
        if msg.channel not in self.inputs:
            if msg.channel not in self.warned_channels:
                self.warned_channels.add(msg.channel)
                self.get_logger().warning(
                    f"ignoring input from '{msg.operator_id}' on unknown channel '{msg.channel}'"
                )
            return
        # Staleness is measured from arrival, not header.stamp: client clocks
        # (browsers) aren't synchronized with this machine. The issue-time
        # stamp stays in the message for the session logs.
        self.inputs[msg.channel][msg.operator_id] = (msg, self.now_seconds())

    def tick(self):
        now = self.now_seconds()
        timeout = self.get_parameter("input_timeout").value
        forget_after = self.get_parameter("operator_forget_after").value
        # Re-read each tick so `ros2 param set` switches strategy/operator live.
        strategy = make_strategy(
            self.get_parameter("strategy").value,
            self.get_parameter("selected_operator").value,
        )

        ui = UiState()
        ui.header.stamp = self.get_clock().now().to_msg()

        for channel, publisher in self.cmd_publishers.items():
            latest = self.inputs[channel]
            for operator_id in [op for op, (_, t) in latest.items() if now - t > forget_after]:
                del latest[operator_id]

            contributions = [
                Contribution(
                    operator_id=operator_id,
                    twist=msg.twist,
                    age=now - arrived,
                    active=(now - arrived) <= timeout,
                )
                for operator_id, (msg, arrived) in sorted(latest.items())
            ]
            weights = strategy.weights(contributions)
            fused, commanding = self.resolve(contributions, weights)

            if commanding:
                publisher.publish(fused)
                self.idle_zeros_left[channel] = self.get_parameter("idle_zero_ticks").value
            elif self.idle_zeros_left[channel] > 0:
                publisher.publish(Twist())
                self.idle_zeros_left[channel] -= 1

            channel_state = ChannelState()
            channel_state.channel = channel
            channel_state.fused = fused
            channel_state.operators = [
                OperatorState(
                    operator_id=c.operator_id,
                    twist=c.twist,
                    weight=float(weights.get(c.operator_id, 0.0)),
                    age=float(c.age),
                    active=c.active,
                )
                for c in contributions
            ]
            ui.channels.append(channel_state)

        self.ui_publisher.publish(ui)

    def resolve(self, contributions, weights) -> tuple:
        """Weighted mean of the active contributions, clamped.

        Returns (twist, commanding); commanding is False when no weight is
        assigned (idle channel, or e.g. "select" with nobody chosen)."""
        total = sum(weights.get(c.operator_id, 0.0) for c in contributions if c.active)
        if total <= 0.0:
            return Twist(), False

        fused = Twist()
        for c in contributions:
            if not c.active:
                continue
            w = weights.get(c.operator_id, 0.0) / total
            fused.linear.x += w * c.twist.linear.x
            fused.linear.y += w * c.twist.linear.y
            fused.linear.z += w * c.twist.linear.z
            fused.angular.x += w * c.twist.angular.x
            fused.angular.y += w * c.twist.angular.y
            fused.angular.z += w * c.twist.angular.z
        return self.clamp(fused), True

    def clamp(self, twist: Twist) -> Twist:
        max_linear = self.get_parameter("max_linear_speed").value
        max_angular = self.get_parameter("max_angular_speed").value
        speed = math.sqrt(twist.linear.x**2 + twist.linear.y**2 + twist.linear.z**2)
        if speed > max_linear:
            scale = max_linear / speed
            twist.linear.x *= scale
            twist.linear.y *= scale
            twist.linear.z *= scale
        if abs(twist.angular.z) > max_angular:
            twist.angular.z = math.copysign(max_angular, twist.angular.z)
        return twist


def main(args=None):
    rclpy.init(args=args)
    aggregator = OperatorAggregator()
    rclpy.spin(aggregator)
    aggregator.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
