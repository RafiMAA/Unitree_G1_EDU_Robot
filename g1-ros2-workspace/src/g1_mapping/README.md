# G1 Live 3D Mapping

This package accumulates the simulated MID-360 point cloud into a voxelized
3D map and displays it in RViz. It contains no planner, costmap, or obstacle
avoidance behavior.

The current map registration uses MuJoCo ground-truth odometry. Replace that
odometry source with LiDAR-inertial odometry or SLAM before using the mapper on
the physical G1.

## Run

Build once:

```bash
cd ~/Desktop/Unitree_G1/g1-ros2-workspace
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select g1_core g1_mujoco g1_mapping
source install/setup.bash
```

Launch the simulation, mapper, and RViz:

```bash
ros2 launch g1_mapping mapping.launch.py
```

In another terminal, start keyboard control:

```bash
cd ~/Desktop/Unitree_G1/g1-ros2-workspace
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run g1_core teleop_keyboard
```

Use `W`/`S` to walk, `A`/`D` to turn, space to stop, and `X` to exit.
The conservative default is 0.2 m/s; use `+` and `-` to adjust it.

Clear the accumulated map without restarting:

```bash
ros2 service call /g1/map/clear std_srvs/srv/Trigger '{}'
```
