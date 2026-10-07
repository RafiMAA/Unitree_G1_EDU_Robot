"""Deterministic name/language onboarding for a new passenger session."""

import re
import unicodedata


LANGUAGE_QUESTION = "Nice to meet you, {name}. Which language do you prefer?"
NAME_RETRY = "Sorry, I did not catch your name. What is your name?"

AIRPORT_INTRODUCTIONS = {
    'en': 'Nice to meet you, {name}. I can help you find boarding gates, washrooms, baggage claim and other places in the airport. Where would you like to go?',
    'fr': "Enchanté, {name}. Je peux vous aider à trouver les portes d'embarquement, les toilettes et les autres services de l'aéroport. Où souhaitez-vous aller ?",
    'de': 'Schön, Sie kennenzulernen, {name}. Ich helfe Ihnen, Flugsteige, Toiletten und andere Einrichtungen im Flughafen zu finden. Wohin möchten Sie gehen?',
    'es': 'Mucho gusto, {name}. Puedo ayudarle a encontrar puertas de embarque, baños y otros servicios del aeropuerto. ¿Adónde desea ir?',
    'ru': 'Приятно познакомиться, {name}. Я помогу найти выходы на посадку, туалеты и другие службы аэропорта. Куда вы хотите пройти?',
    'ja': '{name}さん、よろしくお願いします。搭乗口、お手洗い、空港内の施設を探すお手伝いをします。どちらへ行きたいですか？',
    'zh': '很高兴认识您，{name}。我可以帮您寻找登机口、洗手间和机场内的其他设施。您想去哪里？',
    'ko': '만나서 반갑습니다, {name}님. 탑승구, 화장실 및 공항 내 시설을 찾는 것을 도와드릴 수 있습니다. 어디로 가시겠습니까?',
    'hi': 'आपसे मिलकर खुशी हुई, {name}। मैं हवाई अड्डे में बोर्डिंग गेट, शौचालय और अन्य सुविधाएँ खोजने में आपकी मदद कर सकता हूँ। आप कहाँ जाना चाहते हैं?',
    'si': 'ඔබ හමුවීම සතුටක්, {name}. ගුවන්තොටුපළේ ගුවන් යානයට පිවිසෙන දොරටු, වැසිකිළි සහ අනෙකුත් සේවා සොයා ගැනීමට මට උදව් කළ හැකියි. ඔබට යන්න අවශ්\u200dය කොහෙටද?',
    'ta': 'உங்களைச் சந்திப்பதில் மகிழ்ச்சி, {name}. விமான நிலையத்தில் ஏறும் வாயில்கள், கழிப்பறைகள் மற்றும் பிற வசதிகளைக் கண்டறிய உதவ முடியும். நீங்கள் எங்கு செல்ல விரும்புகிறீர்கள்?',
}

_GREETING_PREFIX = r"(?:(?:hi|hello|hey)(?:\s+there)?[\s,!.:-]+)?"
_NAME_PATTERNS = (
    (
        "explicit",
        _GREETING_PREFIX + r"my\s+name(?:\s+is|['’]s)\s+(.+)",
    ),
    ("explicit", _GREETING_PREFIX + r"call\s+me\s+(.+)"),
    ("ambiguous", _GREETING_PREFIX + r"i\s+am\s+(.+)"),
    ("ambiguous", _GREETING_PREFIX + r"i['’]m\s+(.+)"),
    ("ambiguous", _GREETING_PREFIX + r"this\s+is\s+(.+)"),
    ("ambiguous", r"(?:hi|hello|hey)[,\s]+(.+)"),
)

# These words make an unchecked short reply much more likely to be a sentence
# than a passenger's name. Deliberately do not blacklist valid names such as
# May, Will, Grace, Hope, or Mark.
_BARE_REPLY_WORDS = {
    "app", "can", "could", "favorite", "first", "happy", "hello", "here",
    "hey", "hi", "is", "like", "looking", "much", "my", "need", "no",
    "please", "prefer", "repeat", "ride", "speak", "taxi", "thank", "thanks",
    "that", "there", "this", "time", "very", "want", "what", "when", "where",
    "who", "why", "would", "yes", "you", "your",
}
_AMBIGUOUS_FIRST_WORDS = {
    "fine", "from", "going", "good", "happy", "here", "interested", "looking",
    "my", "not", "ready", "sorry", "there", "trying", "visiting", "waiting",
}
_CLAUSE_WORDS = {
    "and", "because", "but", "for", "from", "how", "that", "then", "to", "when",
    "where", "while", "who", "why", "with",
}


def _is_name_token(token: str) -> bool:
    """Return whether one token contains only letters and name punctuation."""
    token = token.strip(".")
    if not token:
        return False
    parts = re.split(r"['’.-]", token)
    return all(
        part
        and all(
            unicodedata.category(character).startswith(("L", "M"))
            for character in part
        )
        for part in parts
    )


def _validated_name(candidate: str, introduction: str, source: str) -> str | None:
    """Validate a captured or bare name without treating sentences as names."""
    candidate = candidate.strip().strip(".,!?;:…-")
    candidate = " ".join(candidate.split())
    words = candidate.split()
    if not words or len(words) > 4 or len(candidate) > 60:
        return None
    if any(character.isdigit() or character == "_" for character in candidate):
        return None
    if not all(_is_name_token(word) for word in words):
        return None

    folded_words = [word.strip(".").casefold() for word in words]
    if any(word in _CLAUSE_WORDS for word in folded_words):
        return None
    if introduction == "ambiguous" and folded_words[0] in _AMBIGUOUS_FIRST_WORDS:
        return None
    if introduction == "bare":
        if "?" in source or any(word in _BARE_REPLY_WORDS for word in folded_words):
            return None

    return candidate


def extract_passenger_name(text: str) -> str | None:
    """Extract a spoken name while rejecting short sentence-like answers."""
    cleaned = " ".join(text.strip().split())
    # Whisper occasionally joins these two words as "nameis". The trailing
    # boundary avoids changing unrelated phrases such as "name island".
    cleaned = re.sub(
        r"\bname\s*is\b", "name is", cleaned, flags=re.IGNORECASE
    )

    for introduction, pattern in _NAME_PATTERNS:
        match = re.fullmatch(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            return _validated_name(match.group(1), introduction, cleaned)

    return _validated_name(cleaned, "bare", cleaned)


def get_airport_introduction(lang_code: str, name: str) -> str:
    template = AIRPORT_INTRODUCTIONS.get(lang_code, AIRPORT_INTRODUCTIONS["en"])
    return template.format(name=name)
