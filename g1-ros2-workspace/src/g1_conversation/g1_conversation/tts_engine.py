"""Natural multilingual speech synthesis with local-first latency.

Two backends are supported:
    * **Piper TTS** (default) — runs entirely on the robot's CPU with ~50 ms
      latency per sentence.  No network access required.
    * **edge-tts** — Microsoft Edge neural voices synthesized in the cloud.
      Higher quality, but each sentence costs a ~1 s network round-trip.

Short multi-sentence replies are synthesized as one utterance so punctuation
controls the pauses and prosody. Streamed Gemini sentences use one-segment
lookahead, allowing the next audio clip to generate while the current one is
playing instead of leaving a network-sized gap between sentences.
"""

import asyncio
import hashlib
import io
import os
import re
import struct
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    from piper import PiperVoice
    _PIPER_AVAILABLE = True
except ImportError:
    PiperVoice = None
    _PIPER_AVAILABLE = False

try:
    import pygame

    pygame.mixer.init()
    _PYGAME_AVAILABLE = True
except Exception:
    _PYGAME_AVAILABLE = False

try:
    import numpy as np
    import sounddevice as sd

    _SD_AVAILABLE = True
except (ImportError, OSError):
    _SD_AVAILABLE = False


# ── Voice maps ───────────────────────────────────────────────────────────

# Edge-tts cloud voices — conversational/friendly voices per language.
EDGE_VOICE_MAP = {
    "en": "en-US-AvaMultilingualNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ar": "ar-SA-ZariyahNeural",
    "hi": "hi-IN-SwaraNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "nl": "nl-NL-ColetteNeural",
    "sv": "sv-SE-SofieNeural",
    "da": "da-DK-ChristelNeural",
    "no": "nb-NO-PernilleNeural",
    "fi": "fi-FI-NooraNeural",
    "pl": "pl-PL-AgnieszkaNeural",
    "tr": "tr-TR-EmelNeural",
    "th": "th-TH-PremwadeeNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "id": "id-ID-GadisNeural",
    "ms": "ms-MY-YasminNeural",
    "si": "si-LK-ThiliniNeural",
    "ta": "ta-LK-SaranyaNeural",
}

# Piper local voices — maps language code to a Piper voice model identifier.
# These are downloaded automatically on first use to ~/.g1_conversation/piper_models/
PIPER_VOICE_MAP = {
    "en": "en_US-amy-medium",
    "fr": "fr_FR-siwis-medium",
    "de": "de_DE-thorsten-medium",
    "es": "es_ES-sharvard-medium",
    "it": "it_IT-riccardo-x_low",
    "pt": "pt_BR-faber-medium",
    "zh": "zh_CN-huayan-medium",
    "ru": "ru_RU-irina-medium",
    "nl": "nl_NL-mls-medium",
    "pl": "pl_PL-darkman-medium",
    "tr": "tr_TR-dfki-medium",
    "vi": "vi_VN-vivos-x_low",
}

# Backwards-compatible alias
VOICE_MAP = EDGE_VOICE_MAP
FALLBACK_VOICE = VOICE_MAP["en"]

TTS_MAX_ATTEMPTS = 3
TTS_RETRY_DELAY_SECONDS = 1.0
TTS_MAX_CHUNK_CHARACTERS = 260
TTS_CACHE_VERSION = "human-v2"
TTS_RATE = "+8%"
TTS_PITCH = "+0Hz"
TTS_VOLUME = "+0%"
PYGAME_POLL_INTERVAL_MS = 20

# Sinhala and Sri Lankan Tamil are clearest at their voices' native pace.
LANGUAGE_RATES = {
    "si": "+0%",
    "ta": "+0%",
}

PIPER_MODELS_DIR = os.path.join(
    os.path.expanduser("~"), ".g1_conversation", "piper_models"
)


@dataclass(frozen=True)
class VoiceProfile:
    """Voice and prosody settings that also identify a cached audio file."""

    voice: str
    rate: str
    pitch: str = TTS_PITCH
    volume: str = TTS_VOLUME
    backend: str = "edge"   # "piper" or "edge"


@dataclass(frozen=True)
class _PreparedAudio:
    path: str
    temporary: bool


# ── Piper model management ──────────────────────────────────────────────

def _piper_model_path(voice_id: str) -> str:
    """Return the local filesystem path for a Piper ONNX model."""
    return os.path.join(PIPER_MODELS_DIR, f"{voice_id}.onnx")


