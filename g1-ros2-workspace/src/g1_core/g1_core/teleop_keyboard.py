#!/usr/bin/env python3
"""Keyboard teleop for the G1 robot.

Controls use standard WASD + QE layout:

  W / S  : walk forward / backward   (linear.x)
  A / D  : turn left / right          (angular.z)
  Q / E  : strafe left / right        (linear.y)
  + / -  : increase / decrease speed
  SPACE  : stop (zero all commands)
  X      : quit

These publish geometry_msgs/Twist on /cmd_vel.
The state_machine_node feeds these into the RL policy as velocity commands.
Movement commands remain active until they are changed or stopped with SPACE.
"""
import sys
import tty
import termios
import select
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

# Speed increment per key press
SPEED_STEP = 0.1
# Initial maximum speeds
DEFAULT_LIN_SPEED = 0.2
DEFAULT_ANG_SPEED = 0.3

BANNER = """
┌──────────────────────────────────────────┐
│       G1 Robot — Keyboard Teleop         │
├──────────────────────────────────────────┤
│                                          │
│         W          Walk Forward          │
│       A   D    Turn Left / Right         │
│         S          Walk Backward         │
│                                          │
│       Q   E    Strafe Left / Right       │
│       + / -    Increase / Decrease spd   │
│       SPACE    Stop / Clear Command      │
│       X        Quit                      │
│                                          │
└──────────────────────────────────────────┘

  Commands stay active until changed; press SPACE to stop.
"""


def get_key(settings, timeout=0.1):
    """Read a single keypress."""
    tty.setraw(sys.stdin.fileno())
    rlist, _, _ = select.select([sys.stdin], [], [], timeout)
    key = ""
    if rlist:
        key = sys.stdin.read(1)
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
    return key


class TeleopKeyboard(Node):
    def __init__(self):
        super().__init__("teleop_keyboard")
        self.pub = self.create_publisher(Twist, "cmd_vel_teleop", 10)
        self.settings = termios.tcgetattr(sys.stdin)

        self.lin_x = 0.0  # forward/backward
        self.lin_y = 0.0  # strafe left/right
        self.ang_z = 0.0  # turn

        self.max_lin = DEFAULT_LIN_SPEED
        self.max_ang = DEFAULT_ANG_SPEED

    def run(self):
        print(BANNER)
        self._print_status()

        try:
            while rclpy.ok():
                key = get_key(self.settings)

                if key in ("w", "W"):          # forward
                    self.lin_x = self.max_lin
                    self.lin_y = 0.0
                    self.ang_z = 0.0
                elif key in ("s", "S"):        # backward
                    self.lin_x = -self.max_lin
                    self.lin_y = 0.0
                    self.ang_z = 0.0
                elif key in ("a", "A"):        # turn left
                    self.lin_x = 0.0
                    self.lin_y = 0.0
                    self.ang_z = self.max_ang
                elif key in ("d", "D"):        # turn right
                    self.lin_x = 0.0
                    self.lin_y = 0.0
                    self.ang_z = -self.max_ang
                elif key in ("q", "Q"):        # strafe left
                    self.lin_x = 0.0
                    self.lin_y = self.max_lin
                    self.ang_z = 0.0
                elif key in ("e", "E"):        # strafe right
                    self.lin_x = 0.0
                    self.lin_y = -self.max_lin
                    self.ang_z = 0.0
                elif key in ("=", "+"):        # speed up
                    self.max_lin = min(self.max_lin + SPEED_STEP, 2.0)
                    self.max_ang = min(self.max_ang + SPEED_STEP, 1.5)
                    self._print_status()
                elif key in ("-", "_"):        # speed down
                    self.max_lin = max(self.max_lin - SPEED_STEP, 0.1)
                    self.max_ang = max(self.max_ang - SPEED_STEP, 0.1)
                    self._print_status()
                elif key == " ":               # stop
                    self.lin_x = 0.0
                    self.lin_y = 0.0
                    self.ang_z = 0.0
                elif key in ("\x03", "x", "X"):  # Ctrl-C or X = quit
                    self.lin_x = 0.0
                    self.lin_y = 0.0
                    self.ang_z = 0.0
                    self._publish()
                    break

                # A terminal reports key presses, not key releases. Keep the
                # selected velocity latched and publish it continuously. The
                # previous exponential decay dropped a 0.5 m/s command below
                # the policy's 0.1 gait threshold in only 0.3 seconds, before
                # the 0.6-second gait cycle could complete.

                self._publish()

        except Exception as e:
            print(f"\nError: {e}")
        finally:
            # Send zero on exit
            self.pub.publish(Twist())
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.settings)
            print("\nTeleop stopped.")

    def _publish(self):
        t = Twist()
        t.linear.x = self.lin_x
        t.linear.y = self.lin_y
        t.angular.z = self.ang_z
        self.pub.publish(t)

    def _print_status(self):
        print(
            f"\r  Speed: lin={self.max_lin:.1f} m/s  ang={self.max_ang:.1f} rad/s"
            "              ",
            end="",
            flush=True,
        )


def main(args=None):
    rclpy.init(args=args)
    node = TeleopKeyboard()
    node.run()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
