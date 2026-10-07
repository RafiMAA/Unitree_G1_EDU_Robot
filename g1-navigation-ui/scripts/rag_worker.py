#!/usr/bin/env python3
"""Browser-audio adapter for the existing airport RAG/STT/VAD/TTS engines."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get('G1_RAG_PORT', '8767'))
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_AUDIO_SECONDS = 60


def decode_recording(encoded):
    """Normalize phone WebM/MP4/WAV to bounded 16 kHz mono float samples."""
    import numpy as np
    if not isinstance(encoded, str):
        raise ValueError('Select a microphone recording')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid audio encoding') from exc
    if not 0 < len(data) <= MAX_AUDIO_BYTES:
        raise ValueError('Maximum recording size is 4 MB')
    if not shutil.which('ffmpeg'):
        raise ValueError('Install ffmpeg on the computer to enable microphone input')
    with tempfile.TemporaryDirectory(prefix='g1-browser-audio-') as directory:
        path = Path(directory) / 'recording'
        path.write_bytes(data)
        decoded = subprocess.run(
            ['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-t', str(MAX_AUDIO_SECONDS + 1),
             '-ac', '1', '-ar', '16000', '-f', 'f32le', 'pipe:1'],
            capture_output=True, timeout=20,
        )
    if decoded.returncode or not decoded.stdout or len(decoded.stdout) % 4:
        raise ValueError('The recording could not be decoded; try recording again')
    audio = np.frombuffer(decoded.stdout, dtype='<f4').copy()
    if len(audio) > MAX_AUDIO_SECONDS * 16000:
        raise ValueError('Keep each recording under 60 seconds')
    if not np.isfinite(audio).all():
        raise ValueError('Invalid recording samples')
    return audio


class Runtime:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.ready = False
        self.error = ''
        self.stt = self.detector = self.tts = None
        self.stage = 'Preparing conversation'

    def initialize(self):
        try:
            from dotenv import load_dotenv
            for path in (ROOT / '.env', ROOT / 'g1-ros2-workspace/.env'):
                load_dotenv(path, override=False)
            if os.environ.get('G1_RAG_ENV_FILE'):
                load_dotenv(os.environ['G1_RAG_ENV_FILE'], override=False)
            from g1_conversation.agent.rag_agent import create_agent
            from g1_conversation.agent.prompts import SYSTEM_PROMPT_TEMPLATE
            self.create_agent = create_agent
            self.languages = set(SYSTEM_PROMPT_TEMPLATE)
            self.ready = True
            self.stage = 'Ready'
        except Exception as exc:
            self.error = f'Conversation dependencies could not load ({type(exc).__name__}). Check rag.log.'
            print(self.error, flush=True)

    def status(self):
        return {'ready': self.ready, 'configured': bool(os.environ.get('GOOGLE_API_KEY')),
                'stage': self.stage, 'error': self.error,
                'microphone_available': bool(shutil.which('ffmpeg'))}

    def turn(self, payload):
        if not self.ready:
            raise ValueError(self.error or 'Conversation is still preparing; please wait')
        if not os.environ.get('GOOGLE_API_KEY'):
            raise ValueError('Set GOOGLE_API_KEY in the computer environment or workspace .env, then restart the UI')
        identifier = payload.get('session_id')
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 80:
            raise ValueError('Start a new conversation')
        language = payload.get('language', 'en')
        if language not in self.languages:
            raise ValueError('Select a supported conversation language')
        text = payload.get('text', '')
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError('Maximum message length is 2000 characters')
        name = payload.get('name', '')
        if not isinstance(name, str) or len(name) > 60:
            raise ValueError('Maximum name length is 60 characters')
        if not self.lock.acquire(blocking=False):
            raise ValueError('Another conversation turn is processing; try again shortly')
        try:
            now = time.monotonic()
            for key, value in list(self.sessions.items()):
                if now - value['last_used'] > 900:
                    self.sessions.pop(key)
            if payload.get('audio'):
                self.stage = 'Checking speech'
                from g1_conversation.vad import _SileroStreamDetector, trim_browser_recording
                from g1_conversation.stt_engine import STTEngine
                if self.detector is None:
                    self.detector = _SileroStreamDetector(16000)
                audio = trim_browser_recording(decode_recording(payload['audio']), self.detector)
                if not len(audio):
                    raise ValueError('No speech detected. Speak clearly and try again')
                self.stage = 'Transcribing'
                if self.stt is None:
                    self.stt = STTEngine(model_name=os.environ.get('G1_STT_MODEL', 'base'), backend='faster-whisper')
                result = self.stt.transcribe(audio, language=language)
                text = result.text.strip()
                if result.is_empty or not text:
                    raise ValueError('No speech recognized. Try again or type your message')
            if not text.strip():
                raise ValueError('Type a message or record your voice')
            self.stage = 'Answering'
            session = self.sessions.get(identifier)
            if session is None or (session['language'], session['name']) != (language, name):
                if len(self.sessions) >= 32 and identifier not in self.sessions:
                    oldest = min(self.sessions, key=lambda key: self.sessions[key]['last_used'])
                    self.sessions.pop(oldest)
                session = {'agent': self.create_agent(lang_code=language, passenger_name=name or None),
                           'language': language, 'name': name, 'last_used': now}
                self.sessions[identifier] = session
            # Use the bounded agent directly so failures are shown rather than
            # being disguised as a successful "please repeat" response.
            answer = session['agent'].invoke({'input': text.strip()})['output']
            session['last_used'] = time.monotonic()
            response = {'transcript': text.strip(), 'answer': answer, 'language': language}
            if payload.get('speak', True):
                try:
                    self.stage = 'Preparing reply audio'
                    from g1_conversation.tts_engine import TTSEngine
                    if self.tts is None:
                        self.tts = TTSEngine(backend=os.environ.get('G1_TTS_BACKEND', 'auto'), playback_enabled=False)
                    audio, mime = self.tts.synthesize_bytes(answer, language)
                    response['audio'] = {'data': base64.b64encode(audio).decode(), 'mime': mime}
                except Exception as exc:
                    response['audio_error'] = 'Speech playback could not be generated. The text reply is available.'
                    print(f'TTS failed: {type(exc).__name__}', flush=True)
            return response
        finally:
            self.stage = 'Ready'
            self.lock.release()

    def end(self, identifier):
        with self.lock:
            self.sessions.pop(identifier, None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == '/status':
            self.respond(200, self.server.runtime.status())
        else:
            self.respond(404, {'error': 'Unknown conversation route'})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 6 * 1024 * 1024:
                raise ValueError('Conversation request is too large')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Expected a conversation object')
            if self.path == '/turn':
                result = self.server.runtime.turn(payload)
            elif self.path == '/end':
                self.server.runtime.end(payload.get('session_id'))
                result = {'ended': True}
            else:
                self.respond(404, {'error': 'Unknown conversation route'})
                return
            self.respond(200, result)
        except ValueError as exc:
            self.respond(400, {'error': str(exc)})
        except Exception as exc:
            print(f'Conversation failed: {type(exc).__name__}', flush=True)
            self.respond(500, {'error': 'Conversation failed. Check the computer RAG log and API configuration.'})


def main():
    runtime = Runtime()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.runtime = runtime
    threading.Thread(target=runtime.initialize, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
