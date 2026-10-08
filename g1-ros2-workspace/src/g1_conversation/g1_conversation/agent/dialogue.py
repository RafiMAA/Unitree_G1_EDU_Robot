"""Deterministic dialogue guard: language models cannot re-trigger an escort."""
import logging
import re
from g1_core.guide_behavior import CONFIG, Phrases
from .destinations import match_destination, normalize, requested_place, suppress_navigation

LOG = logging.getLogger('airport.guide')


class GuideDialogue:
    def __init__(self):
        self.state = 'IDLE'
        self.destination = None
        self.offered = None
        self.map_id = None
        self.clarifying_escort = False
        self.phrases = Phrases()

    def update_navigation(self, status):
        state = status.get('state')
        if state == 'succeeded':
            self.state = 'ARRIVED'
        elif state in ('failed', 'canceled', 'rejected', 'unavailable'):
            self.state = 'IDLE'
            self.destination = None
        LOG.info('dialogue=%s navigation=%s destination=%s', self.state, state, self.destination)

    def started(self, location):
        self.state, self.destination = 'GUIDING', location

    def decide(self, text, locations, context):
        map_id = context.get('map_id')
        if self.map_id != map_id:
            self.state, self.destination, self.offered = 'IDLE', None, None
            self.map_id = map_id
        value = normalize(text)
        intent = 'general'
        result = {'answer': None, 'action': 'none', 'location_id': None, 'destination_name': None}
        if re.match(r'^(?:(?:please|can you|could you|would you|hey|robot) )*(?:stop|wait|cancel|pause)\b', value):
            intent = 'stop'
            result.update(answer=self.phrases.say('stop'), action='cancel')
            self.state, self.destination = 'IDLE', None
        elif re.search(r'\b(?:bye|goodbye|see you)\b', value):
            intent = 'goodbye'
            result.update(answer=self.phrases.say('bye'), action='cancel' if self.state == 'GUIDING' else 'none')
            self.state, self.destination, self.offered = 'IDLE', None, None
        elif re.fullmatch(r'(?:okay|ok|yes|sure|alright|thanks|thank you|great|fine)(?: (?:thanks|thank you|please))?', value):
            intent = 'acknowledgement'
            result['answer'] = self.phrases.say('ack')
        elif re.fullmatch(r'(?:hi|hello|hey|good morning|good afternoon)', value):
            intent = 'greeting'
            result['answer'] = self.phrases.say('greet')
        else:
            explicit = bool(re.search(r'\b(?:take|guide|lead|escort|bring|walk) me\b|\bnavigate(?: me)? to\b|\bi (?:want|need|would like) to (?:go|reach)\b', text, re.I))
            place = requested_place(text)
            if explicit and place and normalize(place) in ('there', 'that place', 'that one'):
                place = self.offered['text'] if self.offered else None
            # "Albert area" and similar unrecognized area questions also need clarification.
            area = re.search(r'\b(?:in|around) (?:the )?(.+?) area\b', text, re.I)
            place = place or (area.group(1) if area else None)
            if self.state == 'CLARIFYING' and not place and match_destination(text, locations)['id']:
                place, explicit = text, self.clarifying_escort
            if suppress_navigation(text):
                explicit = False
                place = None
            if place or explicit:
                intent = 'escort' if explicit else 'location_question'
                resolution = match_destination(place, locations)
                label = next((loc for loc in locations if loc['id'] == resolution['id']), None)
                if label is None:
                    self.clarifying_escort = explicit
                    self.state = 'CLARIFYING' if self.state != 'GUIDING' else self.state
                    candidates = resolution['candidates'][:2]
                    names = [item['name'] for item in candidates] or [item['text'] for item in locations[:2]]
                    result['answer'] = self.phrases.say('clarify', choices=' or '.join(names)) if names else self.phrases.say('empty')
                else:
                    self.offered = label
                    result['destination_name'] = label['text']
                    if self.state == 'GUIDING' and self.destination and self.destination['id'] == label['id']:
                        result['answer'] = self.phrases.say('already', destination=label['text'])
                    elif explicit:
                        result.update(action='navigate', location_id=label['id'],
                                      answer=self.phrases.say('switch' if self.state == 'GUIDING' else 'follow', destination=label['text']))
                    else:
                        # No invented spatial description when only waypoint coordinates exist.
                        result['answer'] = self.phrases.say('offer', destination=label['text'])
        result['intent'] = intent
        LOG.info('dialogue=%s intent=%s destination=%s action=%s', self.state, intent, result['destination_name'], result['action'])
        return result


class GuideNarrator:
    """Status edges and distance milestones; never invent progress or an arrival."""
    def __init__(self, dialogue):
        self.dialogue = dialogue
        self.reset()

    def reset(self):
        self.distance = None
        self.last_spoken = 0.
        self.last_blocked = -1e9
        self.near = False
        self.turn = None
        self.finished = False

    def observe(self, status, now):
        if self.finished or self.dialogue.state != 'GUIDING':
            return None
        state = status.get('state')
        cfg = CONFIG['narration']
        key = None
        distance = status.get('distance_remaining')
        if state == 'navigating' and isinstance(distance, (int, float)) and self.distance is None:
            self.distance = distance
        if state in ('succeeded', 'failed', 'rejected', 'unavailable', 'canceled'):
            self.finished = True
            key = 'arrived' if state == 'succeeded' else ('stop' if state == 'canceled' else 'failed')
        elif state in ('blocked', 'recovering', 'paused') and now-self.last_blocked >= cfg['blocked_cooldown_seconds']:
            key, self.last_blocked = 'blocked', now
        elif state == 'navigating' and now-self.last_spoken >= cfg['min_gap_seconds']:
            distance = status.get('distance_remaining')
            if isinstance(distance, (int, float)):
                if self.distance is None:
                    self.distance = distance
                if not self.near and self.distance > cfg['near_distance'] and distance <= min(cfg['near_distance'], self.distance * cfg['near_fraction']):
                    key, self.near = 'near', True
                elif abs(status.get('heading_error', 0)) > cfg['turn_threshold_radians']:
                    turn = 'left' if status['heading_error'] > 0 else 'right'
                    if turn != self.turn:
                        key, self.turn = turn, turn
                elif now-self.last_spoken >= cfg['interval_seconds'] and distance < self.distance-cfg['minimum_progress']:
                    key = 'progress'
        if key:
            destination = (self.dialogue.destination or {}).get('text', 'your destination')
            line = self.dialogue.phrases.say(key, destination=destination)
            self.last_spoken = now
            self.dialogue.update_navigation(status)
            return line
        return None
