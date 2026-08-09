"""Streaming voice activity detection for microphone utterances.

Silero VAD is the default because the robot's internal microphone can contain
high-energy mechanical/electrical noise that WebRTC VAD mistakes for speech.
The Silero v6 ONNX model is already bundled with ``faster-whisper``.  WebRTC
remains available as an explicit fallback backend.
"""

import collections
import math
import warnings
from typing import Callable, Optional

import numpy as np

try:
    import webrtcvad
except ImportError:
    webrtcvad = None

try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None


SAMPLE_RATE = 16_000
SILERO_FRAME_SIZE = 512       # Required by Silero v6 at 16 kHz (32 ms)
WEBRTC_FRAME_DURATION_MS = 30
AGGRESSIVENESS = 3

# Start only after sustained neural speech, but keep the window short enough
# to capture brief replies such as a passenger's name.
VOICED_FRAMES_TO_START = 4
START_WINDOW_FRAMES = 6

SPEECH_PROBABILITY_THRESHOLD = 0.50
END_PROBABILITY_THRESHOLD = 0.35
SILENCE_DURATION_MS = 900.0
TRAILING_SILENCE_MS = 160.0
MAX_RECORDING_SECONDS = None  # Speech is never cut at an arbitrary duration.
LISTEN_STATUS_INTERVAL_SECONDS = 4.0


class _SileroStreamDetector:
    """Stateful, frame-by-frame adapter for faster-whisper's Silero model."""

    name = "silero"
    frame_size = SILERO_FRAME_SIZE
    _context_size = 64

    def __init__(self, sample_rate: int) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError("Silero streaming VAD requires a 16000 Hz sample rate")

        try:
            from faster_whisper.vad import get_vad_model
        except ImportError as exc:
            raise RuntimeError(
                "Silero VAD requires faster-whisper and onnxruntime"
            ) from exc

        model = get_vad_model()
        self._session = model.session
        input_names = {item.name for item in self._session.get_inputs()}
        expected_inputs = {"input", "h", "c"}
        if not expected_inputs.issubset(input_names):
            raise RuntimeError(
                "The bundled faster-whisper Silero model has an unsupported "
                f"input layout: {sorted(input_names)}"
            )
        self.reset()

    def reset(self) -> None:
        """Clear recurrent state before a new microphone turn."""
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self._context_size), dtype=np.float32)

    def speech_probability(self, frame_bytes: bytes, sample_rate: int) -> float:
        if sample_rate != SAMPLE_RATE:
            raise ValueError("Silero streaming VAD requires a 16000 Hz sample rate")

        audio_int16 = np.frombuffer(frame_bytes, dtype=np.int16)
        if len(audio_int16) != self.frame_size:
            raise ValueError(
                f"Silero VAD needs {self.frame_size} samples per frame; "
                f"received {len(audio_int16)}"
            )

        audio = audio_int16.astype(np.float32) / 32768.0
        model_input = np.concatenate((self._context, audio[None, :]), axis=1)
        output, self._h, self._c = self._session.run(
            None,
            {
                "input": model_input,
                "h": self._h,
                "c": self._c,
            },
        )
        self._context = audio[-self._context_size:][None, :]
        return float(output.reshape(-1)[0])


class _WebRTCDetector:
    """Probability-shaped adapter around the binary WebRTC classifier."""

    name = "webrtc"

    def __init__(self, sample_rate: int, aggressiveness: int) -> None:
        if webrtcvad is None:
            raise ImportError(
                "webrtcvad is required for the WebRTC VAD backend. "
                "Install with: pip install webrtcvad"
            )
        self.frame_size = int(
            sample_rate * WEBRTC_FRAME_DURATION_MS / 1000
        )
        self._vad = webrtcvad.Vad(aggressiveness)

    def reset(self) -> None:
        return None

    def speech_probability(self, frame_bytes: bytes, sample_rate: int) -> float:
        return 1.0 if self._vad.is_speech(frame_bytes, sample_rate) else 0.0


