# G1 2D Mapping and A* Navigation

The console uses SLAM Toolbox for mapping, AMCL for saved-map localization, and
standalone A* plus lookahead/P-heading control for navigation. The planner follows
[RafiMAA's Qbot architecture](https://github.com/RafiMAA/Qbot_mapping_and_navigating_to_the_goal)
with G1-specific full-body checks, live obstacle replanning and bounded recovery.
It runs no Nav2 planning, control or behavior servers. The existing ROS
`NavigateToPose` action type is retained for browser/voice compatibility.

Build from the workspace (ROS Humble):

```bash
unset PYTHONPATH
export PYTHONNOUSERSITE=1
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select g1_core g1_mujoco g1_navigation
source install/setup.bash
```

Start the simulator separately using `g1_mujoco sim.launch.py` with
`start_rosbridge:=false cmd_vel_topic:=/cmd_vel_safe`. Then run `npm run dev` in
`g1-navigation-ui`. Mapping, Navigate, Maps & Localization and voice navigation
start their own processes only when selected. Do not also launch duplicate stacks
in another terminal. Load a saved map and set the actual robot pose with the
2D Pose Estimate arrow before navigating with AMCL.

Navigation parameters: `config/astar_params.yaml`:

- Inflation radius **0.15 m**, forward limit **0.65 m/s**, turn limit **1 rad/s**.
- Eight-connected A*, no corner cutting or unknown-space traversal.
- Lookahead **0.6 m**, turn before walking, apply the goal orientation on arrival.
- Full padded body checks on the map and live LiDAR; collision monitoring and
  velocity smoothing remain separate safety components.
- Replan once per second; after 3 seconds without progress, try a 30 cm backup
  at 0.15 m/s and replan the same goal, with four bounded attempts.

`config/nav2_params.yaml` contains only shared map-server, AMCL, velocity smoother
and Collision Monitor settings. Those components retain their upstream names.
`behavior_trees/` contains historical Nav2 configuration; it is not launched.

Interfaces:

- `/map`, `/g1/mid360/points_filtered` in `base_footprint`, fresh TF
  `map -> odom -> base_footprint` are required.
- `/navigate_to_pose`: existing action contract, implemented by `g1_astar`.
- `/g1_astar/ready`: `std_srvs/srv/Trigger` readiness check.
- `/plan`: browser path; `/ui/goal`: browser or saved-location pose.
- `/cmd_vel_controller` enters mux, smoother, monitor and final guard;
  `/cmd_vel_safe` is the robot input.
- Idle, emergency stop, stale sensor data and cancellation stop movement.
  Manual mapping retains the requested collision-stop bypass.

Synthetic ROS checks live in `scripts/check_navigation_recovery.py`,
`check_narrow_corridor.py` and `check_path_heading.py`; run sequentially after
sourcing the workspace. They start no robot and do not test locomotion dynamics.

This is planar indoor navigation. Terrain, stairs and footstep planning remain
outside this implementation.


### Saved-map starting gaps

Some SLAM maps have a small unknown patch where the robot stood during mapping.
A* may find a centreline route while the padded body cannot make its initial turn.
At goal acceptance, the navigator snapshots unknown cells inside the starting
body's turning envelope, provided its centre is a known free cell. This fixed,
transient patch allows departure and never follows the moving robot. Occupied
map cells and fresh cloud obstacles are still blocking; unknown space beyond
that starting patch remains blocked. Planning inflation remains 0.15 m. Saved
map images and labels are not edited. Incorrect AMCL poses still require a new
2D Pose Estimate in Maps & Localization.
