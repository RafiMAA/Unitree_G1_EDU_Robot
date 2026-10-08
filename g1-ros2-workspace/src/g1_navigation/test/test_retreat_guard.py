import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from g1_navigation.retreat_guard import retreat_velocity


def wall(x=None, y=None):
    if x is not None:
        return np.array([[x, side, 0.8] for side in np.linspace(-0.3, 0.3, 20)])
    return np.array([[front, y, 0.8] for front in np.linspace(-0.25, 0.35, 20)])


def test_front_wall_permits_capped_reverse_but_blocks_forward_and_rotation():
    points = wall(x=0.46)
    assert retreat_velocity(points, -0.8, 0, 0) == (-0.1, 0)
    assert retreat_velocity(points, 0.2, 0, 0) is None
    assert retreat_velocity(points, 0, 0, 0.3) is None
    assert retreat_velocity(points, -0.2, 0, 0.3) is None


def test_rear_wall_permits_only_forward():
    points = wall(x=-0.39)
    assert retreat_velocity(points, 0.8, 0, 0) == (0.1, 0)
    assert retreat_velocity(points, -0.2, 0, 0) is None


def test_side_wall_permits_strafe_away_not_tangent_or_toward():
    points = wall(y=0.38)
    assert retreat_velocity(points, 0, -0.5, 0) == (0, -0.1)
    assert retreat_velocity(points, 0, 0.2, 0) is None
    assert retreat_velocity(points, -0.2, 0, 0) is None


def test_corner_permits_slide_without_reducing_either_wall_clearance():
    points = np.concatenate([wall(x=0.46), wall(y=0.38)])
    assert retreat_velocity(points, -0.2, 0, 0) == (-0.1, 0)
    vx, vy = retreat_velocity(points, -0.2, -0.2, 0)
    assert vx < 0 and vy < 0
    assert math.hypot(vx, vy) == pytest.approx(0.1)


def test_trapped_or_penetrating_footprint_keeps_stop():
    points = np.concatenate([wall(x=0.46), wall(x=-0.39)])
    assert retreat_velocity(points, -0.2, 0, 0) is None
    assert retreat_velocity(wall(x=0.38), -0.2, 0, 0) is None


def test_no_observations_or_invalid_velocity_cannot_authorize_retreat():
    assert retreat_velocity([], -0.2, 0, 0) is None
    assert retreat_velocity(wall(x=0.46), float('nan'), 0, 0) is None
    assert retreat_velocity([[0.46, 0, float('nan')]], -0.2, 0, 0) is None


def test_navigation_prediction_uses_the_padded_costmap_footprint():
    config = yaml.safe_load((Path(__file__).parents[1] / 'config/nav2_params.yaml').read_text())
    local = config['local_costmap']['local_costmap']['ros__parameters']
    points = np.array(yaml.safe_load(local['footprint']))
    padding = local['footprint_padding']
    assert points[:, 0].min() - padding == pytest.approx(-0.29)
    assert points[:, 0].max() + padding == pytest.approx(0.39)
    assert abs(points[:, 1]).max() + padding == pytest.approx(0.33)
    monitor = config['collision_monitor']['ros__parameters']
    assert monitor['polygons'] == ['footprint_approach']
    approach = monitor['footprint_approach']
    assert approach['action_type'] == 'approach'
    assert approach['footprint_topic'] == '/local_costmap/published_footprint'
    assert approach['time_before_collision'] >= monitor['source_timeout']


def test_repeated_padding_intrusion_can_escape_without_crossing_physical_body():
    for x in (0.64, 0.60, 0.46, 0.42, 0.40, 0.39):
        points = wall(x=x)
        for _ in range(10):
            assert retreat_velocity(points, -0.2, 0, 0) == (-0.1, 0)
            assert retreat_velocity(points, 0.2, 0, 0) is None


def test_side_padding_intrusion_and_corner_allow_slide_away():
    points = wall(y=-0.36)
    assert retreat_velocity(points, 0, 0.2, 0) == (0, 0.1)
    assert retreat_velocity(points, 0, -0.2, 0) is None
    assert retreat_velocity(points, 0.2, 0, 0) is None
    corner = np.concatenate([wall(x=0.42), wall(y=-0.36)])
    assert retreat_velocity(corner, -0.2, 0, 0) == (-0.1, 0)


def test_far_rear_obstacle_does_not_prevent_short_forward_wall_retreat():
    points = np.concatenate([wall(x=0.42), wall(x=-0.80)])
    assert retreat_velocity(points, -0.2, 0, 0) == (-0.1, 0)


def test_recorded_wall_points_inside_padding_keep_safe_exits_available():
    points = [[-0.2937155, -0.3612124, 1.27355],
              [-0.3009439, -0.3651286, 1.27359],
              [-0.3085464, -0.3692476, 1.27363],
              [-0.3165528, -0.3735854, 1.27368]]
    for _ in range(20):
        assert retreat_velocity(points, 0.2, 0, 0) == (0.1, 0)
        assert retreat_velocity(points, 0, 0.2, 0) == (0, 0.1)
        assert retreat_velocity(points, -0.2, 0, 0) is None
        assert retreat_velocity(points, 0, -0.2, 0) is None


def test_manual_mapping_bypasses_obstacles_and_monitor_but_keeps_timeouts():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from geometry_msgs.msg import Twist
    from g1_navigation.retreat_guard import RetreatGuard
    request = Twist()
    request.linear.x, request.angular.z = 0.4, 0.6
    guard = SimpleNamespace(mode='mapping', estop=False, request=request,
                            points=None, monitor_active=False, output=Mock(),
                            report=Mock(), now=lambda: 10., fresh=lambda *args: True)
    RetreatGuard.tick(guard)
    assert guard.output.publish.call_args.args[0] is request
    guard.fresh = lambda *args: False
    RetreatGuard.tick(guard)
    assert guard.output.publish.call_args.args[0].linear.x == 0
    guard.fresh = lambda *args: True
    for mode, estop in [('mapping', True), ('idle', False)]:
        guard.mode, guard.estop = mode, estop
        RetreatGuard.tick(guard)
        assert guard.output.publish.call_args.args[0].linear.x == 0
        assert guard.output.publish.call_args.args[0].angular.z == 0


def test_navigation_obeys_collision_output_and_sensor_freshness():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from geometry_msgs.msg import Twist
    from g1_navigation.retreat_guard import RetreatGuard
    request, monitored = Twist(), Twist()
    request.linear.x = 0.4
    guard = SimpleNamespace(mode='navigate', estop=False, request=request,
                            monitored=monitored, points=wall(x=.42), monitor_active=True,
                            timeout=1., output=Mock(), report=Mock(), now=lambda: 10.,
                            fresh=lambda *args: True)
    RetreatGuard.tick(guard)
    assert guard.output.publish.call_args.args[0].linear.x == 0
    monitored.linear.x = .12
    RetreatGuard.tick(guard)
    assert guard.output.publish.call_args.args[0].linear.x == .12
    guard.fresh = lambda name, *args: name != 'points'
    RetreatGuard.tick(guard)
    assert guard.output.publish.call_args.args[0].linear.x == 0
