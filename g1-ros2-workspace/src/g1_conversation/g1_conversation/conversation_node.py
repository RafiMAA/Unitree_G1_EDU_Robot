"""Main ROS2 conversation node — orchestrates the full concierge pipeline.

Pipeline: Mic → VAD → Whisper STT → LangChain RAG Agent → edge-tts → Speaker

The conversation loop runs in a separate thread to avoid blocking the
ROS2 event loop.  All intermediate results (transcriptions, responses,
state changes) are published as ROS2 topics for monitoring and debugging.
"""

import json
import queue
import threading
import time
import traceback
from contextlib import contextmanager

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from unitree_api.msg import Request

from .session import SessionManager
from .vad import VAD
from .stt_engine import STTEngine
from .tts_engine import TTSEngine
from .language_switch import detect_language_switch, get_language_confirmation
from .onboarding import (
    LANGUAGE_QUESTION,
    NAME_RETRY,
    extract_passenger_name,
    get_pickme_introduction,
)
from .agent.rag_agent import create_agent, stream_agent_sentences
from .agent.prompts import (
    INITIAL_GREETING,
    UNSUPPORTED_LANGUAGE_MESSAGE,
    is_language_supported,
)

class ConversationNode(Node):
    """PickMe Robotic Mobility Concierge — main ROS2 node.

    Manages the full voice conversation pipeline:
    1. VAD detects passenger speech
    2. Whisper transcribes + detects language
    3. LangChain RAG agent processes the query
    4. edge-tts speaks the response

    Session management ensures PII is cleared between passengers.
    Language is pinned on the first utterance to avoid flip-flopping.
    """

    def __init__(self) -> None:
        super().__init__("g1_conversation")

        # ── Parameters ───────────────────────────────────────────────
        self.declare_parameter("whisper_model", "small")
        self.declare_parameter("stt_backend", "auto")
        self.declare_parameter("stt_compute_type", "int8")
        self.declare_parameter("idle_timeout_sec", 90.0)
        self.declare_parameter("greeting_wave_enabled", True)
        self.declare_parameter("greeting_wave_with_turn", False)
        self.declare_parameter("unitree_loco_request_topic", "/api/sport/request")
        self.declare_parameter("pipeline_heartbeat_sec", 5.0)
        self.declare_parameter("listen_window_sec", 4.0)
        self.declare_parameter("post_tts_echo_guard_sec", 0.5)
        self.declare_parameter("vad_backend", "silero")
        self.declare_parameter("vad_threshold", 0.5)
        self.declare_parameter("vad_end_threshold", 0.35)
        self.declare_parameter("vad_silence_ms", 900.0)

        whisper_model = self.get_parameter("whisper_model").get_parameter_value().string_value
        stt_backend = self.get_parameter("stt_backend").value
        stt_compute_type = self.get_parameter("stt_compute_type").value
        self.idle_timeout = self.get_parameter("idle_timeout_sec").get_parameter_value().double_value
        self.greeting_wave_enabled = (
            self.get_parameter("greeting_wave_enabled")
            .get_parameter_value().bool_value
        )
        self.greeting_wave_with_turn = (
            self.get_parameter("greeting_wave_with_turn")
            .get_parameter_value().bool_value
        )
        loco_request_topic = (
            self.get_parameter("unitree_loco_request_topic")
            .get_parameter_value().string_value
        )
        self.pipeline_heartbeat_sec = max(
            1.0,
            self.get_parameter("pipeline_heartbeat_sec")
            .get_parameter_value().double_value,
        )
        self.listen_window_sec = max(1.0, float(self.get_parameter("listen_window_sec").value))
        self.post_tts_echo_guard_sec = max(
            0.0, float(self.get_parameter("post_tts_echo_guard_sec").value)
        )
        vad_backend = str(self.get_parameter("vad_backend").value)
        vad_threshold = float(self.get_parameter("vad_threshold").value)
        vad_end_threshold = float(
            self.get_parameter("vad_end_threshold").value
        )
        vad_silence_ms = float(self.get_parameter("vad_silence_ms").value)

        # ── ROS2 Publishers ──────────────────────────────────────────
        self.speech_text_pub = self.create_publisher(String, "g1/speech_text", 10)
        self.agent_response_pub = self.create_publisher(String, "g1/agent_response", 10)
        self.conversation_state_pub = self.create_publisher(String, "g1/conversation_state", 10)
        self.gesture_pub = self.create_publisher(String, "g1/gesture_command", 10)
        self.loco_request_pub = self.create_publisher(
            Request, loco_request_topic, 1
        )

        # ── Session Manager ──────────────────────────────────────────
        self.session_mgr = SessionManager()
        self.agent_executor = None

        # ── Speech Engines ───────────────────────────────────────────
        # Startup timing uses this flag from heartbeat threads, so it must
        # exist before either neural model is initialized.
        self._running = True
        self.tts = TTSEngine()
        self.stt = STTEngine(
            model_name=whisper_model,
            backend=stt_backend,
            compute_type=stt_compute_type,
        )
        with self._timed_stage(
            f"startup: initialize voice detector '{vad_backend}'"
        ):
            self.vad = VAD(
                backend=vad_backend,
                probability_threshold=vad_threshold,
                end_probability_threshold=vad_end_threshold,
                silence_duration_ms=vad_silence_ms,
            )
        self.get_logger().info(
            "Voice detector ready: "
            f"backend={self.vad.backend}, frame={self.vad.frame_size} samples, "
            f"start={self.vad.probability_threshold:.2f}, "
            f"end={self.vad.end_probability_threshold:.2f}, "
            f"silence={self.vad.silence_duration_seconds:.2f}s"
        )

        # Pre-load Whisper model
        with self._timed_stage(f"startup: load Whisper '{whisper_model}'"):
            self.stt.load()

        # ── Start conversation loop ──────────────────────────────────
        self._thread = threading.Thread(
            target=self._conversation_loop, daemon=False
        )
        self._thread.start()

        self.get_logger().info("g1_conversation node started")
        self._publish_state("idle")

    # ── State Publishing ─────────────────────────────────────────────

    def _publish_state(self, state: str) -> None:
        msg = String()
        msg.data = state
        self.conversation_state_pub.publish(msg)

    def _publish_speech_text(self, text: str) -> None:
        msg = String()
        msg.data = text
        self.speech_text_pub.publish(msg)

    def _publish_agent_response(self, text: str) -> None:
        msg = String()
        msg.data = text
        self.agent_response_pub.publish(msg)

    # ── Conversation Loop ────────────────────────────────────────────

    @contextmanager
    def _timed_stage(self, name: str):
        """Log start, periodic progress, and total time for a blocking stage."""
        started = time.perf_counter()
        finished = threading.Event()
        if rclpy.ok():
            self.get_logger().info(f"[PIPELINE] START {name}")

        def heartbeat():
            while not finished.wait(self.pipeline_heartbeat_sec):
                if not self._running or not rclpy.ok():
                    break
                elapsed = time.perf_counter() - started
                self.get_logger().info(
                    f"[PIPELINE] WAIT  {name} ({elapsed:.1f}s elapsed)"
                )

        monitor = threading.Thread(target=heartbeat, daemon=True)
        monitor.start()
        try:
            yield
        except Exception:
            elapsed = time.perf_counter() - started
            if rclpy.ok():
                self.get_logger().error(
                    f"[PIPELINE] FAIL  {name} after {elapsed:.2f}s"
                )
            raise
        finally:
            finished.set()
            monitor.join(timeout=0.2)
            elapsed = time.perf_counter() - started
            if rclpy.ok():
                self.get_logger().info(
                    f"[PIPELINE] DONE  {name} in {elapsed:.2f}s"
                )

    def _pipeline_event(self, message: str) -> None:
        if rclpy.ok():
            self.get_logger().info(f"[PIPELINE] EVENT {message}")

    def _conversation_loop(self) -> None:
        """Main conversation loop — runs in a separate thread."""
        while self._running and rclpy.ok():
            try:
                self._voice_loop_iteration()
            except KeyboardInterrupt:
                break
            except Exception as e:
                if not self._running or not rclpy.ok():
                    break
                self.get_logger().error(f"Conversation error: {e}")
                traceback.print_exc()
                # Don't crash — reset and continue
                self._end_session("error")

    def _voice_loop_iteration(self) -> None:
        """Single iteration of the voice conversation loop."""
        session = self.session_mgr.current

        if session is None:
            # No active session — wait for a passenger
            self._publish_state("waiting_for_passenger")
            self.get_logger().info("Waiting for passenger... (speak to start)")

            # The G1 executes the motion asynchronously, so publish the wave
            # immediately before the blocking TTS call to start both together.
            self._publish_state("greeting")
            self._wave_hello()
            with self._timed_stage("greeting: text-to-speech"):
                self.tts.speak(INITIAL_GREETING, lang_code="en")

            # Start a new session
            session = self.session_mgr.start_session()
            self.get_logger().info(f"Session started: {session.session_id}")

        # Let the robot speaker tail and room echo decay before opening the
        # microphone. Logs showed false speech consistently 0.15s after TTS.
        if self.post_tts_echo_guard_sec > 0:
            self._pipeline_event(
                f"speaker echo guard {self.post_tts_echo_guard_sec:.1f}s"
            )
            time.sleep(self.post_tts_echo_guard_sec)

        # Listen for speech with timeout
        self._publish_state("listening")
        self.get_logger().info("Listening ...")

        with self._timed_stage("microphone: wait for utterance"):
            audio = self.vad.listen_with_timeout(
                timeout_seconds=self.idle_timeout,
                status_interval_seconds=self.listen_window_sec,
                status_callback=self._pipeline_event,
                cancel_check=lambda: not self._running or not rclpy.ok(),
            )

        if audio is None:
            if not self._running or not rclpy.ok():
                return
            # Timeout — no speech detected
            self.get_logger().info("Idle timeout — ending session")
            self._end_session("idle_timeout")
            return

        # Transcribe
        self._publish_state("transcribing")
        # The first transcription auto-detects language. Later turns use the
        # pinned session language, avoiding a separate ~9 second detection pass.
        if session.onboarding_stage == "awaiting_name":
            # The English greeting asks for a short name; avoiding automatic
            # language detection reduces this onboarding transcription delay.
            language_hint = "en"
        elif session.onboarding_stage == "active":
            language_hint = session.language
        else:
            # The preferred-language answer must remain auto-detected.
            language_hint = None
        with self._timed_stage(
            f"Whisper: transcribe (hint={language_hint or 'auto'})"
        ):
            stt_result = self.stt.transcribe(audio, language=language_hint)

        if not self._running or not rclpy.ok():
            return

        audio_duration = len(audio) / 16_000
        self.get_logger().info(
            f"STT metrics: audio={audio_duration:.2f}s, "
            f"confidence={stt_result.confidence:.3f}, "
            f"language={stt_result.language}, empty={stt_result.is_empty}"
        )

        if stt_result.is_empty:
            self.get_logger().info("Empty transcription — ignoring")
            return

        self.get_logger().info(
            f"Transcribed [{stt_result.language}]: {stt_result.text}"
        )
        self._publish_speech_text(stt_result.text)

        if session.onboarding_stage == "awaiting_name":
            passenger_name = extract_passenger_name(stt_result.text)
            if passenger_name is None:
                self.get_logger().info("Name reply was unclear — asking again")
                self._publish_agent_response(NAME_RETRY)
                self._publish_state("speaking")
                with self._timed_stage("onboarding: repeat name question"):
                    self.tts.speak(NAME_RETRY, lang_code="en")
                return

            session.passenger_name = passenger_name
            session.onboarding_stage = "awaiting_language"
            language_question = LANGUAGE_QUESTION.format(name=passenger_name)
            self.get_logger().info("Passenger name stored for current session")
            self._publish_agent_response(language_question)
            self._publish_state("speaking")
            with self._timed_stage("onboarding: ask preferred language"):
                self.tts.speak(language_question, lang_code="en", cache=False)
            return

        if session.onboarding_stage == "awaiting_language":
            requested_language = detect_language_switch(stt_result.text)
            selected_language = requested_language or stt_result.language
            if not is_language_supported(selected_language):
                self.get_logger().info(
                    f"Preferred language {selected_language} is unsupported; "
                    "continuing in English"
                )
                self._publish_agent_response(UNSUPPORTED_LANGUAGE_MESSAGE)
                with self._timed_stage("fallback: text-to-speech"):
                    self.tts.speak(UNSUPPORTED_LANGUAGE_MESSAGE, lang_code="en")
                selected_language = "en"

            session.language = selected_language
            session.onboarding_stage = "active"
            session.turn_count = 1
            self._publish_state("initializing_agent")
            with self._timed_stage(
                f"agent: initialize language={selected_language}"
            ):
                self.agent_executor = create_agent(
                    lang_code=selected_language,
                    passenger_name=session.passenger_name,
                )

            introduction = get_pickme_introduction(
                selected_language, session.passenger_name or "friend"
            )
            self.get_logger().info(
                f"Onboarding complete; language={selected_language}"
            )
            self._publish_agent_response(introduction)
            self._publish_state("speaking")
            with self._timed_stage("onboarding: PickMe introduction"):
                self.tts.speak(
                    introduction, lang_code=selected_language, cache=False
                )
            return

        # Explicit requests override audio-language detection and work on every
        # turn (for example, "Speak in Sinhala" or "Switch to Tamil").
        requested_language = detect_language_switch(stt_result.text)
        if requested_language is not None:
            session.language = requested_language
            session.turn_count += 1
            self._publish_state("initializing_agent")
            with self._timed_stage(
                f"agent: initialize language={requested_language}"
            ):
                self.agent_executor = create_agent(
                    lang_code=requested_language,
                    passenger_name=session.passenger_name,
                )
            self.get_logger().info(
                f"Language switch requested — changed to {requested_language}"
            )

            confirmation = get_language_confirmation(requested_language)
            self._publish_agent_response(confirmation)
            self._publish_state("speaking")
            with self._timed_stage("language confirmation: text-to-speech"):
                self.tts.speak(confirmation, lang_code=requested_language)
            return

        # Language detection on first turn
        if session.turn_count == 0:
            detected_lang = stt_result.language
            session.language = detected_lang

            if is_language_supported(detected_lang):
                self.get_logger().info(
                    f"Language detected: {detected_lang} — switching prompts"
                )
            else:
                self.get_logger().info(
                    f"Language detected: {detected_lang} — not supported, "
                    f"falling back to English"
                )
                with self._timed_stage("fallback: text-to-speech"):
                    self.tts.speak(UNSUPPORTED_LANGUAGE_MESSAGE, lang_code="en")
                session.language = "en"

            # Create agent with detected language
            self._publish_state("initializing_agent")
            with self._timed_stage(
                f"agent: initialize language={session.language}"
            ):
                self.agent_executor = create_agent(
                    lang_code=session.language,
                    passenger_name=session.passenger_name,
                )
            self.get_logger().info("Agent initialized")

        session.turn_count += 1

        # Check for goodbye intent
        if self._is_goodbye(stt_result.text):
            farewell = self._get_farewell(session.language)
            with self._timed_stage("farewell: text-to-speech"):
                self.tts.speak(farewell, lang_code=session.language)
            self._publish_agent_response(farewell)
            self._end_session("passenger_goodbye")
            return

        # Retrieve once and stream a single Gemini response. The producer keeps
        # receiving model output while the consumer speaks completed sentences.
        self._publish_state("thinking")
        self.get_logger().info("Agent thinking ...")
        sentence_queue = queue.Queue()
        stream_finished = object()

        def produce_sentences():
            try:
                with self._timed_stage("FAISS + Gemini: stream response"):
                    first = True
                    for sentence in stream_agent_sentences(
                        self.agent_executor, stt_result.text
                    ):
                        if first:
                            self._pipeline_event("first Gemini sentence available")
                            first = False
                        sentence_queue.put(sentence)
            finally:
                # Always release the consumer, including on an unexpected error.
                sentence_queue.put(stream_finished)

        producer = threading.Thread(target=produce_sentences, daemon=False)
        producer.start()
        response_sentences = []
        stream_exhausted = False

        def queued_response_sentences():
            """Yield Gemini sentences to TTS while retaining the full reply."""
            nonlocal stream_exhausted
            while self._running and rclpy.ok():
                try:
                    sentence = sentence_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if sentence is stream_finished:
                    stream_exhausted = True
                    return
                response_sentences.append(sentence)
                yield sentence

        self._publish_state("speaking")
        with self._timed_stage("streamed response: generate + speak"):
            self.tts.speak_sequence(
                queued_response_sentences(), lang_code=session.language
            )

        # A synthesis failure can stop playback before the iterator reaches
        # Gemini's sentinel. Still retain/publish the complete textual answer.
        while not stream_exhausted and self._running and rclpy.ok():
            try:
                sentence = sentence_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if sentence is stream_finished:
                stream_exhausted = True
                break
            response_sentences.append(sentence)
        producer.join()

        if not self._running or not rclpy.ok():
            return
        response_text = " ".join(response_sentences)
        self.get_logger().info(f"Agent response: {response_text}")
        self._publish_agent_response(response_text)

    # ── Helpers ──────────────────────────────────────────────────────

    def _wave_hello(self) -> None:
        """Ask the G1 high-level controller to perform its built-in wave."""
        if not self.greeting_wave_enabled:
            return

        gesture = String()
        gesture.data = "wave_with_turn" if self.greeting_wave_with_turn else "wave"
        self.gesture_pub.publish(gesture)

        request = Request()
        # This matches Unitree G1 LocoClient::WaveHand: API 7106 selects an
        # arm task; task 0 waves in place and task 1 waves with a body turn.
        request.header.identity.id = time.monotonic_ns()
        request.header.identity.api_id = 7106
        task_id = 1 if self.greeting_wave_with_turn else 0
        request.parameter = json.dumps({"data": task_id})
        self.loco_request_pub.publish(request)
        self.get_logger().info(
            f"Greeting wave command sent (arm task {task_id})"
        )

    def _end_session(self, reason: str) -> None:
        """End the current passenger session and clear all state."""
        self.get_logger().info(f"Ending session: {reason}")
        self.session_mgr.end_session(reason)
        self.agent_executor = None
        self._publish_state("idle")

    def _is_goodbye(self, text: str) -> bool:
        """Simple goodbye intent detection."""
        goodbye_keywords = {
            "goodbye", "bye", "thank you", "thanks", "see you",
            "au revoir", "merci", "danke", "tschüss",
            "gracias", "adiós", "adios",
            "ありがとう", "さようなら",
            "谢谢", "再见",
            "감사합니다", "안녕히",
            "धन्यवाद", "अलविदा",
            "ස්තුතියි", "ආයුබෝවන්",
            "நன்றி", "விடைபெறுகிறேன்",
        }
        text_lower = text.lower().strip()
        return any(keyword in text_lower for keyword in goodbye_keywords)

    def _get_farewell(self, lang_code: str) -> str:
        """Get a farewell message in the session language."""
        farewells = {
            "en": "It was a pleasure assisting you today. Enjoy your stay in Sri Lanka with PickMe as your mobility companion!",
            "fr": "Ce fut un plaisir de vous aider. Profitez de votre séjour au Sri Lanka avec PickMe !",
            "de": "Es war mir eine Freude, Ihnen zu helfen. Genießen Sie Ihren Aufenthalt in Sri Lanka mit PickMe!",
            "es": "Fue un placer ayudarte. ¡Disfruta tu estancia en Sri Lanka con PickMe!",
            "ja": "お手伝いできて光栄です。PickMeと一緒にスリランカでのご滞在をお楽しみください！",
            "zh": "很高兴为您服务。祝您在斯里兰卡旅途愉快，PickMe随时为您服务！",
            "ko": "도움을 드릴 수 있어서 기뻤습니다. PickMe와 함께 스리랑카에서의 체류를 즐기세요!",
            "hi": "आपकी सहायता करके खुशी हुई। PickMe के साथ श्रीलंका में अपने प्रवास का आनंद लें!",
            "si": "ඔබට සහය වීම සතුටක්. PickMe සමඟ ශ්‍රී ලංකාවේ ඔබේ සංචාරය භුක්ති විඳින්න!",
            "ta": "உங்களுக்கு உதவ மகிழ்ச்சி. PickMe உடன் இலங்கையில் உங்கள் தங்குதலை மகிழுங்கள்!",
        }
        return farewells.get(lang_code, farewells["en"])

    def destroy_node(self) -> None:
        self._running = False
        if hasattr(self, "tts"):
            self.tts.stop()
        if self._thread.is_alive():
            self._thread.join()
        if hasattr(self, 'tts'):
            self.tts.cleanup()
        super().destroy_node()


# ── Entry Point ──────────────────────────────────────────────────────

def main(args=None):
    """Voice mode — mic in, speaker out. The only public entry point."""
    rclpy.init(args=args)
    node = ConversationNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
