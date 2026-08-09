import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    params_default = os.path.join(
        get_package_share_directory('g1_navigation'), 'config', 'nav2_params.yaml'
    )
    params_file = LaunchConfiguration('params_file')
    use_sim_time = LaunchConfiguration('use_sim_time')
    return LaunchDescription(
        [
            DeclareLaunchArgument('params_file', default_value=params_default),
            DeclareLaunchArgument('use_sim_time', default_value='false'),
            Node(
                package='g1_navigation',
                executable='command_mux',
                name='g1_command_mux',
                output='screen',
            ),
            Node(
                package='nav2_velocity_smoother',
                executable='velocity_smoother',
                name='velocity_smoother',
                output='screen',
                parameters=[params_file, {'use_sim_time': use_sim_time}],
                remappings=[('cmd_vel', '/cmd_vel_muxed')],
            ),
            Node(
                package='nav2_collision_monitor',
                executable='collision_monitor',
                name='collision_monitor',
                output='screen',
                parameters=[params_file, {'use_sim_time': use_sim_time}],
            ),
            Node(
                package='nav2_lifecycle_manager',
                executable='lifecycle_manager',
                name='lifecycle_manager_safety',
                output='screen',
                parameters=[
                    {'use_sim_time': use_sim_time},
                    {'autostart': True},
                    {'node_names': ['velocity_smoother', 'collision_monitor']},
                ],
            ),
        ]
    )
