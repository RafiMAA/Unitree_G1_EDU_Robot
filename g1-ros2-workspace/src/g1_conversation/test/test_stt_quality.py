"""Silence/confidence filtering, without downloading or calling models."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import numpy as np
import pytest
from g1_conversation.stt_engine import STTEngine


def engine_with_segment(prob=.05, confidence=-.3, compression=1.):
    engine=STTEngine(model_name='base',backend='faster-whisper')
    engine._loaded=True
    segment=NS(text='Take me to Office',avg_logprob=confidence,no_speech_prob=prob,compression_ratio=compression)
    engine.model=NS(transcribe=Mock(return_value=(iter([segment]), NS(language='en'))))
    return engine


def test_silence_is_rejected_before_loading_or_decoding():
    engine=engine_with_segment()
    assert engine.transcribe(np.zeros(16000,dtype=np.float32),'en').is_empty
    engine.model.transcribe.assert_not_called()


@pytest.mark.parametrize('prob,confidence,compression',[(.9,-.3,1.),(.1,-1.4,1.),(.1,-.3,3.)])
def test_hallucination_signals_each_reject_a_turn(prob,confidence,compression):
    engine=engine_with_segment(prob,confidence,compression)
    wave=(np.sin(np.arange(16000)*.1)*.05).astype(np.float32)
    assert engine.transcribe(wave,'en').is_empty
    assert engine.model.transcribe.call_args.kwargs['vad_filter'] is True


def test_valid_voice_still_reaches_the_agent():
    engine=engine_with_segment()
    wave=(np.sin(np.arange(16000)*.1)*.05).astype(np.float32)
    result=engine.transcribe(wave,'en')
    assert not result.is_empty and result.text=='Take me to Office'
