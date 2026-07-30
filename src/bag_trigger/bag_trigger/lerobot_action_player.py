#!/usr/bin/env python3
"""
Replays the recorded actions from a LeRobot dataset back to a real Spot robot.

Reads the raw 'action' column straight out of the dataset's parquet file (not
through ros2 bag, which doesn't reliably capture these command topics -- see
bag_trigger/listener_node.py for how the dataset is originally recorded) and
republishes each frame's action to the same topics the recorder listened on,
at the dataset's recorded fps.

This moves the physical robot. Make sure the robot is powered on, standing,
and in the same starting arm configuration the recording began from (e.g. via
the /<spot_name>/arm_stow service) before running this.
"""
import argparse
import glob
import os
import time

import numpy as np
import pandas as pd

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, PoseStamped
from std_msgs.msg import Float32

# Matches the action layout written by bag_trigger/listener_node.py:
# cmd_vel (6) + arm_pose position+quaternion (7) + gripper_angle (1) = 14
ARM_POSE_FRAME_ID = "body"

# Where listener_node.py writes new recordings (see its trigger_callback).
DEFAULT_RECORDINGS_ROOT = "/ros2_ws/recordings"


def resolve_parquet_paths(bag: str, recordings_root: str) -> list:
    """Turn a bag name, dataset dir, or parquet path into concrete parquet file(s).

    Accepts, in order of how directly they resolve:
      - a path straight to a .parquet file
      - a path to a lerobot dataset root (dir containing data/chunk-*/file-*.parquet)
      - a bag name to look up under recordings_root, in any of the forms
        listener_node.py's timestamped output uses: "20260727_175555",
        "bag_20260727_175555", or "bag_20260727_175555_lerobot"
    """
    if os.path.isfile(bag) and bag.endswith(".parquet"):
        return [bag]

    dataset_dir = bag if os.path.isdir(bag) else None
    if dataset_dir is None:
        # Check "_lerobot"-suffixed variants first: a raw (non-lerobot) bag dir of
        # the same name also exists alongside every recording (see listener_node.py's
        # trigger_callback) but has no parquet data, so it must not shadow the real one.
        for candidate in (f"bag_{bag}_lerobot", f"{bag}_lerobot", bag, f"bag_{bag}"):
            candidate_path = os.path.join(recordings_root, candidate)
            if os.path.isdir(candidate_path):
                dataset_dir = candidate_path
                break

    if dataset_dir is None:
        raise FileNotFoundError(
            f"Could not find a recording matching '{bag}' under {recordings_root} "
            "(and it isn't a .parquet file or dataset directory itself)."
        )

    chunks = sorted(glob.glob(os.path.join(dataset_dir, "data", "chunk-*", "file-*.parquet")))
    if not chunks:
        raise FileNotFoundError(f"No data/chunk-*/file-*.parquet found under {dataset_dir}")
    return chunks


class LerobotActionPlayer(Node):
    def __init__(self, bag: str, spot_name: str, fps: float, recordings_root: str = DEFAULT_RECORDINGS_ROOT):
        super().__init__('lerobot_action_player')

        self.cmd_vel_pub = self.create_publisher(Twist, f'/{spot_name}/cmd_vel', 10)
        self.arm_pose_pub = self.create_publisher(PoseStamped, f'/{spot_name}/arm_pose_commands', 10)
        self.gripper_pub = self.create_publisher(Float32, f'/{spot_name}/gripper_angle_command', 10)

        parquet_paths = resolve_parquet_paths(bag, recordings_root)
        df = pd.concat([pd.read_parquet(p) for p in parquet_paths], ignore_index=True)
        self.actions = np.stack(df['action'].values)
        self.period = 1.0 / fps
        self.get_logger().info(
            f"Loaded {len(self.actions)} actions from {', '.join(parquet_paths)} "
            f"({len(self.actions) / fps:.1f}s at {fps} fps)"
        )

    def play(self):
        for i, action in enumerate(self.actions):
            cmd_vel, arm_pose, gripper_angle = action[0:6], action[6:13], action[13]

            twist = Twist()
            twist.linear.x, twist.linear.y, twist.linear.z = (float(v) for v in cmd_vel[0:3])
            twist.angular.x, twist.angular.y, twist.angular.z = (float(v) for v in cmd_vel[3:6])
            self.cmd_vel_pub.publish(twist)

            pose = PoseStamped()
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.header.frame_id = ARM_POSE_FRAME_ID
            pose.pose.position.x, pose.pose.position.y, pose.pose.position.z = (
                float(v) for v in arm_pose[0:3]
            )
            (
                pose.pose.orientation.x,
                pose.pose.orientation.y,
                pose.pose.orientation.z,
                pose.pose.orientation.w,
            ) = (float(v) for v in arm_pose[3:7])
            self.arm_pose_pub.publish(pose)

            gripper_msg = Float32()
            gripper_msg.data = float(gripper_angle)
            self.gripper_pub.publish(gripper_msg)

            if i % 15 == 0:
                self.get_logger().info(f"Playing frame {i}/{len(self.actions)}")

            time.sleep(self.period)

        self.get_logger().info("Playback finished.")


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'bag',
        help="Which recording to replay. Any of: a bag timestamp/name "
             "('20260727_175555', 'bag_20260727_175555'), a full path to a "
             "lerobot dataset directory, or a full path straight to a .parquet file.",
    )
    parser.add_argument('--spot_name', default='spot')
    parser.add_argument('--fps', type=float, default=15.0, help="Must match the dataset's recorded fps")
    parser.add_argument('--rate', type=float, default=1.0, help="Playback speed multiplier (0.5 = half speed)")
    parser.add_argument(
        '--recordings_root', default=DEFAULT_RECORDINGS_ROOT,
        help="Directory to look up a bare bag name/timestamp under",
    )
    parsed = parser.parse_args(args)

    rclpy.init()
    node = LerobotActionPlayer(
        parsed.bag, parsed.spot_name, parsed.fps * parsed.rate, parsed.recordings_root
    )
    try:
        node.play()
    except KeyboardInterrupt:
        node.get_logger().warn("Playback interrupted by user.")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
