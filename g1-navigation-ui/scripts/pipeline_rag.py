"""Continuous browser voice using WebRTC VAD -> Whisper -> FAISS/Gemini -> TTS."""
import asyncio
import base64
import json
import os
import re
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from difflib import SequenceMatcher
from g1_core.guide_behavior import CONFIG

LOG = logging.getLogger("airport.voice")
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import numpy as np


def console_request(route, payload=None):
    port = int(os.environ.get('G1_CONSOLE_PORT', '8765'))
    request = Request(f'http://127.0.0.1:{port}/api/rag/{route}',
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=50 if route == 'prepare-navigation' else (2 if route in ('gesture', 'transcript') else 5)) as response:
            return json.load(response)
    except HTTPError as exc:
        raise ValueError(json.load(exc).get('error', 'Navigation unavailable')) from exc


class SpeechTurns:
    """Bounded 30 ms WebRTC frames with pre-roll, pause detection."""
    def __init__(self, sample_rate=16000, detector=None):
        if detector is None:
            import webrtcvad
            detector = webrtcvad.Vad(3)
        self.detector, self.sample_rate = detector, sample_rate
        self.noise_rms = CONFIG['speech']['minimum_rms'] / CONFIG['speech']['noise_multiplier']
        self.pending = np.empty(0, dtype='<i2')
        self.reset()

    def reset(self):
        self.frames, self.pre = [], []
        self.active = False
        self.voiced = self.silent = self.total_voiced = 0

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
            vad = self.detector.is_speech(frame, 16000)
            rms = float(np.sqrt(np.mean(np.frombuffer(frame, dtype='<i2').astype(np.float32)**2))) / 32768.
            cfg = CONFIG['speech']
            if not vad and not self.active:
                self.noise_rms = .98*self.noise_rms + .02*min(rms, .02)
            voice = vad and rms >= max(cfg['minimum_rms'], self.noise_rms*cfg['noise_multiplier'])
            if not self.active:
                self.pre = (self.pre + [frame])[-10:]
                self.voiced = self.voiced + 1 if voice else 0
                if self.voiced >= cfg['start_frames']:
                    self.active = True; self.frames = self.pre.copy(); self.silent = 0; self.total_voiced = self.voiced
                    events.append(('speech_start', None))
            else:
                self.frames.append(frame)
                self.total_voiced += int(voice)
                self.silent = 0 if voice else self.silent + 1
                if self.silent >= cfg['pause_frames'] or len(self.frames) >= 1000:
                    frames = self.frames[:len(self.frames) - max(0, self.silent - 6)]
                    audio = np.frombuffer(b''.join(frames), dtype='<i2').astype(np.float32) / 32768
                    if self.total_voiced >= cfg['minimum_voiced_frames'] and self.total_voiced / max(1, len(frames)) >= cfg['minimum_voice_ratio']:
                        events.append(('utterance', audio))
                    else:
                        events.append(('discarded', None))
                    self.reset()
        return events


