#!/usr/bin/env python3
"""Project a configurable horizontal slice of PointCloud2 into LaserScan."""

import math

import numpy as np
import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan, PointCloud2
from sensor_msgs_py import point_cloud2
from tf2_ros import Buffer, TransformException, TransformListener


def quaternion_matrix(x, y, z, w):
    """Return a rotation matrix for a ROS quaternion."""
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm == 0.0:
        return np.eye(3)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def project_ranges(
    points,
    angle_min,
    angle_max,
    angle_increment,
    range_min,
    range_max,
):
    """Return nearest planar range per angular bin for already filtered points."""
    bin_count = int(math.ceil((angle_max - angle_min) / angle_increment))
    ranges = np.full(bin_count, np.inf, dtype=np.float32)
    if points.size == 0:
        return ranges
    planar_ranges = np.hypot(points[:, 0], points[:, 1])
    angles = np.arctan2(points[:, 1], points[:, 0])
    valid = (
        (planar_ranges >= range_min)
        & (planar_ranges <= range_max)
        & (angles >= angle_min)
        & (angles < angle_max)
    )
    indices = ((angles[valid] - angle_min) / angle_increment).astype(np.int32)
    np.minimum.at(ranges, indices, planar_ranges[valid].astype(np.float32))
    return ranges


class CloudToScan(Node):
    """Convert the live MID-360 cloud into the planar scan used by 2D SLAM."""

    def __init__(self):
        super().__init__('g1_cloud_to_scan')
        self.cloud_topic = self.declare_parameter(
            'cloud_topic', '/g1/mid360/points'
        ).value
        self.scan_topic = self.declare_parameter('scan_topic', '/scan').value
        self.target_frame = self.declare_parameter(
            'target_frame', 'base_footprint'
        ).value
        self.min_height = float(self.declare_parameter('min_height', 0.10).value)
        self.max_height = float(self.declare_parameter('max_height', 1.40).value)
        self.range_min = float(self.declare_parameter('range_min', 0.35).value)
        self.range_max = float(self.declare_parameter('range_max', 20.0).value)
        self.angle_min = float(self.declare_parameter('angle_min', -math.pi).value)
        self.angle_max = float(self.declare_parameter('angle_max', math.pi).value)
        self.angle_increment = float(
            self.declare_parameter('angle_increment', math.radians(0.5)).value
        )
        self.scan_time = float(self.declare_parameter('scan_time', 0.20).value)

        if self.min_height >= self.max_height:
            raise ValueError('min_height must be less than max_height')
        if self.range_min >= self.range_max:
            raise ValueError('range_min must be less than range_max')
        if self.angle_increment <= 0.0:
            raise ValueError('angle_increment must be positive')

        self.bin_count = int(
            math.ceil((self.angle_max - self.angle_min) / self.angle_increment)
        )
        self.tf_buffer = Buffer(cache_time=Duration(seconds=10.0))
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.publisher = self.create_publisher(
            LaserScan, self.scan_topic, qos_profile_sensor_data
        )
        self.subscription = self.create_subscription(
            PointCloud2,
            self.cloud_topic,
            self.on_cloud,
            qos_profile_sensor_data,
        )
        self._warned_tf = False
        self.get_logger().info(
            f'Projecting {self.cloud_topic} -> {self.scan_topic} in {self.target_frame}'
        )

    def on_cloud(self, msg):
        try:
            transform = self.tf_buffer.lookup_transform(
                self.target_frame,
                msg.header.frame_id,
                # Sensor rendering and TF publishing run on different timers.
                # The latest transform avoids millisecond-scale future
                # extrapolation while remaining well inside our 0.1 s limit.
                rclpy.time.Time(),
                timeout=Duration(seconds=0.10),
            )
        except TransformException as exc:
            if not self._warned_tf:
                self.get_logger().warning(f'Cloud transform unavailable: {exc}')
                self._warned_tf = True
            return
        self._warned_tf = False

        points = point_cloud2.read_points_numpy(
            msg, field_names=('x', 'y', 'z'), skip_nans=True
        )
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if points.size == 0:
            return

        q = transform.transform.rotation
        t = transform.transform.translation
        points = points @ quaternion_matrix(q.x, q.y, q.z, q.w).T
        points += np.array([t.x, t.y, t.z])
        height_mask = (points[:, 2] >= self.min_height) & (
            points[:, 2] <= self.max_height
        )
        points = points[height_mask]

        ranges = project_ranges(
            points,
            self.angle_min,
            self.angle_max,
            self.angle_increment,
            self.range_min,
            self.range_max,
        )

        scan = LaserScan()
        # Points were transformed using latest TF, so publish the projected
        # scan with the corresponding completion time instead of propagating
        # a potentially stale render timestamp into slam_toolbox.
        scan.header.stamp = self.get_clock().now().to_msg()
        scan.header.frame_id = self.target_frame
        scan.angle_min = self.angle_min
        scan.angle_max = self.angle_min + (self.bin_count - 1) * self.angle_increment
        scan.angle_increment = self.angle_increment
        scan.time_increment = 0.0
        scan.scan_time = self.scan_time
        scan.range_min = self.range_min
        scan.range_max = self.range_max
        scan.ranges = ranges.tolist()
        self.publisher.publish(scan)


def main(args=None):
    rclpy.init(args=args)
    node = CloudToScan()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
