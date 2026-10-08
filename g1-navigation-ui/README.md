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
5. Once AMCL publishes a pose estimate, select **Navigate**. Wait for A* navigation
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
| Mapping | Perception/web gateway, SLAM and drive safety; no navigation |
| Navigate | A* navigation and drive safety for the current map |
| Maps & Localization | Location JSON saver; navigation and drive safety stop |
| Load map | Perception, map server and AMCL; no navigation until Navigate is selected |

Mapping must receive a map before Navigate can start. A loaded map requires an
initial pose first. Existing SLAM/AMCL and perception remain running across tab
changes to preserve the map and localization; loading a different map or starting
new mapping replaces them. Navigation and label editing start only when selected.
Humble's SLAM node starts directly without a separate SLAM lifecycle manager.
Static maps are replayed for late browser connections. AMCL never assumes a
starting position of `(0, 0)`.

Ctrl+C stops only console-owned services; the separately launched simulator
continues running. Do not run duplicate mapping/navigation/label/rosbridge launches in
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

**Navigate** uses a standalone **A* (A-Star)** planner and a lookahead/P-heading
controller, following the architecture of
[RafiMAA's Qbot project](https://github.com/RafiMAA/Qbot_mapping_and_navigating_to_the_goal).
The implementation is adapted for the G1's ROS Humble frames, rectangular body,
UI goals and saved location orientations; it does not run Nav2's planner,
controller, behavior server or behavior tree.

- Planning inflation: **0.25 m** in `config/astar_params.yaml`. Eight-connected
  A* rejects occupied/unknown cells and diagonal corner cutting. A soft clearance
  cost prefers corridor centres without widening the hard inflation buffer.
  Shortcuts are accepted only when the entire segment remains clear.
- Forward speed limit: **0.65 m/s**; turning limit: **1.0 rad/s**.
  Lookahead is **0.6 m**. The robot turns toward the path before walking;
  normal tracking has no sideways or reverse commands. Goal orientation is
  applied after reaching the goal position.
- Live filtered LiDAR points are overlaid for planning and removed when fresh
  observations clear them; the saved map is not modified. Replanning runs at
  most once per second while following the path. Planning runs in a worker so
  sensor callbacks, cancellation and command updates remain responsive.
- Movement checks the full rectangular body with **1 cm padding** against the
  static map, unknown cells and live obstacles. There is no Nav2 local costmap
  or separate local inflation radius. Collision Monitor also checks the padded
  footprint along the requested motion; smaller inflation never shrinks the body.
- After **3 seconds** without positional or angular progress, recovery tries a
  collision-checked **30 cm reverse at 0.15 m/s**, then replans the original goal.
  Recovery is bounded to four attempts; goals time out after 180 seconds.
  Emergency stop, cancellation, stale commands, stale localization and stale
  LiDAR pause or stop motion.

AMCL, map server, velocity smoother and Collision Monitor remain as standalone
localization/safety components. Their shared settings are in `nav2_params.yaml`;
that filename is retained for launch compatibility. `/navigate_to_pose` retains
its existing ROS action message type for UI/RAG clients, but its server is now
`g1_astar`. Readiness is `/g1_astar/ready` (`std_srvs/srv/Trigger`), the path is
`/plan`, and commands still enter the existing guarded pipeline at
`/cmd_vel_controller`. Restart the UI/navigation processes after changing settings.

For isolated verification, source ROS and the workspace and run sequentially
from the workspace:

```bash
python3 src/g1_navigation/scripts/check_navigation_recovery.py
python3 src/g1_navigation/scripts/check_narrow_corridor.py
python3 src/g1_navigation/scripts/check_path_heading.py
```

These checks launch the actual A* navigator and safety nodes with synthetic
sensors on domains 97/98. They verify live obstacle detours, cancellation, sensor
staleness, heading alignment, and backup followed by reaching the original goal
through an 80 cm corridor. They start no robot or simulator and clean up their
own processes. Logs are `/tmp/g1-recovery-*.log`, `/tmp/g1-corridor-*.log` and
`/tmp/g1-heading-*.log`. They do not model MuJoCo locomotion dynamics.

## Save and browse maps

**Save new map** works while live SLAM exists, including in Maps & Localization.
It saves the received occupancy grid as a YAML/PNG pair in the map library,
preserving its resolution, origin, orientation and unknown cells. The library
refreshes and selects the saved map. Use a unique output name; existing maps
are preserved. Saving a loaded static map is disabled.

Under **Import a map from disk**, choose the two files separately using
**1. Map YAML** and **2. Map image**. Their names must match before the extension
(for example `airport.yaml` and `airport.png`), and the YAML must reference that
image. The import name is generated automatically; an existing name gets a
numeric suffix. **Import map** stores the pair; **Import & load** stores it and
starts AMCL. Imported filenames that already exist receive a suggested copy name.
Select an imported/saved map, click **Load map**, then place the initial pose.
**Continue mapping** returns to an existing SLAM session; **New mapping** starts
SLAM when a saved map is loaded or no mapping session exists.

## Continuous RAG conversation and spoken navigation

The default conversation backend follows the project architecture:
**phone/computer microphone → WebRTC VAD → faster-whisper STT → LangChain
orchestration / FAISS retrieval → Gemini → TTS → browser speaker**.
The UI remains a continuous **Start conversation / End conversation** interface;
there are no record/send or typed-message controls. Speech pauses delimit turns.
TTS replies and narration share one acknowledged playback queue. Each visible
reply finishes fully: microphone capture is paused during synthesis and playback,
then for a 0.6-second echo tail. Speak after the reply finishes. End conversation
and Emergency stop remain available for immediate control. This is the project
pipeline, not native Gemini Live audio generation.

A stateful dialogue guard resolves explicit “Take me / guide me / navigate me to”
commands only to saved location IDs. “Where is / show me” questions offer an
escort; “Okay / yes / sure / thanks” never send a goal. “Take me there” accepts a
single previous offer. Unknown or garbled names ask a natural clarification.
General questions still retrieve FAISS knowledge and call Gemini.

The console reads each label's position and yaw from `<map>_labels.json`; the
LLM never supplies coordinates. Localization, readiness, free-space and E-stop
checks precede motion. Voice escorts use 0.50 m/s and 0.80 rad/s, compact gestures,
and status-driven progress/arrival narration. Goals wait for the browser's speech
playback acknowledgement. TTS can speak while the A* navigator walks. A compact live map above the
conversation shows the robot heading, saved destinations, planned route and spoken
goal, with zoom, pan, Fit map and Center robot controls.

Configuration, complete startup commands, isolated motion checks and acceptance
replay: [Conversational airport guide](../g1-ros2-workspace/docs/concierge_guide.md).

Startup:

1. Start simulation in its own terminal with `cmd_vel_topic:=/cmd_vel_safe`.
2. Start `npm run dev` as usual; opening the UI does not start simulation.
3. In **Maps & Localization**, load a saved map, place **2D Pose Estimate**, and
   wait for localization. Mark/import destination labels and their headings.
4. Open **RAG Conversation**, press **Start conversation**, and allow the mic.
5. Ask airport questions or say “Take me to [saved destination]”. A* navigation and its
   collision/velocity controls start automatically for that guidance request;
   RAG remains running. “Stop navigation” cancels guidance. Ending the voice
   session or leaving its tab cancels spoken guidance too.

You can also choose **Conversation map** and click **Load conversation map**
directly in the RAG tab. End the conversation before changing maps. Loading
keeps the RAG tab open, stops live SLAM, and starts saved-map localization;
simulation remains external. The displayed saved destinations come from that
map's `<map_name>_labels.json`, read again for each spoken turn. FAISS supplies
airport knowledge; the selected map's label catalog supplies destination IDs
and coordinates. Use **Set robot pose / edit labels** to open Maps & Localization,
place the initial pose, and then return to RAG. No saved-map selection means
the assistant has no saved destination catalog, even when a live SLAM map exists.

`GOOGLE_API_KEY` must be set in `g1-ros2-workspace/.env` or exported before
starting the UI. Restart after changing it. It stays on the computer.
Dependencies are prepared in the separate `.rag-venv` on first selection;
the first Whisper utterance may download the configured model. FAISS reuses
its airport index. Default Edge TTS and Gemini require internet. The microphone
and playback are on the device displaying the UI; no computer audio hardware
or separate `conversation_node` launch is needed.

Environment options:

- `G1_CONVERSATION_MODE=pipeline` (default) follows the staged architecture.
  `gemini_live` keeps the optional native Live relay for experiments, with
  audio sent to Google and airport retrieval via a tool; it does not implement
  the staged STT/TTS or spoken navigation path.
- `G1_STT_MODEL=base`, `G1_TTS_BACKEND=auto` (Edge in the prepared environment).
- Optional native mode: `G1_LIVE_MODEL=gemini-3.8-live`, `G1_LIVE_VOICE=Kore`.
- `G1_RAG_PYTHON`: an existing fully configured Python, bypassing installation.
- `G1_RAG_ENV_FILE`: additional computer dotenv file.
- `G1_RAG_PORT=8767`; `G1_RAG_LIVE_PORT` defaults to `G1_RAG_PORT + 1`.

The `/api/rag/live` WebSocket streams PCM to the computer. The default staged
backend processes microphone audio locally; only recognized text and retrieved
context go to Gemini. Edge TTS receives reply text. API keys never enter the
browser. One live device/session is supported at a time. Logs are in
`g1-ros2-workspace/log/console/rag.log`. Phone microphones require the HTTPS
setup below.

Architecture and validation limits:
[Browser voice and navigation](../g1-ros2-workspace/docs/browser_voice_navigation.md).

Offline pipeline/relay checks after preparing `.rag-venv`:

```bash
PYTHONPATH="$PWD/../g1-ros2-workspace/src/g1_conversation:$PWD/../g1-ros2-workspace/src/g1_core" \
  ../g1-ros2-workspace/.rag-venv/bin/python -m unittest discover -s scripts -p 'test_pipeline_rag.py'
PYTHONPATH="$PWD/../g1-ros2-workspace/src/g1_conversation:$PWD/../g1-ros2-workspace/src/g1_core" \
  ../g1-ros2-workspace/.rag-venv/bin/python -m unittest discover -s scripts -p 'test_live_rag.py'
node --test src/liveAudio.test.js
```

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

### Understanding passenger destinations

A deterministic dialogue guard handles English escort intent and context; Gemini
translates multilingual intents and answers grounded general questions. RapidFuzz normalizes and
matches the requested name to this map's saved locations, including common
synonyms and small recognition errors. FAISS retrieves airport facts rather
than choosing navigation coordinates. The resolver uses conservative string
similarity and a gap between the top matches; those scores are not probabilities.
Ambiguous names such as “washroom” with east/west washrooms require clarification.
Gate/terminal/floor numbers must agree. No location outside the saved map catalog
can become a navigation goal. Contextual phrases such as “yes, take me there”
require an actual single prior offer; “yes” alone does not move the robot.
