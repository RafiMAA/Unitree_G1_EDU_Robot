"""Wayfinding paraphrases, fuzzy names and ambiguity regression tests."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from g1_conversation.agent.destinations import match_destination, requested_place
from g1_conversation.agent.rag_agent import DirectRAGAgent

LOCATIONS = [{'id':'office','text':'Office'}, {'id':'check','text':'Bag checking area'}, {'id':'wc','text':'Toilets'}]

def agent_result(question, locations=LOCATIONS, action='none', label_id=None, name=None):
    llm = Mock()
    llm.invoke.return_value = SimpleNamespace(content=json.dumps({'answer':'I can help.','action':action,'location_id':label_id,'destination_name':name}))
    retriever=Mock();retriever.invoke.return_value=[]
    agent=DirectRAGAgent('en',llm,retriever)
    return agent.invoke_guidance(question,locations)

@pytest.mark.parametrize('question',[
    'Take me to the office', 'Navigate to the office', 'Can you navigate me to the office?',
    'Could you guide me to the office please?', 'I want to go to the office',
    'Take me to the office because I need help',
])
def test_wayfinding_requests_navigate_even_when_model_only_answers(question):
    result=agent_result(question)
    assert result['action']=='navigate'
    assert result['location_id']=='office'

@pytest.mark.parametrize('question', ['Take me to the restroom', 'Take me to the offce', 'Navigate to bag cheking area'])
def test_synonyms_and_minor_recognition_errors(question):
    result=agent_result(question)
    assert result['action']=='navigate'
    assert result['location_id'] in {'wc','office','check'}

@pytest.mark.parametrize('question',[
    'Do not take me to the office', "Don't navigate to the office", 'Where is the office? Just asking.',
    'What time does the office open?', 'Translate "take me to the office"',
    'For example, where is the office?',
])
def test_negation_facts_and_quoted_commands_do_not_move(question):
    result=agent_result(question,action='navigate',label_id='office',name='Office')
    assert result['action']=='none'


def test_ambiguous_washrooms_require_clarification():
    locations=[{'id':'east','text':'East washroom'},{'id':'west','text':'West washroom'}]
    result=agent_result('Where is the washroom?',locations,action='navigate',label_id='east',name='East washroom')
    assert result['action']=='none'
    assert 'East washroom' in result['answer'] and 'West washroom' in result['answer']
    assert agent_result('Navigate to the east washroom',locations)['location_id']=='east'


def test_gate_numbers_must_not_fuzzy_match_other_gate_numbers():
    assert match_destination('Gate 13',[{'id':'g12','text':'Gate 12'}])['id'] is None
    assert match_destination('Gate 13',[{'id':'g12','text':'Gate 12'},{'id':'g13','text':'Gate 13'}])['id']=='g13'


def test_unknown_place_does_not_use_model_guessed_coordinates_or_label():
    result=agent_result('Take me to the moon',action='navigate',label_id='office',name='Office')
    assert result['action']=='none'


def test_contextual_requests_require_an_actual_prior_offer():
    llm=Mock(); retriever=Mock();retriever.invoke.return_value=[]
    agent=DirectRAGAgent('en',llm,retriever)
    assert agent.invoke_guidance('Yes, take me there',LOCATIONS)['action']=='none'
    agent.invoke_guidance('Where is the office?',LOCATIONS)
    assert agent.invoke_guidance('Yes, take me there',LOCATIONS)['location_id']=='office'


def test_cancellation_has_priority_over_destination():
    assert agent_result('Stop navigation to the office',action='cancel')['action']=='cancel'


@pytest.mark.parametrize('content', ['Here are directions to the office.', '{"answer": "I can help', ''])
def test_malformed_guidance_speaks_clarification_without_moving(content):
    llm = Mock(); llm.invoke.return_value = SimpleNamespace(content=content)
    retriever = Mock(); retriever.invoke.return_value = []
    agent = DirectRAGAgent('en', llm, retriever)
    result = agent.invoke_guidance('How do airport services work?', LOCATIONS)
    assert result['answer'] and result['action'] == 'none'
    assert result['location_id'] is None
    assert len(agent.history) == 1
    kwargs = llm.invoke.call_args.kwargs
    assert kwargs['response_mime_type'] == 'application/json'
    assert kwargs['response_json_schema']['properties']['action']['enum'] == ['none', 'navigate', 'cancel']
    assert kwargs['max_output_tokens'] >= 1024


def test_guidance_receives_actual_map_identity_and_saved_catalog():
    llm = Mock(); llm.invoke.return_value = SimpleNamespace(content=json.dumps({'answer': 'The loaded map is g1_map.', 'action': 'none'}))
    retriever = Mock(); retriever.invoke.return_value = []
    agent = DirectRAGAgent('en', llm, retriever)
    agent.invoke_guidance('Which map are you using?', LOCATIONS, {}, {'map_name': 'g1_map', 'mode': 'localization', 'localized': False})
    prompt = '\n'.join(message.content for message in llm.invoke.call_args.args[0])
    assert '"map_name": "g1_map"' in prompt
    assert '"name": "Office"' in prompt
    assert 'Never invent a default airport map' in prompt

@pytest.mark.parametrize('question', ['Where is the office?', 'Can you show me the office?', 'How do I get to the office?', 'Where is the office? Can you show me?'])
def test_location_questions_offer_an_escort_without_starting(question):
    result = agent_result(question)
    assert result['action'] == 'none' and 'Office' in result['answer']


def test_translated_acknowledgement_cannot_issue_model_hallucinated_goal():
    llm=Mock(); retriever=Mock();retriever.invoke.return_value=[]
    llm.invoke.return_value=SimpleNamespace(content=json.dumps({'answer':'Okay','action':'navigate','location_id':'office','destination_name':'Office','normalized_input':'Okay'}))
    # Translation of the validated reply is a separate call, with no action schema.
    llm.invoke.side_effect=[llm.invoke.return_value, SimpleNamespace(content='හරි')]
    agent=DirectRAGAgent('si',llm,retriever)
    result=agent.invoke_guidance('හරි',LOCATIONS)
    assert result['action']=='none' and result['answer']=='හරි'


def test_multilingual_escort_still_requires_a_valid_saved_destination():
    llm=Mock(); retriever=Mock();retriever.invoke.return_value=[]
    llm.invoke.side_effect=[SimpleNamespace(content=json.dumps({'answer':'Follow me','action':'navigate','location_id':'invented','destination_name':'Office','normalized_input':'Take me to Office'})), SimpleNamespace(content='මා සමඟ එන්න')]
    agent=DirectRAGAgent('si',llm,retriever)
    result=agent.invoke_guidance('මාව කාර්යාලයට රැගෙන යන්න',LOCATIONS)
    assert result['location_id']=='office' and result['action']=='navigate'
