"""Shared, ROS-independent concierge settings and bounded gesture animation.

Edit CONFIG here; all voice, narration and escort motion settings use this file.
G1's 29-DOF model has no neck: idle looking/nodding uses a small waist motion.
"""
import math
import time

CONFIG = {
    'persona': (
        'You are a warm, attentive airport guide speaking to a passenger beside you. '
        'Use one to three short natural spoken sentences, no markdown or lists. '
        'Avoid technical words such as request, tool, map, coordinates or navigation server. '
        'Vary your wording; never repeat your previous sentence verbatim. '
        'Use only retrieved facts and the supplied saved destinations. Never invent directions, '
        'landmarks, distances, visitor counts or arrival. For missing facts, admit uncertainty '
        'and offer a useful next step. Keep follow-up questions relevant and gentle. '
        'Location questions explain verified information and offer an escort; only an explicit '
        'take/guide/navigate command starts walking. Okay/yes/sure/thanks are acknowledgements, '
        'never movement commands. Respond in the passenger language.'
    ),
    'phrases': {
        'greet': ["Hello! I'm your airport guide. Where would you like to go?", "Welcome! I can help you find your way. Where are you heading?"],
        'follow': ["Of course! Follow me, I'll show you the way to {destination}.", "Certainly, let's head to {destination}. Come along with me."],
        'switch': ["Sure, let's go to {destination} instead. Follow me.", "Of course, we'll head to {destination} instead. Come along."],
        'already': ["We're already on our way to {destination}, just follow me.", "Yes, we're heading to {destination}. Come along with me."],
        'offer': ["I can show you the way to {destination}. Would you like me to take you there?", "I can help you find {destination}. Say 'take me there' when you're ready."],
        'ack': ["Of course.", "You're welcome."],
        'stop': ["Of course, I'll stop here. Let me know when you're ready.", "I'll wait here with you. Where would you like to go next?"],
        'bye': ["You're very welcome. Have a lovely journey!", "It was lovely helping you. Safe travels!"],
        'clarify': ["Sorry, I didn't catch that. Did you mean {choices}?", "Could you help me with the place name? Did you mean {choices}?"],
        'empty': ["I don't have a destination to guide you to yet. The information desk can help you find your way."],
        'progress': ["Just a little further.", "We're making our way there."],
        'near': ["Almost there.", "We're getting close now."],
        'left': ["We'll turn left here."], 'right': ["We'll turn right here."],
        'blocked': ["Excuse me, one moment, I'm finding a way around.", "One moment, I'll check another way through."],
        'arrived': ["Here we are, this is {destination}. Is there anything else I can help you with?", "We've arrived at {destination}. Can I help with anything else?"],
        'failed': ["I'm sorry, I can't get through safely right now. Would you like to try another destination?", "I'm sorry, I couldn't get us there. Shall we try another place?"],
    },
    'narration': {'poll_seconds': .5, 'interval_seconds': 20., 'min_gap_seconds': 8.,
                  'blocked_cooldown_seconds': 25., 'idle_seconds': 30.,
                  'playback_timeout_seconds': 20., 'playback_end_timeout_seconds': 90.,
                  'near_distance': 1., 'near_fraction': .25, 'minimum_progress': .25,
                  'feedback_stale_seconds': 3., 'turn_threshold_radians': .55},
    'speech': {'minimum_rms': .0025, 'noise_multiplier': 3.,
               'start_frames': 6, 'minimum_voiced_frames': 6, 'minimum_voice_ratio': .30,
               'pause_frames': 22, 'minimum_logprob': -1.1, 'maximum_no_speech': .65,
               'tts_timeout_seconds': 25., 'echo_seconds': 3., 'microphone_tail_seconds': .6},
    'walking': {'linear': .5, 'angular': .8, 'face_user_timeout_seconds': 8., 'user_pose_max_age_seconds': 2.,
                'goal_timeout_seconds': 600., 'gesture_walking_scale': .25},
    'gestures': {
        # Joint offsets relative to the current controller target. No leg commands.
        'greet': {'duration': 2.8, 'joints': {22: -.35, 23: -.10, 25: .20, 28: .12, 14: .035}},
        'point': {'duration': 2.5, 'joints': {22: -.45, 25: .08}},
        'follow_me': {'duration': 3., 'joints': {22: -.35, 25: .22, 27: .10}},
        'idle-look-around': {'duration': 4., 'joints': {12: .055}},
    },
}


class Phrases:
    def __init__(self):
        self.indices = {}
        self.last = None

    def say(self, key, **values):
        options = CONFIG['phrases'][key]
        index = self.indices.get(key, 0)
        result = options[index % len(options)].format(**values)
        if result == self.last and len(options) > 1:
            index += 1
            result = options[index % len(options)].format(**values)
        self.indices[key] = index + 1
        self.last = result
        return result


class GesturePlayer:
    """Smooth bounded offsets, attenuated while walking; no locomotion activation."""
    def __init__(self):
        self.name = None
        self.started = 0.

    def play_gesture(self, name, now=None):
        name = {'wave': 'greet', 'wave_with_turn': 'greet'}.get(name, name)
        if name not in CONFIG['gestures']:
            return False
        self.name, self.started = name, time.monotonic() if now is None else now
        return True

    def apply(self, target, moving=False, now=None):
        if self.name is None:
            return target
        spec = CONFIG['gestures'][self.name]
        phase = ((time.monotonic() if now is None else now) - self.started) / spec['duration']
        if not 0 <= phase <= 1:
            self.name = None
            return target
        envelope = math.sin(math.pi * phase)**2
        for joint, offset in spec['joints'].items():
            if moving and joint in (12, 13, 14):
                continue
            target[joint] += offset * envelope * (CONFIG['walking']['gesture_walking_scale'] if moving else 1.)
        return target
