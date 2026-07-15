#!/usr/bin/env python3

import numpy as np
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped
from std_srvs.srv import Trigger
from tf2_ros import Buffer, TransformListener
import time


class ArmPositionCycler(Node):
    def __init__(self):
        super().__init__('arm_position_cycler')
        self.publisher = self.create_publisher(PoseStamped, '/spot2/arm_pose_body_assist_commands', 10)

        # Create service client for stowing the arm
        self.stow_client = self.create_client(Trigger, '/spot2/arm_stow')

        # Call stow service first
        self.get_logger().info('Waiting for stow service...')
        self.stow_client.wait_for_service(timeout_sec=5.0)

        if self.stow_client.service_is_ready():
            self.get_logger().info('Calling /spot2/arm_stow...')
            stow_request = Trigger.Request()
            stow_future = self.stow_client.call_async(stow_request)
            rclpy.spin_until_future_complete(self, stow_future, timeout_sec=10.0)

            if stow_future.result() is not None:
                response = stow_future.result()
                if response.success:
                    self.get_logger().info('Arm stowed successfully')
                else:
                    self.get_logger().warn(f'Stow failed: {response.message}')
                    exit(1)
            else:
                self.get_logger().warn('Stow service call failed')
                exit(1)
        else:
            self.get_logger().warn('Stow service not available')
            exit(1)

        # Set up TF2 listener
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        # Wait for TF to be available
        self.get_logger().info('Waiting for TF spot2/odom -> spot2/body...')

        # Get initial pose from TF (spot2/odom -> body)
        transform = None
        max_retries = 10
        for i in range(max_retries):
            # Spin a bit to allow TF messages to be received
            rclpy.spin_once(self, timeout_sec=0.5)

            # Check if transform is available
            if self.tf_buffer.can_transform('spot2/odom', 'spot2/body', rclpy.time.Time()):
                try:
                    # Look up the transform
                    transform = self.tf_buffer.lookup_transform('spot2/odom', 'spot2/body', rclpy.time.Time())
                    self.get_logger().info('Successfully got TF transform')
                    break
                except Exception as e:
                    self.get_logger().warn(f'can_transform returned True but lookup failed: {e}')
            else:
                if i < max_retries - 1:
                    self.get_logger().info(f'Waiting for TF... (attempt {i+1}/{max_retries})')
                    time.sleep(0.5)
                else:
                    self.get_logger().error(f'Transform not available after {max_retries} attempts')

        if transform is not None:
            initial_x = transform.transform.translation.x
            initial_y = transform.transform.translation.y
            initial_z = transform.transform.translation.z + 0.467  # Add 0.467 to z

            self.get_logger().info(f'Initial pose from TF: x={initial_x:.3f}, y={initial_y:.3f}, z={initial_z:.3f}')

            # Define positions [x, y, z] based on initial pose
            self.positions = []
            for x_offset in np.arange(0.4, 0.7, 0.01):
                self.positions.append([initial_x + x_offset, initial_y, initial_z])
            self.positions += list(reversed(self.positions))
        else:
            self.get_logger().error('Could not get TF transform')
            exit(1)

        self.current_index = 0

        # Wait for connections
        time.sleep(1.0)

        # Create timer to publish at 0.5 Hz (2 second interval)
        self.timer = self.create_timer(1 / 20.0, self.publish_position)

        self.get_logger().info('Arm position cycler started')
        self.get_logger().info(f'Cycling through {len(self.positions)} positions')

    def publish_position(self):
        msg = PoseStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'odom'

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
        self.get_logger().info(f'Publishing position: [{pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}]')

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
