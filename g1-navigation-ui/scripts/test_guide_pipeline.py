"""Browser playback, interruption and escort lifecycle without cloud or robot."""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest
import numpy as np
from test_live_rag import Socket
from pipeline_rag import pipeline_session


class Turns:
    def __init__(self, *args): pass
    def reset(self): pass
    def feed(self, packet):
        return [('speech_start', None), ('utterance', np.ones(1000, dtype=np.float32))]


class GuidePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, words, scenario, auto_playback=True, tts_callback=None, prepare_callback=None, initialize=True, stt_callback=None):
        ws = Socket(); trace = []; context = {'map_id':'test.yaml', 'locations':[
            {'id':'office','text':'Office','x':2.,'y':0.},
            {'id':'entrance','text':'Entrance','x':0.,'y':3.}], 'navigation':{}}
        stt=Mock(); stt.transcribe.side_effect=[NS(is_empty=text is None,text=text or '') for text in words]
        if stt_callback: stt.transcribe.side_effect=stt_callback
        tts=Mock(); tts.synthesize_bytes.return_value=(b'mock-audio','audio/mpeg')
        if tts_callback: tts.synthesize_bytes.side_effect=tts_callback
        original_send = ws.send
        async def send(packet):
            await original_send(packet)
            event = json.loads(packet)
            if event['type']=='audio_file' and (auto_playback or event['id']=='speech-1'):
                await ws.queue.put(json.dumps({'type':'playback_started', 'id':event['id']}))
                await ws.queue.put(json.dumps({'type':'playback_ended', 'id':event['id']}))
        ws.send=send
        def request(route, payload=None):
            trace.append((route,payload))
            if route=='context': return context.copy()
            if route=='prepare-navigation' and prepare_callback: return prepare_callback()
            if route=='goal': return {'state':'submitted','destination':payload['location_id']}
            return {'ready':True}
        async def until(predicate):
            async def wait():
                while not predicate(): await asyncio.sleep(.005)
            await asyncio.wait_for(wait(), 3.)
        async def speak(): await ws.queue.put(b'\x00\x00'*640)
        with patch.dict(__import__('pipeline_rag').CONFIG['speech'], microphone_tail_seconds=.02), patch('pipeline_rag.SpeechTurns', Turns), patch('g1_conversation.stt_engine.STTEngine',return_value=stt), patch('g1_conversation.tts_engine.TTSEngine',return_value=tts):
            task=asyncio.create_task(pipeline_session(ws,NS(create_agent=Mock()),'en','',16000,request))
            try:
                if initialize:
                    await until(lambda:any(e['type']=='turn_complete' for e in ws.sent))
                    ws.sent.clear()
                await scenario(ws,trace,context,speak,until)
            finally:
                await ws.queue.put(None)
                await asyncio.wait_for(task, 3.)
        return ws,trace

    async def test_passenger_cannot_interrupt_invitation_and_goal_waits_for_playback(self):
        async def scenario(ws,trace,context,speak,until):
            await speak()
            await until(lambda:any(e['type']=='transcript' and 'Follow me' in e.get('text','') for e in ws.sent))
            follow_id=next(e['id'] for e in ws.sent if e['type']=='transcript' and 'Follow me' in e.get('text',''))
            await until(lambda:any(e['type']=='audio_file' and e['id']==follow_id for e in ws.sent))
            self.assertFalse(any(route=='goal' for route,_ in trace))
            first=next(e for e in ws.sent if e['type']=='audio_file' and e['id']==follow_id)
            count=sum(e['type']=='transcript' and e.get('role')=='user' for e in ws.sent)
            await speak()  # Passenger speech during the reply is ignored.
            await asyncio.sleep(.05)
            self.assertEqual(sum(e['type']=='audio_file' for e in ws.sent),1)
            self.assertEqual(sum(e['type']=='transcript' and e.get('role')=='user' for e in ws.sent),count)
            await ws.queue.put(json.dumps({'type':'playback_started','id':first['id']}))
            await until(lambda:any(route=='goal' for route,_ in trace))
            await ws.queue.put(json.dumps({'type':'playback_ended','id':first['id']}))
            await speak()  # Echo immediately after audio ends is still ignored.
            await until(lambda:any(e['type']=='turn_complete' for e in ws.sent))
            self.assertEqual(sum(e['type']=='transcript' and e.get('role')=='user' for e in ws.sent),count)
            await speak()
            await until(lambda:sum(e['type']=='transcript' and e.get('role')=='user' for e in ws.sent)==count+1)
        await self.run_case(['Take me to Office', 'Okay'],scenario,auto_playback=False)

    async def test_switch_cancels_old_goal_and_arrival_narrates_once(self):
        async def scenario(ws,trace,context,speak,until):
            await speak()
            await until(lambda:sum(route=='goal' for route,_ in trace)==1)
            await until(lambda:any(e['type']=='turn_complete' for e in ws.sent))
            await speak()
            await until(lambda:sum(e['type']=='turn_complete' for e in ws.sent)>=2)
            self.assertEqual(sum(route=='goal' for route,_ in trace),1)
            await speak()
            await until(lambda:sum(route=='goal' for route,_ in trace)==2)
            goals=[i for i,(route,_) in enumerate(trace) if route=='goal']
            self.assertTrue(any(route=='cancel' for route,_ in trace[goals[0]+1:goals[1]]))
            self.assertEqual(trace[goals[1]][1]['location_id'],'entrance')
            context['navigation']={'state':'succeeded','goal':{'x':0.,'y':3.}}
            await until(lambda:any(e['type']=='transcript' and e.get('role')=='assistant' and 'Here we are' in e['text'] for e in ws.sent))
            await asyncio.sleep(.6)
            self.assertEqual(sum(e['type']=='transcript' and 'Here we are' in e.get('text','') for e in ws.sent),1)
            self.assertTrue(any(route=='gesture' and payload['name']=='follow_me' for route,payload in trace))
        await self.run_case(['Take me to Office','Okay','Can you navigate me to Entrance'],scenario)

    async def test_stop_cancels_without_model_or_new_goal(self):
        async def scenario(ws,trace,context,speak,until):
            await speak()
            await until(lambda:any(route=='goal' for route,_ in trace))
            await until(lambda:any(e['type']=='turn_complete' for e in ws.sent))
            baseline=sum(route=='cancel' for route,_ in trace)
            await speak()
            await until(lambda:sum(route=='cancel' for route,_ in trace)>baseline)
            self.assertEqual(sum(route=='goal' for route,_ in trace),1)
            await until(lambda:any(e['type']=='transcript' and "I'll stop" in e.get('text','') for e in ws.sent))
        await self.run_case(['Take me to Office','Stop'],scenario)

    async def test_rejected_noise_does_not_cancel_pending_office_goal(self):
        async def scenario(ws,trace,context,speak,until):
            await speak()
            await until(lambda:any(e['type']=='transcript' and 'Follow me' in e.get('text','') for e in ws.sent))
            ident=next(e['id'] for e in ws.sent if e['type']=='transcript' and 'Follow me' in e.get('text',''))
            await until(lambda:any(e['type']=='audio_file' and e['id']==ident for e in ws.sent))
            count=sum(e['type']=='interrupted' for e in ws.sent)
            await speak()  # VAD candidate that Whisper rejects as silence.
            await asyncio.sleep(.1)
            self.assertEqual(sum(e['type']=='interrupted' for e in ws.sent), count)
            await ws.queue.put(json.dumps({'type':'playback_started','id':ident}))
            await ws.queue.put(json.dumps({'type':'playback_ended','id':ident}))
            await until(lambda:any(route=='goal' for route,_ in trace))
            self.assertEqual(sum(route=='goal' for route,_ in trace),1)
        await self.run_case(['Take me to Office',None],scenario,auto_playback=False)

    async def test_speaker_echo_is_not_published_as_passenger_text(self):
        async def scenario(ws,trace,context,speak,until):
            await speak()
            await until(lambda:any(e['type']=='navigation' for e in ws.sent))
            await until(lambda:sum(e['type']=='turn_complete' for e in ws.sent)>=1)
            count=sum(e['type']=='transcript' and e.get('role')=='user' for e in ws.sent)
            await speak()
            await asyncio.sleep(.15)
            self.assertEqual(sum(e['type']=='transcript' and e.get('role')=='user' for e in ws.sent),count)
            self.assertEqual(sum(route=='goal' for route,_ in trace),1)
        await self.run_case(['Take me to Office',"Of course! Follow me, I'll show you the way to Office."],scenario)

    async def test_end_conversation_still_cancels_during_slow_tts(self):
        import threading
        started, release = threading.Event(), threading.Event()
        def slow_tts(*args):
            started.set()
            release.wait(2.)
            return b'audio', 'audio/mpeg'
        async def scenario(ws,trace,context,speak,until):
            try:
                await until(started.is_set)
                await speak()
                await asyncio.sleep(.05)
                self.assertFalse(any(e['type']=='transcript' and e.get('role')=='user' for e in ws.sent))
                await ws.queue.put(json.dumps({'type':'end'}))
                await until(lambda:any(route=='cancel' for route,_ in trace))
                self.assertFalse(any(route=='goal' for route,_ in trace))
            finally:
                release.set()
        await self.run_case(['Take me to Office'],scenario,tts_callback=slow_tts,initialize=False)

    async def test_late_stt_from_before_narration_is_discarded_even_after_reply(self):
        import threading
        started, release = threading.Event(), threading.Event()
        calls = 0
        def transcribe(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                started.set(); release.wait(2.)
            return NS(is_empty=False, text='Take me to Office' if calls==1 else 'Show')
        async def scenario(ws,trace,context,speak,until):
            try:
                await speak()
                await until(lambda:any(e['type']=='turn_complete' for e in ws.sent))
                await speak()
                await until(started.is_set)
                context['navigation']={'state':'succeeded','goal':{'x':2.,'y':0.}}
                await until(lambda:any(e['type']=='transcript' and 'Here we are' in e.get('text','') for e in ws.sent))
                await until(lambda:sum(e['type']=='turn_complete' for e in ws.sent)==2)
                release.set()
                await asyncio.sleep(.05)
                self.assertEqual(sum(e['type']=='transcript' and e.get('role')=='user' for e in ws.sent),1)
                self.assertEqual(sum(route=='goal' for route,_ in trace),1)
            finally:
                release.set()
        await self.run_case([],scenario,stt_callback=transcribe)

    async def test_cancel_is_not_queued_behind_navigation_preparation(self):
        import threading
        release=threading.Event()
        def prepare():
            release.wait(2.)
            return {'ready':True}
        async def scenario(ws,trace,context,speak,until):
            try:
                await speak()
                await until(lambda:any(route=='prepare-navigation' for route,_ in trace))
                await speak()
                await until(lambda:sum(route=='cancel' for route,_ in trace)>=2)
                await speak()
                await until(lambda:sum(route=='cancel' for route,_ in trace)>=3)
                self.assertFalse(any(route=='goal' for route,_ in trace))
            finally:
                release.set()
        await self.run_case(['Take me to Office','Take me to Entrance','Stop'],scenario,prepare_callback=prepare)

if __name__=='__main__': unittest.main()
