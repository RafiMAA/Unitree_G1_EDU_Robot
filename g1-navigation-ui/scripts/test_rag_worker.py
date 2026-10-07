"""Offline browser-audio/session regression checks (no microphone/cloud requests)."""
import base64
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np
from rag_worker import Runtime, decode_recording
from g1_conversation.vad import trim_browser_recording
from g1_conversation.tts_engine import TTSEngine


class BrowserConversationTests(unittest.TestCase):
    def runtime(self):
        runtime = Runtime()
        runtime.ready = True
        runtime.languages = {'en', 'si'}
        runtime.create_agent = Mock(side_effect=lambda **kwargs: Mock(invoke=Mock(return_value={'output': 'Airport answer'})))
        return runtime

    @patch.dict(os.environ, {'GOOGLE_API_KEY': 'offline-test-key'})
    def test_sessions_are_isolated_reused_and_cleared(self):
        runtime = self.runtime()
        turn = {'session_id': 'phone', 'text': 'Baggage claim?', 'speak': False}
        result = runtime.turn(turn)
        self.assertEqual(result['answer'], 'Airport answer')
        runtime.turn(turn)
        self.assertEqual(runtime.create_agent.call_count, 1)
        runtime.turn({**turn, 'session_id': 'desktop'})
        self.assertEqual(runtime.create_agent.call_count, 2)
        runtime.end('phone')
        self.assertNotIn('phone', runtime.sessions)
        self.assertIn('desktop', runtime.sessions)

    @patch.dict(os.environ, {'GOOGLE_API_KEY': 'offline-test-key'})
    def test_failures_release_turn_lock_and_reject_bad_input(self):
        runtime = self.runtime()
        runtime.create_agent.side_effect = RuntimeError('offline failure')
        with self.assertRaises(RuntimeError):
            runtime.turn({'session_id': 'phone', 'text': 'Hello', 'speak': False})
        self.assertFalse(runtime.lock.locked())
        self.assertEqual(runtime.stage, 'Ready')
        with self.assertRaisesRegex(ValueError, 'supported'):
            runtime.turn({'session_id': 'phone', 'text': 'Hello', 'language': 'invalid'})
        with self.assertRaisesRegex(ValueError, '2000'):
            runtime.turn({'session_id': 'phone', 'text': 'x' * 2001})
        runtime.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, 'processing'):
                runtime.turn({'session_id': 'phone', 'text': 'Hello'})
        finally:
            runtime.lock.release()

    def test_decodes_browser_recording_to_16khz_mono(self):
        data = io.BytesIO()
        with wave.open(data, 'wb') as output:
            output.setnchannels(2); output.setsampwidth(2); output.setframerate(8000)
            output.writeframes(np.zeros((8000, 2), dtype='<i2').tobytes())
        audio = decode_recording(base64.b64encode(data.getvalue()).decode())
        self.assertEqual(audio.shape, (16000,))
        self.assertEqual(audio.dtype, np.float32)
        for value in ('!', '', base64.b64encode(b'not audio').decode()):
            with self.assertRaises(ValueError):
                decode_recording(value)

    def test_vad_rejects_silence_and_retains_speech_padding(self):
        detector = Mock(frame_size=512)
        detector.speech_probability.return_value = 0.0
        self.assertEqual(len(trim_browser_recording(np.zeros(16000, dtype=np.float32), detector)), 0)
        detector.speech_probability.side_effect = [0.0] * 10 + [1.0] * 4 + [0.0] * 18
        audio = np.ones(16000, dtype=np.float32) * .1
        trimmed = trim_browser_recording(audio, detector)
        self.assertGreater(len(trimmed), 4 * 512)
        self.assertLess(len(trimmed), len(audio))
        detector.reset.assert_called()

    def test_tts_returns_audio_without_playing_on_computer(self):
        from types import SimpleNamespace
        engine = TTSEngine.__new__(TTSEngine)
        import threading
        engine._speak_lock = threading.Lock()
        engine.get_profile = Mock(return_value=SimpleNamespace(backend='edge'))
        engine._remove_temporary = Mock()
        engine.speak = Mock(side_effect=AssertionError('must not play locally'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reply.mp3'; path.write_bytes(b'audio-bytes')
            engine._prepare_audio = Mock(return_value=SimpleNamespace(path=str(path)))
            self.assertEqual(engine.synthesize_bytes('Hello'), (b'audio-bytes', 'audio/mpeg'))
        engine._remove_temporary.assert_called_once()
        engine.speak.assert_not_called()


if __name__ == '__main__':
    unittest.main()
