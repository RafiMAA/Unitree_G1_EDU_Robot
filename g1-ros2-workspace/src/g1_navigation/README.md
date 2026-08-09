# G1 2D Mapping and Navigation

This package connects the simulated Unitree G1 locomotion controller to SLAM
Toolbox and Nav2. It provides:

- A planar `map -> odom -> base_footprint -> pelvis -> mid360_link` TF chain.
- A filtered 2D `/scan` for SLAM while preserving the live PointCloud2 for
  Nav2's voxel costmap and Collision Monitor.
- Mode-aware command arbitration, smoothing, stale-command stops and a final
  `/cmd_vel_safe` command topic.
- A browser gateway for Nav2 `NavigateToPose` goals and status.

## Build

```bash
cd /path/to/Unitree_G1/g1-ros2-workspace
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select g1_core g1_mujoco g1_navigation
source install/setup.bash
```

## Create the initial map

```bash
ros2 launch g1_navigation mapping.launch.py
```

Start the React application in another terminal:

```bash
cd /path/to/Unitree_G1/g1-navigation-ui
npm install
npm run dev
```

Open `http://localhost:5173`, select **Mapping**, and use the arrow keys or
WASD. Q/E commands lateral walking. Enter an absolute output path before
pressing **Save**, for example:

```text
/path/to/Unitree_G1/g1-ros2-workspace/src/g1_navigation/maps/g1_map
```

The same operation can be performed without the UI:

```bash
ros2 service call /slam_toolbox/save_map slam_toolbox/srv/SaveMap \
  "{name: {data: /absolute/path/to/g1_map}}"
```

## Navigate on the saved map

Stop the mapping launch and start navigation:

```bash
ros2 launch g1_navigation navigation.launch.py \
  map:=/absolute/path/to/g1_map.yaml
```

Select **Navigate** in the React application and click a free map cell. Nav2
plans globally from the saved occupancy grid. Its rolling local voxel costmap
and Collision Monitor consume `/g1/mid360/points` directly so temporary and
moving obstacles are not written into the static map.

## Real G1 deployment

Launch with `start_sim:=false` only after the physical system provides:

- `/g1/mid360/points` with sensor-data QoS.
- `/g1/odom` including pose and twist.
- `odom -> base_footprint` and `base_footprint -> pelvis -> mid360_link` TF.
- A G1 controller subscribed to `/cmd_vel_safe`.

Example:

```bash
ros2 launch g1_navigation mapping.launch.py start_sim:=false
```

The current settings are conservative starting values, not calibrated safety
limits. Verify self-filtering, footprint, obstacle-height bands, stop distance,
LiDAR blind regions and locomotion stability on the actual robot.

## Important limitation

This stack is for approximately flat indoor floors. It does not provide
drop-off, hole, downward-stair, slope, swing-foot or footstep planning. A 3D
voxel costmap still produces a 2D navigation decision. Use a safety operator,
physical emergency stop and separate terrain perception on hardware.
