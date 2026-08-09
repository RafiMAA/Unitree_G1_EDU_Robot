#!/usr/bin/env python3
"""Remove floor and G1 self returns from the live MID-360 point cloud."""

import numpy as np
import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header
from tf2_ros import Buffer, TransformException, TransformListener

from .cloud_to_scan import quaternion_matrix


def filter_navigation_points(
    points,
    min_height=0.10,
    max_height=2.0,
    self_min_x=-0.38,
    self_max_x=0.42,
    self_half_width=0.36,
):
    """Filter points already expressed in base_footprint coordinates."""
    height_ok = (points[:, 2] >= min_height) & (points[:, 2] <= max_height)
    inside_robot = (
        (points[:, 0] >= self_min_x)
        & (points[:, 0] <= self_max_x)
        & (np.abs(points[:, 1]) <= self_half_width)
    )
    return points[height_ok & ~inside_robot]


class CloudFilter(Node):
    """Publish a navigation-safe cloud in the stable planar base frame."""

    def __init__(self):
        super().__init__('g1_cloud_filter')
        self.input_topic = self.declare_parameter(
            'input_topic', '/g1/mid360/points'
        ).value
        self.output_topic = self.declare_parameter(
            'output_topic', '/g1/mid360/points_filtered'
        ).value
        self.target_frame = self.declare_parameter(
            'target_frame', 'base_footprint'
        ).value
        self.min_height = float(self.declare_parameter('min_height', 0.10).value)
        self.max_height = float(self.declare_parameter('max_height', 2.0).value)
        self.self_min_x = float(self.declare_parameter('self_min_x', -0.38).value)
        self.self_max_x = float(self.declare_parameter('self_max_x', 0.42).value)
        self.self_half_width = float(
            self.declare_parameter('self_half_width', 0.36).value
        )

        self.tf_buffer = Buffer(cache_time=Duration(seconds=10.0))
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.publisher = self.create_publisher(
            PointCloud2, self.output_topic, qos_profile_sensor_data
        )
        self.subscription = self.create_subscription(
            PointCloud2, self.input_topic, self.on_cloud, qos_profile_sensor_data
        )
        self._warned_tf = False
        self.get_logger().info(
            f'Filtering {self.input_topic} -> {self.output_topic} in {self.target_frame}'
        )

    def on_cloud(self, msg):
        try:
            transform = self.tf_buffer.lookup_transform(
                self.target_frame,
                msg.header.frame_id,
                rclpy.time.Time(),
                timeout=Duration(seconds=0.10),
            )
        except TransformException as exc:
            if not self._warned_tf:
                self.get_logger().warning(f'Cloud filter transform unavailable: {exc}')
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
        points = filter_navigation_points(
            points,
            self.min_height,
            self.max_height,
            self.self_min_x,
            self.self_max_x,
            self.self_half_width,
        ).astype(np.float32)

        header = Header()
        # We deliberately transformed with the latest available TF above.
        # Timestamp the resulting base-frame cloud at completion as well. The
        # simulated four-camera LiDAR render can take ~0.5 s; retaining its
        # pre-render stamp makes Collision Monitor reject a newly delivered
        # cloud as stale and repeatedly chop the walking command.
        header.stamp = self.get_clock().now().to_msg()
        header.frame_id = self.target_frame
        self.publisher.publish(point_cloud2.create_cloud_xyz32(header, points))


def main(args=None):
    rclpy.init(args=args)
    node = CloudFilter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
