import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    nav_share = get_package_share_directory('g1_navigation')
    start_sim = LaunchConfiguration('start_sim')
    start_slam = LaunchConfiguration('start_slam')
    use_sim_time = LaunchConfiguration('use_sim_time')
    slam_params = os.path.join(nav_share, 'config', 'slam_mapping.yaml')
    astar_params = os.path.join(nav_share, 'config', 'astar_params.yaml')

    start_nav = LaunchConfiguration('start_nav')
    start_web = LaunchConfiguration('start_web')
    cmd_vel_topic = LaunchConfiguration('cmd_vel_topic')

    common = {'use_sim_time': use_sim_time}

    return LaunchDescription(
        [
            DeclareLaunchArgument('start_sim', default_value='true'),
            DeclareLaunchArgument('start_slam', default_value='true'),
            DeclareLaunchArgument('start_nav', default_value='true'),
            DeclareLaunchArgument('start_web', default_value='true'),
            DeclareLaunchArgument('cmd_vel_topic', default_value='/cmd_vel_controller'),
            DeclareLaunchArgument('use_sim_time', default_value='false'),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(
                        get_package_share_directory('g1_mujoco'),
                        'launch',
                        'sim.launch.py',
                    )
                ),
                launch_arguments={
                    'start_rosbridge': 'false',
                    'cmd_vel_topic': '/cmd_vel_safe',
                }.items(),
                condition=IfCondition(start_sim),
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(nav_share, 'launch', 'perception_web.launch.py')
                ),
                launch_arguments={'start_rosbridge': 'false'}.items(),
                condition=IfCondition(start_web),
            ),
            Node(
                package='rosbridge_server',
                executable='rosbridge_websocket',
                name='rosbridge_websocket',
                condition=IfCondition(start_web),
                output='screen',
                parameters=[{'port': 9090}],
            ),
            Node(
                package='slam_toolbox',
                executable='async_slam_toolbox_node',
                name='slam_toolbox',
                output='screen',
                condition=IfCondition(start_slam),
                parameters=[slam_params, {'use_sim_time': use_sim_time}],
            ),
            # A* navigation is independent of the SLAM process.

            Node(
                package='g1_navigation', executable='astar_navigator', name='g1_astar',
                output='screen', condition=IfCondition(start_nav),
                parameters=[astar_params, common, {'cmd_vel_topic': cmd_vel_topic}],
            ),
        ]
    )
