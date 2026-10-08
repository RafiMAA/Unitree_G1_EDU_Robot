# Conversational airport guide

The default `G1_CONVERSATION_MODE=pipeline` keeps WebRTC VAD, faster-whisper,
FAISS retrieval, Gemini Flash Lite and phone TTS. Navigation remains the project's
standalone A* navigator, using its existing `NavigateToPose` ROS action contract.
AMCL, saved maps, JSON labels, collision monitoring and UI navigation remain in use.

Previously every wayfinding question could issue a goal, the spoken reply was
replaced with “I sent your request…”, and no ongoing navigation narrator or
conversation-driven gestures existed. Now a deterministic dialogue guard keeps
IDLE, CLARIFYING, GUIDING and ARRIVED state. A model cannot turn English general
questions, acknowledgements, ambiguous matches or farewells into movement.

## Behaviour and configuration

Edit **`src/g1_core/g1_core/guide_behavior.py`** for persona, response variants,
gestures, narration timing, playback acknowledgement timeout and escort speeds.
The same module is loaded by the voice environment, navigator, RL controller and
MuJoCo bridge. Defaults: escort 0.50 m/s, 0.80 rad/s; narration every 20 seconds
when actual progress exists, minimum 8-second gap, blocked cooldown 25 seconds,
idle gesture every 30 seconds. Ordinary Navigate-tab speeds remain separately
configured in `g1_navigation/config/astar_params.yaml`. Inflation remains 25 cm.

* “Take/guide/navigate me to …” uses RapidFuzz only against the current map's label
  catalog, then validates map identity, free space, localization and readiness.
* “Where is … / show me …” identifies the place and offers an escort. The waypoint
  catalog contains names and poses, not verified descriptive directions; those
  are not invented. “Yes” alone never starts motion. “Take me there” accepts the
  last single offer. A clarification can be answered with the saved place name.
* Repeating an active destination acknowledges the existing escort. A different
  destination cancels the old goal before preparation and speaking the new line.
* General questions keep FAISS retrieval and Gemini, with recent conversation
  history plus actual guide state/destination. No sourced visitor count means
  the assistant must admit uncertainty. Destinations never come from FAISS.
* Multilingual requests use a model-produced English translation, pass through
  the same saved-location guard, then translate the validated spoken phrase.
  This can require an extra Gemini call. English escort commands use no LLM call.
* Stop/wait/cancel ends guidance; it does not auto-resume. Ask explicitly to guide
  again. A goodbye cancels an active escort and speaks a farewell.

## Speech and motion sequencing

There is one speaker queue. The browser acknowledges actual scheduled WebAudio
playback using `playback_started` and `playback_ended` messages. A new escort goal
is submitted only after the spoken invitation begins. No acknowledgement means
no new goal. Once an assistant caption appears, that reply finishes in full.
Microphone PCM is discarded during synthesis/playback and a 0.6-second acoustic
tail. Buffered VAD frames and queued clips are cleared; late STT results from
before playback are invalidated. The browser also gates capture without changing
manual mute. Speak after the reply finishes; End conversation and Emergency stop
remain available immediately. Ending the conversation cancels guidance.

The navigator publishes `/g1/guide_status` with goal coordinates, state, remaining
distance and heading error. A separate asynchronous narrator binds these events
to the active escort, speaks left/right turns, real progress, block/recovery,
arrival and failure. Feedback older than 3 seconds becomes paused, preventing
invented progress. Speech synthesis and playback do not block ROS walking. Dedicated thread pools
separate Whisper, TTS, Gemini, navigation requests and feedback. A separate
recognizer validates candidate speech before changing the conversation generation.

`POST /api/rag/gesture {"name":"follow_me"}` publishes `/g1/gesture_command`.
The clean `play_gesture(name)` interface supports `greet`, `point`, `follow_me`
and `idle-look-around`. Compact smooth arm/waist offsets preserve leg commands;
walking reduces arm offsets to 25% and suppresses waist offsets. The bridge keeps
its stable standing gains while greeting, instead of activating walking for a
zero velocity. Legacy wave commands now use the compact greeting.

A **fresh `geometry_msgs/PoseStamped` on `/g1/user_pose`, frame `map`**, enables
facing the supplied passenger position before aligning with the route. The stamp
must be within 2 seconds. The facing stage lasts at most 8 seconds and uses the
normal collision checks. Without tracking data, it skips that stage and uses the
gesture plus route alignment. The phone microphone does not locate a passenger.
There is no neck joint: looking/nodding uses the waist, not a literal head actuator.
Automatic follower-distance waiting is not implemented.

## Run in simulation

Stop your existing UI and simulator with Ctrl+C in their own terminals, then build:

