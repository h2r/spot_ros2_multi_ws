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
# Written by set_recording_session.sh -- re-read on every recording start (not just
# once at node startup) so switching sessions with that script takes effect on the
# next /bag_trigger call without restarting this node.
RECORDING_SESSION_FILE = "/ros2_ws/.recording_session"


class BagTriggerNode(Node):
    def __init__(self):
        super().__init__('bag_trigger_node')

        self.declare_parameter('spot_name', 'spot')
        self.spot_name = self.get_parameter('spot_name').get_parameter_value().string_value

        self.srv = self.create_service(SetBool, '/bag_trigger', self.trigger_callback)
        self.bag_process = None
        
        # --- LeRobot Integration Variables ---
        self.br = CvBridge()
        self.is_recording = False
        self.dataset = None
        # Measured across 16 recordings on 2026-08-03: the camera topics (esp. the
        # stitched frontmiddle_virtual feed) actually update at ~8Hz, well under the
        # old 15Hz sample rate, so ~40-59% of "frames" at 15Hz were exact duplicate
        # images (image_callback/hand_image_callback have no staleness check, unlike
        # cmd_vel). Recording at the real cadence avoids baking in duplicate frames.
        self.lerobot_fps = 8

        # Keep track of how many frames we've written in the current run
        self.frames_in_current_episode = 0
        
        # Cache buffers for incoming Spot data
        self.latest_image = None
        self.latest_hand_image = None
        self.latest_joints = None
        # No cmd_vel/arm_pose/gripper message is published while the operator holds
        # things still, so default these to zero instead of blocking recording on them.
        self.latest_cmd_vel = np.zeros(6, dtype=np.float32)
        # /cmd_vel is only published on stick movement, not continuously, so a stray
        # message (e.g. controller drift) would otherwise latch forever. Treat it as
        # stale and fall back to zero once this long has passed since the last message.
        self.latest_cmd_vel_time = None
        self.cmd_vel_timeout_sec = 0.3
        self.latest_arm_pose = np.zeros(7, dtype=np.float32)
        self.latest_arm_pose[6] = 1.0  # quaternion w=1 (identity rotation) as the zero default
        self.latest_gripper_angle = np.zeros(1, dtype=np.float32)

        self.lerobot_features = {
            "observation.images.front": {
                "dtype": "image",
                "shape": (224, 224, 3),
                "names": ["height", "width", "channel"],
            },
            "observation.images.hand": {
                "dtype": "image",
                "shape": (224, 224, 3),
                "names": ["height", "width", "channel"],
            },
            "observation.state": {
                "dtype": "float32",
                "shape": (19,),
                "names": [
                    "front_left_hip_x", "front_left_hip_y", "front_left_knee",
                    "front_right_hip_x", "front_right_hip_y", "front_right_knee",
                    "rear_left_hip_x", "rear_left_hip_y", "rear_left_knee",
                    "rear_right_hip_x", "rear_right_hip_y", "rear_right_knee",
                    "arm_sh0", "arm_sh1", "arm_el0", "arm_el1", "arm_wr0", "arm_wr1", "arm_f1x",
                ],
            },
            "action": {
                "dtype": "float32",
                "shape": (14,),
                "names": [
                    "linear_x", "linear_y", "linear_z", "angular_x", "angular_y", "angular_z",
                    "arm_pos_x", "arm_pos_y", "arm_pos_z",
                    "arm_quat_x", "arm_quat_y", "arm_quat_z", "arm_quat_w",
                    "gripper_angle",
                ],
            }
        }

        # ROS 2 Subscriptions
        self.image_topic = f'/{self.spot_name}/camera/frontmiddle_virtual/image'
        self.hand_image_topic = f'/{self.spot_name}/camera/hand/image'
        self.joint_topic = f'/{self.spot_name}/joint_states'
        self.cmd_vel_topic = f'/{self.spot_name}/cmd_vel'
        self.arm_pose_topic = f'/{self.spot_name}/arm_pose_commands'
        self.gripper_angle_topic = f'/{self.spot_name}/gripper_angle_command'

        self.image_sub = self.create_subscription(
            Image,
            self.image_topic,
            self.image_callback,
            10
        )
        self.hand_image_sub = self.create_subscription(
            Image,
            self.hand_image_topic,
            self.hand_image_callback,
            10
        )
        self.joint_sub = self.create_subscription(
            JointState,
            self.joint_topic,
            self.joint_callback,
            10
        )
        self.cmd_vel_sub = self.create_subscription(
            Twist,
            self.cmd_vel_topic,
            self.cmd_vel_callback,
            10
        )
        self.arm_pose_sub = self.create_subscription(
            PoseStamped,
            self.arm_pose_topic,
            self.arm_pose_callback,
            10
        )
        self.gripper_angle_sub = self.create_subscription(
            Float32,
            self.gripper_angle_topic,
            self.gripper_angle_callback,
            10
        )

        self.record_timer = self.create_timer(1.0 / self.lerobot_fps, self.record_lerobot_frame)
        self.get_logger().info(f'Bag Trigger Node (with LeRobot) has been initialized for "{self.spot_name}" and is listening on /bag_trigger')

    def image_callback(self, msg: Image):
        try:
            cv_img = self.br.imgmsg_to_cv2(msg, desired_encoding='rgb8')
            self.latest_image = cv2.resize(cv_img, (224, 224))
        except Exception as e:
            self.get_logger().error(f"Image conversion error: {e}")

    def hand_image_callback(self, msg: Image):
        try:
            cv_img = self.br.imgmsg_to_cv2(msg, desired_encoding='rgb8')
            self.latest_hand_image = cv2.resize(cv_img, (224, 224))
        except Exception as e:
            self.get_logger().error(f"Hand image conversion error: {e}")

    def joint_callback(self, msg: JointState):
        # Joint order is legs (0-11) then arm+gripper (12-18); see spot_joint_map.hpp.
        if len(msg.position) >= 19:
            self.latest_joints = np.array(msg.position[:19], dtype=np.float32)

    def cmd_vel_callback(self, msg: Twist):
        self.latest_cmd_vel = np.array([
            msg.linear.x, msg.linear.y, msg.linear.z,
            msg.angular.x, msg.angular.y, msg.angular.z,
        ], dtype=np.float32)
        self.latest_cmd_vel_time = self.get_clock().now()

    def arm_pose_callback(self, msg: PoseStamped):
        p, q = msg.pose.position, msg.pose.orientation
        self.latest_arm_pose = np.array(
            [p.x, p.y, p.z, q.x, q.y, q.z, q.w], dtype=np.float32
        )

    def gripper_angle_callback(self, msg: Float32):
        self.latest_gripper_angle = np.array([msg.data], dtype=np.float32)

    def record_lerobot_frame(self):
        if not self.is_recording or self.dataset is None:
            return
            
        # If we are missing data streams, print a throttled warning so you know what's wrong
        if self.latest_image is None or self.latest_hand_image is None or self.latest_joints is None:
            missing = []
            if self.latest_image is None: missing.append(f"Images ({self.image_topic})")
            if self.latest_hand_image is None: missing.append(f"Hand Images ({self.hand_image_topic})")
            if self.latest_joints is None: missing.append(f"Joint States ({self.joint_topic})")
            self.get_logger().warn(
                f"LeRobot recording active but waiting for data streams. Missing: {', '.join(missing)}",
                throttle_duration_sec=3.0
            )
            return

        # Treat cmd_vel as zero once it's gone stale, rather than replaying whatever
        # value happened to arrive last (see latest_cmd_vel_time comment in __init__).
        cmd_vel = self.latest_cmd_vel
        if self.latest_cmd_vel_time is None:
            cmd_vel = np.zeros(6, dtype=np.float32)
        else:
            age_sec = (self.get_clock().now() - self.latest_cmd_vel_time).nanoseconds / 1e9
            if age_sec > self.cmd_vel_timeout_sec:
                cmd_vel = np.zeros(6, dtype=np.float32)

        # Append frame data
        action = np.concatenate([
            cmd_vel,
            self.latest_arm_pose,
            self.latest_gripper_angle,
        ])
        self.dataset.add_frame({
            "observation.images.front": self.latest_image,
            "observation.images.hand": self.latest_hand_image,
            "observation.state": self.latest_joints,
            "action": action,
            "task": f"{self.spot_name} teleop",
        })
        self.frames_in_current_episode += 1

    def _current_recording_session(self) -> str:
        """Name set via set_recording_session.sh, or '' if none is set (see trigger_callback --
        an unset session falls back to recordings/unsorted/, never the flat recordings/ root)."""
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

            # 1. Generate unique timestamped dataset directory, under recordings/<session>/.
            # Never falls back to the flat recordings/ root -- an unset session goes to
            # recordings/unsorted/ instead, so recordings/ itself always stays organized
            # into subfolders.
            session = self._current_recording_session()
            if not session:
                self.get_logger().warn(
                    "No recording session set -- saving to recordings/unsorted/. "
                    "Run ./set_recording_session.sh --name <name> to use a named folder instead."
                )
            effective_session = session or "unsorted"
            session_dir = f"{RECORDINGS_ROOT}/{effective_session}"
            os.makedirs(session_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = f"{session_dir}/bag_{timestamp}"
            unique_repo_id = f"jtoribio/{effective_session}_{timestamp}"
            lerobot_root = f"{session_dir}/bag_{timestamp}_lerobot"
            self.get_logger().info(f"Recording session '{effective_session}' active -> saving under {session_dir}")

            # 2. Always create a brand-new dataset with this unique name
            self.get_logger().info(f"Initializing a brand new LeRobot Dataset: {unique_repo_id}")
            self.dataset = LeRobotDataset.create(
                repo_id=unique_repo_id,
                fps=self.lerobot_fps,
                root=lerobot_root,
                features=self.lerobot_features,
                robot_type="spot"
            )
            
            # Reset the counter and enable recording
            self.frames_in_current_episode = 0
            self.is_recording = True
            self.get_logger().info(f"Starting ROS2 bag recording to: {output_dir}")
            
            cmd_str = (
                "export FASTRTPS_DEFAULT_PROFILES_FILE=/ros2_ws/fastdds_config.xml && "
                "source /opt/ros/humble/setup.bash && "
                "source /ros2_ws/install/setup.bash && "
                f"ros2 bag record -a -o {output_dir}"
            )
            
            self.bag_process = subprocess.Popen(
                cmd_str, 
                shell=True, 
                executable="/bin/bash",
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                start_new_session=True  
            )
            
            response.success = True
            response.message = f"ROS2 Bag & LeRobot recording ({unique_repo_id}) started successfully."

        else:
            # --- STOP RECORDING ---
            if self.bag_process is None:
                response.success = False
                response.message = "No recording process is currently active."
                return response
            
            # 1. Stop capturing frames
            self.is_recording = False
            
            # 2. Save LeRobot episode & Finalize this unique dataset
            if self.dataset is not None:
                if self.frames_in_current_episode > 0:
                    self.get_logger().info(f"Saving completed LeRobot episode ({self.frames_in_current_episode} frames)...")
                    self.dataset.save_episode()
                    
                    self.get_logger().info("Finalizing this LeRobot dataset (writing metadata)...")
                    self.dataset.finalize()
                else:
                    self.get_logger().error(
                        "LeRobot: Stopped recording, but ZERO frames were captured! "
                        "Skipping episode save/finalize. Check your ROS topics."
                    )
                
                # Reset dataset variable back to None so the next start trigger spawns a fresh one
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
            response.message = "Recording stopped. (LeRobot saved and finalized)"

        return response

def main(args=None):
    rclpy.init(args=args)
    node = BagTriggerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        # If the user CTRL+Cs while a recording is currently active, finalize it so metadata writes
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