"""Speech-to-Text engine using OpenAI Whisper (local).

Returns both the transcribed text and the auto-detected language code
(ISO 639-1) so the conversation node can pin the session language on
the passenger's first utterance.
"""

from dataclasses import dataclass
import os
from typing import Optional

import numpy as np

# Hugging Face's optional Xet transport can stall on some networks. Standard
# HTTPS supports the same public model and is more predictable on the robot PC.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
try:
    import whisper
except ImportError:
    whisper = None

try:
    from faster_whisper import WhisperModel as FasterWhisperModel
except ImportError:
    FasterWhisperModel = None

@dataclass
class TranscriptionResult:
    """Result from a single STT inference."""

    text: str
    language: str              # ISO 639-1, e.g. "en", "fr", "si"
    confidence: float          # average log-probability (higher = better)
    is_empty: bool = False     # True if nothing meaningful was transcribed


# Whisper model sizes: tiny, base, small, medium, large
DEFAULT_MODEL = "small"
LOCAL_FASTER_WHISPER_ROOT = os.path.join(
    os.path.expanduser("~"), ".cache", "g1_conversation"
)


class STTEngine:
    """Whisper-based speech-to-text with automatic language detection.

    Usage::

        stt = STTEngine(model_name="small")
        result = stt.transcribe(audio_float32)
        print(result.text, result.language)
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: Optional[str] = None,
        backend: str = "auto",
        compute_type: str = "int8",
    ) -> None:
        if whisper is None and FasterWhisperModel is None:
            raise ImportError(
                "faster-whisper or openai-whisper is required"
            )

        self.model_name = model_name
        self.device = device or "cpu"
        self.backend = (
            "faster-whisper"
            if backend == "auto" and FasterWhisperModel is not None
            else backend
        )
        if self.backend == "auto":
            self.backend = "openai-whisper"
        if self.backend == "faster-whisper" and FasterWhisperModel is None:
            print("[STT] faster-whisper unavailable; using openai-whisper")
            self.backend = "openai-whisper"
        self.compute_type = compute_type
        self.model = None  # lazy-loaded
        self._loaded = False

    def load(self) -> None:
        """Load the Whisper model into memory.

        Called automatically on first transcribe(), but can be called
        eagerly at startup to front-load the latency.
        """
        if self._loaded:
            return
        print(
            f"[STT] Loading '{self.model_name}' with backend={self.backend}, "
            f"device={self.device} ..."
        )
        if self.backend == "faster-whisper":
            try:
                local_model = os.path.join(
                    LOCAL_FASTER_WHISPER_ROOT,
                    f"faster-whisper-{self.model_name}",
                )
                local_weights = os.path.join(local_model, "model.bin")
                local_model_ready = (
                    os.path.exists(local_weights)
                    and os.path.getsize(local_weights) > 100_000_000
                )
                model_source = local_model if local_model_ready else self.model_name
                self.model = FasterWhisperModel(
                    model_source,
                    device=self.device,
                    compute_type=self.compute_type,
                )
            except Exception as exc:
                if whisper is None:
                    raise
                print(
                    f"[STT] faster-whisper load failed ({exc}); "
                    "falling back to openai-whisper"
                )
                self.backend = "openai-whisper"
                self.model = whisper.load_model(
                    self.model_name, device=self.device
                )
        else:
            self.model = whisper.load_model(self.model_name, device=self.device)
        self._loaded = True
        print(f"[STT] Whisper model loaded ({self.backend})")

    def transcribe(
        self,
        audio: np.ndarray,
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe an audio segment.

        Parameters
        ----------
        audio : np.ndarray
            Audio samples as float32, 16 kHz mono, range [-1, 1].
        language : str, optional
            Force a specific language instead of auto-detecting.
            Use the ISO 639-1 code (e.g. "en", "fr", "si").

        Returns
        -------
        TranscriptionResult
            Contains text, detected language, and confidence score.
        """
        self.load()

        if self.backend == "faster-whisper":
            segments_iter, info = self.model.transcribe(
                audio,
                language=language,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            segments = list(segments_iter)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            avg_logprob = (
                sum(segment.avg_logprob for segment in segments) / len(segments)
                if segments else -1.0
            )
            no_speech_prob = max(
                (segment.no_speech_prob for segment in segments), default=1.0
            )
            is_empty = len(text) < 2 or (
                no_speech_prob > 0.7 and avg_logprob < -1.0
            )
            return TranscriptionResult(
                text=text,
                language=info.language or language or "en",
                confidence=avg_logprob,
                is_empty=is_empty,
            )

        # Whisper expects float32, 16 kHz
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Pad or trim to Whisper's expected length
        audio = whisper.pad_or_trim(audio)

        # Build transcription options
        options = {
            "fp16": False,  # Use fp32 for CPU compatibility
        }
        if language is not None:
            options["language"] = language

        result = self.model.transcribe(audio, **options)

        text = result.get("text", "").strip()
        detected_lang = result.get("language", "en")

        # Compute average log probability as a confidence proxy
        segments = result.get("segments", [])
        if segments:
            avg_logprob = sum(s.get("avg_logprob", -1.0) for s in segments) / len(
                segments
            )
            # A threshold around -0.8 to -1.0 usually means noise
            no_speech_prob = max(s.get("no_speech_prob", 0.0) for s in segments)
        else:
            avg_logprob = -1.0
            no_speech_prob = 1.0

        # Filter out noise / empty transcriptions
        # Do not discard useful speech merely because one confidence signal is
        # weak. Treat it as noise only when both Whisper signals agree.
        confidence_indicates_noise = (
            no_speech_prob > 0.7 and avg_logprob < -1.0
        )
        is_empty = len(text) < 2 or confidence_indicates_noise

        return TranscriptionResult(
            text=text,
            language=detected_lang,
            confidence=avg_logprob,
            is_empty=is_empty,
        )

    def detect_language(self, audio: np.ndarray) -> str:
        """Detect the language of an audio segment without full transcription.

        Faster than full transcribe() — useful for initial language detection.

        Returns
        -------
        str
            ISO 639-1 language code.
        """
        self.load()

        if self.backend == "faster-whisper":
            _, info = self.model.transcribe(
                audio,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            return info.language or "en"

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        audio = whisper.pad_or_trim(audio)
        mel = whisper.log_mel_spectrogram(audio).to(self.model.device)
        _, probs = self.model.detect_language(mel)

        detected = max(probs, key=probs.get)
        return detected
