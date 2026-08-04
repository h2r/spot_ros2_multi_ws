import numpy as np
if not hasattr(np, 'bool8'):
    np.bool8 = np.bool_

import rclpy
from rclpy.node import Node
from std_srvs.srv import SetBool
import subprocess
import os
import signal
import time
from datetime import datetime
import cv2

# ROS Message & Conversion Imports
from sensor_msgs.msg import Image, JointState
from geometry_msgs.msg import Twist, PoseStamped
from std_msgs.msg import Float32
from cv_bridge import CvBridge

# LeRobot Imports
from lerobot.datasets.lerobot_dataset import LeRobotDataset

RECORDINGS_ROOT = "/ros2_ws/recordings"
# Same file set_recording_session.sh writes / listener_node.py reads -- shared
# between single- and dual-robot recording, so setting one session name affects
# both. Dual recordings go into "<session>_dual" rather than "<session>" so the
# two recorders never write into the same folder for the same session name.
RECORDING_SESSION_FILE = "/ros2_ws/.recording_session"

# Joint order within each robot's 19-dim state, same as listener_node.py: legs
# (0-11) then arm+gripper (12-18) -- see spot_joint_map.hpp.
_STATE_JOINT_NAMES = [
    "front_left_hip_x", "front_left_hip_y", "front_left_knee",
    "front_right_hip_x", "front_right_hip_y", "front_right_knee",
    "rear_left_hip_x", "rear_left_hip_y", "rear_left_knee",
    "rear_right_hip_x", "rear_right_hip_y", "rear_right_knee",
    "arm_sh0", "arm_sh1", "arm_el0", "arm_el1", "arm_wr0", "arm_wr1", "arm_f1x",
]
_ACTION_NAMES = [
    "linear_x", "linear_y", "linear_z", "angular_x", "angular_y", "angular_z",
    "arm_pos_x", "arm_pos_y", "arm_pos_z",
    "arm_quat_x", "arm_quat_y", "arm_quat_z", "arm_quat_w",
    "gripper_angle",
]