```bash
cd ~/Desktop/Rafi_Unitree_sem_Project/Unitree_G1_EDU_Robot_recovered/g1-ros2-workspace
unset PYTHONPATH
export PYTHONNOUSERSITE=1
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select g1_core g1_mujoco g1_navigation g1_conversation
source install/setup.bash
```

Terminal 1, keep running:

```bash
cd ~/Desktop/Rafi_Unitree_sem_Project/Unitree_G1_EDU_Robot_recovered/g1-ros2-workspace
export PYTHONNOUSERSITE=1 ROS_DOMAIN_ID=0 ROS_LOCALHOST_ONLY=1 MUJOCO_GL=glfw
source /opt/ros/humble/setup.bash
source install/setup.bash
export PYTHONPATH="$PWD/.venv/lib/python3.10/site-packages:${PYTHONPATH:-}"
ros2 launch g1_mujoco sim.launch.py start_rosbridge:=false cmd_vel_topic:=/cmd_vel_safe
```

Terminal 2, keep running:

```bash
source ~/.nvm/nvm.sh
nvm use 22
cd ~/Desktop/Rafi_Unitree_sem_Project/Unitree_G1_EDU_Robot_recovered/g1-navigation-ui
export G1_CONVERSATION_MODE=pipeline
npm run dev
```

In **Maps & Localization**, load your labeled saved map and set the actual robot
position/orientation. In **RAG Conversation**, select that same map and wait for
localization. Start conversation on your phone/PC with microphone and speaker
permission. Say the actual saved label names; Office/Entrance below are fixtures,
not newly added labels. No simulator is started by `npm run dev`.

Logs: `g1-ros2-workspace/log/console/rag.log` and the managed navigation logs.
Dialogue logging includes state, intent and chosen destination. To inspect ROS:

```bash
ros2 topic echo /g1/guide_status
ros2 topic echo /g1/gesture_command
ros2 topic echo /cmd_vel_safe
```

## Reproduce automated checks

From the repository root:

```bash
PYTHONPATH=g1-navigation-ui/scripts:g1-ros2-workspace/src/g1_core:g1-ros2-workspace/src/g1_conversation \
  g1-ros2-workspace/.rag-venv/bin/python -m pytest -q \
  g1-navigation-ui/scripts/test_pipeline_rag.py g1-navigation-ui/scripts/test_guide_pipeline.py \
  g1-navigation-ui/scripts/test_live_rag.py g1-navigation-ui/scripts/test_rag_worker.py \
  g1-ros2-workspace/src/g1_conversation/test

PYTHONPATH=g1-ros2-workspace/src/g1_core:g1-ros2-workspace/src/g1_conversation \
  g1-ros2-workspace/.rag-venv/bin/python g1-navigation-ui/scripts/replay_guide.py

cd g1-navigation-ui
node --test src/liveAudio.test.js
npm run build
```

Pytest is a development dependency (installed in `.rag-venv` for these checks).
For isolated motion and physical gesture checks, start a separate terminal and
source ROS/workspace as in Terminal 1 above, then run:

```bash
python3 src/g1_navigation/scripts/check_guide_escort.py
python3 src/g1_core/test/check_guide_gestures.py
python3 src/g1_core/test/check_guide_walking.py
```

The escort check uses ROS domain 98 and synthetic sensors/kinematics. The standing
check runs headless MuJoCo without ROS nodes; the walking check uses the actual
ONNX policy and headless MuJoCo on isolated domain 97. They do not command your
running simulator. No paid Gemini or live phone/audio end-to-end test is included.
The optional native Gemini Live mode keeps its existing implementation; this
stateful escort behaviour applies to the default VAD/Whisper/RAG pipeline.

## Validation results

* Conversation/voice/relay/worker unit suite: 139 passed, 3 skipped.
* Navigation and console regression suite: 50 passed.
* Browser audio tests: 6 passed; production UI build passed. All four ROS packages were built during the
  earlier integration checks.
* Real ROS action with synthetic sensors: faced passenger, aligned to the route,
  reached the goal, caps 0.50 m/s / 0.80 rad/s, no strafing or reversing.
* Actual MuJoCo standing gestures: minimum base height 0.782 m, peak tilt at most
  3.41 degrees across all four gestures.
* Actual ONNX walking with all four gestures: commanded 0.50 m/s, displacement
  4.67 m in 15 seconds, minimum base height 0.770 m, peak tilt 5.32 degrees.

## Changed implementation files

* `g1_core/g1_core/guide_behavior.py`: central configuration and smooth gestures.
* `g1_conversation/g1_conversation/agent/dialogue.py`, `rag_agent.py`,
  `destinations.py`: dialogue state, intent guards, fuzzy clarification, memory,
  warm persona, multilingual validation and grounded general answers.
