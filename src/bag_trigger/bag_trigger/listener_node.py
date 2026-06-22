import rclpy
from rclpy.node import Node
from std_srvs.srv import SetBool
import subprocess
import os
from datetime import datetime

class BagTriggerNode(Node):
    def __init__(self):
        super().__init__('bag_trigger_node')
        # Create the service that Unity will call
        self.srv = self.create_service(SetBool, 'bag_trigger', self.trigger_callback)
        self.bag_process = None
        self.get_logger().info('Bag Trigger Node has been initialized and is listening on /bag_trigger')

    def trigger_callback(self, request, response):
        # IF REQUEST IS TRUE: START RECORDING
        if request.data:
            if self.bag_process is not None:
                response.success = False
                response.message = "Bag recording is already running!"
                return response
            
            # Generate a clean timestamp for the directory name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = f"/ros2_ws/src/recordings/bag_{timestamp}"
            
            self.get_logger().info(f"Starting ROS2 bag recording to: {output_dir}")
            
            # Fire off 'ros2 bag record -a' as an independent background process
            cmd = ["ros2", "bag", "record", "-a", "-o", output_dir]
            self.bag_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            response.success = True
            response.message = f"Recording started successfully at {output_dir}"
            
        # IF REQUEST IS FALSE: STOP RECORDING
        else:
            if self.bag_process is None:
                response.success = False
                response.message = "No recording process is currently active."
                return response
                
            self.get_logger().info("Stopping ROS2 bag recording...")
            # Cleanly terminate the recording process
            self.bag_process.terminate()
            self.bag_process.wait()
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
        # Emergency cleanup if the node is killed while recording
        if node.bag_process is not None:
            node.bag_process.terminate()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()


