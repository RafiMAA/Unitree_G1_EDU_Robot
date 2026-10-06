"""Permit slow manual translation away from a stop-zone obstacle."""
import json
import math

import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from lifecycle_msgs.srv import GetState
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Bool, String


def retreat_velocity(points, vx, vy, wz, speed_limit=0.10):
    """Return a capped retreat only if every nearby return gains clearance.

    Coordinates are base_footprint metres. Use the physical rectangular footprint from nav2_params.yaml, rather than
    its warning padding. An obstacle within padding may still be escaped.
    Rotation and physical-footprint contact remain stopped.
    """
    if not all(math.isfinite(v) for v in (vx, vy, wz)) or abs(wz) > 0.01:
        return None
    speed = math.hypot(vx, vy)
    if speed < 0.01:
        return None
    vx, vy = vx * min(1.0, speed_limit / speed), vy * min(1.0, speed_limit / speed)
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    points = points[np.isfinite(points).all(axis=1)]
    # Include the whole slowdown zone plus margin, not just the stop polygon.
    nearby = points[(points[:, 0] >= -0.85) & (points[:, 0] <= 1.0)
                    & (np.abs(points[:, 1]) <= 0.70)
                    & (points[:, 2] >= 0.10) & (points[:, 2] <= 2.0)]
    if not len(nearby):
        return None
    nearest_x = np.clip(nearby[:, 0], -0.28, 0.38)
    nearest_y = np.clip(nearby[:, 1], -0.32, 0.32)
    dx, dy = nearby[:, 0] - nearest_x, nearby[:, 1] - nearest_y
    distance = np.hypot(dx, dy)
    # Padding is an early warning, not a physical penetration. Close returns
    # must never lose clearance, but tangential translation is allowed if at
    # least one close return gains clearance. Far returns may approach only
    # while retaining a 10 cm gap over the full one-second sensor timeout.
    if np.any(distance <= 0.005):
        return None
    clearance_rate = -(dx * vx + dy * vy) / distance
    close = distance <= 0.35
    if not np.any(close) or np.any(clearance_rate[close] < -1e-6):
        return None
    if not np.any(clearance_rate[close] > 0.005):
        return None
    shifted_x, shifted_y = nearby[:, 0] - vx, nearby[:, 1] - vy
    predicted = np.hypot(shifted_x - np.clip(shifted_x, -0.28, 0.38),
                         shifted_y - np.clip(shifted_y, -0.32, 0.32))
    if np.any(predicted[~close] < 0.10):
        return None
    return vx, vy


