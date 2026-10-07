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
2. Open **03 · Maps & Localization**, then in **Saved locations**, click **Add location**.
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

`npm run dev` starts only the local process manager, rosbridge connection and
React UI. It never launches or stops simulation. Start the simulator separately
and keep its terminal running:

```bash
cd ~/Desktop/Rafi_Unitree_sem_Project/Unitree_G1_EDU_Robot_recovered/g1-ros2-workspace
unset PYTHONPATH
export PYTHONNOUSERSITE=1 ROS_DOMAIN_ID=0 ROS_LOCALHOST_ONLY=1 MUJOCO_GL=glfw
source /opt/ros/humble/setup.bash
source install/setup.bash
export PYTHONPATH="$PWD/.venv/lib/python3.10/site-packages:${PYTHONPATH:-}"
ros2 launch g1_mujoco sim.launch.py start_rosbridge:=false cmd_vel_topic:=/cmd_vel_safe
```

Start the UI in another terminal:

```bash
source ~/.nvm/nvm.sh
nvm use 22
cd ~/Desktop/Rafi_Unitree_sem_Project/Unitree_G1_EDU_Robot_recovered/g1-navigation-ui
npm run dev
```

ROS environments are sourced by the launcher. Defaults are Humble,
`ROS_DOMAIN_ID=0`, `ROS_LOCALHOST_ONLY=1`. The workspace must
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
**03 · Maps & Localization**. Opening this tab puts robot control into idle and
cancels active navigation. The live map remains visible for placing poses and
labels. Returning to Mapping or Navigate closes placement tools.

### Load a saved map and set the robot pose

1. Save any live SLAM map you want to keep. Loading a saved map stops SLAM;
   **New mapping** begins a new SLAM session, rather than continuing a saved grid.
2. Open **03 · Maps & Localization**. In **Saved map / localization**, choose an existing map and click **Load map**.
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
5. Once AMCL publishes a pose estimate, select **Navigate**. Wait for Nav2
   and safety to become ready, then click a free cell to set a destination. Labels work on loaded maps too.