def _piper_model_ready(voice_id: str) -> bool:
    """Check if a Piper model is downloaded and ready."""
    onnx_path = _piper_model_path(voice_id)
    json_path = onnx_path + ".json"
    return (
        os.path.exists(onnx_path)
        and os.path.getsize(onnx_path) > 1_000_000   # Must be >1 MB
        and os.path.exists(json_path)
    )


def _download_piper_model(voice_id: str) -> None:
    """Download a Piper voice model using the piper download helper."""
    os.makedirs(PIPER_MODELS_DIR, exist_ok=True)
    onnx_path = _piper_model_path(voice_id)

    if _piper_model_ready(voice_id):
        return

    print(f"[TTS/Piper] Downloading voice model '{voice_id}' ...")
    try:
        # Use piper's built-in download mechanism
        from piper.download import ensure_voice_exists, find_voice, get_voices

        data_dirs = [PIPER_MODELS_DIR]
        voices_info = get_voices(PIPER_MODELS_DIR, update_voices=True)
        ensure_voice_exists(voice_id, data_dirs, PIPER_MODELS_DIR, voices_info)
        print(f"[TTS/Piper] Voice model '{voice_id}' ready at {PIPER_MODELS_DIR}")
    except Exception as exc:
        print(f"[TTS/Piper] Download via piper.download failed: {exc}")
        # Fallback: try huggingface_hub download
        try:
            _download_piper_model_hf(voice_id, onnx_path)
        except Exception as exc2:
            raise RuntimeError(
                f"Could not download Piper model '{voice_id}': {exc2}"
            ) from exc


def _download_piper_model_hf(voice_id: str, onnx_path: str) -> None:
    """Fallback download from Hugging Face Hub."""
    from urllib.request import urlretrieve

    # Piper models are hosted at rhasspy/piper-voices on HF
    # URL pattern: https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/<lang>/<country>/<voice>/<quality>/
    parts = voice_id.split("-")
    lang_country = parts[0]      # e.g. "en_US"
    lang = lang_country.split("_")[0]  # e.g. "en"
    country = lang_country.split("_")[1] if "_" in lang_country else lang_country

    base_url = (
        f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
        f"{lang}/{lang_country}/{parts[1]}/{parts[2]}"
    )

    onnx_url = f"{base_url}/{voice_id}.onnx"
    json_url = f"{base_url}/{voice_id}.onnx.json"

    print(f"[TTS/Piper] Downloading from Hugging Face: {onnx_url}")
    urlretrieve(onnx_url, onnx_path)
    urlretrieve(json_url, onnx_path + ".json")
    print(f"[TTS/Piper] Downloaded '{voice_id}' to {PIPER_MODELS_DIR}")


# ── TTS Engine ──────────────────────────────────────────────────────────

