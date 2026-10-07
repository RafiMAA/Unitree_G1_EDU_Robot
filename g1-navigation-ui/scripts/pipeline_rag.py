"""Continuous browser voice using WebRTC VAD -> Whisper -> FAISS/Gemini -> TTS."""
import asyncio
import base64
import json
import os
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import numpy as np


def console_request(route, payload=None):
    port = int(os.environ.get('G1_CONSOLE_PORT', '8765'))
    request = Request(f'http://127.0.0.1:{port}/api/rag/{route}',
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=50) as response:
            return json.load(response)
    except HTTPError as exc:
        raise ValueError(json.load(exc).get('error', 'Navigation unavailable')) from exc


class SpeechTurns:
    """Bounded 30 ms WebRTC frames with pre-roll, pause detection and barge-in."""
    def __init__(self, sample_rate=16000, detector=None):
        if detector is None:
            import webrtcvad
            detector = webrtcvad.Vad(3)
        self.detector, self.sample_rate = detector, sample_rate
        self.pending = np.empty(0, dtype='<i2')
        self.reset()

    def reset(self):
        self.frames, self.pre = [], []
        self.active = False
        self.voiced = self.silent = 0

    def feed(self, packet):
        if not packet or len(packet) > 8192 or len(packet) % 2:
            raise ValueError('Invalid microphone PCM packet')
        samples = np.frombuffer(packet, dtype='<i2')
        if self.sample_rate != 16000:
            count = round(len(samples) * 16000 / self.sample_rate)
            samples = np.interp(np.arange(count) * self.sample_rate / 16000, np.arange(len(samples)), samples).astype('<i2')
        self.pending = np.concatenate((self.pending, samples))
        events = []
        while len(self.pending) >= 480:
            frame, self.pending = self.pending[:480].tobytes(), self.pending[480:]
            voice = self.detector.is_speech(frame, 16000)
            if not self.active:
                self.pre = (self.pre + [frame])[-10:]
                self.voiced = self.voiced + 1 if voice else 0
                if self.voiced >= 3:
                    self.active = True; self.frames = self.pre.copy(); self.silent = 0
                    events.append(('speech_start', None))
            else:
                self.frames.append(frame)
                self.silent = 0 if voice else self.silent + 1
                if self.silent >= 30 or len(self.frames) >= 1000:
                    frames = self.frames[:len(self.frames) - max(0, self.silent - 6)]
                    audio = np.frombuffer(b''.join(frames), dtype='<i2').astype(np.float32) / 32768
                    events.append(('utterance', audio))
                    self.reset()
        return events


async def pipeline_session(websocket, runtime, language, name, sample_rate, request=console_request):
    from g1_conversation.stt_engine import STTEngine
    from g1_conversation.tts_engine import TTSEngine
    from g1_conversation.onboarding import get_airport_introduction
    turns = SpeechTurns(sample_rate)
    stt = STTEngine(model_name=os.environ.get('G1_STT_MODEL', 'base'), backend='faster-whisper')
    tts = TTSEngine(backend=os.environ.get('G1_TTS_BACKEND', 'auto'), playback_enabled=False)
    agent = None
    generation = 0
    queue = asyncio.Queue(maxsize=1)
    await queue.put((0, None))  # Speak a greeting while keeping the microphone live.

    async def emit(kind, **values):
        await websocket.send(json.dumps({'type': kind, **values}))

    async def microphone():
        nonlocal generation
        async for packet in websocket:
            if isinstance(packet, bytes):
                for kind, audio in turns.feed(packet):
                    if kind == 'speech_start':
                        generation += 1
                        await emit('interrupted')
                        await emit('state', state='Listening')
                    else:
                        if queue.full():
                            queue.get_nowait()
                        await queue.put((generation, audio))
            else:
                control = json.loads(packet)
                if control.get('type') == 'end':
                    return
                if control.get('type') == 'mute' and control.get('value'):
                    turns.reset(); turns.pending = np.empty(0, dtype='<i2')

    async def process_turns():
        nonlocal agent
        while True:
            current, recording = await queue.get()
            if current != generation:
                continue
            try:
                if recording is None:
                    answer = get_airport_introduction(language, name or 'passenger')
                else:
                    await emit('state', state='Transcribing')
                    transcription = await asyncio.to_thread(stt.transcribe, recording, language=language)
                    if current != generation:
                        continue
                    if transcription.is_empty or not transcription.text.strip():
                        await emit('state', state='Listening'); continue
                    text = transcription.text.strip()
                    await emit('transcript', role='user', id=f'user-{current}', text=text)
                    context = await asyncio.to_thread(request, 'context')
                    await emit('state', state='Retrieving airport knowledge / Gemini')
                    if agent is None:
                        agent = await asyncio.to_thread(runtime.create_agent, lang_code=language, passenger_name=name or None, max_tokens=256)
                    if current != generation:
                        continue
                    result = await asyncio.to_thread(agent.invoke_guidance, text, context.get('locations', []), context.get('navigation', {}))
                    if current != generation:
                        continue
                    answer = result['answer']
                    if result['action'] == 'cancel':
                        await asyncio.to_thread(request, 'cancel', {})
                        answer = 'Navigation canceled.' if language == 'en' else answer
                    elif result['action'] == 'navigate':
                        destination = {'map_id': context.get('map_id'), 'location_id': result['location_id']}
                        await emit('state', state='Preparing navigation')
                        try:
                            await asyncio.to_thread(request, 'prepare-navigation', destination)
                            if current != generation:
                                continue
                            receipt = await asyncio.to_thread(request, 'goal', destination)
                            await emit('navigation', **receipt)
                            if language == 'en':
                                answer = f"I sent your request to navigate to {receipt['destination']}."
                        except ValueError as exc:
                            await emit('navigation', state='unavailable', message=str(exc))
                            if language == 'en':
                                answer = f'Navigation is unavailable: {exc}'
                            else:
                                answer = result['answer']
                    await asyncio.to_thread(request, 'transcript', {'text':text,'answer':answer})
                if current != generation:
                    continue
                await emit('transcript', role='assistant', id=f'assistant-{current}', text=answer)
                for sentence in re.split(r'(?<=[.!?。！？])\s+', answer):
                    if not sentence.strip() or current != generation:
                        break
                    await emit('state', state='Preparing speech')
                    data, mime = await asyncio.to_thread(tts.synthesize_bytes, sentence, language)
                    if current != generation:
                        break
                    await emit('audio_file', data=base64.b64encode(data).decode(), mime=mime)
                if current == generation:
                    await emit('turn_complete')
            except Exception as exc:
                print(f'Voice pipeline failed: {type(exc).__name__}', flush=True)
                if current == generation:
                    await emit('notice', message='This turn could not finish. Check the computer RAG log, API key and speech engines; try speaking again.')
                    await emit('state', state='Listening')

    await emit('ready')
    tasks = [asyncio.create_task(microphone()), asyncio.create_task(process_turns())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        generation += 1
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.to_thread(request, 'cancel', {})
        except Exception:
            pass
