"""Natural multilingual speech synthesis using Microsoft Edge neural voices.

Short multi-sentence replies are synthesized as one utterance so punctuation
controls the pauses and prosody. Streamed Gemini sentences use one-segment
lookahead, allowing the next audio clip to generate while the current one is
playing instead of leaving a network-sized gap between sentences.
"""

import asyncio
import hashlib
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

try:
    import edge_tts
except ImportError:
    edge_tts = None

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


# Voice choices favor conversational/friendly voices and local pronunciation.
VOICE_MAP = {
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


@dataclass(frozen=True)
class VoiceProfile:
    """Voice and prosody settings that also identify a cached audio file."""

    voice: str
    rate: str
    pitch: str = TTS_PITCH
    volume: str = TTS_VOLUME


@dataclass(frozen=True)
class _PreparedAudio:
    path: str
    temporary: bool


class TTSEngine:
    """Friendly edge-tts speech with caching and lookahead generation."""

    def __init__(self, output_dir: Optional[str] = None) -> None:
        if edge_tts is None:
            raise ImportError(
                "edge-tts is required. Install with: pip install edge-tts"
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

    def get_voice(self, lang_code: str) -> str:
        """Look up the preferred neural voice for a language code."""
        return VOICE_MAP.get(lang_code, FALLBACK_VOICE)

    def get_profile(self, lang_code: str) -> VoiceProfile:
        """Return the friendly voice and natural pace for one language."""
        return VoiceProfile(
            voice=self.get_voice(lang_code),
            rate=LANGUAGE_RATES.get(lang_code, TTS_RATE),
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

    def _cache_path(self, text: str, profile: VoiceProfile) -> str:
        digest = hashlib.sha256(
            "|".join(
                (
                    TTS_CACHE_VERSION,
                    profile.voice,
                    profile.rate,
                    profile.pitch,
                    profile.volume,
                    text,
                )
            ).encode("utf-8")
        ).hexdigest()
        return os.path.join(self._cache_dir, f"{digest}.mp3")

    def _prepare_audio(
        self, text: str, profile: VoiceProfile, cache: bool
    ) -> _PreparedAudio:
        if cache:
            output_path = self._cache_path(text, profile)
        else:
            output_path = os.path.join(
                self._output_dir,
                f"personal-{threading.get_ident()}-{time.time_ns()}.mp3",
            )
        self._ensure_audio(text, profile, output_path)
        return _PreparedAudio(path=output_path, temporary=not cache)

    def _ensure_audio(
        self, text: str, profile: VoiceProfile, output_path: str
    ) -> None:
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"[TTS] Cache hit ({len(text)} characters)")
            return

        started = time.perf_counter()
        print(
            f"[TTS] Generating {len(text)} characters with {profile.voice} "
            f"at {profile.rate}"
        )
        partial_path = (
            f"{output_path}.{threading.get_ident()}-{time.time_ns()}.part"
        )
        try:
            asyncio.run(self._generate_audio(text, profile, partial_path))
            if not os.path.exists(partial_path) or os.path.getsize(partial_path) == 0:
                raise RuntimeError("edge-tts returned an empty audio file")
            os.replace(partial_path, output_path)
        finally:
            try:
                os.remove(partial_path)
            except OSError:
                pass
        print(f"[TTS] Audio generated in {time.perf_counter() - started:.2f}s")

    async def _generate_audio(
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