* `g1-navigation-ui/scripts/pipeline_rag.py`: concurrent microphone, serialized
  synthesis/playback, full-reply microphone gating, playback-gated goal sequencing and feedback narration.
* `scripts/console_server.py`, `scripts/rag-launcher.py`: gesture endpoint,
  guide feedback, conservative profile and shared-module voice environment.
* `src/liveAudio.js`, `src/RagPanel.jsx`: real playback acknowledgements.
* `g1_navigation/g1_navigation/astar_navigator.py`: guide profile, optional
  user-facing stage and per-goal narration feedback.
* `g1_core/g1_core/state_machine_node.py`,
  `g1_mujoco/g1_mujoco/mujoco_bridge_node.py`: arm/waist overlays with standing
  and walking controllers; package manifests declare the shared-core dependency.
* Added dialogue/pipeline/audio regression checks, offline replay, headless
  standing/walking checks and an isolated ROS escort check; updated voice docs.

## Acceptance replay

Offline fixture replay, with mocked Gemini/knowledge responses, using real dialogue
and narrator code. The destination switch occurs while the first escort is active,
so Office is not falsely announced as reached. An independent Office narration test
checks Office arrival; the ROS escort check verifies actual goal completion.

```text
Passenger: Take me to the office.
Guide: Of course! Follow me, I'll show you the way to Office.
[After playback starts: follow_me gesture, face supplied user pose, align to path, escort at 0.50 m/s / 0.80 rad/s]
[State=GUIDING, action=navigate, total goals=1]
Passenger: Okay.
Guide: Of course.
[State=GUIDING, action=none, total goals=1]
Passenger: Can you navigate me to the entrance?
Guide: Sure, let's go to Entrance instead. Follow me.
[Cancel previous escort]
[After playback starts: follow_me gesture, face supplied user pose, align to path, escort at 0.50 m/s / 0.80 rad/s]
[State=GUIDING, action=navigate, total goals=2]
Guide (navigator feedback): We'll turn left here.
Guide (navigator feedback): Excuse me, one moment, I'm finding a way around.
Guide (navigator feedback): Almost there.
Guide (navigator feedback): Here we are, this is Entrance. Is there anything else I can help you with?
Passenger: Where is the office? Can you show me?
Guide: I can show you the way to Office. Would you like me to take you there?
[State=ARRIVED, action=none, total goals=2]
Passenger: What are the things in the Albert area?
Guide: Sorry, I didn't catch that. Did you mean Office or Entrance?
[State=CLARIFYING, action=none, total goals=2]
Passenger: How many people go around there?
Guide: I'm not sure how many people visit there. The information desk may be able to tell you more; can I help you find somewhere?
[State=CLARIFYING, action=none, total goals=2]
Passenger: Thanks, bye.
Guide: You're very welcome. Have a lovely journey!
[State=IDLE, action=none, total goals=2]
PASS: 2 validated escort goals, 1 switch cancellation, acknowledgements and farewell add no goals; general question uses retrieval.

```

## Noise, missing speech and an invitation without movement

A raw VAD start used to cancel a spoken invitation before Whisper could reject
noise. This could leave a “Follow me” caption without submitting its goal. VAD
candidates now pass an adaptive RMS floor, sustained voiced-frame/voice-ratio
checks, Silero VAD and Whisper confidence/no-speech checks before they appear as
passenger text. Recently played assistant phrases are checked for likely speaker
echo. Full-reply microphone gating also rejects short echo fragments (such as
“show” heard as “so”), which phrase similarity alone cannot reliably reject.
Very quiet voices may need a lower `speech.minimum_rms` in `guide_behavior.py`.

Browser playback uses a completion fallback when a phone output clock stalls;
explicitly stopped audio cannot trigger a goal. Suspended contexts are resumed
before decoding. Audio decode errors report a notice without terminating the
conversation. Gateway rejection before an A* job exists is now narrated too.

TTS has a bounded cloud-generation timeout, serialized synthesis in its own thread,
and a 16-reply in-memory session cache. Cached audio is released with the session;
no new persistent recordings or transcript files are saved. Whisper/STT, Gemini,
control requests and feedback have independent worker pools, so a slow speech
request cannot prevent explicit End conversation or navigation cancellation. Spoken
stop commands are accepted when listening resumes after the full reply.

After restarting `npm run dev`, reload the phone page and start a fresh conversation.
Keep the simulator running. New log entries distinguish accepted/rejected audio,
`speech ready`, `Browser speech started`, `Submitting escort goal`, and the actual
`Escort goal receipt` state. Real-device microphone echo and acoustics still need
a live check; no filter can distinguish every real voice from background speech.
