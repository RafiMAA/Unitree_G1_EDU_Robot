#!/usr/bin/env python3
"""Accumulate registered PointCloud2 scans into a voxelized 3D map."""

import math

from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry, Path
import numpy as np
import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import PointCloud2, PointField
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header
from std_srvs.srv import Trigger
from tf2_ros import Buffer, TransformException, TransformListener


def _rotation_matrix(x, y, z, w):
    """Return a 3x3 rotation matrix for a normalized ROS quaternion."""
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm == 0.0:
        return np.eye(3, dtype=np.float64)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


class VoxelMapper(Node):
    """Build a bounded point-cloud map using the current sensor TF pose."""

    def __init__(self):
        super().__init__('g1_voxel_mapper')

        self.map_frame = self.declare_parameter('map_frame', 'odom').value
        self.cloud_topic = self.declare_parameter(
            'cloud_topic', '/g1/mid360/points'
        ).value
        self.map_topic = self.declare_parameter(
            'map_topic', '/g1/map/points'
        ).value
        self.voxel_size = float(self.declare_parameter('voxel_size', 0.10).value)
        self.min_range = float(self.declare_parameter('min_range', 0.35).value)
        self.max_range = float(self.declare_parameter('max_range', 35.0).value)
        self.min_height = float(self.declare_parameter('min_height', -0.25).value)
        self.max_height = float(self.declare_parameter('max_height', 4.0).value)
        self.max_voxels = int(self.declare_parameter('max_voxels', 500000).value)
        publish_rate = float(self.declare_parameter('publish_rate', 1.0).value)

        if self.voxel_size <= 0.0:
            raise ValueError('voxel_size must be greater than zero')
        if publish_rate <= 0.0:
            raise ValueError('publish_rate must be greater than zero')

        sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        map_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        self.tf_buffer = Buffer(cache_time=Duration(seconds=10.0))
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.map_pub = self.create_publisher(PointCloud2, self.map_topic, map_qos)
        self.path_pub = self.create_publisher(Path, '/g1/mapping_path', map_qos)
        self.create_subscription(PointCloud2, self.cloud_topic, self._on_cloud, sensor_qos)
        self.create_subscription(Odometry, '/g1/odom', self._on_odom, 10)
        self.create_service(Trigger, '/g1/map/clear', self._clear_map)
        self.create_timer(1.0 / publish_rate, self._publish_map)

        self._voxels = {}
        self._dirty = False
        self._path = Path()
        self._path.header.frame_id = self.map_frame
        self._last_path_position = None
        self._last_status_ns = 0
        self._warned_tf = False

        self.get_logger().info(
            f'3D voxel mapper ready: {self.cloud_topic} -> {self.map_topic}, '
            f'frame={self.map_frame}, voxel={self.voxel_size:.2f} m'
        )

    def _on_cloud(self, msg):
        try:
            transform = self.tf_buffer.lookup_transform(
                self.map_frame,
                msg.header.frame_id,
                rclpy.time.Time(),
                timeout=Duration(seconds=0.2),
            )
        except TransformException as exc:
            if not self._warned_tf:
                self.get_logger().warning(
                    f'Waiting for TF {self.map_frame} <- {msg.header.frame_id}: {exc}'
                )
                self._warned_tf = True
            return

        self._warned_tf = False
        points = point_cloud2.read_points_numpy(
            msg, field_names=('x', 'y', 'z'), skip_nans=True
        )
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if points.size == 0:
            return

        ranges = np.linalg.norm(points, axis=1)
        valid = (ranges >= self.min_range) & (ranges <= self.max_range)
        points = points[valid]
        if points.size == 0:
            return

        translation = transform.transform.translation
        rotation = transform.transform.rotation
        rotation_matrix = _rotation_matrix(
            rotation.x, rotation.y, rotation.z, rotation.w
        )
        offset = np.array(
            [translation.x, translation.y, translation.z], dtype=np.float64
        )
        world_points = points @ rotation_matrix.T + offset
        height_mask = (
            (world_points[:, 2] >= self.min_height)
            & (world_points[:, 2] <= self.max_height)
        )
        world_points = world_points[height_mask]
        if world_points.size == 0:
            return

        voxel_indices = np.floor(world_points / self.voxel_size).astype(np.int32)
        unique_voxels, unique_indices = np.unique(
            voxel_indices, axis=0, return_index=True
        )

        room = self.max_voxels - len(self._voxels)
        for voxel, point_index in zip(unique_voxels, unique_indices):
            key = (int(voxel[0]), int(voxel[1]), int(voxel[2]))
            if key in self._voxels or room > 0:
                if key not in self._voxels:
                    room -= 1
                self._voxels[key] = world_points[point_index].astype(np.float32)

        self._dirty = True
        now_ns = self.get_clock().now().nanoseconds
        if now_ns - self._last_status_ns >= 5_000_000_000:
            self.get_logger().info(f'3D map contains {len(self._voxels):,} voxels')
            self._last_status_ns = now_ns

    def _on_odom(self, msg):
        position = msg.pose.pose.position
        current = np.array([position.x, position.y, position.z])
        if self._last_path_position is not None:
            if np.linalg.norm(current - self._last_path_position) < 0.10:
                return

        pose = PoseStamped()
        pose.header = msg.header
        pose.header.frame_id = self.map_frame
        pose.pose = msg.pose.pose
        self._path.header.stamp = msg.header.stamp
        self._path.poses.append(pose)
        if len(self._path.poses) > 5000:
            self._path.poses.pop(0)
        self._last_path_position = current
        self.path_pub.publish(self._path)

    def _publish_map(self):
        if not self._voxels or not self._dirty:
            return

        points = np.asarray(list(self._voxels.values()), dtype=np.float32)
        self._publish_points(points)
        self._dirty = False

    def _publish_points(self, points):
        """Publish points, including an empty cloud used to clear RViz."""
        msg = PointCloud2()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.map_frame
        msg.height = 1
        msg.width = len(points)
        msg.fields = [
            PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1),
        ]
        msg.is_bigendian = False
        msg.point_step = 12
        msg.row_step = msg.point_step * msg.width
        msg.data = points.tobytes()
        msg.is_dense = True
        self.map_pub.publish(msg)

    def _clear_map(self, _request, response):
        self._voxels.clear()
        self._path.poses.clear()
        self._last_path_position = None
        self._dirty = False
        self._publish_points(np.empty((0, 3), dtype=np.float32))
        response.success = True
        response.message = '3D voxel map and mapping path cleared'
        self.get_logger().info(response.message)
        return response


def main(args=None):
    rclpy.init(args=args)
    node = VoxelMapper()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
