import os
import math
from pathlib import Path

import yaml
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState, Imu
from std_msgs.msg import Float64MultiArray, String
from geometry_msgs.msg import Twist

import onnxruntime as ort

# Actuator order (matches data.ctrl layout in g1_mujoco_bridge)
ACTUATOR_ORDER = [
    "left_hip_pitch", "left_hip_roll", "left_hip_yaw",
    "left_knee", "left_ankle_pitch", "left_ankle_roll",
    "right_hip_pitch", "right_hip_roll", "right_hip_yaw",
    "right_knee", "right_ankle_pitch", "right_ankle_roll",
    "waist_yaw", "waist_roll", "waist_pitch",
    "left_shoulder_pitch", "left_shoulder_roll", "left_shoulder_yaw",
    "left_elbow", "left_wrist_roll", "left_wrist_pitch", "left_wrist_yaw",
    "right_shoulder_pitch", "right_shoulder_roll", "right_shoulder_yaw",
    "right_elbow", "right_wrist_roll", "right_wrist_pitch", "right_wrist_yaw",
]

POLICY_RELATIVE_PATH = Path("deploy/robots/g1/config/policy/velocity/v0")


def resolve_policy_dir():
    """Locate the Unitree policy without depending on a user's home path."""
    override = os.environ.get("UNITREE_RL_MJLAB_DIR")
    if override:
        candidate = Path(override).expanduser() / POLICY_RELATIVE_PATH
        if candidate.is_dir():
            return candidate
        raise FileNotFoundError(
            f"UNITREE_RL_MJLAB_DIR does not contain {POLICY_RELATIVE_PATH}: "
            f"{candidate}"
        )

    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in (start, *start.parents):
            candidate = parent / "unitree_rl_mjlab" / POLICY_RELATIVE_PATH
            if candidate.is_dir():
                return candidate

    raise FileNotFoundError(
        "G1 policy not found. Clone unitree_rl_mjlab beside "
        "g1-ros2-workspace or set UNITREE_RL_MJLAB_DIR."
    )

RIGHT_ARM_INDICES = np.arange(22, 29)
RIGHT_ARM_WAVE_POSE = np.array(
    [-2.2, -0.8, 0.0, 0.49, 0.0, 0.0, 0.0], dtype=np.float32
)
WAVE_RAISE_SEC = 2.0
WAVE_MOTION_SEC = 2.5
WAVE_LOWER_SEC = 2.0
WAVE_DURATION_SEC = WAVE_RAISE_SEC + WAVE_MOTION_SEC + WAVE_LOWER_SEC
DEFAULT_CMD_VEL_TIMEOUT_SEC = 0.5
# Do not hand balance from fixed-stand to the walking policy while a velocity
# smoother is only partway through its initial ramp. The terminal controller
# starts at 0.2 m/s; 0.15 keeps all three UI axes usable while avoiding the
# marginal 0.10 m/s gait boundary.
POLICY_ACTIVATION_THRESHOLD = 0.15

# Slew-rate limits: max velocity change per second.  These act as a safety net
# against impossible command jumps.  When the full navigation stack is running,
# the nav2 velocity_smoother handles the primary ramp (1.0 m/s²), so these must
# be wider to avoid double-smoothing that halves effective acceleration.  In
# sim-only mode (no smoother), these still prevent 0→max step changes.
SLEW_RATE_LIN = 0.8     # m/s per second  (per axis) - smooth ramp for stability at high speeds
SLEW_RATE_ANG = 1.5     # rad/s per second
CONTROL_DT = 0.02       # 50 Hz