class RetreatGuard(Node):
    def __init__(self):
        super().__init__('g1_retreat_guard')
        self.mode = 'mapping'
        self.estop = False
        self.request = Twist()
        self.monitored = Twist()
        self.points = None
        self.stamps = {}
        self.last_status = None
        self.hint_points = None
        self.escape_hint = ""
        self.monitor_active = False
        self.monitor_client = self.create_client(GetState, '/collision_monitor/get_state')
        self.monitor_future = None
        self.monitor_checked = None
        self.create_timer(0.5, self.check_monitor)
        self.timeout = float(self.declare_parameter('source_timeout', 1.0).value)
        self.output = self.create_publisher(Twist, '/cmd_vel_safe', 10)
        self.status = self.create_publisher(String, '/ui/safety_status', 10)
        self.create_subscription(Twist, '/cmd_vel_smoothed', lambda m: self.receive('request', m), 10)
        self.create_subscription(Twist, '/cmd_vel_collision', lambda m: self.receive('monitored', m), 10)
        self.create_subscription(PointCloud2, '/g1/mid360/points_filtered', self.on_cloud, qos_profile_sensor_data)
        self.create_subscription(String, '/ui/mode', self.on_mode, 10)
        self.create_subscription(Bool, '/ui/emergency_stop', self.on_estop, 10)
        self.create_timer(0.05, self.tick)

    def check_monitor(self):
        if self.monitor_future is not None and not self.monitor_future.done():
            if self.monitor_checked is not None and self.now() - self.monitor_checked > 1.5:
                self.monitor_active = False
            return
        if self.monitor_future is not None:
            try:
                self.monitor_active = self.monitor_future.result().current_state.id == 3
            except Exception:
                self.monitor_active = False
        if self.monitor_client.service_is_ready():
            self.monitor_future = self.monitor_client.call_async(GetState.Request())
            self.monitor_checked = self.now()
        else:
            self.monitor_active = False
            self.monitor_future = None

    def now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def receive(self, name, msg):
        setattr(self, name, msg)
        self.stamps[name] = self.now()

    def on_mode(self, msg):
        self.mode = msg.data
        self.last_status = None
        self.request = self.monitored = Twist()
        self.stamps.pop('request', None)
        self.stamps.pop('monitored', None)
        self.output.publish(Twist())

    def on_estop(self, msg):
        self.estop = msg.data
        if self.estop:
            self.output.publish(Twist())

    def on_cloud(self, msg):
        if msg.header.frame_id != 'base_footprint':
            self.points = None
            return
        try:
            self.points = np.asarray(point_cloud2.read_points_numpy(msg, field_names=('x', 'y', 'z'), skip_nans=True), dtype=float).reshape(-1, 3)
            # Keep the sensor's stamp: repeated delivery must not refresh old data.
            self.stamps['points'] = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
        except (ValueError, TypeError):
            self.points = None

    def fresh(self, name, now, timeout):
        return name in self.stamps and 0 <= now - self.stamps[name] <= timeout

    def retreat_hint(self):
        if self.hint_points is not self.points:
            directions = [('Forward (W / ↑)', 0.1, 0), ('Reverse (S / ↓)', -0.1, 0),
                          ('Strafe left (Q)', 0, 0.1), ('Strafe right (E)', 0, -0.1)]
            allowed = [name for name, vx, vy in directions
                       if retreat_velocity(self.points, vx, vy, 0) is not None]
            self.escape_hint = ('Clear retreat: ' + ', '.join(allowed) + '. Move clear before turning.'
                                if allowed else 'No clear retreat detected; stop and check robot clearance.')
            self.hint_points = self.points
        return self.escape_hint

    def report(self, state, message):
        if (state, message) != self.last_status:
            self.last_status = state, message
            self.status.publish(String(data=json.dumps({'state': state, 'message': message})))

    def tick(self):
        now = self.now()
        output = Twist()
        if self.estop or self.mode not in ('mapping', 'navigate'):
            self.output.publish(output)
            return
        if self.points is None or not self.fresh('points', now, self.timeout):
            self.report('stop', 'Waiting for fresh obstacle data; motion paused')
        elif not self.monitor_active or not self.fresh('request', now, 0.3):
            self.report('stop', 'Waiting for the collision monitor; motion paused')
        elif np.count_nonzero((self.points[:, 0] >= -0.55) & (self.points[:, 0] <= 0.65)
                              & (np.abs(self.points[:, 1]) <= 0.50)
                              & (self.points[:, 2] >= 0.10) & (self.points[:, 2] <= 2.0)) > 4:
            retreat = None
            if self.mode == 'mapping':
                retreat = retreat_velocity(self.points, self.request.linear.x, self.request.linear.y, self.request.angular.z)
            if retreat is not None:
                output.linear.x, output.linear.y = retreat
                self.report('retreat', 'Moving away from obstacle slowly (maximum 0.10 m/s)')
            else:
                self.report('stop', self.retreat_hint() if self.mode == 'mapping' else 'Obstacle stop: select Mapping for manual retreat')
        else:
            if self.fresh('monitored', now, 0.3):
                output = self.monitored
                self.report('ready', 'Obstacle clearance restored' if self.last_status else 'Safety controls ready')
            else:
                self.report('stop', 'Waiting for the collision monitor; motion paused')
        self.output.publish(output)


def main(args=None):
    rclpy.init(args=args)
    node = RetreatGuard()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            node.output.publish(Twist())
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