The pose tool publishes `geometry_msgs/PoseWithCovarianceStamped` in `map` on
`/initialpose`, as used by
[Nav2's Humble localization interface](https://api.nav2.org/nav2-humble/html/robot__navigator_8py_source.html).
It supplies an estimate for localization; it does **not** teleport the simulator
or move the physical robot. Set the arrow to the robot's real position/heading
within the saved environment. AMCL needs matching live laser scans and odometry.

| UI action | Services started |
| --- | --- |
| Open UI | Process manager, rosbridge and Vite only |
| Mapping | Perception/web gateway, SLAM and drive safety; no Nav2 |
| Navigate | Nav2 and drive safety for the current map |
| Maps & Localization | Location JSON saver; navigation and drive safety stop |
| Load map | Perception, map server and AMCL; no Nav2 until Navigate is selected |

Mapping must receive a map before Navigate can start. A loaded map requires an
initial pose first. Existing SLAM/AMCL and perception remain running across tab
changes to preserve the map and localization; loading a different map or starting
new mapping replaces them. Navigation and label editing start only when selected.
Humble's SLAM node starts directly without a separate SLAM lifecycle manager.
Static maps are replayed for late browser connections. AMCL never assumes a
starting position of `(0, 0)`.

Ctrl+C stops only console-owned services; the separately launched simulator
continues running. Do not run duplicate mapping/Nav2/label/rosbridge launches in
that ROS domain. `npm run ui` starts only Vite for externally managed ROS services
and does not provide the process-control API. `G1_START_SIM` is no longer used.
Optional test/deployment overrides: `G1_UI_PORT`, `G1_CONSOLE_PORT`,
`G1_ROSBRIDGE_PORT`, and `G1_MAPS_DIR`.

```bash
source /opt/ros/humble/setup.bash
PYTHONPATH="$PWD/../g1-ros2-workspace/src/g1_conversation:${PYTHONPATH:-}" \
  /usr/bin/python3 -m unittest discover -s scripts -p 'test_*.py'
node --test src/mapGeometry.test.js src/initialPose.test.js
npm run build
```

## Manual mapping and navigation collision zones

Manual **Mapping** bypasses collision stopping and slowdown. WASD / arrow-button
commands and rotation pass through the velocity smoother to `/cmd_vel_safe`,
even when LiDAR reports wall contact or obstacle data is missing. Emergency stop,
Idle and stale-command timeouts still stop motion. Map-canvas arrow keys pan the
view; use the drive buttons or WASD for robot motion.

**Navigate** continues to use Collision Monitor with a smaller zone:
- Stop: x from -0.31 to 0.41 m, y from -0.35 to 0.35 m.
- Slowdown: x from -0.40 to 0.50 m, y from -0.39 to 0.39 m.

Coordinates are relative to `base_footprint`; the stop zone surrounds the padded
physical footprint. Both costmaps use 1 cm footprint padding, a 0.25 m
inflation radius and a 6.0 cost scaling factor to reduce the extra corridor
margin. The self-return mask uses 1 cm padding so it does not hide obstacles
inside the smaller stop region. Navigation still stops on stale obstacle data. Its final
output follows Collision Monitor without a second, larger hard-coded stop zone.

## Save and browse maps

**Save new map** works while live SLAM exists, including in Maps & Localization.
It saves the received occupancy grid as a YAML/PNG pair in the map library,
preserving its resolution, origin, orientation and unknown cells. The library
refreshes and selects the saved map. Use a unique output name; existing maps
are preserved. Saving a loaded static map is disabled.

Under **Import a map from disk**, **Browse map files** accepts the map YAML and
its referenced image together (Ctrl-click both files). The separate YAML/image
pickers also work. **Import map** stores the pair; **Import & load** stores it and
starts AMCL. Imported filenames that already exist receive a suggested copy name.
Select an imported/saved map, click **Load map**, then place the initial pose.
**Continue mapping** returns to an existing SLAM session; **New mapping** starts
SLAM when a saved map is loaded or no mapping session exists.

## RAG Conversation tab

Run the usual command, then select **04 · RAG Conversation**:

```bash
source ~/.nvm/nvm.sh
nvm use 22
cd ~/Desktop/Rafi_Unitree_sem_Project/Unitree_G1_EDU_Robot_recovered/g1-navigation-ui
npm run dev
```

The conversation worker starts only when you select this tab and stops when you
leave it. It does not start simulation, microphone capture on the computer,
or computer speaker playback. Selecting RAG cancels navigation and pauses
manual robot control; an existing map session is retained. Simulation stays in
its own terminal. You do not need to launch `conversation_node` separately.

Set `GOOGLE_API_KEY` in `g1-ros2-workspace/.env` on the computer (or export it
before starting the UI). This file is ignored by Git. Restart the UI after
changing the key. The browser never receives the key. Gemini/embeddings need
an active key and internet access. The existing airport knowledge base and
per-conversation history are reused; no robot navigation goal is sent by chat.

On first selection, the console installs conversation dependencies into
`g1-ros2-workspace/.rag-venv`, separate from the simulator `.venv`.
System prerequisites are `python3-venv` and `ffmpeg`; for Ubuntu:

```bash
sudo apt install python3-venv ffmpeg
```

First voice use can also download a Whisper model. The prepared environment
and model downloads are reused. Worker logs are in
`g1-ros2-workspace/log/console/rag.log`.

Choose a language, type a message or press **Record voice**. Allow microphone
access, speak, and pause to send, or press **Stop & send recording**. Each
recording is limited to 60 seconds/4 MB. The computer checks speech with the
existing Silero VAD, transcribes with faster-whisper, answers with airport RAG,
and returns TTS audio to this browser. Silence does not reach the LLM.
**Speak replies on this device** controls speech output; the audio player is
also available when mobile browsers prevent automatic playback. Clear stops
local recording/playback and clears this session's history. A submitted cloud
request may finish on the backend before a new turn is accepted.

Audio goes through the same-origin HTTP API; ROS state, commands, and the
`/g1/speech_text` and `/g1/agent_response` notifications use ROS/rosbridge.
The RAG tab uses bounded replies rather than the local microphone node's
continuous streaming conversation. Browser pause detection ends the recording;
the server's neural VAD validates and trims it independently.

Optional computer environment settings:

- `G1_STT_MODEL`: Whisper model name, default `base`.
- `G1_TTS_BACKEND`: `auto` (Edge in the prepared browser environment), `edge`,
  or `piper` if separately installed with voice models.
- `G1_RAG_PYTHON`: absolute path to an existing fully configured Python;
  skips automatic environment preparation.
- `G1_RAG_ENV_FILE`: an additional dotenv file on the computer.
- `G1_RAG_PORT`: loopback worker port, default `8767`.

## Phone microphone and speaker

Plain HTTP on a computer's LAN address does not provide browser microphone
access. `localhost` works on the computer; a phone needs trusted HTTPS.
Create local certificates once on the computer:

```bash
cd ~/Desktop/Rafi_Unitree_sem_Project/Unitree_G1_EDU_Robot_recovered/g1-navigation-ui
npm run phone:setup
```

Copy **only** `.phone-tls/ca.crt` to the phone and explicitly trust this local CA
in its operating system settings. Never copy `.key` files. On iOS, install the
profile and enable its full trust in Certificate Trust Settings. On Android,
install the CA certificate in Security / Encryption & credentials (wording
varies by device). A certificate-warning bypass alone may not enable the mic.
Trust this CA on the desktop too if continuing to use its HTTPS browser.

Restart `npm run dev`. With the certificates present, Vite serves HTTPS on the
computer's LAN interfaces. Connect both devices to the same trusted Wi-Fi and
open the `https://<computer-IP>:5173` address printed by `phone:setup`.
The browser uses a secure same-origin `/rosbridge` WebSocket proxy; rosbridge
and the API/RAG ports do not need to be opened to the phone. Firewall access to
TCP 5173 may be needed. Existing `VITE_ROSBRIDGE_URL` overrides should be
removed when using HTTPS unless they point to a valid secure WebSocket.

Run `phone:setup` again after the computer IP changes or certificates expire
(one year). Keys/certificates remain local and are ignored by Git. Remove the
`.phone-tls` directory to return to localhost HTTP. For externally managed TLS,
set `G1_TLS_KEY` and `G1_TLS_CERT`; set `G1_UI_ALLOWED_ORIGINS` to the exact
HTTPS origin when using a host not listed in the generated certificates.