class TTSEngine:
    """Friendly speech synthesis with local-first latency and caching.

    Backends (selected via ``tts_backend`` parameter):
        ``"auto"``  — use Piper if available, else edge-tts  (default)
        ``"piper"`` — Piper TTS only (local, ~50 ms/sentence)
        ``"edge"``  — edge-tts only  (cloud, ~1 s/sentence)
    """

    def __init__(
        self,
        output_dir: Optional[str] = None,
        backend: str = "auto",
    ) -> None:
        # Resolve backend preference
        if backend == "auto":
            if _PIPER_AVAILABLE:
                self._preferred_backend = "piper"
                print("[TTS] Auto-selected Piper TTS (local, low-latency)")
            elif edge_tts is not None:
                self._preferred_backend = "edge"
                print("[TTS] Auto-selected edge-tts (cloud)")
            else:
                raise ImportError(
                    "No TTS backend available. Install piper-tts or edge-tts."
                )
        elif backend == "piper":
            if not _PIPER_AVAILABLE:
                raise ImportError(
                    "piper-tts is required. Install with: pip install piper-tts"
                )
            self._preferred_backend = "piper"
        elif backend == "edge":
            if edge_tts is None:
                raise ImportError(
                    "edge-tts is required. Install with: pip install edge-tts"
                )
            self._preferred_backend = "edge"
        else:
            raise ValueError(
                f"Unknown TTS backend '{backend}'. Use 'auto', 'piper', or 'edge'."
            )

        if not _PYGAME_AVAILABLE and not _SD_AVAILABLE:
            raise ImportError(
                "An audio playback library is required. "
                "Install pygame (pip install pygame) or sounddevice."
            )

        self._output_dir = output_dir or tempfile.mkdtemp(prefix="g1_tts_")
        self._cache_dir = os.path.join(
            os.path.expanduser("~"), ".g1_conversation", "tts_cache"
        )
        os.makedirs(self._cache_dir, exist_ok=True)
        self._stop_requested = threading.Event()
        self._speak_lock = threading.Lock()

        # Loaded Piper voice models (cached across turns)
        self._piper_voices: dict[str, "PiperVoice"] = {}
        self._piper_lock = threading.Lock()

    # ── Public API (unchanged interface) ─────────────────────────────

    def get_voice(self, lang_code: str) -> str:
        """Look up the preferred neural voice for a language code."""
        return EDGE_VOICE_MAP.get(lang_code, FALLBACK_VOICE)

    def get_profile(self, lang_code: str) -> VoiceProfile:
        """Return the friendly voice and natural pace for one language."""
        backend = self._select_backend(lang_code)
        if backend == "piper":
            voice_id = PIPER_VOICE_MAP.get(lang_code, PIPER_VOICE_MAP.get("en", "en_US-amy-medium"))
            return VoiceProfile(
                voice=voice_id,
                rate=LANGUAGE_RATES.get(lang_code, TTS_RATE),
                backend="piper",
            )
        return VoiceProfile(
            voice=self.get_voice(lang_code),
            rate=LANGUAGE_RATES.get(lang_code, TTS_RATE),
            backend="edge",
        )

    def speak(
        self, text: str, lang_code: str = "en", cache: bool = True
    ) -> None:
        """Speak one complete reply with native punctuation-based pauses."""
        self.speak_sequence((text,), lang_code=lang_code, cache=cache)

    def speak_sequence(
        self,
        texts: Iterable[str],
        lang_code: str = "en",
        cache: bool = True,
    ) -> None:
        """Speak streamed text segments in order with one-segment lookahead.

        The next text may arrive from Gemini while the current audio is playing.
        Its synthesis also happens concurrently with playback, removing the
        one-second generation gaps previously heard between streamed sentences.
        """
        profile = self.get_profile(lang_code)
        with self._speak_lock:
            self._stop_requested.clear()
            self._speak_sequence_locked(
                self._iter_chunks(texts), profile=profile, cache=cache
            )

    # ── Backend Selection ────────────────────────────────────────────

    def _select_backend(self, lang_code: str) -> str:
        """Pick the best available backend for a given language."""
        if self._preferred_backend == "piper":
            if lang_code in PIPER_VOICE_MAP:
                return "piper"
            # Language not supported by Piper — fall back to edge-tts
            if edge_tts is not None:
                print(
                    f"[TTS] Piper has no voice for '{lang_code}'; "
                    "falling back to edge-tts"
                )
                return "edge"
            # No edge-tts either — use English Piper voice as last resort
            print(
                f"[TTS] No Piper voice for '{lang_code}' and edge-tts unavailable; "
                "using English Piper voice"
            )
            return "piper"
        return self._preferred_backend

    # ── Piper Voice Loading ──────────────────────────────────────────

    def _get_piper_voice(self, voice_id: str) -> "PiperVoice":
        """Load a Piper voice, downloading the model if needed.

        Voices are cached in memory for reuse across conversation turns.
        """
        with self._piper_lock:
            if voice_id in self._piper_voices:
                return self._piper_voices[voice_id]

            # Download if needed
            if not _piper_model_ready(voice_id):
                _download_piper_model(voice_id)

            onnx_path = _piper_model_path(voice_id)
            if not os.path.exists(onnx_path):
                # Try to find the model in a subdirectory (piper.download layout)
                for root, _dirs, files in os.walk(PIPER_MODELS_DIR):
                    for f in files:
                        if f == f"{voice_id}.onnx":
                            onnx_path = os.path.join(root, f)
                            break

            print(f"[TTS/Piper] Loading voice model: {onnx_path}")
            voice = PiperVoice.load(onnx_path)
            self._piper_voices[voice_id] = voice
            print(f"[TTS/Piper] Voice '{voice_id}' loaded (sample_rate={voice.config.sample_rate})")
            return voice

    # ── Sequence Playback (shared logic) ─────────────────────────────

    def _speak_sequence_locked(
        self,
        chunks: Iterator[str],
        profile: VoiceProfile,
        cache: bool,
    ) -> None:
        total_started = time.perf_counter()
        playback_seconds = 0.0
        segment_index = 1

        try:
            first_text = next(chunks)
        except StopIteration:
            return

        try:
            current = self._prepare_audio(first_text, profile, cache)
        except Exception as exc:
            print(f"[TTS] Audio generation failed; continuing silently: {exc}")
            return

        while current is not None and not self._stop_requested.is_set():
            next_result = {}

            def prepare_next() -> None:
                try:
                    next_text = next(chunks)
                except StopIteration:
                    next_result["finished"] = True
                    return

                if self._stop_requested.is_set():
                    next_result["finished"] = True
                    return
                try:
                    next_result["audio"] = self._prepare_audio(
                        next_text, profile, cache
                    )
                except Exception as exc:
                    next_result["error"] = exc

            # This worker can wait for Gemini and synthesize its next sentence
            # without delaying playback of the current sentence.
            prefetch = threading.Thread(target=prepare_next, daemon=True)
            prefetch.start()

            playback_started = time.perf_counter()
            print(f"[TTS] Playback segment {segment_index} started")
            self._play_audio(current.path)
            playback_seconds += time.perf_counter() - playback_started
            self._remove_temporary(current)

            while prefetch.is_alive() and not self._stop_requested.is_set():
                prefetch.join(timeout=0.05)

            if self._stop_requested.is_set():
                prefetch.join(timeout=0.25)
                prepared = next_result.get("audio")
                if prepared is not None:
                    self._remove_temporary(prepared)
                break

            if "error" in next_result:
                print(
                    "[TTS] Next segment generation failed: "
                    f"{next_result['error']}"
                )
                break

            current = next_result.get("audio")
            segment_index += 1

        total_seconds = time.perf_counter() - total_started
        print(
            f"[TTS] Playback finished in {playback_seconds:.2f}s "
            f"(total TTS={total_seconds:.2f}s)"
        )

    def _iter_chunks(self, texts: Iterable[str]) -> Iterator[str]:
        for text in texts:
            if self._stop_requested.is_set():
                return
            yield from self._chunk_text(text)

    @classmethod
    def _chunk_text(
        cls,
        text: str,
        max_characters: int = TTS_MAX_CHUNK_CHARACTERS,
    ) -> list[str]:
        """Group sentences into natural utterances below the service limit."""
        normalized = " ".join(text.strip().split())
        if not normalized:
            return []
        if max_characters < 20:
            raise ValueError("max_characters must be at least 20")

        units = []
        for sentence in cls._split_sentences(normalized):
            remaining = sentence
            while len(remaining) > max_characters:
                split_at = remaining.rfind(" ", 0, max_characters + 1)
                if split_at <= 0:
                    split_at = max_characters
                units.append(remaining[:split_at].strip())
                remaining = remaining[split_at:].strip()
            if remaining:
                units.append(remaining)

        chunks = []
        current = ""
        for unit in units:
            combined = f"{current} {unit}".strip()
            if current and len(combined) > max_characters:
                chunks.append(current)
                current = unit
            else:
                current = combined
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text only at explicit sentence-ending punctuation."""
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[.!?。！？])\s+", text.strip())
            if part.strip()
        ]
        return sentences or [text.strip()]

    # ── Audio Preparation (routing) ──────────────────────────────────

    def _cache_path(self, text: str, profile: VoiceProfile) -> str:
        digest = hashlib.sha256(
            "|".join(
                (
                    TTS_CACHE_VERSION,
                    profile.backend,
                    profile.voice,
                    profile.rate,
                    profile.pitch,
                    profile.volume,
                    text,
                )
            ).encode("utf-8")
        ).hexdigest()
        ext = ".wav" if profile.backend == "piper" else ".mp3"
        return os.path.join(self._cache_dir, f"{digest}{ext}")

    def _prepare_audio(
        self, text: str, profile: VoiceProfile, cache: bool
    ) -> _PreparedAudio:
        if cache:
            output_path = self._cache_path(text, profile)
        else:
            ext = ".wav" if profile.backend == "piper" else ".mp3"
            output_path = os.path.join(
                self._output_dir,
                f"personal-{threading.get_ident()}-{time.time_ns()}{ext}",
            )
        self._ensure_audio(text, profile, output_path)
        return _PreparedAudio(path=output_path, temporary=not cache)

    def _ensure_audio(
        self, text: str, profile: VoiceProfile, output_path: str
    ) -> None:
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"[TTS] Cache hit ({len(text)} characters, {profile.backend})")
            return

        started = time.perf_counter()
        print(
            f"[TTS] Generating {len(text)} characters with "
            f"{profile.backend}:{profile.voice} at {profile.rate}"
        )

        if profile.backend == "piper":
            self._generate_piper(text, profile, output_path)
        else:
            self._generate_edge(text, profile, output_path)

        elapsed = time.perf_counter() - started
        print(f"[TTS] Audio generated in {elapsed:.3f}s ({profile.backend})")

    # ── Piper TTS Generation ─────────────────────────────────────────

    def _generate_piper(
        self, text: str, profile: VoiceProfile, output_path: str
    ) -> None:
        """Generate audio locally using Piper TTS."""
        voice = self._get_piper_voice(profile.voice)

        partial_path = (
            f"{output_path}.{threading.get_ident()}-{time.time_ns()}.part"
        )
        try:
            with wave.open(partial_path, "wb") as wav_file:
                voice.synthesize(text, wav_file)

            if not os.path.exists(partial_path) or os.path.getsize(partial_path) == 0:
                raise RuntimeError("Piper returned an empty audio file")
            os.replace(partial_path, output_path)
        finally:
            try:
                os.remove(partial_path)
            except OSError:
                pass

    # ── Edge-tts Generation ──────────────────────────────────────────

    def _generate_edge(
        self, text: str, profile: VoiceProfile, output_path: str
    ) -> None:
        """Generate audio via Microsoft Edge neural TTS (network call)."""
        partial_path = (
            f"{output_path}.{threading.get_ident()}-{time.time_ns()}.part"
        )
        try:
            asyncio.run(self._generate_edge_async(text, profile, partial_path))
            if not os.path.exists(partial_path) or os.path.getsize(partial_path) == 0:
                raise RuntimeError("edge-tts returned an empty audio file")
            os.replace(partial_path, output_path)
        finally:
            try:
                os.remove(partial_path)
            except OSError:
                pass

    async def _generate_edge_async(
        self, text: str, profile: VoiceProfile, output_path: str
    ) -> None:
        """Generate one MP3 with retry and profile-specific prosody."""
        for attempt in range(1, TTS_MAX_ATTEMPTS + 1):
            try:
                communicate = edge_tts.Communicate(
                    text,
                    profile.voice,
                    rate=profile.rate,
                    volume=profile.volume,
                    pitch=profile.pitch,
                )
                await communicate.save(output_path)
                return
            except Exception:
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                if attempt == TTS_MAX_ATTEMPTS:
                    raise
                print(
                    f"[TTS] Connection failed (attempt {attempt}/"
                    f"{TTS_MAX_ATTEMPTS}); retrying..."
                )
                await asyncio.sleep(TTS_RETRY_DELAY_SECONDS)

    # ── Audio Playback ───────────────────────────────────────────────

    @staticmethod
    def _remove_temporary(prepared: _PreparedAudio) -> None:
        if not prepared.temporary:
            return
        try:
            os.remove(prepared.path)
        except OSError:
            pass

    def _play_audio(self, path: str) -> None:
        """Play an audio file through the default speaker."""
        if _PYGAME_AVAILABLE:
            self._play_with_pygame(path)
        elif _SD_AVAILABLE:
            self._play_with_sounddevice(path)
        else:
            print(f"[TTS] Cannot play audio — no playback library available: {path}")

    def _play_with_pygame(self, path: str) -> None:
        """Play audio with responsive interruption and minimal seam delay."""
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if self._stop_requested.is_set():
                    pygame.mixer.music.stop()
                    break
                pygame.time.wait(PYGAME_POLL_INTERVAL_MS)
        except Exception as exc:
            print(f"[TTS] Pygame playback error: {exc}")

    def _play_with_sounddevice(self, path: str) -> None:
        """Play audio using sounddevice (requires soundfile)."""
        try:
            import soundfile as sf

            data, samplerate = sf.read(path)
            sd.play(data, samplerate)
            while sd.get_stream().active and not self._stop_requested.wait(0.02):
                pass
            if self._stop_requested.is_set():
                sd.stop()
        except Exception as exc:
            print(f"[TTS] Sounddevice playback error: {exc}")

    # ── Convenience ──────────────────────────────────────────────────

    def speak_unsupported_language_fallback(self) -> None:
        """Speak the standard unsupported-language fallback message."""
        self.speak(
            "I don't speak your preferred language yet, "
            "but we can continue the conversation in English.",
            lang_code="en",
        )

    def cleanup(self) -> None:
        """Remove temporary audio files while retaining the persistent cache."""
        try:
            for filename in os.listdir(self._output_dir):
                os.remove(os.path.join(self._output_dir, filename))
            os.rmdir(self._output_dir)
        except OSError:
            pass

    def stop(self) -> None:
        """Interrupt local audio playback during node shutdown."""
        self._stop_requested.set()
        if _PYGAME_AVAILABLE:
            pygame.mixer.music.stop()
        if _SD_AVAILABLE:
            sd.stop()
