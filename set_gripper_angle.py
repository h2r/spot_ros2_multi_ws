#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32
import time


class GripperAngleCycler(Node):
    def __init__(self):
        super().__init__('gripper_angle_cycler')
        self.publisher = self.create_publisher(Float32, '/spot2/gripper_angle_command', 10)
        self.angles = [0.0, 45.0, 90.0, 45.0]
        self.current_index = 0

        # Wait for connections
        time.sleep(1.0)

        # Create timer to publish at 1 Hz (1 second interval)
        self.timer = self.create_timer(1, self.publish_angle)

        self.get_logger().info('Gripper angle cycler started')
        self.get_logger().info(f'Cycling through angles: {self.angles}')

    def publish_angle(self):
        msg = Float32()
        msg.data = self.angles[self.current_index]

        self.publisher.publish(msg)
        self.get_logger().info(f'Publishing angle: {msg.data}')

        # Move to next angle in cycle
        self.current_index = (self.current_index + 1) % len(self.angles)


def main(args=None):
    rclpy.init(args=args)
    node = GripperAngleCycler()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('Shutting down gripper angle cycler')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
