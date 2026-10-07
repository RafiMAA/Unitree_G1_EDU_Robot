"""Continuous speech segmentation and destination validation without cloud calls."""
import asyncio
import importlib.util
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import numpy as np
from pipeline_rag import SpeechTurns
from spoken_navigation import goal_is_free

class SegmentationTests(unittest.TestCase):
    def test_sustained_speech_starts_and_pause_finishes_one_utterance(self):
        detector = Mock()
        detector.is_speech.side_effect = [False] * 4 + [True] * 6 + [False] * 30
        turns = SpeechTurns(detector=detector)
        events = turns.feed(np.zeros(480 * 8, dtype='<i2').tobytes())
        self.assertEqual([event[0] for event in events], ['speech_start'])
        for _ in range(4):
            events += turns.feed(np.zeros(480 * 8, dtype='<i2').tobytes())
        self.assertEqual([event[0] for event in events], ['speech_start','utterance'])
        self.assertGreater(len(events[-1][1]), 480 * 6)
        self.assertFalse(turns.active)

    def test_silence_does_not_reach_stt_and_48khz_is_normalized(self):
        detector = Mock(); detector.is_speech.return_value = False
        turns = SpeechTurns(48000, detector)
        for _ in range(8):
            self.assertEqual(turns.feed(np.zeros(1440,dtype='<i2').tobytes()), [])
        self.assertEqual(len(turns.pending),0)
        self.assertEqual(len(detector.is_speech.call_args.args[0]),960)
        with self.assertRaises(ValueError):
            turns.feed(b'bad')

    def test_saved_goal_must_be_inside_known_free_map_cells(self):
        grid=NS(info=NS(resolution=1.,width=3,height=2,origin=NS(position=NS(x=0.,y=0.),orientation=NS(x=0.,y=0.,z=0.,w=1.))),data=[0,100,-1,0,0,0])
        self.assertTrue(goal_is_free(grid, {'x':.5,'y':.5}))
        for x,y in ((1.5,.5),(2.5,.5),(-.5,.5),(.5,2.5)):
            self.assertFalse(goal_is_free(grid, {'x':x,'y':y}))

@unittest.skipUnless(importlib.util.find_spec('langchain_core'), 'Use conversation test environment')
class GuidanceTests(unittest.TestCase):
    def test_guidance_uses_faiss_and_only_saved_id(self):
        from g1_conversation.agent.rag_agent import DirectRAGAgent
        llm=Mock();llm.invoke.return_value=NS(content=json.dumps({'answer':'I can guide you.','action':'navigate','location_id':'office'}))
        retriever=Mock();retriever.invoke.return_value=[]
        agent=DirectRAGAgent('en',llm,retriever)
        result=agent.invoke_guidance('Take me to the office',[{'id':'office','text':'Office'}])
        self.assertEqual(result['location_id'],'office')
        retriever.invoke.assert_called_once_with('Take me to the office')
        llm.invoke.return_value=NS(content=json.dumps({'answer':'Walking','action':'navigate','location_id':'invented'}))
        with self.assertRaises(ValueError):
            agent.invoke_guidance('Take me to gate 999',[{'id':'office','text':'Office'}])

@unittest.skipUnless(importlib.util.find_spec('faster_whisper'), 'Use .rag-venv')
class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_pcm_stt_guidance_navigation_and_tts_in_one_continuous_session(self):
        from pipeline_rag import pipeline_session
        from test_live_rag import Socket
        ws=Socket(); trace=[]
        class Turns:
            def __init__(self,*args): pass
            def feed(self,packet):return [('speech_start',None),('utterance',np.ones(1000,dtype=np.float32))]
        stt=Mock();stt.transcribe.return_value=NS(is_empty=False,text='Take me to office')
        tts=Mock();tts.synthesize_bytes.return_value=(b'fake-mp3','audio/mpeg')
        agent=Mock();agent.invoke_guidance.return_value={'answer':'I can guide you.','action':'navigate','location_id':'office'}
        runtime=NS(create_agent=Mock(return_value=agent))
        def request(route,payload=None):
            trace.append(route)
            if route=='context':return {'map_id':'map.yaml','locations':[{'id':'office','text':'Office'}]}
            if route=='goal':return {'state':'submitted','destination':'Office','message':'Goal submitted'}
            return {'ready':True}
        with patch('pipeline_rag.SpeechTurns',Turns),patch('g1_conversation.stt_engine.STTEngine',return_value=stt),patch('g1_conversation.tts_engine.TTSEngine',return_value=tts):
            task=asyncio.create_task(pipeline_session(ws,runtime,'en','Passenger',16000,request))
            await ws.queue.put(b'\x00\x00'*640)
            async def wait():
                while not any(event['type']=='navigation' for event in ws.sent):await asyncio.sleep(.005)
                while not any(event['type']=='audio_file' and event for event in ws.sent):await asyncio.sleep(.005)
            await asyncio.wait_for(wait(),2)
            await ws.queue.put(None);await asyncio.wait_for(task,2)
        self.assertIn('prepare-navigation',trace);self.assertIn('goal',trace)
        self.assertEqual(trace[-1],'cancel')
        stt.transcribe.assert_called_once()
        self.assertTrue(any(event['type']=='transcript' and event.get('role')=='user' for event in ws.sent))

if __name__=='__main__':unittest.main()
