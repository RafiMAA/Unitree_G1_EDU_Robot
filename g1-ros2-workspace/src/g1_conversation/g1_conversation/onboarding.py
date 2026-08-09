"""Deterministic name/language onboarding for a new passenger session."""

import re
import unicodedata


LANGUAGE_QUESTION = "Nice to meet you, {name}. Which language do you prefer?"
NAME_RETRY = "Sorry, I did not catch your name. What is your name?"

PICKME_INTRODUCTIONS = {
    "en": "Great, {name}. PickMe is Sri Lanka's leading ride-hailing app. I can explain its services and help you install it.",
    "fr": "Très bien, {name}. PickMe est la principale application de transport au Sri Lanka. Je peux présenter ses services et vous aider à l'installer.",
    "de": "Sehr gut, {name}. PickMe ist Sri Lankas führende Fahrdienst-App. Ich kann die Dienste erklären und bei der Installation helfen.",
    "es": "Muy bien, {name}. PickMe es la principal aplicación de transporte de Sri Lanka. Puedo explicar sus servicios y ayudarle a instalarla.",
    "ru": "Отлично, {name}. PickMe — ведущее приложение для поездок в Шри-Ланке. Я расскажу об услугах и помогу установить приложение.",
    "ja": "承知しました、{name}さん。PickMeはスリランカを代表する配車アプリです。サービスの説明とアプリのインストールをお手伝いします。",
    "zh": "好的，{name}。PickMe 是斯里兰卡领先的叫车应用。我可以介绍服务并帮助您安装应用。",
    "ko": "좋습니다, {name}님. PickMe는 스리랑카의 대표 차량 호출 앱입니다. 서비스 안내와 앱 설치를 도와드리겠습니다.",
    "hi": "बहुत अच्छा, {name}। PickMe श्रीलंका का प्रमुख राइड-हेलिंग ऐप है। मैं इसकी सेवाएँ समझाने और ऐप इंस्टॉल करने में मदद कर सकता हूँ।",
    "si": "හොඳයි, {name}. PickMe ශ්‍රී ලංකාවේ ප්‍රමුඛ ගමන් සේවා ඇප් එකයි. එහි සේවාවන් ගැන කියා දී ඇප් එක ස්ථාපනය කිරීමට මට උදව් කළ හැකියි.",
    "ta": "சரி, {name}. PickMe இலங்கையின் முன்னணி பயணச் சேவை செயலி. அதன் சேவைகளை விளக்கி, செயலியை நிறுவ நான் உதவ முடியும்.",
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


def get_pickme_introduction(lang_code: str, name: str) -> str:
    template = PICKME_INTRODUCTIONS.get(lang_code, PICKME_INTRODUCTIONS["en"])
    return template.format(name=name)
