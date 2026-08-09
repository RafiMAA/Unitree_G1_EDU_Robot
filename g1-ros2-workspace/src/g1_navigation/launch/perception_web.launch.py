from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    start_rosbridge = LaunchConfiguration('start_rosbridge')
    return LaunchDescription(
        [
            DeclareLaunchArgument('start_rosbridge', default_value='true'),
            Node(
                package='g1_navigation',
                executable='cloud_filter',
                name='g1_cloud_filter',
                output='screen',
            ),
            Node(
                package='g1_navigation',
                executable='cloud_to_scan',
                name='g1_cloud_to_scan',
                output='screen',
                parameters=[
                    {
                        'cloud_topic': '/g1/mid360/points_filtered',
                        'scan_topic': '/scan',
                        'target_frame': 'base_footprint',
                    }
                ],
            ),
            Node(
                package='g1_navigation',
                executable='web_gateway',
                name='g1_web_gateway',
                output='screen',
            ),
            Node(
                package='rosbridge_server',
                executable='rosbridge_websocket',
                name='rosbridge_websocket',
                output='screen',
                condition=IfCondition(start_rosbridge),
                parameters=[{'port': 9090}],
            ),
        ]
    )
