import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_cfg = os.path.join(get_package_share_directory("aquarius_bridge"), "config", "bridge.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("config", default_value=default_cfg,
                              description="bridge parameter file"),
        DeclareLaunchArgument("use_sim_time", default_value="true",
                              description="true in Gazebo SITL (A4), false on real drones"),
        DeclareLaunchArgument("kafka_bootstrap", default_value="localhost:9092"),
        Node(
            package="aquarius_bridge",
            executable="bridge",
            name="aquarius_bridge",
            output="screen",
            parameters=[
                LaunchConfiguration("config"),
                {"use_sim_time": LaunchConfiguration("use_sim_time"),
                 "kafka_bootstrap": LaunchConfiguration("kafka_bootstrap")},
            ],
        ),
    ])
