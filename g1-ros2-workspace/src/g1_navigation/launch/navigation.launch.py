import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    nav_share = get_package_share_directory('g1_navigation')
    start_nav = LaunchConfiguration('start_nav')
    start_sim = LaunchConfiguration('start_sim')
    start_web = LaunchConfiguration('start_web')
    start_safety = LaunchConfiguration('start_safety')
    start_rosbridge = LaunchConfiguration('start_rosbridge')
    use_sim_time = LaunchConfiguration('use_sim_time')
    params_file = LaunchConfiguration('params_file')
    map_file = LaunchConfiguration('map')
    default_params = os.path.join(nav_share, 'config', 'nav2_params.yaml')

    lifecycle_nodes = [
        'map_server',
        'amcl',
        'controller_server',
        'smoother_server',
        'planner_server',
        'behavior_server',
        'bt_navigator',
    ]
    common = {'use_sim_time': use_sim_time}

    def lifecycle_manager(context):
        navigating = start_nav.perform(context).lower() in ('true', '1')
        return [Node(
            package='nav2_lifecycle_manager', executable='lifecycle_manager',
            name='lifecycle_manager_navigation' if navigating else 'lifecycle_manager_localization',
            output='screen', parameters=[common, {'autostart': True},
                {'node_names': lifecycle_nodes if navigating else ['map_server', 'amcl']}],
        )]

    return LaunchDescription(
        [
            DeclareLaunchArgument('start_nav', default_value='true'),
            DeclareLaunchArgument('start_sim', default_value='true'),
            DeclareLaunchArgument('start_web', default_value='true'),
            DeclareLaunchArgument('start_safety', default_value='true'),
            DeclareLaunchArgument('start_rosbridge', default_value='true'),
            DeclareLaunchArgument('use_sim_time', default_value='false'),
            DeclareLaunchArgument('params_file', default_value=default_params),
            DeclareLaunchArgument(
                'map',
                description='Absolute path to the saved occupancy-grid YAML',
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(
                        get_package_share_directory('g1_mujoco'),
                        'launch',
                        'sim.launch.py',
                    )
                ),
                launch_arguments={'cmd_vel_topic': '/cmd_vel_safe', 'start_rosbridge': 'false'}.items(),
                condition=IfCondition(start_sim),
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(nav_share, 'launch', 'perception_web.launch.py')
                ),
                launch_arguments={'start_rosbridge': start_rosbridge}.items(),
                condition=IfCondition(start_web),
            ),
            Node(
                package='nav2_map_server',
                executable='map_server',
                name='map_server',
                output='screen',
                parameters=[params_file, common, {'yaml_filename': map_file}],
            ),
            Node(
                package='nav2_amcl',
                executable='amcl',
                name='amcl',
                output='screen',
                parameters=[params_file, common, {'set_initial_pose': False}],
            ),
            Node(
                package='nav2_controller',
                executable='controller_server',
                condition=IfCondition(start_nav),
                name='controller_server',
                output='screen',
                parameters=[params_file, common],
                remappings=[('cmd_vel', '/cmd_vel_controller')],
            ),
            Node(
                package='nav2_smoother',
                executable='smoother_server',
                condition=IfCondition(start_nav),
                name='smoother_server',
                output='screen',
                parameters=[params_file, common],
            ),
            Node(
                package='nav2_planner',
                executable='planner_server',
                condition=IfCondition(start_nav),
                name='planner_server',
                output='screen',
                parameters=[params_file, common],
            ),
            Node(
                package='nav2_behaviors',
                executable='behavior_server',
                condition=IfCondition(start_nav),
                name='behavior_server',
                output='screen',
                parameters=[params_file, common],
                remappings=[('cmd_vel', '/cmd_vel_controller')],
            ),
            Node(
                package='nav2_bt_navigator',
                executable='bt_navigator',
                condition=IfCondition(start_nav),
                name='bt_navigator',
                output='screen',
                parameters=[params_file, common],
            ),
            OpaqueFunction(function=lifecycle_manager),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(nav_share, 'launch', 'safety.launch.py')
                ),
                launch_arguments={
                    'params_file': params_file,
                    'use_sim_time': use_sim_time,
                }.items(),
                condition=IfCondition(start_safety),
            ),
        ]
    )
