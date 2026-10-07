"""Conservative saved-location matching; fuzzy scores are not intent probabilities."""
import re
import unicodedata
from rapidfuzz import fuzz

_GROUPS = (
    ('washroom', 'washrooms', 'restroom', 'restrooms', 'toilet', 'toilets', 'bathroom', 'bathrooms'),
    ('information desk', 'information counter', 'help desk', 'help counter'),
    ('baggage claim', 'baggage reclaim', 'luggage claim', 'luggage reclaim'),
    ('check in', 'checkin', 'check-in'),
)

def normalize(value):
    text = unicodedata.normalize('NFKC', str(value)).casefold()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    for group in _GROUPS:
        for alias in sorted(group, key=len, reverse=True):
            text = re.sub(r'\b' + re.escape(alias.replace('-', ' ')) + r'\b', group[0], text)
    return text


def requested_place(question):
    """Extract names from common English wayfinding phrases; Gemini handles others."""
    match = re.search(
        r'\b(?:take me to|navigate to|guide me to|lead me to|escort me to|show me(?: the way)? to|'
        r'bring me to|walk me to|go to|where (?:is|are)|where can i find|how (?:do|can) i get to|'
        r'can you (?:find|show me)|i (?:want|need|would like) to (?:go to|reach|find)|i (?:need|want) to find)\s+(?:the\s+|a\s+|an\s+)?(.+)',
        question, re.I,
    )
    if not match:
        return None
    text = re.sub(r'\s+(?:please|thank you|thanks)\s*[?.!]*$', '', match.group(1), flags=re.I)
    text = re.split(r'\s+(?:because|since|from here|I am|I have|I need to|I want to)\b', text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r'^(?:nearest|closest)\s+', '', text, flags=re.I)
    text = text.strip(' ?.!,')
    text = re.sub(r'\s+(?:located|situated)$', '', text, flags=re.I)
    return text.strip(' ?.!,') or None


def suppress_navigation(question):
    if re.search(r'\b(?:hypothetical|suppose|for example|translate|what time|opening hours|what does.+mean)\b', question, re.I):
        return True
    if re.search(r'[\"“].*(?:take me|navigate|where is).*[\"”]', question, re.I):
        return True
    return bool(re.search(r"\b(?:don['’]?t|do not|not now|no need to|just (?:asking|explain|tell)|without (?:moving|going|navigation))\b", question, re.I))


def match_destination(query, locations):
    if not query:
        return {'id': None, 'candidates': []}
    query = normalize(query)
    if len(query) < 3:
        return {'id': None, 'candidates': []}
    scores = []
    for location in locations:
        names = [location['text'], *location.get('aliases', [])]
        score = 0.
        for name in names:
            name = normalize(name)
            # Never fuzzy-match one gate/floor/terminal number to another.
            numbers = re.findall(r'\d+', query)
            if numbers and numbers != re.findall(r'\d+', name):
                continue
            value = fuzz.token_sort_ratio(query, name)
            if set(query.split()).issubset(set(name.split())):
                value = max(value, 98. if query != name else 100.)
            score = max(score, value)
        scores.append((score, location['id'], location['text']))
    scores.sort(reverse=True)
    candidates = [{'id': identifier, 'name': name, 'score': round(score, 1)} for score, identifier, name in scores if score >= 75][:5]
    if not scores or scores[0][0] < 86:
        return {'id': None, 'candidates': candidates}
    if len(scores) > 1 and scores[0][0] - scores[1][0] < 8:
        return {'id': None, 'candidates': candidates}
    return {'id': scores[0][1], 'candidates': candidates}
