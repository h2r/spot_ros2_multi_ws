#!/usr/bin/env python3

import numpy as np
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped
import time


class ArmPositionCycler(Node):
    def __init__(self):
        super().__init__('arm_position_cycler')
        self.publisher = self.create_publisher(PoseStamped, '/spot2/arm_pose_commands', 10)

        # Define positions [x, y, z]
        self.positions = []
        for x in np.arange(0.4, 0.7, 0.01):
            self.positions.append([x, 0.0, 0.467])
        self.current_index = 0

        # Wait for connections
        time.sleep(1.0)

        # Create timer to publish at 0.5 Hz (2 second interval)
        self.timer = self.create_timer(1 / 9.0, self.publish_position)

        self.get_logger().info('Arm position cycler started')
        self.get_logger().info(f'Cycling through {len(self.positions)} positions')

    def publish_position(self):
        msg = PoseStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'body'

        pos = self.positions[self.current_index]
        msg.pose.position.x = pos[0]
        msg.pose.position.y = pos[1]
        msg.pose.position.z = pos[2]

        # Default orientation (identity quaternion)
        msg.pose.orientation.x = 0.0
        msg.pose.orientation.y = 0.0
        msg.pose.orientation.z = 0.0
        msg.pose.orientation.w = 1.0

        self.publisher.publish(msg)
        self.get_logger().info(f'Publishing position: [{pos[0]}, {pos[1]}, {pos[2]}]')

        # Move to next position in cycle
        self.current_index = (self.current_index + 1) % len(self.positions)


def main(args=None):
    rclpy.init(args=args)
    node = ArmPositionCycler()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('Shutting down arm position cycler')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
