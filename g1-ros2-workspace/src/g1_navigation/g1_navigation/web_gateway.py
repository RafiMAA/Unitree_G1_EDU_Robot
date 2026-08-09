#!/usr/bin/env python3
"""Small browser-facing adapter for Nav2 goals, cancellation and status."""

import json

import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.node import Node
from std_msgs.msg import Bool, String
from tf2_ros import Buffer, TransformException, TransformListener


class WebGateway(Node):
    """Validate UI mode and translate PoseStamped messages to Nav2 actions."""

    def __init__(self):
        super().__init__('g1_web_gateway')
        self.mode = 'mapping'
        self.goal_handle = None
        self.action_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.status_pub = self.create_publisher(String, '/ui/navigation_status', 10)
        self.pose_pub = self.create_publisher(PoseStamped, '/ui/robot_pose', 10)
        self.tf_buffer = Buffer(cache_time=Duration(seconds=10.0))
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.create_subscription(PoseStamped, '/ui/goal', self.on_goal, 10)
        self.create_subscription(String, '/ui/mode', self.on_mode, 10)
        self.create_subscription(Bool, '/ui/cancel_navigation', self.on_cancel, 10)
        self.create_timer(0.10, self.publish_robot_pose)
        self.publish_status('idle', 'Gateway ready')

    def publish_robot_pose(self):
        try:
            transform = self.tf_buffer.lookup_transform(
                'map', 'base_footprint', rclpy.time.Time()
            )
        except TransformException:
            return
        pose = PoseStamped()
        pose.header = transform.header
        pose.pose.position.x = transform.transform.translation.x
        pose.pose.position.y = transform.transform.translation.y
        pose.pose.position.z = transform.transform.translation.z
        pose.pose.orientation = transform.transform.rotation
        self.pose_pub.publish(pose)

    def publish_status(self, state, message, **values):
        payload = {'state': state, 'message': message, **values}
        msg = String()
        msg.data = json.dumps(payload)
        self.status_pub.publish(msg)

    def on_mode(self, msg):
        self.mode = msg.data
        if self.mode != 'navigate':
            self.cancel_active_goal()
        self.publish_status('idle', f'Mode: {self.mode}')

    def on_cancel(self, msg):
        if msg.data:
            self.cancel_active_goal()

    def cancel_active_goal(self):
        if self.goal_handle is not None:
            self.goal_handle.cancel_goal_async()
            self.goal_handle = None
        self.publish_status('canceled', 'Navigation canceled')

    def on_goal(self, pose):
        if self.mode != 'navigate':
            self.publish_status('rejected', 'Switch to navigation mode first')
            return
        if pose.header.frame_id != 'map':
            self.publish_status('rejected', 'Goal frame must be map')
            return
        if not self.action_client.server_is_ready():
            self.publish_status('unavailable', 'Nav2 action server is not ready')
            return
        self.cancel_active_goal()
        goal = NavigateToPose.Goal()
        goal.pose = pose
        future = self.action_client.send_goal_async(
            goal, feedback_callback=self.on_feedback
        )
        future.add_done_callback(self.on_goal_response)
        self.publish_status('sending', 'Sending goal to Nav2')

    def on_goal_response(self, future):
        try:
            handle = future.result()
        except Exception as exc:  # rclpy action transport failure
            self.publish_status('failed', f'Goal request failed: {exc}')
            return
        if not handle.accepted:
            self.publish_status('rejected', 'Nav2 rejected the goal')
            return
        self.goal_handle = handle
        result_future = handle.get_result_async()
        result_future.add_done_callback(self.on_result)
        self.publish_status('navigating', 'Goal accepted')

    def on_feedback(self, feedback):
        value = feedback.feedback
        self.publish_status(
            'navigating',
            'Walking to goal',
            distance_remaining=round(float(value.distance_remaining), 3),
            navigation_time={
                'sec': value.navigation_time.sec,
                'nanosec': value.navigation_time.nanosec,
            },
        )

    def on_result(self, future):
        try:
            wrapped = future.result()
            status = int(wrapped.status)
        except Exception as exc:
            self.publish_status('failed', f'Navigation result failed: {exc}')
            return
        self.goal_handle = None
        if status == 4:
            self.publish_status('succeeded', 'Goal reached')
        elif status == 5:
            self.publish_status('canceled', 'Navigation canceled')
        else:
            self.publish_status('failed', f'Navigation ended with status {status}')


def main(args=None):
    rclpy.init(args=args)
    node = WebGateway()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
