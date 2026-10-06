# G1 Navigation Console

Run with Node 22 (or another version supported by Vite 7):

```bash
npm ci
npm run dev
```

Open the URL printed by Vite. The default ROS connection is
`ws://localhost:9090`; set `VITE_ROSBRIDGE_URL` to override it.
The default development command starts ROS services automatically; see the complete-console instructions below. Use `npm run ui` only when ROS is managed separately.

## Map controls

The north-up 2D view follows the zoom and pan conventions of RViz's
[TopDownOrtho view](https://github.com/ros2/ros2_documentation/blob/rolling/source/Developer-Tools/Visualization/RViz/RViz-User-Guide/RViz-User-Guide.rst).

| Control | Action |
| --- | --- |
| Mouse wheel / trackpad scroll | Zoom around the cursor |
| Two-finger pinch on a touchscreen | Zoom and pan |
| Left-drag, middle-drag, or Shift+left-drag | Pan |
| Right-drag up / down | Zoom in / out |
| − / + buttons | Zoom around the center |
| Fit map | Show the entire map; 100% means fitted to the viewport |
| Center robot | Center the view on the robot at the current zoom |
| Click a known free cell in Navigate mode | Send a navigation goal |

Dragging or pinching never places a goal. Robot, goal, and path overlays use
the same world-coordinate transform as the occupancy grid. A manually adjusted
view stays fixed as SLAM expands the map; Fit map restores automatic fitting.
The scale bar shows world distance in metres.

White cells are observed free space, dark cells are obstacles, and gray is
unmapped space. Unknown cells blend into the viewport so the current grid's
rectangular storage bounds do not look like room walls. The map expands as
SLAM observes more space. A badge shows when the UI last received a map and
reports when the robot is beyond the last published grid. The live robot marker
is never clamped to the grid's edge.

When the map canvas has keyboard focus, `+` / `-` zoom, arrow keys pan, and
`Home` fits the map. These keys control the view rather than robot motion.
Space still stops the robot. Use the manual-drive buttons to resume driving.
Text fields do not trigger the global driving shortcuts.

## Label positions on the map

1. Set the map output name/path (for example `g1_map`) so labels are associated
   with that map. The basename identifies the location collection.
2. Open **03 · Maps & Locations**, then in **Saved locations**, click **Add location**.
3. Click a white, known-free map cell, type the location name, and click
   **Save location**. Dragging pans the view without selecting a point.
4. Purple markers show saved names. **Rename**, **Move**, and **Delete** edit
   the collection. Editing pauses manual motion and cancels the UI's active goal.

The `g1_navigation map_labels` ROS node saves JSON on the ROS computer. In a
source workspace its default directory is `src/g1_navigation/maps`; an installed
deployment without source uses `~/.ros/g1_navigation/maps`. Each map has its own
`<map-name>_labels.json`. Writes are atomic and the UI waits for confirmation.
Locations reload after a browser refresh; **Reload locations** also reads the file.

The updated mapping/navigation launches start the label node automatically. To
add labeling to a mapping session that is already running, start it in another
sourced ROS terminal without restarting SLAM:

```bash
ros2 run g1_navigation map_labels
```

Run one label saver per ROS domain. To choose a different storage directory:

```bash
ros2 run g1_navigation map_labels --ros-args -p labels_dir:=/absolute/path/to/maps
```

**Export JSON** downloads a copy to the browser's Downloads folder. **Import JSON**
merges an earlier collection: matching names update positions, and other saved
locations remain. It accepts a plain array with `text` or `name` plus numeric
`x`, `y`, and optional `z`/`yaw`, or a document containing `labels`/`locations`.
Files specifying a different `map_id` or a frame other than `map` are rejected.

Example saved file:

```json
{
  "schema_version": 1,
  "map_id": "g1_map",
  "frame_id": "map",
  "labels": [
    {
      "id": "entrance",
      "text": "Entrance",
      "x": 2.5,
      "y": 1.2,
      "z": 0.0,
      "yaw": 0.0
    }
  ]
}
```

Keep the location JSON with its corresponding saved occupancy map. Reusing a
name for a different map does not transform the old locations into the new frame.

## Checks

```bash
node --test src/mapGeometry.test.js
npm run build
```

The geometry tests cover cursor-anchored zoom, coordinate conversion after
pan/resize, rotated map origins, map expansion, and rejecting unknown, occupied,
or out-of-bounds goal cells.

## Start the complete console

The default `npm run dev` now starts a local supervisor, MuJoCo, perception,
rosbridge, label persistence, the velocity safety pipeline, SLAM + Nav2, and Vite.
Stop older simulation/mapping/UI/label terminals first to avoid duplicate ROS
nodes. Existing processes are never killed by the supervisor. Occupied console
ports cause startup to stop with an explanation.

```bash
source ~/.nvm/nvm.sh
nvm use 22
cd ~/Desktop/Rafi_Unitree_sem_Project/Unitree_G1_EDU_Robot_recovered/g1-navigation-ui
npm run dev
```

ROS environments are sourced by the launcher. Defaults are Humble,
`ROS_DOMAIN_ID=0`, `ROS_LOCALHOST_ONLY=1`, `MUJOCO_GL=glfw`. The workspace must
already be built and its `.venv` must contain the working simulation dependencies.
The console API uses system Python with `python3-yaml` and `python3-pil`.
For a fresh checkout, build once:

```bash
cd ../g1-ros2-workspace
unset PYTHONPATH
export PYTHONNOUSERSITE=1
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select g1_core g1_mujoco g1_mapping g1_navigation
```

Open `http://localhost:5173`. Ctrl+C in the startup terminal stops all processes
owned by that console. Logs are in `g1-ros2-workspace/log/console/`.
The UI and local process-control API listen on loopback addresses.

Map loading, initial-pose placement and saved-location editing are grouped under
**03 · Maps & Locations**. Opening this tab puts robot control into idle and
cancels active navigation. The live map remains visible for placing poses and
labels. Returning to Mapping or Navigate closes placement tools.

### Load a saved map and set the robot pose

1. Save any live SLAM map you want to keep. Loading a saved map stops SLAM;
   **New mapping** begins a new SLAM session, rather than continuing a saved grid.
2. Open **03 · Maps & Locations**. In **Saved map / localization**, choose an existing map and click **Load map**.
   YAML/image pairs in `src/g1_navigation/maps` and the workspace root appear in
   the library. **Refresh maps** discovers newly saved files.
3. For files elsewhere, expand **Import a map from disk** and select the ROS map
   `.yaml` plus the image referenced by its `image` field (`.pgm`/`.png`/`.bmp`/
   `.jpg`). Enter a unique map name and click **Import map**, then **Load map**.
   The original resolution and origin are preserved. Import does not overwrite
   existing maps. Location JSON is imported separately through Saved locations.
4. Wait for AMCL to activate, then click **2D Pose Estimate**. Press on the robot's
   actual location in white free space, drag toward its actual heading, and release.
   A green arrow previews the heading. Shift-drag/middle-drag still pan, and
   scrolling still zooms. A short click alone does not submit a pose.
5. Once AMCL publishes a pose estimate and Nav2 is active, select **Navigate**
   and click a free cell to set a destination. Labels work on loaded maps too.

The pose tool publishes `geometry_msgs/PoseWithCovarianceStamped` in `map` on
`/initialpose`, as used by
[Nav2's Humble localization interface](https://api.nav2.org/nav2-humble/html/robot__navigator_8py_source.html).
It supplies an estimate for localization; it does **not** teleport the simulator
or move the physical robot. Set the arrow to the robot's real position/heading
within the saved environment. AMCL needs matching live laser scans and odometry.

Cold startup starts Nav2 after the first SLAM map arrives, so the simulator
and its transforms are ready before navigation configuration. Humble's SLAM
node starts directly without a separate SLAM lifecycle manager.

The supervisor keeps simulation, rosbridge, labels and safety running during a
map switch. It stops and reaps the previous SLAM/localization/Nav2 process group
before starting the next. Static maps are replayed for browsers opening after
map_server activation. Mapping and saved-map localization never run together.
Navigation startup does not assume the robot is at `(0, 0)`.

For existing external ROS launches, `npm run ui` starts only Vite; it does not
provide the map-switching API. For an externally supplied robot/sensor setup,
`G1_START_SIM=false npm run dev` omits MuJoCo but still owns the remaining stack.
Do not run duplicate mapping/Nav2/rosbridge launches in that ROS domain.
Optional test/deployment overrides: `G1_UI_PORT`, `G1_CONSOLE_PORT`,
`G1_ROSBRIDGE_PORT`, and `G1_MAPS_DIR`.

```bash
/usr/bin/python3 -m unittest discover -s scripts -p 'test_*.py'
node --test src/mapGeometry.test.js src/initialPose.test.js
npm run build
```

## Recover from an obstacle stop

In **Mapping**, use **S / ↓** to retreat from a wall in front, **W / ↑** for
an obstacle behind, or **Q / E** to move sideways away from an obstacle.
Near a stop-zone obstacle, manual recovery is capped at a commanded **0.10 m/s**.
Move clear before turning. The drive buttons also accept arrow keys and Space
when they have keyboard focus; map-canvas arrow keys continue to pan the map.

The final `retreat_guard` uses fresh base-frame LiDAR points to check that close obstacles retain clearance from the physical robot footprint and at
least one gains clearance. It authorizes
translation only: rotation, movement toward an obstacle, a trapped/penetrating physical
footprint, emergency stop, idle mode and stale data remain stopped. Nav2 commands
cannot use this manual recovery exception. Rear stop-zone coverage includes the
padded rear footprint and its clearance margin.

The velocity pipeline is `/cmd_vel_muxed` → `/cmd_vel_smoothed` →
Collision Monitor `/cmd_vel_collision` → retreat guard `/cmd_vel_safe`.
The guard retains sensor timestamps, checks that Collision Monitor is active,
and publishes zero when sensor or command input becomes stale.
The locomotion activation threshold accepts the slow recovery command.

The fixed stop polygon in
[Humble Collision Monitor](https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_collision_monitor/src/collision_monitor_node.cpp)
sets every commanded velocity component to zero while obstacles remain inside;
the guarded recovery step resolves the resulting manual-drive deadlock.

Recovery uses the physical footprint separately from its 5 cm planning padding.
A wall entering the padding can still be escaped. Tangential motion is allowed
when it preserves clearance and another close obstacle gains clearance; distant
returns cannot veto a short retreat when their predicted clearance stays safe.
The stop zone now starts farther away (0.65 m front, 0.55 m rear and 0.50 m
on either side of the base origin). When stopped, Robot status lists the currently
clear manual retreat directions. These hints update from the latest LiDAR cloud.
