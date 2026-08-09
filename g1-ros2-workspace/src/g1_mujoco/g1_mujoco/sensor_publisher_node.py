"""Sensor publisher node for simulated G1 perception sensors.

Renders MuJoCo cameras defined in the robot model and publishes:
  - /g1/d435/color/image_raw   (sensor_msgs/Image, RGB8, 640×480)
  - /g1/d435/depth/image_raw   (sensor_msgs/Image, 32FC1, 640×480)
  - /g1/d435/camera_info        (sensor_msgs/CameraInfo)
  - /g1/mid360/points           (sensor_msgs/PointCloud2, from 4 depth cameras)

The bridge supplies coherent state snapshots. Rendering uses private MuJoCo
model/data objects, so it never races with physics.

ARCHITECTURE: A dedicated background thread handles ALL offscreen rendering
with its own EGL GL context.  This means:
  - Rendering never holds the live simulation-state lock.
  - The viewer (GLFW/GLX) is unaffected — EGL is a separate GL stack.
  - Sensor data is published even while the robot is being controlled.
"""

import copy
import math
import threading
import time
import traceback

import numpy as np

import mujoco

from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo, PointCloud2, PointField


# ---------------------------------------------------------------------------
# RealSense D435 intrinsics (matching the MuJoCo camera definition)
# ---------------------------------------------------------------------------
D435_WIDTH = 640
D435_HEIGHT = 480
D435_FOVY_DEG = 58.0

# Livox MID-360 simulated via 4 × 256×128 cameras
LIDAR_WIDTH = 256
LIDAR_HEIGHT = 1
LIDAR_FOVY_DEG = 1.0
LIDAR_CAM_NAMES = ["lidar_front", "lidar_left", "lidar_back", "lidar_right"]
LIDAR_YAW_OFFSETS_DEG = [0.0, 90.0, 180.0, 270.0]
LIDAR_MIN_RANGE_M = 0.10
LIDAR_MAX_RANGE_M = 40.0

# Target sensor rates
D435_RATE_HZ = 10.0   # RGB + Depth
LIDAR_RATE_HZ = 5.0   # Full 360° point cloud


def _fovy_to_focal(fovy_deg, height):
    """Convert vertical FOV (degrees) to focal length in pixels."""
    return height / (2.0 * math.tan(math.radians(fovy_deg) / 2.0))


