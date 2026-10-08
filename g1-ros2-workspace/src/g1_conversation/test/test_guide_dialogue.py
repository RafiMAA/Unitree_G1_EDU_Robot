"""Concierge acceptance replay, intent guards and real-status narration edges."""
from g1_conversation.agent.dialogue import GuideDialogue, GuideNarrator
from g1_core.guide_behavior import GesturePlayer

LOCATIONS = [{'id':'office', 'text':'Office', 'x':2., 'y':0.}, {'id':'entrance', 'text':'Entrance', 'x':0., 'y':3.}]
CONTEXT = {'map_id': 'test.yaml'}


def test_acceptance_dialogue_without_inventing_goals():
    guide = GuideDialogue()
    first = guide.decide('Take me to the office.', LOCATIONS, CONTEXT)
    assert first['action'] == 'navigate' and first['location_id'] == 'office'
    assert 'Follow me' in first['answer']
    guide.started(LOCATIONS[0])
    for ack in ('Okay.', 'yes', 'sure', 'thanks'):
        assert guide.decide(ack, LOCATIONS, CONTEXT)['action'] == 'none'
    repeat = guide.decide('Take me to Office', LOCATIONS, CONTEXT)
    assert repeat['action'] == 'none' and 'already' in repeat['answer']
    switch = guide.decide('Can you navigate me to the entrance?', LOCATIONS, CONTEXT)
    assert switch['location_id'] == 'entrance' and 'instead' in switch['answer']
    offer = guide.decide('Where is the office? Can you show me?', LOCATIONS, CONTEXT)
    assert offer['action'] == 'none' and 'Office' in offer['answer']
    assert guide.decide('yes', LOCATIONS, CONTEXT)['action'] == 'none'
    guide.update_navigation({'state': 'canceled'})
    assert guide.decide('Take me there', LOCATIONS, CONTEXT)['location_id'] == 'office'
    unknown = guide.decide('What are the things in the Albert area?', LOCATIONS, CONTEXT)
    assert unknown['action'] == 'none' and 'Office or Entrance' in unknown['answer']
    general = guide.decide('How many people go around there?', LOCATIONS, CONTEXT)
    assert general['answer'] is None and general['intent'] == 'general'
    bye = guide.decide('Thanks, bye.', LOCATIONS, CONTEXT)
    assert bye['action'] == 'none' and 'journey' in bye['answer']


def test_narration_only_once_and_only_from_feedback():
    guide = GuideDialogue(); guide.started(LOCATIONS[0])
    narrator = GuideNarrator(guide); narrator.last_spoken = 1.
    assert narrator.observe({'state':'navigating', 'distance_remaining':4.}, 2.) is None
    assert 'left' in narrator.observe({'state':'navigating', 'distance_remaining':3., 'heading_error':.8}, 12.)
    assert narrator.observe({'state':'navigating', 'distance_remaining':3., 'heading_error':.8}, 21.) is None
    assert 'moment' in narrator.observe({'state':'blocked'}, 22.)
    assert narrator.observe({'state':'blocked'}, 24.) is None
    assert 'Almost' in narrator.observe({'state':'navigating', 'distance_remaining':.8}, 32.)
    assert 'Office' in narrator.observe({'state':'succeeded'}, 35.)
    assert guide.state == 'ARRIVED'
    assert narrator.observe({'state':'succeeded'}, 36.) is None


def test_stop_and_map_change_clear_escort():
    guide = GuideDialogue(); guide.decide('Take me to Office', LOCATIONS, CONTEXT); guide.started(LOCATIONS[0])
    assert guide.decide('Stop navigation to the office', LOCATIONS, CONTEXT)['action'] == 'cancel'
    assert guide.state == 'IDLE'
    guide.decide('Where is Office', LOCATIONS, CONTEXT)
    assert guide.decide('Take me there', LOCATIONS, {'map_id':'different'})['action'] == 'none'


def test_gestures_preserve_legs_and_return_to_standing():
    for name in ('greet', 'point', 'follow_me', 'idle-look-around'):
        player = GesturePlayer(); assert player.play_gesture(name, now=0.)
        pose = player.apply([0.]*29, now=1.4)
        assert pose[:12] == [0.]*12
        assert any(pose[12:])
        assert player.apply([0.]*29, now=10.) == [0.]*29
    player = GesturePlayer(); player.play_gesture('greet', now=0.)
    pose = player.apply([0.]*29, moving=True, now=1.4)
    assert pose[12:15] == [0.]*3 and max(abs(v) for v in pose) <= .12


def test_clarification_can_be_answered_with_just_a_saved_name():
    guide = GuideDialogue()
    assert guide.decide('Take me to the Albert area', LOCATIONS, CONTEXT)['action']=='none'
    assert guide.decide('Office', LOCATIONS, CONTEXT)['location_id']=='office'
    guide = GuideDialogue()
    guide.decide('Where is Albert area', LOCATIONS, CONTEXT)
    assert guide.decide('Office', LOCATIONS, CONTEXT)['action']=='none'
