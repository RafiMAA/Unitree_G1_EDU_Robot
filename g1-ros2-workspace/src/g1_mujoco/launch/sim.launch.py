from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    start_rosbridge = LaunchConfiguration('start_rosbridge')
    return LaunchDescription([
        DeclareLaunchArgument("cmd_vel_topic", default_value="/cmd_vel_teleop"),
        DeclareLaunchArgument('start_rosbridge', default_value='true'),
        Node(
            package="g1_mujoco",
            executable="mujoco_bridge",
            output="screen",
        ),
        Node(
            package="g1_core",
            executable="state_machine",
            name="g1_core",
            output="screen",
            parameters=[{"cmd_vel_topic": LaunchConfiguration("cmd_vel_topic")}],
        ),
        Node(
            package='rosbridge_server',
            executable='rosbridge_websocket',
            name='rosbridge_websocket',
            output='screen',
            condition=IfCondition(start_rosbridge),
            parameters=[{'port': 9090}],
        ),
    ])
