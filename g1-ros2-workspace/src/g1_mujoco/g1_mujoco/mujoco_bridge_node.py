import os
import math
import threading
import time
from pathlib import Path

# MuJoCo selects its offscreen GL backend when it is first imported.  The
# passive viewer still uses GLFW, while sensor renderers use EGL.
os.environ.setdefault("MUJOCO_GL", "egl")

import mujoco
import mujoco.viewer
import numpy as np
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState, Imu
from std_msgs.msg import Float64MultiArray
from tf2_ros import TransformBroadcaster

from .joint_names import ACTUATOR_ORDER

MODEL_RELATIVE_PATH = Path("unitree_robots/g1/scene_29dof.xml")


def resolve_model_path():
    """Locate the modified Unitree MuJoCo model in a portable workspace."""
    override = os.environ.get("G1_MUJOCO_MODEL_PATH")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(
            f"G1_MUJOCO_MODEL_PATH does not exist: {candidate}"
        )

    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in (start, *start.parents):
            for relative in (
                Path("unitree_mujoco") / MODEL_RELATIVE_PATH,
                Path("src/unitree_mujoco") / MODEL_RELATIVE_PATH,
            ):
                candidate = parent / relative
                if candidate.is_file():
                    return candidate

    raise FileNotFoundError(
        "G1 MuJoCo model not found. Populate src/unitree_mujoco or set "
        "G1_MUJOCO_MODEL_PATH."
    )

# ---------------------------------------------------------------------------
# PD gains — MUST MATCH the RL training parameters exactly.
# From ONNX metadata / deploy.yaml
# ---------------------------------------------------------------------------
RL_STIFFNESS = [
    40.179, 99.098, 40.179, 99.098, 28.501, 28.501,   # Left leg
    40.179, 99.098, 40.179, 99.098, 28.501, 28.501,   # Right leg
    40.179, 28.501, 28.501,                             # Waist
    14.251, 14.251, 14.251, 14.251, 14.251, 16.778, 16.778,  # Left arm
    14.251, 14.251, 14.251, 14.251, 14.251, 16.778, 16.778   # Right arm
]

RL_DAMPING = [
    2.558, 6.309, 2.558, 6.309, 1.814, 1.814,
    2.558, 6.309, 2.558, 6.309, 1.814, 1.814,
    2.558, 1.814, 1.814,
    0.907, 0.907, 0.907, 0.907, 0.907, 1.068, 1.068,
    0.907, 0.907, 0.907, 0.907, 0.907, 1.068, 1.068
]

# Default joint positions from ONNX metadata
DEFAULT_POS = [
    -0.1, 0, 0, 0.3, -0.2, 0,
    -0.1, 0, 0, 0.3, -0.2, 0,
    0, 0, 0,
    0.35, 0.18, 0, 0.87, 0, 0, 0,
    0.35, -0.18, 0, 0.87, 0, 0, 0
]

# High-gain standing (to hold the robot upright before RL connects)
RIGID_KP = [300.0]*12 + [200.0]*3 + [80.0]*14
RIGID_KD = [20.0]*12 + [15.0]*3 + [5.0]*14


