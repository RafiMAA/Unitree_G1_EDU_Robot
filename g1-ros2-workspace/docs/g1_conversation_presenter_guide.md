# G1 Conversation Node — Presenter Guide

This guide describes the current implementation in `g1_conversation`. Use it
with the companion [architecture diagram](./g1_conversation_architecture.svg).

## Thirty-second introduction

The `g1_conversation` ROS 2 node turns the Unitree G1 into a friendly PickMe
airport concierge. It waves while greeting a passenger, remembers their name,
asks for their preferred language, listens until they finish speaking,
transcribes locally, retrieves trusted PickMe facts from FAISS, streams one
concise Gemini response, and speaks it through the robot in the selected
language.

## What the audience sees

1. The robot waves and says: “Hello! Ayubowan. Welcome to Sri Lanka. I'm
   PickMe. What's your name?”
2. The passenger gives a name. The deterministic parser extracts only the
   name, including common Whisper forms such as “Hi, my name is John” or
   `my nameis John`.
3. The robot uses the name naturally and asks which language the passenger
   prefers.
4. It gives a short localized PickMe introduction.
5. The passenger can ask about PickMe services, vehicles, locations, fares,
   or app installation. They can also request a supported language later.
6. Saying goodbye or remaining silent for the idle timeout ends the session
   and clears passenger data.

## Current conversation pipeline

### 1. ROS 2 orchestration and startup

`conversation_node.py` owns the state machine and runs the blocking voice loop
in a separate thread so ROS 2 publishing remains responsive. At startup it
loads the Silero detector and the local Whisper model. The node does not wait
for a separate passenger detector: when no session is active, it starts the
greeting immediately. It publishes the wave command just before starting the
greeting, so arm motion and speech run concurrently.

The terminal exposes every slow stage with `[PIPELINE] START`, `WAIT`, `EVENT`,
`DONE`, and `FAIL` messages. This makes microphone waits, transcription,
retrieval, Gemini generation, TTS generation, and playback individually
visible.

### 2. Silero neural voice activity detection

Silero v6, bundled with `faster-whisper`, is the default VAD backend. It uses
512-sample frames at 16 kHz, or 32 milliseconds per decision. Four voiced
frames in a six-frame window confirm speech. A start probability of `0.50`
rejects the G1 computer's high-energy microphone noise.

After speech begins, a lower `0.35` continuation threshold provides
hysteresis, so quiet syllables do not prematurely end an utterance. The
default `900 ms` silence setting corresponds to about `0.93 s` at the fixed
frame size. There is no arbitrary four-, ten-, or twelve-second speech cap:
continuous speech records until the passenger stops. The four-second setting
only prints a “still listening” status. WebRTC remains available as an
explicit fallback backend.

### 3. Local faster-whisper speech-to-text

The default recognizer is faster-whisper `small`, running on the CPU with INT8
weights. Beam size 1, deterministic temperature, and disabled previous-text
conditioning reduce latency and avoid carrying recognition mistakes into the
next turn.

The name answer is transcribed with an English hint because the greeting is in
English. The preferred-language answer is auto-detected. Once onboarding is
complete, the session language is supplied as the Whisper hint. Microphone
audio remains local; only the resulting text is used by cloud services.

### 4. Deterministic onboarding and session memory

Every `PassengerSession` moves through three stages:

1. `awaiting_name`
2. `awaiting_language`
3. `active`

The session stores the passenger name, selected language, turn count, and
session ID. The active RAG assistant also keeps the four most recent
question/answer pairs. Supported prompt and voice paths are English, French,
German, Spanish, Russian, Japanese, Chinese, Korean, Hindi, Sinhala, and
Tamil.

Explicit requests such as “Speak in Sinhala” or native-script equivalents can
switch language during the active conversation. The node then recreates the
language-specific RAG session while preserving the passenger's name.

### 5. Direct RAG retrieval — no planning loop

The current implementation is a deterministic retrieval pipeline, not an
iterative tool-calling agent. For every active passenger question it:

1. embeds the question;
2. retrieves the three most similar FAISS chunks;
3. builds one prompt containing the language instruction, passenger name,
   four-pair history window, retrieved facts, and current question; and
