from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    start_rosbridge = LaunchConfiguration('start_rosbridge')
    return LaunchDescription(
        [
            DeclareLaunchArgument('start_labels', default_value='true'),
            DeclareLaunchArgument('start_rosbridge', default_value='true'),
            DeclareLaunchArgument('rosbridge_port', default_value='9090'),
            DeclareLaunchArgument('labels_dir', default_value=''),
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
                package='g1_navigation',
                executable='map_labels',
                condition=IfCondition(LaunchConfiguration('start_labels')),
                name='g1_map_labels',
                output='screen',
                parameters=[{'labels_dir': LaunchConfiguration('labels_dir')}],
            ),
            Node(
                package='rosbridge_server',
                executable='rosbridge_websocket',
                name='rosbridge_websocket',
                output='screen',
                condition=IfCondition(start_rosbridge),
                parameters=[{'port': ParameterValue(LaunchConfiguration('rosbridge_port'), value_type=int),
                             'default_call_service_timeout': 8.0,
                             'call_services_in_new_thread': True}],
            ),
        ]
    )