async def pipeline_session(websocket, runtime, language, name, sample_rate, request=console_request):
    from g1_conversation.stt_engine import STTEngine
    from g1_conversation.tts_engine import TTSEngine
    from g1_conversation.onboarding import get_airport_introduction
    from g1_conversation.agent.dialogue import GuideDialogue, GuideNarrator
    from g1_core.guide_behavior import CONFIG
    import logging
    import threading
    import time
    logging.basicConfig(level=logging.INFO)
    turns = SpeechTurns(sample_rate)
    stt = STTEngine(model_name=os.environ.get('G1_STT_MODEL', 'base'), backend='faster-whisper')
    tts = TTSEngine(backend=os.environ.get('G1_TTS_BACKEND', 'auto'), playback_enabled=False)
    pools = {name: ThreadPoolExecutor(max_workers=count, thread_name_prefix='g1-'+name)
             for name, count in (('stt',1), ('tts',1), ('rag',1), ('navigation',2), ('prepare',1), ('feedback',1))}
    loop = asyncio.get_running_loop()
    async def work(role, fn, *args, **kwargs):
        return await loop.run_in_executor(pools[role], partial(fn, *args, **kwargs))

    agent, generation, serial = None, 0, 0
    tts_lock = threading.Lock()
    speech_cache = {}
    dialogue = GuideDialogue()
    narrator = GuideNarrator(dialogue)
    queue = asyncio.Queue(maxsize=1)
    speech = asyncio.Queue(maxsize=2)
    playback = {}
    history = []
    recordings = asyncio.Queue(maxsize=1)
    spoken_lines = []
    background_tasks = set()
    speaking = False
    microphone_epoch = 0
    microphone_muted = False
    last_presence = time.monotonic()
    active_goal = None
    closing = False
    await queue.put((0, None))

    def synthesize(answer, current):
        # A canceled to_thread task may still be running in its worker. Do not
        # concurrently enter the same TTS engine after a superseded request.
        with tts_lock:
            if current != generation:
                return None
            key = (answer, language)
            if key not in speech_cache:
                speech_cache[key] = tts.synthesize_bytes(answer, language)
                if len(speech_cache) > 16:
                    speech_cache.pop(next(iter(speech_cache)))
            return speech_cache[key]

    async def emit(kind, **values):
        await websocket.send(json.dumps({'type': kind, **values}))

    async def current_work(awaitable, current):
        """Release superseded work promptly; no pending goal callback survives."""
        task = asyncio.ensure_future(awaitable)
        try:
            while not task.done():
                if current != generation:
                    raise asyncio.CancelledError()
                await asyncio.wait({task}, timeout=.05)
            if current != generation:
                raise asyncio.CancelledError()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def gesture(name):
        try:
            await work('feedback', request, 'gesture', {'name': name})
        except Exception as exc:
            print(f'Gesture unavailable: {type(exc).__name__}', flush=True)

    def discard_microphone():
        nonlocal microphone_epoch
        microphone_epoch += 1
        turns.reset()
        turns.pending = np.empty(0, dtype='<i2')
        while not recordings.empty():
            recordings.get_nowait()

    async def microphone():
        nonlocal last_presence, microphone_muted
        async for packet in websocket:
            if isinstance(packet, bytes):
                if speaking or microphone_muted:
                    continue
                for kind, audio in turns.feed(packet):
                    if kind == 'speech_start':
                        last_presence = time.monotonic()
                        # VAD is only a candidate. Noise must not cancel TTS or a goal.
                    elif kind == 'utterance':
                        if recordings.full():
                            recordings.get_nowait()
                        await recordings.put((microphone_epoch, audio))
            else:
                control = json.loads(packet)
                if control.get('type') == 'end':
                    return
                if control.get('type') in ('playback_started', 'playback_ended', 'playback_error'):
                    pair = playback.get(control.get('id'))
                    if pair:
                        if control['type'] == 'playback_error':
                            pair[2].set()
                        else:
                            pair[0 if control['type'] == 'playback_started' else 1].set()
                if control.get('type') == 'mute':
                    microphone_muted = bool(control.get('value'))
                    discard_microphone()

    def speaker_echo(text):
        normalized = ' '.join(re.findall(r'\w+', text.casefold()))
        if len(normalized.split()) < 4:
            return False
        now = time.monotonic()
        for line, expires in spoken_lines:
            reference = ' '.join(re.findall(r'\w+', line.casefold()))
            if now <= expires and (SequenceMatcher(None, normalized, reference).ratio() >= .82 or
                                   (' '+normalized+' ') in (' '+reference+' ')):
                return True
        return False

    async def recognize():
        nonlocal generation
        while True:
            epoch, recording = await recordings.get()
            if speaking or microphone_muted or epoch != microphone_epoch:
                continue
            try:
                transcription = await work('stt', stt.transcribe, recording, language=language)
                # A clip submitted before playback may finish STT during or after
                # that reply. Never let its late result interrupt a visible reply.
                if speaking or microphone_muted or epoch != microphone_epoch:
                    continue
                text = transcription.text.strip()
                confidence = getattr(transcription, 'confidence', None)
                if (transcription.is_empty or not text or
                    (isinstance(confidence, (float,int)) and confidence < CONFIG['speech']['minimum_logprob']) or
                    speaker_echo(text)):
                    LOG.info('Rejected microphone clip: empty/low confidence/speaker echo')
                    continue
                generation += 1
                LOG.info('Accepted passenger speech, turn=%s', generation)
                await emit('interrupted')
                await emit('transcript', role='user', id=f'user-{generation}', text=text)
                if queue.full():
                    queue.get_nowait()
                await queue.put((generation, text))
            except Exception as exc:
                LOG.warning('Speech recognition failed: %s', type(exc).__name__)
                await emit('notice', message='Speech recognition could not finish. Please try speaking again.')

    async def wait_playback(event, failed, timeout, current):
        async def wait():
            a, b = asyncio.create_task(event.wait()), asyncio.create_task(failed.wait())
            try:
                done, _ = await asyncio.wait((a,b), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    raise TimeoutError('Browser playback acknowledgement missing')
                if failed.is_set():
                    raise ValueError('Browser could not play the reply')
            finally:
                a.cancel(); b.cancel()
                await asyncio.gather(a,b,return_exceptions=True)
        await current_work(wait(), current)

    async def say(answer, current, after_start=None, gesture_name=None, background=False):
        completion = asyncio.get_running_loop().create_future()
        item = (current, answer, after_start, gesture_name, completion)
        if background and speech.full():
            return
        await speech.put(item)
        if not background:
            await current_work(asyncio.shield(completion), current)

    async def speaker():
        nonlocal serial, speaking, last_presence
        while True:
            current, answer, after_start, gesture_name, completion = await speech.get()
            ident = None
            speech_stage = 'synthesis'
            try:
                if current != generation:
                    continue
                speaking = True
                discard_microphone()
                await emit('listening', enabled=False)
                serial += 1
                ident = f'speech-{serial}'
                started, ended, failed = asyncio.Event(), asyncio.Event(), asyncio.Event()
                playback[ident] = started, ended, failed
                await emit('transcript', role='assistant', id=ident, text=answer)
                await emit('state', state='Preparing speech')
                data, mime = await current_work(asyncio.wait_for(work('tts', synthesize, answer, current), CONFIG['speech']['tts_timeout_seconds']), current)
                LOG.info('Speech ready id=%s bytes=%s', ident, len(data))
                speech_stage = 'playback'
                await emit('audio_file', id=ident, data=base64.b64encode(data).decode(), mime=mime)
                # Motion is never started just because audio was downloaded or synthesized.
                await wait_playback(started, failed, CONFIG['narration']['playback_timeout_seconds'], current)
                LOG.info('Browser speech started id=%s', ident)
                spoken_lines.append((answer, time.monotonic()+90.))
                del spoken_lines[:-4]
                if gesture_name:
                    task = asyncio.create_task(gesture(gesture_name))
                    background_tasks.add(task)
                    task.add_done_callback(background_tasks.discard)
                if after_start and current == generation:
                    await after_start()
                await wait_playback(ended, failed, CONFIG['narration']['playback_end_timeout_seconds'], current)
                if spoken_lines:
                    spoken_lines[-1] = (answer, time.monotonic()+CONFIG['speech']['echo_seconds'])
                last_presence = time.monotonic()
            except asyncio.CancelledError:
                if closing:
                    raise
            except Exception as exc:
                LOG.warning('Voice %s failed id=%s: %s', speech_stage, ident, type(exc).__name__)
                if current == generation:
                    message = ('Speech generation failed or timed out. Check the computer TTS log and try again.'
                               if speech_stage == 'synthesis' else
                               'The browser could not confirm reply audio. Tap End conversation, then Start conversation to enable sound.')
                    await emit('notice', message=message)
            finally:
                if ident:
                    playback.pop(ident, None)
                    if spoken_lines and spoken_lines[-1][0] == answer:
                        spoken_lines[-1] = (answer, time.monotonic()+CONFIG['speech']['echo_seconds'])
                if not closing:
                    # Keep capture closed through the speaker's acoustic echo tail.
                    await asyncio.sleep(CONFIG['speech']['microphone_tail_seconds'])
                    discard_microphone()
                    speaking = False
                    await emit('listening', enabled=True)
                    await emit('turn_complete')
                if not completion.done():
                    completion.set_result(None)

    async def process_turns():
        nonlocal agent, active_goal
        while True:
            current, recording = await queue.get()
            if current != generation:
                continue
            stage = 'greeting'
            try:
                if recording is None:
                    answer = dialogue.phrases.say('greet') if language == 'en' else get_airport_introduction(language, name or 'passenger')
                    await say(answer, current, gesture_name='greet')
                    continue
                text = recording
                stage = 'destination context'
                context = await current_work(work('feedback', request, 'context'), current)
                # Stop/wait is handled without waiting for retrieval or Gemini.
                decision = dialogue.decide(text, context.get('locations', []), context) if language == 'en' else {'answer': None}
                if decision['answer'] is None:
                    stage = 'airport knowledge / Gemini'
                    await emit('state', state='Retrieving airport knowledge / Gemini')
                    if agent is None:
                        agent = await current_work(work('rag', runtime.create_agent, lang_code=language, passenger_name=name or None, max_tokens=256), current)
                        if isinstance(getattr(agent, 'memory_window', None), int):
                            agent.dialogue = dialogue
                            agent.history.extend(history[-agent.memory_window:])
                    decision = await current_work(work('rag', agent.invoke_guidance, text, context.get('locations', []), context.get('navigation', {}), context), current)
                elif agent is not None and isinstance(getattr(agent, 'memory_window', None), int):
                    agent.history.append((text, decision['answer']))
                    while len(agent.history) > agent.memory_window:
                        agent.history.popleft()
                answer = decision['answer']
                history.append((text, answer))
                del history[:-8]
                after_start = None
                if decision['action'] == 'cancel':
                    active_goal = None
                    narrator.reset()
                    await work('navigation', request, 'cancel', {})
                elif decision['action'] == 'navigate':
                    stage = 'guidance preparation'
                    # A new escort always cancels an old one before preparing the replacement.
                    active_goal = None
                    await work('navigation', request, 'cancel', {})
                    dialogue.update_navigation({'state': 'canceled'})
                    destination = {'map_id': context.get('map_id'), 'location_id': decision['location_id']}
                    label = next(loc for loc in context['locations'] if loc['id'] == decision['location_id'])
                    await emit('state', state='Preparing guidance')
                    try:
                        await current_work(work('prepare', request, 'prepare-navigation', destination), current)
                        async def start_goal(destination=destination, label=label, current=current):
                            nonlocal active_goal
                            if current != generation:
                                return
                            try:
                                LOG.info('Submitting escort goal destination=%s', label['text'])
                                receipt = await work('navigation', request, 'goal', destination)
                                LOG.info('Escort goal receipt state=%s', receipt.get('state'))
                                if current != generation:
                                    await work('navigation', request, 'cancel', {})
                                    return
                                active_goal = label
                                dialogue.started(label)
                                narrator.reset()
                                narrator.last_spoken = time.monotonic()
                                await emit('navigation', **receipt)
                            except Exception as exc:
                                dialogue.update_navigation({'state': 'failed'})
                                print(f'Escort goal failed: {type(exc).__name__}', flush=True)
                                await emit('notice', message='Guidance could not start. Check the computer navigation log.')
                                await say(dialogue.phrases.say('failed'), current, background=True)
                        after_start = start_goal
                    except ValueError as exc:
                        dialogue.update_navigation({'state': 'failed'})
                        print(f'Guidance unavailable: {exc}', flush=True)
                        answer = dialogue.phrases.say('failed')
                # Optional ROS publication runs independently of the browser reply.
                async def publish_transcript(text=text, answer=answer):
                    try:
                        await work('feedback', request, 'transcript', {'text': text, 'answer': answer})
                    except Exception as exc:
                        LOG.warning('ROS transcript publishing failed: %s', type(exc).__name__)
                task = asyncio.create_task(publish_transcript())
                background_tasks.add(task)
                task.add_done_callback(background_tasks.discard)
                await say(answer, current, after_start, 'follow_me' if after_start else None)
            except asyncio.CancelledError:
                if closing:
                    raise
            except Exception as exc:
                print(f'Voice pipeline failed during {stage}: {type(exc).__name__}', flush=True)
                if current == generation:
                    await emit('notice', message=f'This turn failed during {stage} ({type(exc).__name__}). Please try again; details are in the computer RAG log.')
                    await say("I'm sorry, I couldn't finish that. Could you try again?", current)

    async def presence_and_narration():
        nonlocal active_goal, last_presence
        while True:
            await asyncio.sleep(CONFIG['narration']['poll_seconds'])
            now = time.monotonic()
            if active_goal:
                try:
                    context = await work('feedback', request, 'context')
                    status = context.get('navigation', {})
                    goal = status.get('goal')
                    # Bind narration to this escort, excluding stale results from a canceled goal.
                    if not goal or abs(goal['x']-active_goal['x']) > .001 or abs(goal['y']-active_goal['y']) > .001:
                        continue
                    if speaking and status.get('state') not in ('succeeded', 'failed', 'rejected', 'unavailable', 'canceled'):
                        continue
                    line = narrator.observe(status, now)
                    if line:
                        if language != 'en' and agent is not None:
                            line = await work('rag', agent.translate_guide_line, line)
                        await say(line, generation, background=True)
                    if narrator.finished:
                        active_goal = None
                except Exception as exc:
                    print(f'Navigation feedback unavailable: {type(exc).__name__}', flush=True)
            elif not speaking and queue.empty() and now-last_presence >= CONFIG['narration']['idle_seconds']:
                await gesture('idle-look-around')
                last_presence = now

    await emit('ready', finish_replies=True)
    tasks = [asyncio.create_task(fn()) for fn in (microphone, recognize, speaker, process_turns, presence_and_narration)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        closing = True
        generation += 1
        for task in [*tasks, *background_tasks]:
            task.cancel()
        await asyncio.gather(*tasks, *background_tasks, return_exceptions=True)
        try:
            await work('navigation', request, 'cancel', {})
        except Exception:
            pass
        for pool in pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