class VAD:
    """Capture one complete passenger utterance using streaming neural VAD."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        backend: str = "silero",
        aggressiveness: int = AGGRESSIVENESS,
        probability_threshold: float = SPEECH_PROBABILITY_THRESHOLD,
        end_probability_threshold: float = END_PROBABILITY_THRESHOLD,
        voiced_threshold: int = VOICED_FRAMES_TO_START,
        start_window_frames: int = START_WINDOW_FRAMES,
        silence_duration_ms: float = SILENCE_DURATION_MS,
        max_seconds: Optional[float] = MAX_RECORDING_SECONDS,
    ) -> None:
        if sd is None:
            raise ImportError(
                "sounddevice is required for audio capture. "
                "Install with: pip install sounddevice"
            )
        if not 0.0 < probability_threshold <= 1.0:
            raise ValueError("probability_threshold must be in (0, 1]")
        if not 0.0 <= end_probability_threshold <= probability_threshold:
            raise ValueError(
                "end_probability_threshold must be between 0 and the start threshold"
            )
        if voiced_threshold < 1 or start_window_frames < voiced_threshold:
            raise ValueError(
                "start_window_frames must be at least as large as voiced_threshold"
            )
        if silence_duration_ms <= 0:
            raise ValueError("silence_duration_ms must be positive")

        self.sample_rate = sample_rate
        self._detector = self._create_detector(
            backend=backend,
            sample_rate=sample_rate,
            aggressiveness=aggressiveness,
        )
        self.backend = self._detector.name
        self.frame_size = self._detector.frame_size
        self.frame_duration_seconds = self.frame_size / self.sample_rate

        self.probability_threshold = probability_threshold
        self.end_probability_threshold = end_probability_threshold
        self.voiced_threshold = voiced_threshold
        self.start_window_frames = start_window_frames
        self.silence_threshold = max(
            1,
            math.ceil(
                silence_duration_ms / (self.frame_duration_seconds * 1000)
            ),
        )
        self.trailing_silence_frames = min(
            self.silence_threshold,
            max(
                1,
                math.ceil(
                    TRAILING_SILENCE_MS
                    / (self.frame_duration_seconds * 1000)
                ),
            ),
        )
        self.max_frames = (
            math.ceil(max_seconds / self.frame_duration_seconds)
            if max_seconds is not None else None
        )

    @staticmethod
    def _create_detector(backend: str, sample_rate: int, aggressiveness: int):
        normalized_backend = str(backend).strip().lower()
        if normalized_backend == "silero":
            # The default fails clearly if its model cannot load. Silently
            # falling back would restore the known endless-recording bug.
            return _SileroStreamDetector(sample_rate)
        if normalized_backend == "webrtc":
            return _WebRTCDetector(sample_rate, aggressiveness)
        if normalized_backend == "auto":
            try:
                return _SileroStreamDetector(sample_rate)
            except Exception as exc:
                warnings.warn(
                    f"Silero VAD could not load ({exc}); using WebRTC fallback",
                    RuntimeWarning,
                    stacklevel=2,
                )
                return _WebRTCDetector(sample_rate, aggressiveness)
        raise ValueError(
            f"Unsupported VAD backend '{backend}'. Use 'silero', 'webrtc', or 'auto'."
        )

    @property
    def silence_duration_seconds(self) -> float:
        return self.silence_threshold * self.frame_duration_seconds

    def listen(self) -> np.ndarray:
        """Block until speech starts and ends, then return 16 kHz float audio."""
        audio = self._capture(timeout_seconds=None)
        # No timeout or cancellation is supplied, so _capture returns audio.
        assert audio is not None
        return audio

    def listen_with_timeout(
        self,
        timeout_seconds: float = 15.0,
        status_interval_seconds: float = LISTEN_STATUS_INTERVAL_SECONDS,
        status_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> np.ndarray | None:
        """Wait for speech, then record until neural VAD observes silence.

        ``timeout_seconds`` applies only before speech begins. Once speech has
        started there is deliberately no fixed time limit unless ``max_seconds``
        was explicitly supplied to the constructor.
        """
        return self._capture(
            timeout_seconds=timeout_seconds,
            status_interval_seconds=status_interval_seconds,
            status_callback=status_callback,
            cancel_check=cancel_check,
        )

    def _capture(
        self,
        timeout_seconds: Optional[float],
        status_interval_seconds: float = LISTEN_STATUS_INTERVAL_SECONDS,
        status_callback: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> np.ndarray | None:
        ring_buffer = collections.deque(maxlen=self.start_window_frames)
        recording_frames: list[bytes] = []
        is_recording = False
        silent_count = 0
        total_frames = 0
        timeout_frames = (
            max(1, math.ceil(timeout_seconds / self.frame_duration_seconds))
            if timeout_seconds is not None else None
        )
        status_interval_frames = max(
            1,
            math.ceil(status_interval_seconds / self.frame_duration_seconds),
        )
        next_wait_status_frame = status_interval_frames
        next_recording_status_frame = status_interval_frames

        self._detector.reset()
        if status_callback:
            timeout_message = (
                f"waiting up to {timeout_seconds:.1f}s for speech"
                if timeout_seconds is not None else "waiting for speech"
            )
            status_callback(
                f"microphone opened with {self.backend} VAD "
                f"(start>={self.probability_threshold:.2f}, "
                f"end<{self.end_probability_threshold:.2f}, "
                f"silence={self.silence_duration_seconds:.2f}s); "
                f"{timeout_message}"
            )

        with sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.frame_size,
            dtype="int16",
            channels=1,
        ) as stream:
            overflow_reported = False
            while True:
                if cancel_check and cancel_check():
                    if status_callback:
                        status_callback("microphone capture cancelled")
                    return None

                frame_data, overflowed = stream.read(self.frame_size)
                if overflowed and status_callback and not overflow_reported:
                    overflow_reported = True
                    status_callback("microphone input overflow detected")

                frame_bytes = bytes(frame_data)
                total_frames += 1
                probability = self._detector.speech_probability(
                    frame_bytes, self.sample_rate
                )

                if not is_recording:
                    if timeout_frames is not None and total_frames >= timeout_frames:
                        if status_callback:
                            status_callback("no speech detected before timeout")
                        return None

                    if total_frames >= next_wait_status_frame:
                        if status_callback:
                            waited = total_frames * self.frame_duration_seconds
                            status_callback(
                                f"no speech after {waited:.1f}s "
                                f"(latest VAD probability={probability:.3f}); "
                                "continuing to listen"
                            )
                        next_wait_status_frame += status_interval_frames

                    is_speech = probability >= self.probability_threshold
                    ring_buffer.append((frame_bytes, is_speech, probability))
                    voiced_count = sum(1 for _, speech, _ in ring_buffer if speech)

                    if voiced_count >= self.voiced_threshold:
                        is_recording = True
                        silent_count = 0
                        recording_frames.extend(frame for frame, _, _ in ring_buffer)
                        peak_probability = max(
                            frame_probability
                            for _, _, frame_probability in ring_buffer
                        )
                        ring_buffer.clear()
                        if status_callback:
                            waited = total_frames * self.frame_duration_seconds
                            status_callback(
                                f"speech detected after {waited:.2f}s "
                                f"(VAD probability={peak_probability:.3f}); recording"
                            )
                else:
                    recording_frames.append(frame_bytes)

                    # Hysteresis prevents a weak syllable between 0.35 and
                    # 0.50 from ending an utterance that has already started.
                    if probability < self.end_probability_threshold:
                        silent_count += 1
                    else:
                        silent_count = 0

                    if len(recording_frames) >= next_recording_status_frame:
                        if status_callback:
                            duration = (
                                len(recording_frames) * self.frame_duration_seconds
                            )
                            status_callback(
                                f"speech recording continues ({duration:.1f}s captured, "
                                f"latest VAD probability={probability:.3f})"
                            )
                        next_recording_status_frame += status_interval_frames

                    if silent_count >= self.silence_threshold:
                        trim_count = (
                            silent_count - self.trailing_silence_frames
                        )
                        if trim_count > 0:
                            del recording_frames[-trim_count:]
                        if status_callback:
                            status_callback(
                                "end of speech detected after "
                                f"{self.silence_duration_seconds:.2f}s of silence"
                            )
                        break

                    if (
                        self.max_frames is not None
                        and len(recording_frames) >= self.max_frames
                    ):
                        if status_callback:
                            status_callback("maximum recording duration reached")
                        break

        raw = b"".join(recording_frames)
        audio_int16 = np.frombuffer(raw, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        if status_callback:
            duration = len(audio_float32) / self.sample_rate
            status_callback(f"captured {duration:.2f}s of audio")
        return audio_float32