class SensorPublisher(Node):
    """Publishes simulated D435 and MID-360 data from MuJoCo cameras.

    All rendering runs in a **dedicated background thread** with its own
    EGL OpenGL context. Only a short state snapshot is synchronized with the
    physics loop; rendering itself never holds the simulation lock.
    """

    def __init__(
        self,
        model: mujoco.MjModel,
        copy_render_state,
    ):
        super().__init__("g1_sensor_publisher")
        self.model = copy.copy(model)
        self._copy_render_state = copy_render_state

        # Look up camera IDs (safe — no GL needed)
        self._d435_cam_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_CAMERA, "d435_rgb"
        )
        self._lidar_cam_ids = [
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, name)
            for name in LIDAR_CAM_NAMES
        ]

        if self._d435_cam_id < 0:
            self.get_logger().warn("d435_rgb camera not found in model — skipping D435")
        for i, cid in enumerate(self._lidar_cam_ids):
            if cid < 0:
                self.get_logger().warn(
                    f"{LIDAR_CAM_NAMES[i]} camera not found — LiDAR incomplete"
                )

        # Publishers
        self.rgb_pub = self.create_publisher(Image, "g1/d435/color/image_raw", 5)
        self.depth_pub = self.create_publisher(Image, "g1/d435/depth/image_raw", 5)
        self.cam_info_pub = self.create_publisher(CameraInfo, "g1/d435/camera_info", 5)
        self.pc_pub = self.create_publisher(PointCloud2, "g1/mid360/points", 5)

        # ROS graph queries stay on the executor thread. The render worker only
        # reads these booleans, avoiding cross-thread access to ROS entities.
        self._d435_subscribed = False
        self._lidar_subscribed = False
        self._subscriber_timer = self.create_timer(
            0.1, self._update_subscriber_state
        )

        # Camera info (constant)
        self._cam_info = self._make_camera_info()

        # Start dedicated render thread (creates its own EGL context)
        self._shutdown = threading.Event()
        self._render_thread = threading.Thread(
            target=self._render_loop, name="sensor_render", daemon=True
        )
        self._render_thread.start()

        self.get_logger().info(
            f"Sensor publisher started — D435 cam_id={self._d435_cam_id}, "
            f"LiDAR cam_ids={self._lidar_cam_ids} "
            f"(dedicated render thread, physics loop unaffected)"
        )

    def destroy_node(self):
        self._shutdown.set()
        if self._render_thread.is_alive():
            self._render_thread.join(timeout=5.0)
        if self._render_thread.is_alive():
            self.get_logger().error("Sensor render thread did not stop cleanly")
        super().destroy_node()

    # ------------------------------------------------------------------
    # Camera info
    # ------------------------------------------------------------------
    def _make_camera_info(self) -> CameraInfo:
        msg = CameraInfo()
        msg.header.frame_id = "d435_link"
        msg.width = D435_WIDTH
        msg.height = D435_HEIGHT
        msg.distortion_model = "plumb_bob"
        msg.d = [0.0, 0.0, 0.0, 0.0, 0.0]

        fy = _fovy_to_focal(D435_FOVY_DEG, D435_HEIGHT)
        fx = fy
        cx = D435_WIDTH / 2.0
        cy = D435_HEIGHT / 2.0

        msg.k = [fx, 0.0, cx, 0.0, fy, cy, 0.0, 0.0, 1.0]
        msg.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        msg.p = [fx, 0.0, cx, 0.0, 0.0, fy, cy, 0.0, 0.0, 0.0, 1.0, 0.0]
        return msg

    # ------------------------------------------------------------------
    # Subscriber checks
    # ------------------------------------------------------------------
    def _update_subscriber_state(self):
        self._d435_subscribed = (
            self.rgb_pub.get_subscription_count() > 0
            or self.depth_pub.get_subscription_count() > 0
            or self.cam_info_pub.get_subscription_count() > 0
        )
        self._lidar_subscribed = self.pc_pub.get_subscription_count() > 0

    # ==================================================================
    # RENDER THREAD — runs independently of the ROS executor
    # ==================================================================
    def _render_loop(self):
        """Background thread: create EGL renderers and render at target rate.

        The EGL context is created in THIS thread, so it never touches the
        main-thread GLFW/GLX context used by the MuJoCo viewer.
        """
        # Create renderers HERE so the EGL context belongs to this thread.
        d435_renderer = None
        lidar_renderer = None
        render_data = mujoco.MjData(self.model)

        # Pre-allocate buffers
        d435_rgb = np.zeros((D435_HEIGHT, D435_WIDTH, 3), dtype=np.uint8)
        d435_depth = np.zeros((D435_HEIGHT, D435_WIDTH), dtype=np.float32)
        lidar_depths = [
            np.zeros((LIDAR_HEIGHT, LIDAR_WIDTH), dtype=np.float32)
            for _ in LIDAR_CAM_NAMES
        ]

        d435_interval = 1.0 / D435_RATE_HZ
        lidar_interval = 1.0 / LIDAR_RATE_HZ
        last_d435 = 0.0
        last_lidar = 0.0

        try:
            self.get_logger().info(
                f"Sensor render worker ready — GL backend="
                f"{mujoco.GLContext.__module__}"
            )
            while not self._shutdown.is_set():
                now = time.monotonic()
                render_d435 = (
                    now - last_d435 >= d435_interval
                    and self._d435_cam_id >= 0
                    and self._d435_subscribed
                )
                render_lidar = (
                    now - last_lidar >= lidar_interval
                    and all(cid >= 0 for cid in self._lidar_cam_ids)
                    and self._lidar_subscribed
                )

                if render_d435 or render_lidar:
                    self._snapshot_state(render_data)

                if render_d435:
                    if d435_renderer is None:
                        self.get_logger().info(
                            "Creating D435 renderer on render thread "
                            "(first subscriber)"
                        )
                        d435_renderer = mujoco.Renderer(
                            self.model, D435_HEIGHT, D435_WIDTH
                        )
                    self._render_d435(
                        d435_renderer, render_data, d435_rgb, d435_depth
                    )
                    last_d435 = now

                if render_lidar:
                    if lidar_renderer is None:
                        self.get_logger().info(
                            "Creating LiDAR renderer on render thread "
                            "(first subscriber)"
                        )
                        lidar_renderer = mujoco.Renderer(
                            self.model, LIDAR_HEIGHT, LIDAR_WIDTH
                        )
                    self._render_lidar(lidar_renderer, render_data, lidar_depths)
                    last_lidar = now

                # Wait interruptibly to avoid busy-waiting.
                self._shutdown.wait(0.01)
        except Exception:
            # Ctrl-C may invalidate the ROS context while a background publish
            # is completing. That is normal shutdown, not a sensor failure.
            if self.context.ok() and not self._shutdown.is_set():
                self.get_logger().error(
                    "Sensor render thread failed:\n" + traceback.format_exc()
                )
        finally:
            if d435_renderer is not None:
                d435_renderer.close()
            if lidar_renderer is not None:
                lidar_renderer.close()

    def _snapshot_state(self, render_data):
        """Copy a coherent physics state, then derive render transforms."""
        self._copy_render_state(render_data)
        mujoco.mj_forward(self.model, render_data)

    # ------------------------------------------------------------------
    # D435 rendering (called from render thread)
    # ------------------------------------------------------------------
    def _render_d435(self, renderer, render_data, rgb_buf, depth_buf):
        stamp = self.get_clock().now().to_msg()

        # Render RGB
        renderer.update_scene(render_data, camera=self._d435_cam_id)
        rgb_buf[:] = renderer.render()

        rgb_msg = Image()
        rgb_msg.header.stamp = stamp
        rgb_msg.header.frame_id = "d435_link"
        rgb_msg.height = D435_HEIGHT
        rgb_msg.width = D435_WIDTH
        rgb_msg.encoding = "rgb8"
        rgb_msg.is_bigendian = False
        rgb_msg.step = D435_WIDTH * 3
        rgb_msg.data = rgb_buf.tobytes()
        self.rgb_pub.publish(rgb_msg)

        # Render depth
        renderer.enable_depth_rendering()
        depth_buf[:] = renderer.render()
        renderer.disable_depth_rendering()

        # MuJoCo 3.x Renderer returns metric depth after undoing the OpenGL
        # projection. Do not scale it by the clipping-plane range again.
        depth_m = depth_buf

        depth_msg = Image()
        depth_msg.header.stamp = stamp
        depth_msg.header.frame_id = "d435_link"
        depth_msg.height = D435_HEIGHT
        depth_msg.width = D435_WIDTH
        depth_msg.encoding = "32FC1"
        depth_msg.is_bigendian = False
        depth_msg.step = D435_WIDTH * 4
        depth_msg.data = depth_m.astype(np.float32).tobytes()
        self.depth_pub.publish(depth_msg)

        self._cam_info.header.stamp = stamp
        self.cam_info_pub.publish(self._cam_info)

    # ------------------------------------------------------------------
    # LiDAR rendering — all 4 quadrants (called from render thread)
    # ------------------------------------------------------------------
    def _render_lidar(self, renderer, render_data, depth_bufs):
        stamp = self.get_clock().now().to_msg()

        fy = _fovy_to_focal(LIDAR_FOVY_DEG, LIDAR_HEIGHT)
        fx = fy
        cx = LIDAR_WIDTH / 2.0
        cy = LIDAR_HEIGHT / 2.0

        all_points = []

        for idx, cam_id in enumerate(self._lidar_cam_ids):
            renderer.update_scene(render_data, camera=cam_id)
            renderer.enable_depth_rendering()
            depth_bufs[idx][:] = renderer.render()
            renderer.disable_depth_rendering()

            depth_m = depth_bufs[idx]

            v, u = np.mgrid[0:LIDAR_HEIGHT, 0:LIDAR_WIDTH].astype(np.float32)
            z = depth_m
            x = (u - cx) * z / fx
            y = (v - cy) * z / fy

            radial_range = np.sqrt(x * x + y * y + z * z)
            valid = (
                (radial_range >= LIDAR_MIN_RANGE_M)
                & (radial_range <= LIDAR_MAX_RANGE_M)
            )
            x, y, z = x[valid], y[valid], z[valid]

            # Convert camera optical coordinates (right, down, forward) into
            # ROS coordinates (forward, left, up), then rotate each cardinal
            # camera into the common MID-360 frame.
            forward = z
            left = -x
            up = -y
            yaw = math.radians(LIDAR_YAW_OFFSETS_DEG[idx])
            cos_y, sin_y = math.cos(yaw), math.sin(yaw)
            ros_x = cos_y * forward - sin_y * left
            ros_y = sin_y * forward + cos_y * left

            all_points.append(np.stack([ros_x, ros_y, up], axis=-1))

        if all_points:
            cloud = np.concatenate(all_points, axis=0).astype(np.float32)
        else:
            cloud = np.zeros((0, 3), dtype=np.float32)

        pc_msg = PointCloud2()
        pc_msg.header.stamp = stamp
        pc_msg.header.frame_id = "mid360_link"
        pc_msg.height = 1
        pc_msg.width = cloud.shape[0]
        pc_msg.fields = [
            PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
        ]
        pc_msg.is_bigendian = False
        pc_msg.point_step = 12
        pc_msg.row_step = 12 * cloud.shape[0]
        pc_msg.data = cloud.tobytes()
        pc_msg.is_dense = True
        self.pc_pub.publish(pc_msg)