class DualBagTriggerNode(Node):
    """Records both Spots into a single, jointly-aligned LeRobot dataset.

    Unlike listener_node.py (which records one robot's demonstration), this is for
    tasks where a single policy needs to see both robots together -- e.g. coordinated
    or collaborative behavior -- so every frame here has both robots' observations
    and actions side by side, not two separate per-robot datasets to merge later.

    Roles are generic "a"/"b" in the dataset schema (observation.images.a_front,
    b_front, ...) regardless of which spot_*_name each is bound to at runtime, so the
    schema doesn't change if you point this at different robots later. Which physical
    robot was "a" vs "b" for a given recording is in that recording's ROS log output
    and ros2 bag (all topics, both robots, are captured there via `ros2 bag record -a`).

    Recording only starts if BOTH robots' camera/joint topics are already live --
    unlike single-robot recording, an episode missing one robot's data isn't a valid
    training example for a coordinated policy, so this fails loudly at trigger time
    instead of silently producing a broken episode.

    Binds the SAME /bag_trigger service name listener_node.py uses, on purpose --
    Unity's RecordAction.cs always calls /bag_trigger and shouldn't need to know
    which mode is active. Which behavior "bag_trigger" gives you is just whichever
    of listener_node / dual_listener_node you launched for that session. Run only
    ONE of the two at a time: two servers on the same service name is not reliably
    arbitrated by ROS2, so having both nodes up together means requests could go to
    either one unpredictably.
    """

    def __init__(self):
        super().__init__('dual_bag_trigger_node')

        self.declare_parameter('spot_a_name', 'spot')
        self.declare_parameter('spot_b_name', 'spot2')
        self.spot_a_name = self.get_parameter('spot_a_name').get_parameter_value().string_value
        self.spot_b_name = self.get_parameter('spot_b_name').get_parameter_value().string_value

        # Same service name listener_node.py uses -- see class docstring. Run only one
        # of the two nodes at a time; this is not meant to run alongside listener_node.
        self.srv = self.create_service(SetBool, '/bag_trigger', self.trigger_callback)
        self.bag_process = None

        # --- LeRobot Integration Variables ---
        self.br = CvBridge()
        self.is_recording = False
        self.dataset = None
        # Match this to the slower camera's real publish rate, same lesson as
        # listener_node.py's lerobot_fps -- measure it empirically per setup rather
        # than assuming 8Hz carries over unchanged to a different robot/camera pair.
        self.lerobot_fps = 8
        self.frames_in_current_episode = 0

        # Cache buffers per robot -- "a" and "b" mirror listener_node.py's single set
        # of buffers, just doubled.
        for role in ('a', 'b'):
            setattr(self, f'latest_image_{role}', None)
            setattr(self, f'latest_hand_image_{role}', None)
            setattr(self, f'latest_joints_{role}', None)
            # See listener_node.py: cmd_vel only publishes on stick movement, so
            # default to zero and treat a stale command (no message within
            # cmd_vel_timeout_sec) as zero too, instead of latching forever.
            setattr(self, f'latest_cmd_vel_{role}', np.zeros(6, dtype=np.float32))
            setattr(self, f'latest_cmd_vel_time_{role}', None)
            latest_arm_pose = np.zeros(7, dtype=np.float32)
            latest_arm_pose[6] = 1.0  # quaternion w=1 (identity rotation) as the zero default
            setattr(self, f'latest_arm_pose_{role}', latest_arm_pose)
            setattr(self, f'latest_gripper_angle_{role}', np.zeros(1, dtype=np.float32))
        self.cmd_vel_timeout_sec = 0.3

        self.lerobot_features = {
            "observation.images.a_front": {"dtype": "image", "shape": (224, 224, 3), "names": ["height", "width", "channel"]},
            "observation.images.a_hand": {"dtype": "image", "shape": (224, 224, 3), "names": ["height", "width", "channel"]},
            "observation.images.b_front": {"dtype": "image", "shape": (224, 224, 3), "names": ["height", "width", "channel"]},
            "observation.images.b_hand": {"dtype": "image", "shape": (224, 224, 3), "names": ["height", "width", "channel"]},
            "observation.state": {
                "dtype": "float32",
                "shape": (38,),
                "names": [f"a_{n}" for n in _STATE_JOINT_NAMES] + [f"b_{n}" for n in _STATE_JOINT_NAMES],
            },
            "action": {
                "dtype": "float32",
                "shape": (28,),
                "names": [f"a_{n}" for n in _ACTION_NAMES] + [f"b_{n}" for n in _ACTION_NAMES],
            },
        }

        for role, spot_name in (('a', self.spot_a_name), ('b', self.spot_b_name)):
            self._make_subscriptions(role, spot_name)

        self.record_timer = self.create_timer(1.0 / self.lerobot_fps, self.record_lerobot_frame)
        self.get_logger().info(
            f'Dual Bag Trigger Node initialized: a="{self.spot_a_name}", b="{self.spot_b_name}", '
            f'listening on /bag_trigger (make sure listener_node.py is NOT also running)'
        )

    def _make_subscriptions(self, role: str, spot_name: str):
        self.create_subscription(
            Image, f'/{spot_name}/camera/frontmiddle_virtual/image',
            lambda msg, role=role: self._image_callback(role, msg), 10,
        )
        self.create_subscription(
            Image, f'/{spot_name}/camera/hand/image',
            lambda msg, role=role: self._hand_image_callback(role, msg), 10,
        )
        self.create_subscription(
            JointState, f'/{spot_name}/joint_states',
            lambda msg, role=role: self._joint_callback(role, msg), 10,
        )
        self.create_subscription(
            Twist, f'/{spot_name}/cmd_vel',
            lambda msg, role=role: self._cmd_vel_callback(role, msg), 10,
        )
        self.create_subscription(
            PoseStamped, f'/{spot_name}/arm_pose_commands',
            lambda msg, role=role: self._arm_pose_callback(role, msg), 10,
        )
        self.create_subscription(
            Float32, f'/{spot_name}/gripper_angle_command',
            lambda msg, role=role: self._gripper_angle_callback(role, msg), 10,
        )

    def _image_callback(self, role: str, msg: Image):
        try:
            cv_img = self.br.imgmsg_to_cv2(msg, desired_encoding='rgb8')
            setattr(self, f'latest_image_{role}', cv2.resize(cv_img, (224, 224)))
        except Exception as e:
            self.get_logger().error(f"[{role}] Image conversion error: {e}")

    def _hand_image_callback(self, role: str, msg: Image):
        try:
            cv_img = self.br.imgmsg_to_cv2(msg, desired_encoding='rgb8')
            setattr(self, f'latest_hand_image_{role}', cv2.resize(cv_img, (224, 224)))
        except Exception as e:
            self.get_logger().error(f"[{role}] Hand image conversion error: {e}")

    def _joint_callback(self, role: str, msg: JointState):
        if len(msg.position) >= 19:
            setattr(self, f'latest_joints_{role}', np.array(msg.position[:19], dtype=np.float32))

    def _cmd_vel_callback(self, role: str, msg: Twist):
        setattr(self, f'latest_cmd_vel_{role}', np.array([
            msg.linear.x, msg.linear.y, msg.linear.z,
            msg.angular.x, msg.angular.y, msg.angular.z,
        ], dtype=np.float32))
        setattr(self, f'latest_cmd_vel_time_{role}', self.get_clock().now())

    def _arm_pose_callback(self, role: str, msg: PoseStamped):
        p, q = msg.pose.position, msg.pose.orientation
        setattr(self, f'latest_arm_pose_{role}', np.array(
            [p.x, p.y, p.z, q.x, q.y, q.z, q.w], dtype=np.float32
        ))

    def _gripper_angle_callback(self, role: str, msg: Float32):
        setattr(self, f'latest_gripper_angle_{role}', np.array([msg.data], dtype=np.float32))

    def _missing_streams(self, role: str, spot_name: str) -> list:
        missing = []
        if getattr(self, f'latest_image_{role}') is None:
            missing.append(f"{spot_name} front image")
        if getattr(self, f'latest_hand_image_{role}') is None:
            missing.append(f"{spot_name} hand image")
        if getattr(self, f'latest_joints_{role}') is None:
            missing.append(f"{spot_name} joint_states")
        return missing

    def _cmd_vel_for(self, role: str) -> np.ndarray:
        cmd_vel = getattr(self, f'latest_cmd_vel_{role}')
        cmd_vel_time = getattr(self, f'latest_cmd_vel_time_{role}')
        if cmd_vel_time is None:
            return np.zeros(6, dtype=np.float32)
        age_sec = (self.get_clock().now() - cmd_vel_time).nanoseconds / 1e9
        if age_sec > self.cmd_vel_timeout_sec:
            return np.zeros(6, dtype=np.float32)
        return cmd_vel

    def record_lerobot_frame(self):
        if not self.is_recording or self.dataset is None:
            return

        missing = self._missing_streams('a', self.spot_a_name) + self._missing_streams('b', self.spot_b_name)
        if missing:
            self.get_logger().warn(
                f"Dual recording active but waiting for data streams. Missing: {', '.join(missing)}",
                throttle_duration_sec=3.0
            )
            return

        action_a = np.concatenate([self._cmd_vel_for('a'), self.latest_arm_pose_a, self.latest_gripper_angle_a])
        action_b = np.concatenate([self._cmd_vel_for('b'), self.latest_arm_pose_b, self.latest_gripper_angle_b])

        self.dataset.add_frame({
            "observation.images.a_front": self.latest_image_a,
            "observation.images.a_hand": self.latest_hand_image_a,
            "observation.images.b_front": self.latest_image_b,
            "observation.images.b_hand": self.latest_hand_image_b,
            "observation.state": np.concatenate([self.latest_joints_a, self.latest_joints_b]),
            "action": np.concatenate([action_a, action_b]),
            "task": f"{self.spot_a_name}+{self.spot_b_name} coordinated teleop",
        })
        self.frames_in_current_episode += 1

    def _current_recording_session(self) -> str:
        """Name set via set_recording_session.sh, or '' if none is set."""
        try:
            with open(RECORDING_SESSION_FILE, 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            return ''

    def trigger_callback(self, request, response):
        if request.data:
            # --- START RECORDING ---
            if self.bag_process is not None:
                response.success = False
                response.message = "Recording is already running!"
                return response

            missing = self._missing_streams('a', self.spot_a_name) + self._missing_streams('b', self.spot_b_name)
            if missing:
                response.success = False
                response.message = (
                    "Refusing to start: both robots must be live for a coordinated recording. "
                    f"Missing: {', '.join(missing)}"
                )
                self.get_logger().error(response.message)
                return response

            # A session name goes to recordings/<session>_dual/. Never falls back to a flat
            # top-level folder -- an unset session goes to recordings/unsorted_dual/ instead,
            # so recordings/ itself always stays organized into subfolders.
            session = self._current_recording_session()
            if not session:
                self.get_logger().warn(
                    "No recording session set -- saving to recordings/unsorted_dual/. "
                    "Run ./set_recording_session.sh --name <name> to use a named folder instead."
                )
            effective_session = f"{session or 'unsorted'}_dual"
            session_dir = f"{RECORDINGS_ROOT}/{effective_session}"
            os.makedirs(session_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = f"{session_dir}/dualbag_{timestamp}"
            unique_repo_id = f"jtoribio/{effective_session}_{timestamp}"
            lerobot_root = f"{session_dir}/dualbag_{timestamp}_lerobot"
            self.get_logger().info(f"Recording session '{effective_session}' active -> saving under {session_dir}")

            self.get_logger().info(f"Initializing a brand new dual-robot LeRobot Dataset: {unique_repo_id}")
            self.dataset = LeRobotDataset.create(
                repo_id=unique_repo_id,
                fps=self.lerobot_fps,
                root=lerobot_root,
                features=self.lerobot_features,
                robot_type="spot_dual",
            )

            self.frames_in_current_episode = 0
            self.is_recording = True
            self.get_logger().info(f"Starting ROS2 bag recording (both robots, all topics) to: {output_dir}")

            cmd_str = (
                "export FASTRTPS_DEFAULT_PROFILES_FILE=/ros2_ws/fastdds_config.xml && "
                "source /opt/ros/humble/setup.bash && "
                "source /ros2_ws/install/setup.bash && "
                f"ros2 bag record -a -o {output_dir}"
            )
            self.bag_process = subprocess.Popen(
                cmd_str, shell=True, executable="/bin/bash",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            response.success = True
            response.message = f"Dual-robot recording ({unique_repo_id}) started successfully."

        else:
            # --- STOP RECORDING ---
            if self.bag_process is None:
                response.success = False
                response.message = "No recording process is currently active."
                return response

            self.is_recording = False

            if self.dataset is not None:
                if self.frames_in_current_episode > 0:
                    self.get_logger().info(f"Saving completed episode ({self.frames_in_current_episode} frames)...")
                    self.dataset.save_episode()
                    self.get_logger().info("Finalizing dataset (writing metadata)...")
                    self.dataset.finalize()
                else:
                    self.get_logger().error(
                        "Stopped recording, but ZERO frames were captured! "
                        "Skipping episode save/finalize. Check your ROS topics."
                    )
                self.dataset = None

            self.get_logger().info("Stopping ROS2 bag recording...")
            try:
                pgid = os.getpgid(self.bag_process.pid)
                sigint_time = time.monotonic()
                self.get_logger().info(f"Sent SIGINT to bag process group (pgid={pgid}) at {sigint_time:.2f}")
                os.killpg(pgid, signal.SIGINT)
                try:
                    self.bag_process.wait(timeout=20.0)
                    self.get_logger().info(f"Bag process exited gracefully after {time.monotonic() - sigint_time:.2f}s")
                except subprocess.TimeoutExpired:
                    self.get_logger().warn(f"Bag process did not stop after {time.monotonic() - sigint_time:.2f}s. Force killing...")
                    os.killpg(pgid, signal.SIGKILL)
                    self.bag_process.wait()
                    self.get_logger().warn(f"Bag process force-killed at {time.monotonic() - sigint_time:.2f}s total")
            except ProcessLookupError:
                self.get_logger().warn("Process group missing. Forcing terminal cleanup...")
                os.system('pkill -f "ros2 bag record"')
            finally:
                self.bag_process = None

            response.success = True
            response.message = "Dual-robot recording stopped. (LeRobot saved and finalized)"

        return response


def main(args=None):
    rclpy.init(args=args)
    node = DualBagTriggerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node.dataset is not None:
            node.get_logger().info("Finalizing active LeRobot Dataset metadata before exit...")
            node.dataset.finalize()
        if node.bag_process is not None:
            try:
                os.killpg(os.getpgid(node.bag_process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
