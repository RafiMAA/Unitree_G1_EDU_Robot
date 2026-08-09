import math
from pathlib import Path

import numpy as np
import yaml

from g1_navigation.cloud_to_scan import project_ranges, quaternion_matrix
from g1_navigation.cloud_filter import filter_navigation_points
from g1_navigation.command_mux import source_is_fresh


def test_quaternion_matrix_rotates_ninety_degrees():
    half = math.pi / 4.0
    rotation = quaternion_matrix(0.0, 0.0, math.sin(half), math.cos(half))
    result = rotation @ np.array([1.0, 0.0, 0.0])
    np.testing.assert_allclose(result, [0.0, 1.0, 0.0], atol=1e-7)


def test_projection_keeps_nearest_return_in_each_bin():
    points = np.array([[2.0, 0.0, 0.5], [1.0, 0.0, 0.5], [0.0, 2.0, 0.5]])
    ranges = project_ranges(points, -math.pi, math.pi, math.pi / 2, 0.2, 5.0)
    assert ranges[2] == 1.0
    assert ranges[3] == 2.0


def test_stale_command_timeout():
    assert source_is_fresh(1_000_000_000, 0.5, 1_400_000_000)
    assert not source_is_fresh(1_000_000_000, 0.5, 1_600_000_000)
    assert not source_is_fresh(None, 0.5, 1_000_000_000)


def test_cloud_filter_removes_floor_and_robot_but_keeps_obstacle():
    points = np.array(
        [
            [1.0, 0.0, 0.01],
            [0.1, 0.1, 0.8],
            [0.7, 0.0, 0.8],
            [0.0, 0.8, 1.0],
        ]
    )
    filtered = filter_navigation_points(points)
    np.testing.assert_allclose(filtered, [[0.7, 0.0, 0.8], [0.0, 0.8, 1.0]])


def test_navigation_yaml_has_live_cloud_and_safe_output():
    path = Path(__file__).parents[1] / 'config' / 'nav2_params.yaml'
    config = yaml.safe_load(path.read_text())
    local = config['local_costmap']['local_costmap']['ros__parameters']
    collision = config['collision_monitor']['ros__parameters']
    assert local['voxel_layer']['mid360']['data_type'] == 'PointCloud2'
    assert local['voxel_layer']['mid360']['topic'].endswith('points_filtered')
    assert local['voxel_layer']['mid360']['clearing'] is True
    assert collision['cmd_vel_out_topic'] == '/cmd_vel_safe'
    assert collision['mid360']['type'] == 'pointcloud'
