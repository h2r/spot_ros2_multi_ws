import rclpy
from rclpy.node import Node
from std_srvs.srv import SetBool
import subprocess
import os
import signal
from datetime import datetime

class BagTriggerNode(Node):
    def __init__(self):
        super().__init__('bag_trigger_node')
        # Force an absolute global path with a leading slash so Rosbridge maps it cleanly
        self.srv = self.create_service(SetBool, '/bag_trigger', self.trigger_callback)
        self.bag_process = None
        self.get_logger().info('Bag Trigger Node has been initialized and is listening on /bag_trigger')

    def trigger_callback(self, request, response):
        if request.data:
            if self.bag_process is not None:
                response.success = False
                response.message = "Bag recording is already running!"
                return response
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = f"/ros2_ws/src/recordings/bag_{timestamp}"
            
            self.get_logger().info(f"Starting ROS2 bag recording to: {output_dir}")
            
            # REMOVED '& disown' so Python tracks the process group correctly
            cmd_str = (
                "export FASTRTPS_DEFAULT_PROFILES_FILE=/ros2_ws/fastdds_config.xml && "
                "source /opt/ros/humble/setup.bash && "
                "source /ros2_ws/install/setup.bash && "
                f"ros2 bag record -a -o {output_dir}"
            )
            
            # This spawns the process asynchronously in the background instantly
            self.bag_process = subprocess.Popen(
                cmd_str, 
                shell=True, 
                executable="/bin/bash",
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                start_new_session=True  
            )
            
            # Return immediately without waiting for discovery traffic to finish!
            response.success = True
            response.message = f"Recording started successfully at {output_dir}"

        else:
            if self.bag_process is None:
                response.success = False
                response.message = "No recording process is currently active."
                return response
                
            self.get_logger().info("Stopping ROS2 bag recording...")
            
            try:
                pgid = os.getpgid(self.bag_process.pid)
                os.killpg(pgid, signal.SIGINT)
                
                try:
                    self.bag_process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self.get_logger().warn("Bag process did not stop in time. Force killing...")
                    os.killpg(pgid, signal.SIGKILL)
                    self.bag_process.wait()
            except ProcessLookupError:
                # If everything else fails, look up the system process tree to clean up manually
                self.get_logger().warn("Process group missing. Forcing terminal cleanup...")
                os.system('pkill -f "ros2 bag record"')
            finally:
                self.bag_process = None
            
            response.success = True
            response.message = "Recording stopped and saved cleanly."

        return response

def main(args=None):
    rclpy.init(args=args)
    node = BagTriggerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node.bag_process is not None:
            try:
                os.killpg(os.getpgid(node.bag_process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()