#!/usr/bin/env python3
"""
Replays a dual_listener_node.py recording back onto both real Spot robots.

Reads the 'action' column straight out of the dataset's parquet file and republishes
each frame's per-robot half to the same topics dual_listener_node.py listened on, at
the dataset's recorded fps. This is the two-robot counterpart to
lerobot_action_player.py -- do NOT use lerobot_action_player.py on a dual recording:
its 14-dim action slicing happens to land exactly on robot A's data without erroring,
so it would silently replay only one robot and never touch the other.

Before playback, per robot (both run in parallel where possible, not one-then-the-other):
  1. Each robot's first-frame leg joints are compared against a standing reference to
     decide whether that robot began sitting or standing, and the matching /sit or
     /stand service is called for it. --no-stance-pose disables this step.
  2. Each robot's arm/gripper are driven to its recording's first frame and held for
     --start-dwell seconds. --no-start-pose disables this step.

This moves TWO physical robots at once, including autonomous sit/stand before you see
any of the recorded action -- have someone on the e-stop for both. Make sure both
robots are powered on before running this.
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

ROLES = ('a', 'b')

ARM_POSE_FRAME_ID = "body"

# Per-robot layout within the concatenated dual state/action arrays -- must match
# dual_listener_node.py's lerobot_features (observation.state: a's 19 then b's 19;
# action: a's 14 then b's 14).
ROBOT_STATE_DIM = 19
ROBOT_ACTION_DIM = 14


def robot_state_slice(role: str) -> slice:
    i = ROLES.index(role)
    return slice(i * ROBOT_STATE_DIM, (i + 1) * ROBOT_STATE_DIM)


def robot_action_slice(role: str) -> slice:
    i = ROLES.index(role)
    return slice(i * ROBOT_ACTION_DIM, (i + 1) * ROBOT_ACTION_DIM)


# Joint order within each robot's 19-dim state slice, same as listener_node.py: legs
# (0-11) then arm+gripper (12-18).
ARM_JOINT_NAMES = ["arm_sh0", "arm_sh1", "arm_el0", "arm_el1", "arm_wr0", "arm_wr1", "arm_f1x"]
ARM_JOINT_SLICE = slice(12, 19)
LEG_KNEE_INDICES = [2, 5, 8, 11]

# Same reference/caveat as lerobot_action_player.py: derived from this workspace's
# existing standing recordings, NOT validated against a real sitting recording.
# Assumed to generalize across both robots (same Spot model/geometry) -- if one robot
# is a different size/config, recalibrate separately.
STANDING_KNEE_REFERENCE = np.array([-1.567, -1.547, -1.560, -1.593], dtype=np.float32)

RECORDINGS_ROOT = "/ros2_ws/recordings"
# Fallback when neither --folder nor --recordings-root is given -- matches
# dual_listener_node.py's own fallback for when no session is active.
LEGACY_DUAL_RECORDINGS_ROOT = "/ros2_ws/recordings_dual"


def resolve_recordings_root(explicit_root: str, folder: str) -> str:
    """Where to look for recordings:
      1. --recordings-root, if given verbatim (advanced/manual use, a full path).
      2. --folder <name> -> recordings/<name>/, taken exactly as given -- no automatic
         "_dual" suffix and no reading of set_recording_session.sh's active session, so
         e.g. --folder demo vs. --folder demo_dual are fully distinct and explicit, and
         playback never silently changes because someone else changed the active
         session elsewhere.
      3. Neither given -> the legacy top-level recordings_dual/, unchanged from before
         sessions existed.
    """
    if explicit_root and folder:
        raise ValueError("--recordings-root and --folder are mutually exclusive")
    if explicit_root:
        return explicit_root
    if folder:
        return f"{RECORDINGS_ROOT}/{folder}"
    return LEGACY_DUAL_RECORDINGS_ROOT


def resolve_parquet_paths(bag: str, recordings_root: str) -> list:
    """Same resolution rules as lerobot_action_player.py, but for dual_listener_node.py's
    "dualbag_<timestamp>" / "dualbag_<timestamp>_lerobot" naming instead of "bag_...".
    """
    if os.path.isfile(bag) and bag.endswith(".parquet"):
        return [bag]

    dataset_dir = bag if os.path.isdir(bag) else None
    if dataset_dir is None:
        for candidate in (f"dualbag_{bag}_lerobot", f"{bag}_lerobot", bag, f"dualbag_{bag}"):
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


class DualLerobotActionPlayer(Node):
    def __init__(
        self, bag: str, spot_a_name: str, spot_b_name: str, fps: float,
        recordings_root: str = LEGACY_DUAL_RECORDINGS_ROOT, dry_run: bool = False,
    ):
        super().__init__('dual_lerobot_action_player')
        self.dry_run = dry_run
        self.spot_names = {'a': spot_a_name, 'b': spot_b_name}
        if self.dry_run:
            self.get_logger().warn(
                "[DRY RUN] No topics will be published and no services will be called. "
                "Neither robot will move."
            )

        self.cmd_vel_pub = {}
        self.arm_pose_pub = {}
        self.gripper_pub = {}
        self.sit_client = {}
        self.stand_client = {}
        self.latest_joint_state = {'a': None, 'b': None}

        for role in ROLES:
            name = self.spot_names[role]
            self.cmd_vel_pub[role] = self.create_publisher(Twist, f'/{name}/cmd_vel', 10)
            self.arm_pose_pub[role] = self.create_publisher(PoseStamped, f'/{name}/arm_pose_commands', 10)
            self.gripper_pub[role] = self.create_publisher(Float32, f'/{name}/gripper_angle_command', 10)
            self.create_subscription(
                JointState, f'/{name}/joint_states',
                lambda msg, role=role: self._joint_state_callback(role, msg), 10,
            )
            self.sit_client[role] = self.create_client(Trigger, f'/{name}/sit')
            self.stand_client[role] = self.create_client(Trigger, f'/{name}/stand')

        parquet_paths = resolve_parquet_paths(bag, recordings_root)
        df = pd.concat([pd.read_parquet(p) for p in parquet_paths], ignore_index=True)
        self.actions = np.stack(df['action'].values)
        self.start_joint_state = np.stack(df['observation.state'].values)[0]
        self.period = 1.0 / fps
        self.get_logger().info(
            f"Loaded {len(self.actions)} actions from {', '.join(parquet_paths)} "
            f"({len(self.actions) / fps:.1f}s at {fps} fps) for a=\"{spot_a_name}\" b=\"{spot_b_name}\""
        )

    def _joint_state_callback(self, role: str, msg: JointState):
        if len(msg.position) >= ROBOT_STATE_DIM:
            self.latest_joint_state[role] = np.array(msg.position[:ROBOT_STATE_DIM], dtype=np.float32)

    def ensure_start_stance(self, knee_tolerance: float, settle_sec: float, service_timeout_sec: float = 10.0):
        """Same idea as lerobot_action_player.py's ensure_start_stance(), run for both
        robots. Sit/stand calls for both robots are fired concurrently (not one after
        the other) so the physical motions happen in parallel, then a single shared
        settle wait covers both.
        """
        futures = {}
        service_names = {}
        for role in ROLES:
            name = self.spot_names[role]
            start_knees = self.start_joint_state[robot_state_slice(role)][LEG_KNEE_INDICES]
            deviation = np.abs(start_knees - STANDING_KNEE_REFERENCE)
            max_deviation = float(np.max(deviation))
            began_standing = max_deviation <= knee_tolerance

            client = self.stand_client[role] if began_standing else self.sit_client[role]
            service_name = "stand" if began_standing else "sit"
            service_names[role] = service_name
            prefix = "[DRY RUN] " if self.dry_run else ""
            self.get_logger().info(
                f"{prefix}[{name}] Recording's first frame looks like it began "
                f"{'standing' if began_standing else 'sitting'} "
                f"(max knee deviation: {max_deviation:.3f} rad, tolerance {knee_tolerance:.3f} rad). "
                f"{'Would call' if self.dry_run else 'Calling'} /{name}/{service_name}..."
            )

            if self.dry_run:
                continue

            if not client.wait_for_service(timeout_sec=service_timeout_sec):
                self.get_logger().error(
                    f"[{name}] '{service_name}' service did not become available within "
                    f"{service_timeout_sec:.0f}s. Skipping automatic stance change for this robot -- "
                    "make sure it's in the right stance yourself."
                )
                continue

            futures[role] = (client.call_async(Trigger.Request()), service_name)

        if self.dry_run:
            return

        for role, (future, service_name) in futures.items():
            name = self.spot_names[role]
            rclpy.spin_until_future_complete(self, future, timeout_sec=service_timeout_sec)
            result = future.result()
            if result is None or not result.success:
                self.get_logger().error(
                    f"[{name}] '{service_name}' call failed: {getattr(result, 'message', 'no response')}. "
                    "Proceeding anyway -- check this robot's stance before trusting playback."
                )
            else:
                self.get_logger().info(f"[{name}] '{service_name}' succeeded: {result.message}")

        if futures:
            self.get_logger().info(f"Waiting {settle_sec:.1f}s for sit/stand motion to settle...")
            end_time = time.monotonic() + settle_sec
            while time.monotonic() < end_time:
                rclpy.spin_once(self, timeout_sec=max(0.0, end_time - time.monotonic()))

    def move_to_start(self, dwell_sec: float, tolerance: float, hz: float = 10.0):
        """Same idea as lerobot_action_player.py's move_to_start(), run for both robots
        in the same publish loop (one dwell period covers both, not sequential).
        """
        poses = {}
        grippers = {}
        for role in ROLES:
            action = self.actions[0][robot_action_slice(role)]
            arm_pose, gripper_angle = action[6:13], action[13]

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
            poses[role] = pose

            gripper_msg = Float32()
            gripper_msg.data = float(gripper_angle)
            grippers[role] = gripper_msg

        prefix = "[DRY RUN] " if self.dry_run else ""
        verb = "Would move" if self.dry_run else "Moving"
        self.get_logger().info(
            f"{prefix}{verb} both robots' arms to their recorded start pose and hold for {dwell_sec:.1f}s..."
        )
        period = 1.0 / hz
        for _ in range(int(dwell_sec * hz)):
            if not self.dry_run:
                now = self.get_clock().now().to_msg()
                for role in ROLES:
                    poses[role].header.stamp = now
                    self.arm_pose_pub[role].publish(poses[role])
                    self.gripper_pub[role].publish(grippers[role])
            rclpy.spin_once(self, timeout_sec=period)

        if self.dry_run:
            self.get_logger().info("[DRY RUN] Skipping arrival check (nothing was actually moved).")
            return

        for role in ROLES:
            name = self.spot_names[role]
            if self.latest_joint_state[role] is None:
                self.get_logger().warn(
                    f"[{name}] No /joint_states feedback received; cannot verify the arm actually "
                    "reached the recorded start pose."
                )
                continue

            target = self.start_joint_state[robot_state_slice(role)][ARM_JOINT_SLICE]
            current = self.latest_joint_state[role][ARM_JOINT_SLICE]
            diff = np.abs(current - target)
            max_diff = float(np.max(diff))
            if max_diff > tolerance:
                worst = int(np.argmax(diff))
                self.get_logger().warn(
                    f"[{name}] Arm may not have reached the recorded start pose: "
                    f"'{ARM_JOINT_NAMES[worst]}' is off by {diff[worst]:.3f} rad "
                    f"(tolerance {tolerance:.3f} rad). Consider increasing --start-dwell."
                )
            else:
                self.get_logger().info(
                    f"[{name}] Confirmed arm is within tolerance of the recorded start pose "
                    f"(max joint error {max_diff:.3f} rad)."
                )

    def play(self):
        verb = "[DRY RUN] Would play" if self.dry_run else "Playing"
        for i, action in enumerate(self.actions):
            if not self.dry_run:
                for role in ROLES:
                    robot_action = action[robot_action_slice(role)]
                    cmd_vel, arm_pose, gripper_angle = robot_action[0:6], robot_action[6:13], robot_action[13]

                    twist = Twist()
                    twist.linear.x, twist.linear.y, twist.linear.z = (float(v) for v in cmd_vel[0:3])
                    twist.angular.x, twist.angular.y, twist.angular.z = (float(v) for v in cmd_vel[3:6])
                    self.cmd_vel_pub[role].publish(twist)

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
                    self.arm_pose_pub[role].publish(pose)

                    gripper_msg = Float32()
                    gripper_msg.data = float(gripper_angle)
                    self.gripper_pub[role].publish(gripper_msg)

            if i % 15 == 0:
                self.get_logger().info(f"{verb} frame {i}/{len(self.actions)}")

            time.sleep(self.period)

        self.get_logger().info("[DRY RUN] Playback simulation finished." if self.dry_run else "Playback finished.")


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        'bag',
        help="Which dual recording to replay. Any of: a bag timestamp/name "
             "('20260804_014112', 'dualbag_20260804_014112'), a full path to a "
             "lerobot dataset directory, or a full path straight to a .parquet file.",
    )
    parser.add_argument('--spot-a-name', default='spot')
    parser.add_argument('--spot-b-name', default='spot2')
    parser.add_argument('--fps', type=float, default=8.0, help="Must match the dataset's recorded fps")
    parser.add_argument('--rate', type=float, default=1.0, help="Playback speed multiplier (0.5 = half speed)")
    parser.add_argument(
        '--recordings-root', default=None,
        help="Directory to look up a bare bag name/timestamp under, given verbatim. Mutually "
             "exclusive with --folder. If neither is given, defaults to recordings_dual/.",
    )
    parser.add_argument(
        '--folder', default=None,
        help="Look in recordings/<folder>/ for the bag name/timestamp, e.g. --folder demo_dual. "
             "Taken exactly as given -- pass the full folder name yourself (this does NOT read "
             "set_recording_session.sh's active session or add any suffix). Mutually exclusive "
             "with --recordings-root.",
    )
    parser.add_argument(
        '--start-dwell', type=float, default=3.0,
        help="Seconds to hold both arms at their recording's first-frame pose before playback starts",
    )
    parser.add_argument(
        '--no-start-pose', action='store_true',
        help="Skip auto-driving to the recorded start pose for both robots",
    )
    parser.add_argument(
        '--start-tolerance', type=float, default=0.15,
        help="Max allowed per-joint arm error (radians) after --start-dwell before warning",
    )
    parser.add_argument(
        '--no-stance-pose', action='store_true',
        help="Skip auto sit/stand before playback for both robots",
    )
    parser.add_argument(
        '--stance-knee-tolerance', type=float, default=0.35,
        help="Max knee-angle deviation (radians) from the standing reference before a robot's first "
             "frame is classified as having begun sitting rather than standing",
    )
    parser.add_argument(
        '--stance-settle', type=float, default=3.0,
        help="Seconds to wait after the sit/stand service calls before positioning either arm",
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help="Run the full sit/stand + arm-positioning + playback logic without publishing to any "
             "topic or calling any service on either robot.",
    )
    parsed = parser.parse_args(args)

    try:
        recordings_root = resolve_recordings_root(parsed.recordings_root, parsed.folder)
    except ValueError as e:
        parser.error(str(e))

    rclpy.init()
    node = DualLerobotActionPlayer(
        parsed.bag, parsed.spot_a_name, parsed.spot_b_name, parsed.fps * parsed.rate,
        recordings_root, parsed.dry_run,
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
