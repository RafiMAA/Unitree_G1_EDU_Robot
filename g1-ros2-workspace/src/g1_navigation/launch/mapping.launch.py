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
    nav_params = os.path.join(nav_share, 'config', 'nav2_params.yaml')

    start_nav = LaunchConfiguration('start_nav')

    nav_lifecycle_nodes = [
        'controller_server',
        'smoother_server',
        'planner_server',
        'behavior_server',
        'bt_navigator',
    ]
    common = {'use_sim_time': use_sim_time}

    return LaunchDescription(
        [
            DeclareLaunchArgument('start_sim', default_value='true'),
            DeclareLaunchArgument('start_slam', default_value='true'),
            DeclareLaunchArgument('start_nav', default_value='true'),
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
                }.items(),
                condition=IfCondition(start_sim),
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(nav_share, 'launch', 'perception_web.launch.py')
                ),
                launch_arguments={'start_rosbridge': 'false'}.items(),
            ),
            Node(
                package='rosbridge_server',
                executable='rosbridge_websocket',
                name='rosbridge_websocket',
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
            Node(
                package='nav2_lifecycle_manager',
                executable='lifecycle_manager',
                name='lifecycle_manager_slam',
                output='screen',
                condition=IfCondition(start_slam),
                parameters=[
                    {'use_sim_time': use_sim_time},
                    {'autostart': True},
                    {'bond_timeout': 0.0},
                    {'node_names': ['slam_toolbox']},
                ],
            ),
            # --- Navigation Stack (SLAM-Based) ---
            Node(
                package='nav2_controller',
                executable='controller_server',
                name='controller_server',
                output='screen',
                condition=IfCondition(start_nav),
                parameters=[nav_params, common],
                remappings=[('cmd_vel', '/cmd_vel_teleop')],
            ),
            Node(
                package='nav2_smoother',
                executable='smoother_server',
                name='smoother_server',
                output='screen',
                condition=IfCondition(start_nav),
                parameters=[nav_params, common],
            ),
            Node(
                package='nav2_planner',
                executable='planner_server',
                name='planner_server',
                output='screen',
                condition=IfCondition(start_nav),
                parameters=[nav_params, common],
            ),
            Node(
                package='nav2_behaviors',
                executable='behavior_server',
                name='behavior_server',
                output='screen',
                condition=IfCondition(start_nav),
                parameters=[nav_params, common],
                remappings=[('cmd_vel', '/cmd_vel_teleop')],
            ),
            Node(
                package='nav2_bt_navigator',
                executable='bt_navigator',
                name='bt_navigator',
                output='screen',
                condition=IfCondition(start_nav),
                parameters=[nav_params, common],
            ),
            Node(
                package='nav2_lifecycle_manager',
                executable='lifecycle_manager',
                name='lifecycle_manager_navigation',
                output='screen',
                condition=IfCondition(start_nav),
                parameters=[
                    {'use_sim_time': use_sim_time},
                    {'autostart': True},
                    {'node_names': nav_lifecycle_nodes},
                ],
            ),
        ]
    )