4. makes exactly one bounded Gemini generation request.

This removes the older extra Gemini request that was used only to decide
whether to call a search tool.

### 6. Knowledge base, embeddings, and FAISS

The maintained knowledge base contains four Markdown files:

- `pickme_services.md`
- `app_installation.md`
- `sri_lanka_locations.md`
- `robot_concierge.md`

The recursive splitter uses 500-character chunks with 50-character overlap
and retains source and section metadata. The current documents produce 45
chunks; this count changes automatically when the files change.

`models/gemini-embedding-001` converts the chunks and each query into vectors.
FAISS stores and searches those vectors locally. The index and manifest live
under `~/.g1_conversation/faiss_index`. A fingerprint based on the knowledge
files' paths, modification times, and sizes decides whether to load the cache
or rebuild it.

### 7. One concise, streamed Gemini response

The current model is `gemini-3.5-flash-lite` with minimal thinking,
temperature `0.2`, a 96-token limit, a 12-second request timeout, and one
retry. The prompt asks for at most two short sentences and roughly 45 spoken
words. It also fixes the PickMe brand fact: the logo has a yellow background
with a black passenger figure.

Generated content is normalized into plain text. Complete sentences are sent
to TTS as soon as they arrive, while the full answer is retained for ROS
publication and conversation history.

### 8. Friendly multilingual text-to-speech

Edge TTS uses friendly neural voices and natural punctuation-based pauses:

- English: `en-US-AvaMultilingualNeural` at `+8%`
- Sinhala: `si-LK-ThiliniNeural` at native speed
- Sri Lankan Tamil: `ta-LK-SaranyaNeural` at native speed

Short multi-sentence messages are synthesized as one utterance instead of one
MP3 per sentence. Longer text is split only above 260 characters. For a
streamed Gemini answer, the next sentence is synthesized while the current
one plays, removing network-sized gaps. Playback polling is 20 ms for
responsive stopping.

Audio is cached under `~/.g1_conversation/tts_cache`. The key includes a cache
schema, voice, rate, pitch, volume, and text, so a voice or prosody change
cannot accidentally reuse an old recording. Personalized messages use
temporary files and are removed after playback. Generation is atomic and
network failures are retried three times.

## RAG example for a presentation

If the passenger asks, “How can I install PickMe?”:

1. Silero captures the utterance through its natural end.
2. faster-whisper produces the text locally.
3. Gemini Embeddings creates the query vector.
4. FAISS returns the top three installation-related chunks.
5. The direct RAG prompt combines those facts with the language, name, and
   short session history.
6. Gemini produces one brief answer and streams complete sentences.
7. ROS publishes the plain-text answer while friendly Edge TTS speaks it.

RAG is important because the spoken answer is grounded in the project's
maintained PickMe documents rather than relying only on the language model's
general knowledge.

## ROS 2 interfaces

| Topic | Message | Purpose |
|---|---|---|
| `g1/speech_text` | `std_msgs/String` | Passenger transcription |
| `g1/agent_response` | `std_msgs/String` | Complete robot answer |
| `g1/conversation_state` | `std_msgs/String` | Current pipeline state |
| `g1/gesture_command` | `std_msgs/String` | Human-readable `wave` or `wave_with_turn` event |
| `/api/sport/request` | `unitree_api/Request` | Unitree API 7106 wave request; task 0 is in-place and task 1 includes a body turn |

The main published states include waiting, greeting, listening, transcribing,
initializing the agent, thinking, speaking, and idle. Other displays and robot
nodes can observe these topics without depending on the AI implementation.

## Greeting wave and MuJoCo control

The conversation node publishes both a human-readable gesture event and the
physical Unitree Sport API request. This lets the same conversation work with
two motion targets:

- On a physical G1, `/api/sport/request` carries API 7106. Task 0 requests an
  in-place wave; task 1 requests the Unitree wave-with-body-turn behavior.