class G1RLController(Node):
    def __init__(self):
        super().__init__("g1_core")
        self.state = "IDLE"
        
        # RL state buffers
        self.obs_buf = np.zeros((1, 98), dtype=np.float32)
        self.joint_pos = np.zeros(29, dtype=np.float32)
        self.joint_vel = np.zeros(29, dtype=np.float32)
        self.base_ang_vel = np.zeros(3, dtype=np.float32)
        self.base_quat = np.array([0, 0, 0, 1], dtype=np.float32) # x, y, z, w
        self.commands = np.zeros(3, dtype=np.float32)
        self.target_commands = np.zeros(3, dtype=np.float32)  # raw from cmd_vel
        self._slew_max = np.array([
            SLEW_RATE_LIN * CONTROL_DT,   # lin_x
            SLEW_RATE_LIN * CONTROL_DT,   # lin_y
            SLEW_RATE_ANG * CONTROL_DT,   # ang_z
        ], dtype=np.float32)
        self.last_cmd_vel_ns = None
        self.cmd_vel_timeout_sec = float(
            self.declare_parameter(
                "cmd_vel_timeout_sec", DEFAULT_CMD_VEL_TIMEOUT_SEC
            ).value
        )
        if self.cmd_vel_timeout_sec < 0.0:
            raise ValueError("cmd_vel_timeout_sec must be non-negative")
        self.last_action = np.zeros(29, dtype=np.float32)
        # Keep MuJoCo's fixed-stand controller active until the operator or
        # Nav2 sends a real motion request. Publishing even a zero-command RL
        # target makes the bridge switch immediately to the much softer
        # locomotion gains, before the policy has any reason to take over.
        self.policy_active = False
        self.wave_started_at = None
        self.wave_start_pose = np.zeros(7, dtype=np.float32)
        
        self.has_joint_state = False
        self.has_imu = False
        
        # Time tracking for gait phase
        self.start_time = self.get_clock().now().nanoseconds / 1e9
        
        # Load ONNX and Config
        self._load_policy()
        
        self.create_subscription(JointState, "g1/joint_states", self.on_joint_state, 10)
        self.create_subscription(Imu, "g1/imu", self.on_imu, 10)
        cmd_vel_topic = self.declare_parameter(
            "cmd_vel_topic", "/cmd_vel"
        ).value
        self.create_subscription(Twist, cmd_vel_topic, self.on_cmd_vel, 10)
        self.create_subscription(
            String, "g1/gesture_command", self.on_gesture_command, 10
        )
        self.cmd_pub = self.create_publisher(Float64MultiArray, "g1/joint_cmd", 10)

        # RL policy runs at 50 Hz
        self.create_timer(0.02, self.control_tick)

        self.get_logger().info(
            f"g1_core RL controller started (ONNX), velocity input={cmd_vel_topic}"
        )

    def _load_policy(self):
        policy_dir = resolve_policy_dir()
        onnx_path = policy_dir / "exported" / "policy.onnx"
        self.get_logger().info(f"Loading ONNX policy from {onnx_path}")
        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = 1
        sess_options.inter_op_num_threads = 1
        sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.sess = ort.InferenceSession(str(onnx_path), sess_options=sess_options)
        
        yaml_path = policy_dir / "params" / "deploy.yaml"
        with open(yaml_path, 'r') as f:
            cfg = yaml.safe_load(f)
            
        self.action_scale = np.array(cfg["actions"]["JointPositionAction"]["scale"], dtype=np.float32)
        self.action_offset = np.array(cfg["actions"]["JointPositionAction"]["offset"], dtype=np.float32)
        
    def on_joint_state(self, msg: JointState):
        if len(msg.position) == 29 and len(msg.velocity) == 29:
            self.joint_pos[:] = msg.position
            self.joint_vel[:] = msg.velocity
            self.has_joint_state = True

    def on_imu(self, msg: Imu):
        self.base_ang_vel = np.array([
            msg.angular_velocity.x,
            msg.angular_velocity.y,
            msg.angular_velocity.z
        ], dtype=np.float32)
        
        self.base_quat = np.array([
            msg.orientation.x,
            msg.orientation.y,
            msg.orientation.z,
            msg.orientation.w
        ], dtype=np.float32)
        self.has_imu = True

    def on_cmd_vel(self, msg: Twist):
        # Store the raw target; control_tick will ramp toward it.
        self.target_commands[0] = np.clip(msg.linear.x, -0.5, 1.0)
        self.target_commands[1] = np.clip(msg.linear.y, -0.5, 0.5)
        self.target_commands[2] = np.clip(msg.angular.z, -1.0, 1.0)
        self.last_cmd_vel_ns = self.get_clock().now().nanoseconds

        if (
            not self.policy_active
            and np.linalg.norm(self.target_commands) >= POLICY_ACTIVATION_THRESHOLD
        ):
            self.policy_active = True
            self.last_action[:] = 0.0
            self.start_time = self.last_cmd_vel_ns / 1e9
            self.get_logger().info(
                "Motion command received; activating locomotion policy"
            )

    def stop_stale_command(self, now_ns):
        """Stop motion when the command publisher disappears."""
        if self.last_cmd_vel_ns is None or self.cmd_vel_timeout_sec == 0.0:
            return

        command_age_sec = (now_ns - self.last_cmd_vel_ns) / 1e9
        if command_age_sec <= self.cmd_vel_timeout_sec:
            return

        was_moving = np.linalg.norm(self.target_commands) >= 0.1
        self.target_commands[:] = 0.0
        self.last_cmd_vel_ns = None
        if was_moving:
            self.get_logger().warn(
                "cmd_vel timed out; stopping the locomotion policy"
            )

    def _apply_slew_rate(self):
        """Ramp self.commands toward self.target_commands at bounded rate."""
        delta = self.target_commands - self.commands
        clamped = np.clip(delta, -self._slew_max, self._slew_max)
        self.commands += clamped

    def on_gesture_command(self, msg: String):
        """Start a simulated wave without interrupting lower-body control."""
        if msg.data not in ("wave", "wave_with_turn"):
            self.get_logger().warn(f"Unknown gesture command: {msg.data}")
            return

        self.wave_start_pose = self.joint_pos[RIGHT_ARM_INDICES].copy()
        self.wave_started_at = self.get_clock().now().nanoseconds / 1e9
        # A one-arm overhead pose changes the center of mass.  Do not combine
        # it with a stale walking command.
        self.commands[:] = 0.0
        self.get_logger().info("Starting right-hand wave gesture")

    @staticmethod
    def _smoothstep(value):
        value = np.clip(value, 0.0, 1.0)
        return value * value * (3.0 - 2.0 * value)

    def apply_wave_gesture(self, target_pos, now_sec):
        """Blend a smooth right-arm wave over the RL policy output."""
        if self.wave_started_at is None:
            return target_pos

        elapsed = now_sec - self.wave_started_at
        policy_arm = target_pos[RIGHT_ARM_INDICES].copy()

        if elapsed < WAVE_RAISE_SEC:
            blend = self._smoothstep(elapsed / WAVE_RAISE_SEC)
            wave_arm = (
                (1.0 - blend) * self.wave_start_pose
                + blend * RIGHT_ARM_WAVE_POSE
            )
        elif elapsed < WAVE_RAISE_SEC + WAVE_MOTION_SEC:
            wave_time = elapsed - WAVE_RAISE_SEC
            phase = 2.0 * math.pi * 0.65 * wave_time
            wave_arm = RIGHT_ARM_WAVE_POSE.copy()
            # Keep heavy shoulder/elbow joints still.  Moving only the wrist
            # makes the wave visible without injecting large torso momentum.
            wave_arm[5] = 0.14 * math.sin(phase)
            wave_arm[6] = 0.45 * math.sin(phase)
        elif elapsed < WAVE_DURATION_SEC:
            lower_time = elapsed - WAVE_RAISE_SEC - WAVE_MOTION_SEC
            blend = self._smoothstep(lower_time / WAVE_LOWER_SEC)
            wave_arm = (
                (1.0 - blend) * RIGHT_ARM_WAVE_POSE
                + blend * policy_arm
            )
        else:
            self.wave_started_at = None
            self.get_logger().info("Right-hand wave gesture finished")
            return target_pos

        target_pos[RIGHT_ARM_INDICES] = wave_arm
        return target_pos

    def compute_projected_gravity(self):
        # Mathematically equivalent to inverse quaternion rotation of [0, 0, -1]
        x, y, z, w = self.base_quat
        return np.array([
            -2.0 * (x * z - w * y),
            -2.0 * (y * z + w * x),
            -(1.0 - 2.0 * (x * x + y * y))
        ], dtype=np.float32)

    def control_tick(self):
        if not self.has_joint_state or not self.has_imu:
            return

        now_ns = self.get_clock().now().nanoseconds
        self.stop_stale_command(now_ns)

        # The bridge holds DEFAULT_POS with its stable fixed-stand gains until
        # this node publishes its first command. Zero messages from the command
        # mux or collision monitor must not accidentally release that stance.
        if not self.policy_active:
            return

        # Ramp velocity toward the raw target at bounded rate
        self._apply_slew_rate()

        # Fill pre-allocated observation buffer
        # 1. Base Angular Velocity (3)
        self.obs_buf[0, 0:3] = self.base_ang_vel
        
        # 2. Projected Gravity (3)
        self.obs_buf[0, 3:6] = self.compute_projected_gravity()
        
        # 3. Commands (3)
        self.obs_buf[0, 6:9] = self.commands
        
        # 4. Gait Phase (2) — must be [0,0] when standing still!
        cmd_norm = np.linalg.norm(self.commands)
        if cmd_norm < 0.1:
            self.obs_buf[0, 9:11] = 0.0
        else:
            current_time = now_ns / 1e9 - self.start_time
            phase = (current_time % 0.6) / 0.6
            self.obs_buf[0, 9] = math.sin(phase * 2 * math.pi)
            self.obs_buf[0, 10] = math.cos(phase * 2 * math.pi)
        
        # 5. Joint Pos Rel (29)
        self.obs_buf[0, 11:40] = self.joint_pos - self.action_offset
        
        # 6. Joint Vel Rel (29)
        self.obs_buf[0, 40:69] = self.joint_vel

        # The overhead pose is outside the locomotion policy's trained arm
        # range.  Hide only the gesture-controlled joints so the policy does
        # not interpret the intentional arm pose as a whole-body disturbance.
        # IMU feedback remains untouched, so genuine balance corrections still
        # happen normally through the legs and waist.
        if self.wave_started_at is not None:
            self.obs_buf[0, 11 + RIGHT_ARM_INDICES] = 0.0
            self.obs_buf[0, 40 + RIGHT_ARM_INDICES] = 0.0
        
        # 7. Last Action (29)
        self.obs_buf[0, 69:98] = self.last_action
        
        # Debug: print first observation
        if not hasattr(self, '_debug_printed'):
            self._debug_printed = True
            self.get_logger().info(f"First obs ang_vel: {self.obs_buf[0, 0:3]}")
            self.get_logger().info(f"First obs proj_grav: {self.obs_buf[0, 3:6]}")
            self.get_logger().info(f"First obs commands: {self.obs_buf[0, 6:9]}")
            self.get_logger().info(f"First obs phase: {self.obs_buf[0, 9:11]}")
            self.get_logger().info(f"First obs joint_pos_rel[:6]: {self.obs_buf[0, 11:17]}")
            self.get_logger().info(f"First obs joint_vel[:6]: {self.obs_buf[0, 40:46]}")
        
        # Inference
        action = self.sess.run(None, {"obs": self.obs_buf})[0][0]

        # Keep last_action in the locomotion policy's trained action space;
        # the scripted arm pose is deliberately isolated above.
        self.last_action = action
        
        # Scale and offset to get target positions
        target_pos = action * self.action_scale + self.action_offset
        now_sec = now_ns / 1e9
        target_pos = self.apply_wave_gesture(target_pos, now_sec)

        
        # Publish
        cmd = Float64MultiArray()
        cmd.data = target_pos.tolist()
        self.cmd_pub.publish(cmd)


def main():
    rclpy.init()
    node = G1RLController()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
