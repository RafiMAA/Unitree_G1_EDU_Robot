import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    start_sim = LaunchConfiguration('start_sim')
    start_rviz = LaunchConfiguration('start_rviz')
    voxel_size = LaunchConfiguration('voxel_size')

    sim_launch = os.path.join(
        get_package_share_directory('g1_mujoco'), 'launch', 'sim.launch.py'
    )
    rviz_config = os.path.join(
        get_package_share_directory('g1_mapping'), 'rviz', 'mapping.rviz'
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument('start_sim', default_value='true'),
            DeclareLaunchArgument('start_rviz', default_value='true'),
            DeclareLaunchArgument('voxel_size', default_value='0.10'),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(sim_launch),
                condition=IfCondition(start_sim),
            ),
            Node(
                package='g1_mapping',
                executable='voxel_mapper',
                name='g1_voxel_mapper',
                output='screen',
                parameters=[{'voxel_size': voxel_size}],
            ),
            Node(
                package='rviz2',
                executable='rviz2',
                name='g1_mapping_rviz',
                arguments=['-d', rviz_config],
                output='screen',
                condition=IfCondition(start_rviz),
            ),
        ]
    )