- In MuJoCo, `g1_core` consumes `g1/gesture_command`. Its ONNX locomotion policy
  continues at 50 Hz while a scripted right-arm target is blended over the
  policy output. The gesture raises for 2.0 seconds, waves the wrist for 2.5
  seconds, and lowers for 2.0 seconds. Walking velocity is cleared when the
  gesture starts, and the arm observations are isolated from the locomotion
  policy while IMU balance feedback remains active.

The simulator currently uses the same stable 6.5-second animation for `wave`
and `wave_with_turn`; the task distinction applies to the physical Sport API.

## Important runtime defaults

| Parameter | Default | Meaning |
|---|---:|---|
| `whisper_model` | `small` | Local STT model |
| `stt_backend` | `auto` | Selects faster-whisper when available |
| `stt_compute_type` | `int8` | CPU-friendly quantization |
| `vad_backend` | `silero` | Neural voice detector |
| `vad_threshold` | `0.5` | Speech-start probability |
| `vad_end_threshold` | `0.35` | Continue-speech hysteresis threshold |
| `vad_silence_ms` | `900.0` | Silence required to finish an utterance |
| `listen_window_sec` | `4.0` | Status-report interval, not a recording cap |
| `idle_timeout_sec` | `90.0` | Pre-speech session timeout |
| `post_tts_echo_guard_sec` | `0.5` | Delay before reopening the microphone |
| `greeting_wave_enabled` | `true` | Publish the greeting gesture |
| `greeting_wave_with_turn` | `false` | Select task 1 instead of task 0 |

## Launch command used for the current demo

Use a valid key from the environment; never place a real key in documentation
or source control.

```bash
cd ~/Desktop/Unitree_G1/g1-ros2-workspace
source install/setup.bash
export GOOGLE_API_KEY="<your-google-api-key>"

ros2 run g1_conversation conversation \
  --ros-args \
  -p whisper_model:=small \
  -p stt_backend:=faster-whisper \
  -p stt_compute_type:=int8 \
  -p vad_backend:=silero \
  -p vad_threshold:=0.5 \
  -p vad_end_threshold:=0.35 \
  -p vad_silence_ms:=900.0 \
  -p greeting_wave_with_turn:=true
```

### MuJoCo simulation

After building the workspace, launch the MuJoCo bridge and ONNX controller in
one terminal:

```bash
cd ~/Desktop/Unitree_G1/g1-ros2-workspace
source install/setup.bash
ros2 launch g1_mujoco sim.launch.py
```

Then run the conversation node in a second terminal with the command above.
For debugging, the equivalent split-terminal commands are:

```bash
ros2 run g1_mujoco mujoco_bridge
ros2 run g1_core state_machine
```

Each split-terminal command must run in its own sourced terminal. The bridge
publishes `g1/joint_states` and `g1/imu`; the controller publishes the 29-joint
position target on `g1/joint_cmd`.

## Cloud and privacy boundary

- Raw microphone audio is processed locally by Silero and Whisper.
- The transcribed question, retrieved knowledge chunks, short chat history,
  and passenger name are sent to Gemini for response generation.
- The generated reply text is sent to Edge TTS for audio synthesis.
- FAISS vectors and generated TTS files are cached locally.
- On goodbye, idle timeout, replacement, or error, the session is discarded
  and stored PII fields are explicitly overwritten first.
- Normal terminal output can contain transcriptions and generated replies, and
  a reusable TTS cache entry can contain spoken reply text. Personalized
  onboarding lines use temporary audio files that are deleted after playback.
  Clear logs and local caches separately if the deployment requires complete
  removal of historical passenger text.

## Short presentation script

“The robot begins with a synchronized wave and greeting, then remembers the
passenger's name and asks for their language. Silero neural VAD records exactly
until the passenger stops, and faster-whisper transcribes the audio locally.
For every question, our deterministic RAG pipeline retrieves the three most
relevant PickMe facts from a local FAISS index and makes one short Gemini call.
Gemini's answer streams sentence by sentence while the next friendly voice
clip is generated in parallel. ROS topics expose the text, state, response,
and gesture, while timing logs, caches, fallbacks, and per-passenger cleanup
keep the demonstration observable and reliable.”
