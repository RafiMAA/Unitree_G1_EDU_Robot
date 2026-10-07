"""Offline Gemini Live relay tests using SDK schemas and fake audio transports."""
import asyncio
import base64
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from live_rag import live_config, LiveGateway, relay_session
try:
    from google.genai import types
except ImportError:
    types = None


class Socket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.request = SimpleNamespace(path='/api/rag/live', headers={'Origin': 'http://localhost:5173'})
    def __aiter__(self):
        return self
    async def __anext__(self):
        packet = await self.queue.get()
        if packet is None:
            raise StopAsyncIteration
        return packet
    async def send(self, value):
        self.sent.append(json.loads(value))


class Session:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.inputs, self.tools = [], []
    async def send_realtime_input(self, **values):
        self.inputs.append(values)
    async def send_tool_response(self, **values):
        self.tools.extend(values['function_responses'])
    async def receive(self):
        while True:
            value = await self.queue.get()
            yield value
            if value.server_content and value.server_content.turn_complete:
                return


@unittest.skipIf(types is None, 'Run live relay tests with .rag-venv Python')
class LiveRelayTests(unittest.IsolatedAsyncioTestCase):
    async def until(self, check):
        async def poll():
            while not check():
                await asyncio.sleep(.005)
        await asyncio.wait_for(poll(), 2)

    async def test_streams_audio_transcript_and_interruption_over_multiple_turns(self):
        ws, session = Socket(), Session()
        relay = asyncio.create_task(relay_session(ws, session, 16000, lambda query: {'documents': []}))
        await self.until(lambda: ws.sent)
        await ws.queue.put(b'\x01\x00' * 640)
        await self.until(lambda: any('audio' in value for value in session.inputs))
        self.assertEqual(session.inputs[-1]['audio'].mime_type, 'audio/pcm;rate=16000')
        pcm = b'\x10\x00' * 24
        await session.queue.put(types.LiveServerMessage(server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text='Where is baggage claim?'),
            output_transcription=types.Transcription(text='Follow airport signs.'),
            model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(data=pcm,mime_type='audio/pcm;rate=24000'))]),
            turn_complete=True)))
        await session.queue.put(types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True)))
        await self.until(lambda: any(value['type'] == 'interrupted' for value in ws.sent))
        packet = next(value for value in ws.sent if value['type'] == 'audio')
        self.assertEqual(base64.b64decode(packet['data']), pcm)
        self.assertEqual(packet['sample_rate'], 24000)
        self.assertEqual(len([value for value in ws.sent if value['type'] == 'turn_complete']), 2)
        await ws.queue.put(None)
        await asyncio.wait_for(relay, 2)

    async def test_rag_lookup_delivers_tool_result_without_blocking_mic(self):
        ws, session = Socket(), Session()
        queries = []
        def search(query):
            queries.append(query)
            return {'documents': [{'text': 'Check official airport signs.'}]}
        relay = asyncio.create_task(relay_session(ws, session, 16000, search))
        await session.queue.put(types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id='lookup',name='search_airport_knowledge',args={'query':'washrooms'})])))
        await ws.queue.put(b'\x00\x00' * 640)
        await self.until(lambda: session.tools)
        self.assertEqual(queries, ['washrooms'])
        self.assertEqual(session.tools[0].id, 'lookup')
        self.assertIn('documents', session.tools[0].response)
        self.assertTrue(any('audio' in value for value in session.inputs))
        await ws.queue.put(json.dumps({'type':'mute','value':True}))
        await self.until(lambda: any(value.get('audio_stream_end') for value in session.inputs))
        await ws.queue.put(None)
        await asyncio.wait_for(relay, 2)

    async def test_cancelled_lookup_does_not_return_stale_result(self):
        ws, session = Socket(), Session()
        import threading
        finished = threading.Event()
        def search(query):
            finished.wait(.2)
            return {'documents': []}
        relay = asyncio.create_task(relay_session(ws, session, 16000, search))
        await session.queue.put(types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id='old',name='search_airport_knowledge',args={'query':'gate'})])))
        await self.until(lambda: any(value['type']=='state' for value in ws.sent))
        await session.queue.put(types.LiveServerMessage(tool_call_cancellation=types.LiveServerToolCallCancellation(ids=['old'])))
        await asyncio.sleep(.02)
        finished.set()
        await ws.queue.put(None)
        await asyncio.wait_for(relay, 2)
        self.assertEqual(session.tools, [])

    async def test_missing_key_and_invalid_origins_do_not_open_provider(self):
        runtime = SimpleNamespace(ready=True,languages={'en'})
        gateway = LiveGateway(runtime)
        self.assertFalse(gateway.valid_origin('https://untrusted.example:5173'))
        with patch.dict(os.environ, {'GOOGLE_API_KEY':''}):
            ws = Socket()
            await gateway.handle(ws)
        self.assertEqual(ws.sent[-1]['type'], 'error')
        self.assertIn('GOOGLE_API_KEY',ws.sent[-1]['message'])

    def test_live_setup_is_native_audio_with_blocking_airport_tool(self):
        config = live_config('en', 'Passenger')
        self.assertEqual(config.response_modalities, ['AUDIO'])
        self.assertIsNotNone(config.input_audio_transcription)
        self.assertIsNotNone(config.output_audio_transcription)
        declaration = config.tools[0].function_declarations[0]
        self.assertEqual(declaration.name, 'search_airport_knowledge')
        self.assertEqual(declaration.behavior, 'BLOCKING')


if __name__ == '__main__':
    unittest.main()
