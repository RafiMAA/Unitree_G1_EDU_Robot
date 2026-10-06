"""Persist browser-edited map positions as JSON independently of SLAM/Nav2."""

import json

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String

from .label_store import LabelStore, default_labels_dir, validate_map_id


class MapLabels(Node):
    def __init__(self):
        super().__init__('g1_map_labels')
        directory = self.declare_parameter('labels_dir', str(default_labels_dir())).value
        self.store = LabelStore(directory or default_labels_dir())
        self.publisher = self.create_publisher(
            String, '/ui/map_labels', QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        )
        self.create_subscription(String, '/ui/map_label_command', self.on_command, 10)
        self.get_logger().info(f'Map location JSON directory: {self.store.directory}')

    def on_command(self, msg):
        response = {}
        try:
            command = json.loads(msg.data)
            if not isinstance(command, dict):
                raise ValueError('Location command must be an object')
            response = {
                'map_id': validate_map_id(command.get('map_id')),
                'request_id': command.get('request_id'),
            }
            labels = self.store.apply(command)
            response.update(labels=labels, saved_file=str(self.store.path(response['map_id'])))
        except (ValueError, TypeError, OSError) as exc:
            response['error'] = str(exc)
            self.get_logger().warning(f'Location command failed: {exc}')
        result = String()
        result.data = json.dumps(response, ensure_ascii=False)
        self.publisher.publish(result)


def main(args=None):
    rclpy.init(args=args)
    node = MapLabels()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
