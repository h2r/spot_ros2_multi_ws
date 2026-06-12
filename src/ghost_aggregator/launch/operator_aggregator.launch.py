from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="ghost_aggregator",
            executable="operator_aggregator.py",
            name="operator_aggregator",
            output="screen",
        ),
    ])