class MujocoBridge(Node):
    def __init__(self):
        super().__init__("g1_mujoco_bridge")

        # The sensor worker snapshots simulation state from another thread.
        self.state_lock = threading.RLock()

        # ------ Load model ------
        model_path = resolve_model_path()
        self.get_logger().info(f"Loading MuJoCo model from {model_path}")
        self.model = mujoco.MjModel.from_xml_path(str(model_path))

        # ------ Convert actuators to position servos (matching training!) ------
        # Training uses BuiltinPositionActuator which creates <position> actuators
        # inside MuJoCo with: force = Kp*(ctrl - pos) - Kd*vel
        # ctrl IS the desired joint angle. PD runs in the implicit solver.
        self._convert_actuators_to_position(RIGID_KP, RIGID_KD)

        self.data = mujoco.MjData(self.model)

        assert self.model.nu == 29, f"Expected 29 actuators, got {self.model.nu}"

        # Build actuator → joint qpos/dof index maps
        self._act_qpos_adr = np.zeros(self.model.nu, dtype=int)
        self._act_dof_adr = np.zeros(self.model.nu, dtype=int)
        for i in range(self.model.nu):
            jnt_id = self.model.actuator_trnid[i, 0]
            self._act_qpos_adr[i] = self.model.jnt_qposadr[jnt_id]
            self._act_dof_adr[i] = self.model.jnt_dofadr[jnt_id]

        # Look up IMU sensor addresses in sensordata
        imu_quat_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SENSOR, "imu_quat")
        imu_gyro_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SENSOR, "imu_gyro")
        imu_acc_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SENSOR, "imu_acc")
        self._imu_quat_adr = self.model.sensor_adr[imu_quat_id]
        self._imu_gyro_adr = self.model.sensor_adr[imu_gyro_id]
        self._imu_acc_adr = self.model.sensor_adr[imu_acc_id]

        self._pelvis_body_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, "pelvis"
        )
        self._torso_body_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, "torso_link"
        )
        self._lidar_camera_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_CAMERA, "lidar_front"
        )

        self.has_free_base = mujoco.mjtJoint.mjJNT_FREE in self.model.jnt_type

        self._last_sim_time = 0.0
        self._external_cmd_received = False

        # Set initial pose
        self._reset_pose()
        mujoco.mj_forward(self.model, self.data)

        # The render worker consumes this compact snapshot instead of touching
        # live MjData. The lock only protects array copies, never mj_step().
        self._render_state_lock = threading.Lock()
        self._render_qpos = self.data.qpos.copy()
        self._render_qvel = self.data.qvel.copy()
        self._render_ctrl = self.data.ctrl.copy()
        self._render_act = self.data.act.copy()
        self._render_mocap_pos = self.data.mocap_pos.copy()
        self._render_mocap_quat = self.data.mocap_quat.copy()
        self._render_time = self.data.time

        self.get_logger().info(
            f"Free base: {self.has_free_base} | "
            f"nq={self.model.nq} nv={self.model.nv} nu={self.model.nu} | "
            f"Implicit PD active | Standing mode"
        )

        self.viewer = mujoco.viewer.launch_passive(self.model, self.data)
        with self.viewer.lock():
            # The passive viewer leaves trackbodyid unset (-1), so selecting
            # Rendering > Camera > Tracking has no body to follow.
            self.viewer.cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING
            self.viewer.cam.trackbodyid = self._pelvis_body_id
            self.viewer.cam.fixedcamid = -1
            self.viewer.cam.lookat[:] = self.data.xpos[self._pelvis_body_id]
            self.viewer.cam.distance = 4.0
            self.viewer.cam.azimuth = 135.0
            self.viewer.cam.elevation = -20.0
        self.get_logger().info("Viewer camera tracking target: pelvis")

        self.joint_state_pub = self.create_publisher(JointState, "g1/joint_states", 10)
        self.imu_pub = self.create_publisher(Imu, "g1/imu", 10)
        self.odom_pub = self.create_publisher(Odometry, "g1/odom", 10)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.cmd_sub = self.create_subscription(
            Float64MultiArray, "g1/joint_cmd", self.on_cmd, 10
        )

        # Physics sub-stepping: run N_SUBSTEPS of mj_step per policy tick.
        # The RL policy runs at 50 Hz (0.02 s).  The model timestep is 0.002 s,
        # so we need 10 physics steps per policy tick.  Using a single 20 ms
        # ROS timer avoids relying on Python to deliver a reliable 500 Hz
        # callback, and it keeps physics in lock-step with the controller.
        self._physics_dt = self.model.opt.timestep          # 0.002 s
        self._policy_dt = 0.02                               # 50 Hz
        self._n_substeps = round(self._policy_dt / self._physics_dt)  # 10

        self._sim_thread = threading.Thread(target=self._sim_loop, daemon=True)
        self._sim_thread.start()
        self.publish_timer = self.create_timer(self._policy_dt, self.publish_state)

        # Viewer sync is decoupled from physics — render at ~60 Hz.
        # viewer.sync() blocks until the next display VSYNC (~16.7 ms on a
        # 60 Hz monitor).  Calling it inside the physics loop was throttling
        # mj_step to display rate, giving the RL policy incorrect dynamics.
        self.render_timer = self.create_timer(1.0 / 60.0, self.render_sync)

        self.get_logger().info("g1_mujoco bridge started (implicit PD mode)")

    # ------------------------------------------------------------------
    # Convert actuators: motor → position servo (matches training exactly)
    # ------------------------------------------------------------------
    def _convert_actuators_to_position(self, kp_list, kd_list):
        """Convert all actuators to position servos using MuJoCo's implicit solver.

        This exactly replicates how mjlab's BuiltinPositionActuator works:
          gaintype = FIXED, biastype = AFFINE
          gainprm[0] = kp
          biasprm[1] = -kp
          biasprm[2] = -kd
          force = kp * (ctrl - pos) - kd * vel
          ctrl = desired joint angle (radians)
        """
        with self.state_lock:
            for i in range(self.model.nu):
                kp = kp_list[i]
                kd = kd_list[i]

                self.model.actuator_gaintype[i] = 0  # mjGAIN_FIXED
                self.model.actuator_gainprm[i, 0] = kp

                self.model.actuator_biastype[i] = 1  # mjBIAS_AFFINE
                self.model.actuator_biasprm[i, 0] = 0.0
                self.model.actuator_biasprm[i, 1] = -kp
                self.model.actuator_biasprm[i, 2] = -kd

                # Position actuators must allow setpoints beyond joint limits
                # (matching create_position_actuator: ctrllimited = False)
                self.model.actuator_ctrllimited[i] = 0

        self.get_logger().info(
            f"Converted {self.model.nu} actuators to implicit position servos"
        )

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------
    def _reset_pose(self):
        """Set qpos to the default standing pose and zero velocities."""
        with self.state_lock:
            if self.has_free_base:
                self.data.qpos[0:3] = [0.0, 0.0, 0.783]
                self.data.qpos[3:7] = [1.0, 0.0, 0.0, 0.0]

            # Set each joint to its default position using correct qpos address
            for i in range(self.model.nu):
                self.data.qpos[self._act_qpos_adr[i]] = DEFAULT_POS[i]
                self.data.ctrl[i] = DEFAULT_POS[i]

            self.data.qvel[:] = 0.0

    # ------------------------------------------------------------------
    # External command handling
    # ------------------------------------------------------------------
    def on_cmd(self, msg: Float64MultiArray):
        if len(msg.data) != self.model.nu:
            self.get_logger().warn(
                f"Ignoring command: expected {self.model.nu} values, "
                f"got {len(msg.data)}"
            )
            return

        with self.state_lock:
            if not self._external_cmd_received:
                self._external_cmd_received = True
                self.get_logger().info(
                    "External controller connected — switching to RL gains!"
                )
                # Switch to RL-trained gains inside MuJoCo's solver
                self._convert_actuators_to_position(RL_STIFFNESS, RL_DAMPING)

            # ctrl IS the desired joint angle (position target)
            self.data.ctrl[:] = np.array(msg.data)

    # ------------------------------------------------------------------
    # Simulation step — runs N sub-steps of mj_step per policy tick
    # ------------------------------------------------------------------
    def step_sim(self):
        with self.state_lock:
            # Detect MuJoCo viewer reset (time jumps backwards)
            if self.data.time < self._last_sim_time:
                self.get_logger().info(
                    "Viewer reset detected — re-applying standing pose"
                )
                self._reset_pose()
                mujoco.mj_forward(self.model, self.data)

            self._last_sim_time = self.data.time

            # Run exactly N physics sub-steps per policy tick, matching how the
            # RL policy was trained (10 × 0.002 s = 0.02 s per policy step).
            for _ in range(self._n_substeps):
                mujoco.mj_step(self.model, self.data)

            self._update_render_state()

    def _sim_loop(self):
        while rclpy.ok():
            t0 = time.perf_counter()
            self.step_sim()
            elapsed = time.perf_counter() - t0
            sleep_time = self._policy_dt - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _update_render_state(self):
        """Publish a coherent state snapshot for the sensor worker."""
        with self._render_state_lock:
            self._render_time = self.data.time
            self._render_qpos[:] = self.data.qpos
            self._render_qvel[:] = self.data.qvel
            self._render_ctrl[:] = self.data.ctrl
            if self._render_act.size:
                self._render_act[:] = self.data.act
            if self._render_mocap_pos.size:
                self._render_mocap_pos[:] = self.data.mocap_pos
                self._render_mocap_quat[:] = self.data.mocap_quat

    def copy_render_state(self, target_data):
        """Copy the latest immutable snapshot into sensor-owned MjData."""
        with self._render_state_lock:
            target_data.time = self._render_time
            target_data.qpos[:] = self._render_qpos
            target_data.qvel[:] = self._render_qvel
            target_data.ctrl[:] = self._render_ctrl
            if target_data.act.size:
                target_data.act[:] = self._render_act
            if target_data.mocap_pos.size:
                target_data.mocap_pos[:] = self._render_mocap_pos
                target_data.mocap_quat[:] = self._render_mocap_quat

    # ------------------------------------------------------------------
    # Viewer sync — decoupled from physics at ~60 Hz
    # ------------------------------------------------------------------
    def render_sync(self):
        with self.state_lock:
            if self.viewer.is_running():
                self.viewer.sync()

    # ------------------------------------------------------------------
    # Publish state
    # ------------------------------------------------------------------
    def publish_state(self):
        now = self.get_clock().now().to_msg()

        with self.state_lock:
            joint_position = self.data.qpos[self._act_qpos_adr].copy()
            joint_velocity = self.data.qvel[self._act_dof_adr].copy()
            quat = self.data.sensordata[
                self._imu_quat_adr:self._imu_quat_adr+4
            ].copy()
            gyro = self.data.sensordata[
                self._imu_gyro_adr:self._imu_gyro_adr+3
            ].copy()
            acceleration = self.data.sensordata[
                self._imu_acc_adr:self._imu_acc_adr+3
            ].copy()
            pelvis_position = self.data.xpos[self._pelvis_body_id].copy()
            pelvis_quat = self.data.xquat[self._pelvis_body_id].copy()
            pelvis_rotation = self.data.xmat[self._pelvis_body_id].reshape(3, 3).copy()
            torso_rotation = self.data.xmat[self._torso_body_id].reshape(3, 3).copy()
            lidar_position = self.data.cam_xpos[self._lidar_camera_id].copy()
            base_linear_velocity = self.data.qvel[0:3].copy()
            base_angular_velocity = self.data.qvel[3:6].copy()

        # Nav2 requires a non-tilting ground frame.  The simulated pelvis rolls,
        # pitches and changes height while walking, so expose a planar
        # base_footprint and make pelvis its only child.
        yaw = math.atan2(pelvis_rotation[1, 0], pelvis_rotation[0, 0])
        half_yaw = 0.5 * yaw
        yaw_cos = math.cos(yaw)
        yaw_sin = math.sin(yaw)

        # Ground-truth simulation odometry. Real deployment must replace this
        # source with LiDAR-inertial or fused state estimation.
        odom_msg = Odometry()
        odom_msg.header.stamp = now
        odom_msg.header.frame_id = "odom"
        odom_msg.child_frame_id = "base_footprint"
        odom_msg.pose.pose.position.x = float(pelvis_position[0])
        odom_msg.pose.pose.position.y = float(pelvis_position[1])
        odom_msg.pose.pose.position.z = 0.0
        odom_msg.pose.pose.orientation.w = math.cos(half_yaw)
        odom_msg.pose.pose.orientation.z = math.sin(half_yaw)
        # MuJoCo's free-joint translational velocity is world-aligned.  Rotate
        # it into child_frame_id as required by nav_msgs/Odometry.
        odom_msg.twist.twist.linear.x = float(
            yaw_cos * base_linear_velocity[0] + yaw_sin * base_linear_velocity[1]
        )
        odom_msg.twist.twist.linear.y = float(
            -yaw_sin * base_linear_velocity[0] + yaw_cos * base_linear_velocity[1]
        )
        odom_msg.twist.twist.angular.z = float(base_angular_velocity[2])
        odom_msg.pose.covariance[0] = 1e-6
        odom_msg.pose.covariance[7] = 1e-6
        odom_msg.pose.covariance[14] = 1e-6
        odom_msg.pose.covariance[21] = 1e-6
        odom_msg.pose.covariance[28] = 1e-6
        odom_msg.pose.covariance[35] = 1e-6
        self.odom_pub.publish(odom_msg)

        odom_to_base = TransformStamped()
        odom_to_base.header.stamp = now
        odom_to_base.header.frame_id = "odom"
        odom_to_base.child_frame_id = "base_footprint"
        odom_to_base.transform.translation.x = float(pelvis_position[0])
        odom_to_base.transform.translation.y = float(pelvis_position[1])
        odom_to_base.transform.rotation.w = math.cos(half_yaw)
        odom_to_base.transform.rotation.z = math.sin(half_yaw)

        world_to_base_rotation = np.array(
            [[yaw_cos, yaw_sin, 0.0], [-yaw_sin, yaw_cos, 0.0], [0.0, 0.0, 1.0]]
        )
        base_to_pelvis_rotation = world_to_base_rotation @ pelvis_rotation
        base_to_pelvis_quat = np.empty(4, dtype=np.float64)
        mujoco.mju_mat2Quat(
            base_to_pelvis_quat, base_to_pelvis_rotation.reshape(-1)
        )
        base_to_pelvis = TransformStamped()
        base_to_pelvis.header.stamp = now
        base_to_pelvis.header.frame_id = "base_footprint"
        base_to_pelvis.child_frame_id = "pelvis"
        base_to_pelvis.transform.translation.z = float(pelvis_position[2])
        base_to_pelvis.transform.rotation.w = float(base_to_pelvis_quat[0])
        base_to_pelvis.transform.rotation.x = float(base_to_pelvis_quat[1])
        base_to_pelvis.transform.rotation.y = float(base_to_pelvis_quat[2])
        base_to_pelvis.transform.rotation.z = float(base_to_pelvis_quat[3])

        # The synthetic cloud is expressed in a ROS-aligned frame at the
        # LiDAR camera origin. Publish its exact pose relative to the pelvis,
        # including the commanded waist orientation.
        relative_rotation = pelvis_rotation.T @ torso_rotation
        relative_position = pelvis_rotation.T @ (lidar_position - pelvis_position)
        relative_quat = np.empty(4, dtype=np.float64)
        mujoco.mju_mat2Quat(relative_quat, relative_rotation.reshape(-1))

        pelvis_to_lidar = TransformStamped()
        pelvis_to_lidar.header.stamp = now
        pelvis_to_lidar.header.frame_id = "pelvis"
        pelvis_to_lidar.child_frame_id = "mid360_link"
        pelvis_to_lidar.transform.translation.x = float(relative_position[0])
        pelvis_to_lidar.transform.translation.y = float(relative_position[1])
        pelvis_to_lidar.transform.translation.z = float(relative_position[2])
        pelvis_to_lidar.transform.rotation.w = float(relative_quat[0])
        pelvis_to_lidar.transform.rotation.x = float(relative_quat[1])
        pelvis_to_lidar.transform.rotation.y = float(relative_quat[2])
        pelvis_to_lidar.transform.rotation.z = float(relative_quat[3])
        self.tf_broadcaster.sendTransform(
            [odom_to_base, base_to_pelvis, pelvis_to_lidar]
        )

        # Publish JointStates in ACTUATOR_ORDER
        msg = JointState()
        msg.header.stamp = now
        msg.name = list(ACTUATOR_ORDER)
        msg.position = joint_position.tolist()
        msg.velocity = joint_velocity.tolist()
        self.joint_state_pub.publish(msg)

        # Publish IMU using MuJoCo's built-in sensors (body-frame!)
        if self.has_free_base:
            imu_msg = Imu()
            imu_msg.header.stamp = now
            imu_msg.header.frame_id = "pelvis"

            # imu_quat sensor: MuJoCo [w,x,y,z] → ROS [x,y,z,w]
            imu_msg.orientation.x = float(quat[1])
            imu_msg.orientation.y = float(quat[2])
            imu_msg.orientation.z = float(quat[3])
            imu_msg.orientation.w = float(quat[0])

            # imu_gyro sensor: already in LOCAL/body frame
            imu_msg.angular_velocity.x = float(gyro[0])
            imu_msg.angular_velocity.y = float(gyro[1])
            imu_msg.angular_velocity.z = float(gyro[2])

            imu_msg.linear_acceleration.x = float(acceleration[0])
            imu_msg.linear_acceleration.y = float(acceleration[1])
            imu_msg.linear_acceleration.z = float(acceleration[2])

            imu_msg.orientation_covariance[0] = 1e-5
            imu_msg.orientation_covariance[4] = 1e-5
            imu_msg.orientation_covariance[8] = 1e-5
            imu_msg.angular_velocity_covariance[0] = 1e-4
            imu_msg.angular_velocity_covariance[4] = 1e-4
            imu_msg.angular_velocity_covariance[8] = 1e-4
            imu_msg.linear_acceleration_covariance[0] = 1e-3
            imu_msg.linear_acceleration_covariance[4] = 1e-3
            imu_msg.linear_acceleration_covariance[8] = 1e-3

            self.imu_pub.publish(imu_msg)


def main():
    rclpy.init()
    bridge = MujocoBridge()

    # The sensor publisher reads bridge snapshots into private model/data.
    from .sensor_publisher_node import SensorPublisher
    sensor_pub = SensorPublisher(bridge.model, bridge.copy_render_state)

    # Keep ROS callbacks serialized; offscreen rendering has its own worker.
    from rclpy.executors import SingleThreadedExecutor
    executor = SingleThreadedExecutor()
    executor.add_node(bridge)
    executor.add_node(sensor_pub)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.viewer.close()
        sensor_pub.destroy_node()
        bridge.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
