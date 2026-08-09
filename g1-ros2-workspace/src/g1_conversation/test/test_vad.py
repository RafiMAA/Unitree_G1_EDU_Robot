"""Tests for streaming neural voice activity detection."""

import numpy as np
import pytest

import g1_conversation.vad as vad_module
from g1_conversation.vad import VAD, _SileroStreamDetector


class _FakeDetector:
    name = "fake"
    frame_size = 512

    def __init__(self, probabilities):
        self.probabilities = list(probabilities)
        self.index = 0

    def reset(self):
        self.index = 0

    def speech_probability(self, _frame_bytes, _sample_rate):
        if self.index < len(self.probabilities):
            probability = self.probabilities[self.index]
        else:
            probability = self.probabilities[-1]
        self.index += 1
        return probability


class _FakeRawInputStream:
    def __init__(self, *, blocksize, **_kwargs):
        self.blocksize = blocksize

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self, frame_size):
        assert frame_size == self.blocksize
        return bytes(frame_size * 2), False


def _make_vad(monkeypatch, probabilities, **kwargs):
    detector = _FakeDetector(probabilities)
    monkeypatch.setattr(
        VAD,
        "_create_detector",
        staticmethod(lambda **_kwargs: detector),
    )
    monkeypatch.setattr(vad_module.sd, "RawInputStream", _FakeRawInputStream)
    return VAD(backend="silero", **kwargs), detector


def test_background_reaches_prespeech_timeout(monkeypatch):
    vad, detector = _make_vad(monkeypatch, [0.20])
    events = []

    audio = vad.listen_with_timeout(
        timeout_seconds=0.20,
        status_interval_seconds=0.064,
        status_callback=events.append,
    )

    assert audio is None
    assert detector.index == 7
    assert any("no speech detected before timeout" in event for event in events)


def test_silence_ends_recording_and_trims_long_tail(monkeypatch):
    probabilities = [0.90] * 8 + [0.10] * 40
    vad, detector = _make_vad(monkeypatch, probabilities)
    events = []

    audio = vad.listen_with_timeout(
        timeout_seconds=1.0,
        status_callback=events.append,
    )

    assert audio is not None
    assert detector.index == 8 + vad.silence_threshold
    assert len(audio) / vad.sample_rate < 1.0
    assert any("end of speech detected" in event for event in events)


def test_hysteresis_keeps_weak_speech_inside_utterance(monkeypatch):
    weak_speech_frames = 20
    probabilities = (
        [0.90] * 4
        + [0.40] * weak_speech_frames
        + [0.10] * 40
    )
    vad, detector = _make_vad(monkeypatch, probabilities)

    audio = vad.listen_with_timeout(timeout_seconds=1.0)

    assert audio is not None
    assert detector.index == 4 + weak_speech_frames + vad.silence_threshold


def test_continuous_speech_is_not_cut_at_ten_seconds(monkeypatch):
    continuous_frames = 320
    probabilities = [0.90] * continuous_frames + [0.10] * 40
    vad, _detector = _make_vad(monkeypatch, probabilities)

    audio = vad.listen_with_timeout(timeout_seconds=1.0)

    assert audio is not None
    assert len(audio) / vad.sample_rate > 10.0


def test_cancellation_returns_none_before_reading(monkeypatch):
    vad, detector = _make_vad(monkeypatch, [0.90])

    audio = vad.listen_with_timeout(
        timeout_seconds=1.0,
        cancel_check=lambda: True,
    )

    assert audio is None
    assert detector.index == 0


def test_bundled_silero_model_accepts_one_frame_and_resets():
    detector = _SileroStreamDetector(16_000)
    silence = np.zeros(detector.frame_size, dtype=np.int16).tobytes()

    first_probability = detector.speech_probability(silence, 16_000)
    detector.reset()
    reset_probability = detector.speech_probability(silence, 16_000)

    assert first_probability < 0.5
    assert reset_probability == pytest.approx(first_probability)
