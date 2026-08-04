import numpy as np
import cv2

import rclpy
from rclpy.node import Node
from std_srvs.srv import SetBool
from sensor_msgs.msg import Image, JointState
from geometry_msgs.msg import Twist, PoseStamped
from std_msgs.msg import Float32
from cv_bridge import CvBridge

from lerobot.configs.policies import PreTrainedConfig
from lerobot.policies.factory import get_policy_class, make_pre_post_processors
from lerobot.utils.control_utils import predict_action
from lerobot.utils.utils import get_safe_torch_device

# Matches the action layout written by bag_trigger/listener_node.py and
# consumed by lerobot_action_player.py:
# cmd_vel (6) + arm_pose position+quaternion (7) + gripper_angle (1) = 14
ARM_POSE_FRAME_ID = "body"


class PolicyInferenceNode(Node):
    """
    Runs a trained LeRobot policy live against a real Spot robot: subscribes to
    the same camera/joint topics bag_trigger/listener_node.py records from,
    predicts an action every control tick, and publishes it the same way
    lerobot_action_player.py does.

    THIS MOVES THE PHYSICAL ROBOT once enabled. Two independent safety gates:
      - dry_run (default true): predictions are logged, never published.
      - /{spot_name}/policy_inference/enable service (default off): the control
        loop is a no-op until you explicitly enable it, so loading the
        node/policy never by itself starts commanding the robot. Namespaced per
        robot so running this for two robots at once can't cross-trigger.
    Make sure the robot is powered on, standing, and someone is on the e-stop
    before setting dry_run:=false and enabling for real.
    """

    def __init__(self):
        super().__init__('policy_inference_node')

        self.declare_parameter('spot_name', 'spot')
        self.declare_parameter('checkpoint_path', '')
        self.declare_parameter('control_hz', 15.0)
        self.declare_parameter('task', '')
        self.declare_parameter('device', '')
        self.declare_parameter('dry_run', True)

        self.spot_name = self.get_parameter('spot_name').get_parameter_value().string_value
        checkpoint_path = self.get_parameter('checkpoint_path').get_parameter_value().string_value
        control_hz = self.get_parameter('control_hz').get_parameter_value().double_value
        self.task = self.get_parameter('task').get_parameter_value().string_value or f'{self.spot_name} teleop'
        device_override = self.get_parameter('device').get_parameter_value().string_value
        self.dry_run = self.get_parameter('dry_run').get_parameter_value().bool_value

        if not checkpoint_path:
            raise ValueError(
                "Required parameter 'checkpoint_path' is empty. Pass the path to a "
                "pretrained_model directory, e.g.:\n"
                "  --ros-args -p checkpoint_path:=/ros2_ws/recordings/runs/<run>/checkpoints/010000/pretrained_model"
            )

        self.get_logger().info(f"Loading policy from {checkpoint_path} ...")
        policy_cfg = PreTrainedConfig.from_pretrained(checkpoint_path)
        if device_override:
            policy_cfg.device = device_override
        self.device = get_safe_torch_device(policy_cfg.device, log=True)

        policy_cls = get_policy_class(policy_cfg.type)
        self.policy = policy_cls.from_pretrained(checkpoint_path)
        self.policy.to(self.device)
        self.policy.eval()

        self.preprocessor, self.postprocessor = make_pre_post_processors(
            policy_cfg,
            pretrained_path=checkpoint_path,
            # The saved preprocessor pipeline bakes in whatever device it was
            # trained on; override it explicitly or it stays stuck on that
            # device (e.g. fails outright if that was cuda and this machine
            # doesn't have one available).
            preprocessor_overrides={"device_processor": {"device": str(self.device)}},
        )
        self.get_logger().info(f"Policy loaded ({policy_cfg.type}) on {self.device}.")

        if self.dry_run:
            self.get_logger().warn(
                "dry_run=true: predicted actions will be logged but NOT published. "
                "Set dry_run:=false once you've verified the predictions look sane."
            )

        # --- Observation buffers (same pattern as listener_node.py) ---
        self.br = CvBridge()
        self.latest_image = None
        self.latest_hand_image = None
        self.latest_joints = None
        self.active = False

        # --- ROS I/O: same observation topics listener_node.py records from,
        # same command topics lerobot_action_player.py publishes to ---
        self.image_topic = f'/{self.spot_name}/camera/frontmiddle_virtual/image'
        self.hand_image_topic = f'/{self.spot_name}/camera/hand/image'
        self.joint_topic = f'/{self.spot_name}/joint_states'

        self.create_subscription(Image, self.image_topic, self.image_callback, 10)
        self.create_subscription(Image, self.hand_image_topic, self.hand_image_callback, 10)
        self.create_subscription(JointState, self.joint_topic, self.joint_callback, 10)

        self.cmd_vel_pub = self.create_publisher(Twist, f'/{self.spot_name}/cmd_vel', 10)
        self.arm_pose_pub = self.create_publisher(PoseStamped, f'/{self.spot_name}/arm_pose_commands', 10)
        self.gripper_pub = self.create_publisher(Float32, f'/{self.spot_name}/gripper_angle_command', 10)

        # Namespaced per robot -- NOT a bare 'policy_inference/enable' -- so running
        # this for two robots at once doesn't leave two nodes both offering the same
        # service name, with no way to tell which one an enable call actually reaches.
        self.enable_service_name = f'/{self.spot_name}/policy_inference/enable'
        self.srv = self.create_service(SetBool, self.enable_service_name, self.enable_callback)

        self.control_period = 1.0 / control_hz
        self.timer = self.create_timer(self.control_period, self.control_step)

        self.get_logger().info(
            f'Policy inference node ready for "{self.spot_name}". '
            f'Call the {self.enable_service_name} service to start.'
        )

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

    def enable_callback(self, request, response):
        if request.data:
            if self.latest_image is None or self.latest_hand_image is None or self.latest_joints is None:
                response.success = False
                response.message = (
                    "Missing observation streams, refusing to enable. "
                    "Check camera/joint_states topics are publishing."
                )
                return response
            # Clears the policy's internal action-chunk queue and any processor
            # state so a fresh rollout doesn't start mid-chunk from last time.
            self.policy.reset()
            self.preprocessor.reset()
            self.postprocessor.reset()
            self.active = True
            response.success = True
            response.message = f"Policy inference enabled (dry_run={self.dry_run})."
        else:
            self.active = False
            response.success = True
            response.message = "Policy inference disabled."
        return response

    def control_step(self):
        if not self.active:
            return
        if self.latest_image is None or self.latest_hand_image is None or self.latest_joints is None:
            self.get_logger().warn(
                "Missing observation streams, skipping control step.", throttle_duration_sec=3.0
            )
            return

        observation = {
            "observation.images.front": self.latest_image,
            "observation.images.hand": self.latest_hand_image,
            "observation.state": self.latest_joints,
        }

        action = predict_action(
            observation=observation,
            policy=self.policy,
            device=self.device,
            preprocessor=self.preprocessor,
            postprocessor=self.postprocessor,
            use_amp=False,
            task=self.task,
            robot_type="spot",
        )
        action = action.detach().cpu().numpy().reshape(-1)

        cmd_vel, arm_pose, gripper_angle = action[0:6], action[6:13], action[13]

        if self.dry_run:
            self.get_logger().info(
                f"[dry_run] cmd_vel={np.round(cmd_vel, 3)} arm_pose={np.round(arm_pose, 3)} "
                f"gripper={gripper_angle:.1f}",
                throttle_duration_sec=1.0,
            )
            return

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


def main(args=None):
    rclpy.init(args=args)
    node = PolicyInferenceNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
