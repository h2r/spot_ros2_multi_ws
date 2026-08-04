#!/usr/bin/env python3
"""
Replays the recorded actions from a LeRobot dataset back to a real Spot robot.

Reads the raw 'action' column straight out of the dataset's parquet file (not
through ros2 bag, which doesn't reliably capture these command topics -- see
bag_trigger/listener_node.py for how the dataset is originally recorded) and
republishes each frame's action to the same topics the recorder listened on,
at the dataset's recorded fps.

Before playback:
  1. The recording's first-frame leg joints are compared against a known
     standing reference. If they look like the episode started sitting, the
     /<spot_name>/sit service is called (otherwise /<spot_name>/stand, which
     is also what happens if the robot isn't already standing for some other
     reason). See ensure_start_stance() -- --stance-knee-tolerance controls
     the sit/stand classification, --no-stance-pose disables this step.
  2. The arm/gripper are driven to the recording's first frame and held
     there for --start-dwell seconds, so the replay always starts from the
     exact pose it was recorded from (--no-start-pose disables this step).

This moves the physical robot, including an autonomous sit/stand before you
see any of the recorded action -- have someone on the e-stop. Make sure the
robot is powered on before running this.
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
from sensor_msgs.msg import JointState
from std_msgs.msg import Float32
from std_srvs.srv import Trigger

# Matches the action layout written by bag_trigger/listener_node.py:
# cmd_vel (6) + arm_pose position+quaternion (7) + gripper_angle (1) = 14
ARM_POSE_FRAME_ID = "body"

# observation.state joint order written by bag_trigger/listener_node.py:
# legs (indices 0-11), then arm+gripper (indices 12-18).
ARM_JOINT_NAMES = ["arm_sh0", "arm_sh1", "arm_el0", "arm_el1", "arm_wr0", "arm_wr1", "arm_f1x"]
ARM_JOINT_SLICE = slice(12, 19)

# Knee joint indices within the 12 leg joints (front_left, front_right, rear_left, rear_right).
LEG_KNEE_INDICES = [2, 5, 8, 11]

# Reference standing knee angles (radians), for classifying whether a recording's first
# frame began standing or sitting -- see ensure_start_stance(). Derived empirically by
# averaging frame-0 knee angles across this workspace's existing (all-standing) recordings;
# spread across those recordings was under 0.1 rad per knee. This has NOT been validated
# against a real sitting recording -- if you have one, check its knee angles land clearly
# outside --stance-knee-tolerance of this reference, and adjust either value if not.
STANDING_KNEE_REFERENCE = np.array([-1.567, -1.547, -1.560, -1.593], dtype=np.float32)

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
    def __init__(
        self, bag: str, spot_name: str, fps: float,
        recordings_root: str = DEFAULT_RECORDINGS_ROOT, dry_run: bool = False,
    ):
        super().__init__('lerobot_action_player')
        self.dry_run = dry_run
        if self.dry_run:
            self.get_logger().warn(
                "[DRY RUN] No topics will be published and no services will be called. "
                "The robot will not move."
            )

        self.cmd_vel_pub = self.create_publisher(Twist, f'/{spot_name}/cmd_vel', 10)
        self.arm_pose_pub = self.create_publisher(PoseStamped, f'/{spot_name}/arm_pose_commands', 10)
        self.gripper_pub = self.create_publisher(Float32, f'/{spot_name}/gripper_angle_command', 10)

        self.latest_joint_state = None
        self.joint_sub = self.create_subscription(
            JointState, f'/{spot_name}/joint_states', self._joint_state_callback, 10
        )
        self.sit_client = self.create_client(Trigger, f'/{spot_name}/sit')
        self.stand_client = self.create_client(Trigger, f'/{spot_name}/stand')

        parquet_paths = resolve_parquet_paths(bag, recordings_root)
        df = pd.concat([pd.read_parquet(p) for p in parquet_paths], ignore_index=True)
        self.actions = np.stack(df['action'].values)
        self.start_joint_state = np.stack(df['observation.state'].values)[0]
        self.period = 1.0 / fps
        self.get_logger().info(
            f"Loaded {len(self.actions)} actions from {', '.join(parquet_paths)} "
            f"({len(self.actions) / fps:.1f}s at {fps} fps)"
        )

    def _joint_state_callback(self, msg: JointState):
        if len(msg.position) >= 19:
            self.latest_joint_state = np.array(msg.position[:19], dtype=np.float32)

    def ensure_start_stance(self, knee_tolerance: float, settle_sec: float, service_timeout_sec: float = 10.0):
        """Classify whether the recording's first frame began sitting or standing
        (by comparing its leg knee angles to STANDING_KNEE_REFERENCE) and call the
        matching /sit or /stand service, so the body is in roughly the right stance
        before move_to_start() positions the arm on top of it.

        This is coarse and binary -- unlike the arm, legs are dynamically balanced by
        Spot's onboard controller, so there's no way to replay their exact recorded
        joint trajectory open-loop the way move_to_start() does for the arm. Sit vs.
        stand is the only stance distinction actually commandable here.
        """
        start_knees = self.start_joint_state[LEG_KNEE_INDICES]
        deviation = np.abs(start_knees - STANDING_KNEE_REFERENCE)
        max_deviation = float(np.max(deviation))
        began_standing = max_deviation <= knee_tolerance

        client = self.stand_client if began_standing else self.sit_client
        service_name = "stand" if began_standing else "sit"
        prefix = "[DRY RUN] " if self.dry_run else ""
        self.get_logger().info(
            f"{prefix}Recording's first frame looks like it began "
            f"{'standing' if began_standing else 'sitting'} "
            f"(max knee deviation from standing reference: {max_deviation:.3f} rad, "
            f"tolerance {knee_tolerance:.3f} rad). "
            f"{'Would call' if self.dry_run else 'Calling'} /{service_name}..."
        )

        if self.dry_run:
            return

        if not client.wait_for_service(timeout_sec=service_timeout_sec):
            self.get_logger().error(
                f"'{service_name}' service did not become available within {service_timeout_sec:.0f}s. "
                "Skipping automatic stance change -- make sure the robot is in the right stance yourself."
            )
            return

        future = client.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(self, future, timeout_sec=service_timeout_sec)
        result = future.result()
        if result is None or not result.success:
            self.get_logger().error(
                f"'{service_name}' call failed: {getattr(result, 'message', 'no response')}. "
                "Proceeding anyway -- check the robot's stance before trusting playback."
            )
        else:
            self.get_logger().info(f"'{service_name}' succeeded: {result.message}")

        self.get_logger().info(f"Waiting {settle_sec:.1f}s for the {service_name} motion to settle...")
        end_time = time.monotonic() + settle_sec
        while time.monotonic() < end_time:
            rclpy.spin_once(self, timeout_sec=max(0.0, end_time - time.monotonic()))

    def move_to_start(self, dwell_sec: float, tolerance: float, hz: float = 10.0):
        """Drive the arm/gripper to the recording's first frame and hold them
        there for dwell_sec, so playback always starts from the pose it was
        recorded at instead of wherever the arm happens to be sitting now.

        Deliberately does not publish cmd_vel here: the body should just stay
        standing still during this step, not move toward some remembered
        world position (cmd_vel is a velocity command, so it has no notion of
        a "start position" to return the body to).

        This is open-loop (no arrival guarantee) while holding the pose, but
        afterward it checks the arm's actual /joint_states against the
        recording's first-frame joint positions and warns if they're still
        far apart, so a too-short dwell for a big arm move doesn't silently
        start playback from the wrong pose.
        """
        arm_pose, gripper_angle = self.actions[0][6:13], self.actions[0][13]

        pose = PoseStamped()
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

        gripper_msg = Float32()
        gripper_msg.data = float(gripper_angle)

        prefix = "[DRY RUN] " if self.dry_run else ""
        verb = "Would move" if self.dry_run else "Moving"
        self.get_logger().info(
            f"{prefix}{verb} arm to recorded start pose and hold for {dwell_sec:.1f}s..."
        )
        period = 1.0 / hz
        for _ in range(int(dwell_sec * hz)):
            if not self.dry_run:
                pose.header.stamp = self.get_clock().now().to_msg()
                self.arm_pose_pub.publish(pose)
                self.gripper_pub.publish(gripper_msg)
            rclpy.spin_once(self, timeout_sec=period)

        if self.dry_run:
            self.get_logger().info(
                "[DRY RUN] Skipping arrival check (nothing was actually moved)."
            )
            return

        if self.latest_joint_state is None:
            self.get_logger().warn(
                "No /joint_states feedback received; cannot verify the arm actually "
                "reached the recorded start pose. Proceeding with playback anyway."
            )
            return

        target = self.start_joint_state[ARM_JOINT_SLICE]
        current = self.latest_joint_state[ARM_JOINT_SLICE]
        diff = np.abs(current - target)
        max_diff = float(np.max(diff))
        if max_diff > tolerance:
            worst = int(np.argmax(diff))
            self.get_logger().warn(
                f"Arm may not have reached the recorded start pose: '{ARM_JOINT_NAMES[worst]}' "
                f"is off by {diff[worst]:.3f} rad (tolerance {tolerance:.3f} rad). "
                "Consider increasing --start-dwell before trusting this playback."
            )
        else:
            self.get_logger().info(
                f"Confirmed arm is within tolerance of the recorded start pose "
                f"(max joint error {max_diff:.3f} rad). Beginning playback."
            )

    def play(self):
        verb = "[DRY RUN] Would play" if self.dry_run else "Playing"
        for i, action in enumerate(self.actions):
            cmd_vel, arm_pose, gripper_angle = action[0:6], action[6:13], action[13]

            if not self.dry_run:
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
                self.get_logger().info(f"{verb} frame {i}/{len(self.actions)}")

            time.sleep(self.period)

        self.get_logger().info("[DRY RUN] Playback simulation finished." if self.dry_run else "Playback finished.")


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
    parser.add_argument(
        '--start-dwell', type=float, default=3.0,
        help="Seconds to hold the arm at the recording's first-frame pose before playback starts",
    )
    parser.add_argument(
        '--no-start-pose', action='store_true',
        help="Skip auto-driving to the recorded start pose; assume the robot is already positioned there",
    )
    parser.add_argument(
        '--start-tolerance', type=float, default=0.15,
        help="Max allowed per-joint arm error (radians) after --start-dwell before warning that "
             "the arm may not have reached the recorded start pose",
    )
    parser.add_argument(
        '--no-stance-pose', action='store_true',
        help="Skip auto sit/stand before playback; assume the robot is already in the right stance",
    )
    parser.add_argument(
        '--stance-knee-tolerance', type=float, default=0.35,
        help="Max knee-angle deviation (radians) from the standing reference before the recording's "
             "first frame is classified as having begun sitting rather than standing",
    )
    parser.add_argument(
        '--stance-settle', type=float, default=3.0,
        help="Seconds to wait after the sit/stand service call before positioning the arm",
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help="Run the full sit/stand + arm-positioning + playback logic without publishing to any "
             "topic or calling any service -- the robot will not move. Useful for testing timing "
             "and the sit/stand classification safely.",
    )
    parsed = parser.parse_args(args)

    rclpy.init()
    node = LerobotActionPlayer(
        parsed.bag, parsed.spot_name, parsed.fps * parsed.rate, parsed.recordings_root, parsed.dry_run
    )
    try:
        if not parsed.no_stance_pose:
            node.ensure_start_stance(parsed.stance_knee_tolerance, parsed.stance_settle)
        if not parsed.no_start_pose:
            node.move_to_start(parsed.start_dwell, parsed.start_tolerance)
        node.play()
    except KeyboardInterrupt:
        node.get_logger().warn("Playback interrupted by user.")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
