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
    planner = yaml.safe_load((path.parent / 'astar_params.yaml').read_text())['g1_astar']['ros__parameters']
    collision = config['collision_monitor']['ros__parameters']
    assert planner['inflation_radius'] == .15
    assert planner['cmd_vel_topic'] == '/cmd_vel_controller'
    assert collision['mid360']['topic'].endswith('points_filtered')
    assert collision['cmd_vel_out_topic'] == '/cmd_vel_collision'
    assert collision['mid360']['type'] == 'pointcloud'


def test_compact_stop_margin_is_not_removed_as_robot_self_returns():
    # Obstacles just outside the body must remain visible to Collision Monitor.
    points = np.array([[.40, 0, .8], [-.30, 0, .8], [0, .34, .8], [0, -.34, .8]])
    np.testing.assert_allclose(filter_navigation_points(points), points)
    assert len(filter_navigation_points(np.array([[.38, 0, .8], [-.28, 0, .8], [0, .32, .8]]))) == 0


def test_default_launches_use_standalone_astar_and_only_localization_lifecycle():
    from unittest.mock import patch
    from importlib.util import module_from_spec, spec_from_file_location
    from launch_ros.actions import Node
    root = Path(__file__).parents[1]
    for name in ('mapping', 'navigation'):
        spec = spec_from_file_location(name, root / 'launch' / (name + '.launch.py'))
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, 'get_package_share_directory', return_value=str(root)):
            actions = module.generate_launch_description().entities
        packages = {action.node_package for action in actions if isinstance(action, Node)}
        assert 'g1_navigation' in packages
        assert not packages.intersection({'nav2_controller','nav2_planner','nav2_bt_navigator','nav2_behaviors'})
