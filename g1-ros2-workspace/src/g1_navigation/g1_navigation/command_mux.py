#!/usr/bin/env python3
"""Mode-aware, timeout-safe arbitration of browser teleop and Nav2 commands."""

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_msgs.msg import Bool, String


def source_is_fresh(stamp_ns, timeout, now_ns):
    """Whether a command timestamp remains inside its safety timeout."""
    return stamp_ns is not None and (now_ns - stamp_ns) / 1e9 <= timeout


class CommandMux(Node):
    """Select one velocity source and emit zero on stale input or e-stop."""

    def __init__(self):
        super().__init__('g1_command_mux')
        self.teleop_timeout = float(
            self.declare_parameter('teleop_timeout', 0.50).value
        )
        self.nav_timeout = float(self.declare_parameter('nav_timeout', 0.50).value)
        publish_rate = float(self.declare_parameter('publish_rate', 30.0).value)
        self.mode = self.declare_parameter('initial_mode', 'mapping').value
        self.estop = False
        self.last_teleop = Twist()
        self.last_nav = Twist()
        self.teleop_stamp_ns = None
        self.nav_stamp_ns = None

        self.publisher = self.create_publisher(Twist, '/cmd_vel_muxed', 10)
        self.create_subscription(Twist, '/cmd_vel_teleop', self.on_teleop, 10)
        self.create_subscription(Twist, '/cmd_vel_controller', self.on_nav, 10)
        self.create_subscription(String, '/ui/mode', self.on_mode, 10)
        self.create_subscription(Bool, '/ui/emergency_stop', self.on_estop, 10)
        self.create_timer(1.0 / publish_rate, self.publish_selected)
        self.get_logger().info(f'Command mux ready; initial mode={self.mode}')

    def now_ns(self):
        return self.get_clock().now().nanoseconds

    def on_teleop(self, msg):
        self.last_teleop = msg
        self.teleop_stamp_ns = self.now_ns()

    def on_nav(self, msg):
        self.last_nav = msg
        self.nav_stamp_ns = self.now_ns()

    def on_mode(self, msg):
        if msg.data not in ('mapping', 'navigate', 'idle'):
            self.get_logger().warning(f'Ignoring unknown control mode: {msg.data}')
            return
        if msg.data != self.mode:
            self.mode = msg.data
            self.publisher.publish(Twist())
            self.get_logger().info(f'Control mode changed to {self.mode}')

    def on_estop(self, msg):
        self.estop = bool(msg.data)
        if self.estop:
            self.publisher.publish(Twist())
            self.get_logger().error('Software emergency stop engaged')
        else:
            self.get_logger().warning('Software emergency stop released')

    def publish_selected(self):
        output = Twist()
        now = self.now_ns()
        if self.estop or self.mode == 'idle':
            self.publisher.publish(output)
            return

        if self.mode == 'mapping':
            if source_is_fresh(self.teleop_stamp_ns, self.teleop_timeout, now):
                output = self.last_teleop
        elif self.mode == 'navigate':
            if source_is_fresh(self.nav_stamp_ns, self.nav_timeout, now):
                output = self.last_nav
        self.publisher.publish(output)


def main(args=None):
    rclpy.init(args=args)
    node = CommandMux()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            node.publisher.publish(Twist())
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
