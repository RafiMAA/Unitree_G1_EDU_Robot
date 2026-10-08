"""Offline acceptance replay; fixture destinations and Gemini responses, no robot goals.

Runs the real dialogue, FAISS retriever interface and status narrator with mocked
knowledge/model responses. Companion ROS and MuJoCo checks exercise motion.
"""
import json
from g1_core.guide_behavior import CONFIG
from types import SimpleNamespace
from unittest.mock import Mock
from g1_conversation.agent.rag_agent import DirectRAGAgent
from g1_conversation.agent.dialogue import GuideNarrator

locations = [{'id':'office','text':'Office','x':2.,'y':0.}, {'id':'entrance','text':'Entrance','x':0.,'y':3.}]
context = {'map_id':'acceptance.yaml', 'map_name':'acceptance', 'localized':True}
llm = Mock()
llm.invoke.return_value = SimpleNamespace(content=json.dumps({
    'answer': "I'm not sure how many people visit there. The information desk may be able to tell you more; can I help you find somewhere?",
    'action':'none','location_id':None,'destination_name':None}))
retriever = Mock(); retriever.invoke.return_value=[]
agent = DirectRAGAgent('en', llm, retriever)
narrator = GuideNarrator(agent.dialogue)
goals, cancels = 0, 0

def turn(text):
    global goals, cancels
    result = agent.invoke_guidance(text, locations, {}, context)
    print('Passenger:', text)
    print('Guide:', result['answer'])
    if result['action']=='navigate':
        if agent.dialogue.state=='GUIDING':
            cancels += 1; print('[Cancel previous escort]')
        label = next(item for item in locations if item['id']==result['location_id'])
        goals += 1
        agent.dialogue.started(label); narrator.reset(); narrator.last_spoken=1.
        print(f"[After playback starts: follow_me gesture, face supplied user pose, align to path, escort at {CONFIG['walking']['linear']:.2f} m/s / {CONFIG['walking']['angular']:.2f} rad/s]")
    print('[State=%s, action=%s, total goals=%d]' % (agent.dialogue.state, result['action'], goals))
    return result

def status(value, now):
    line = narrator.observe(value, now)
    if line: print('Guide (navigator feedback):', line)

turn('Take me to the office.')
status({'state':'navigating','distance_remaining':4.},2.)
turn('Okay.')
turn('Can you navigate me to the entrance?')
status({'state':'navigating','distance_remaining':4.},2.)
status({'state':'navigating','distance_remaining':3.,'heading_error':.8},12.)
status({'state':'blocked'},22.)
status({'state':'navigating','distance_remaining':.8},32.)
status({'state':'succeeded'},40.)
turn('Where is the office? Can you show me?')
turn('What are the things in the Albert area?')
turn('How many people go around there?')
turn('Thanks, bye.')
assert goals==2 and cancels==1
assert retriever.invoke.call_count==1
print('PASS: 2 validated escort goals, 1 switch cancellation, acknowledgements and farewell add no goals; general question uses retrieval.')
