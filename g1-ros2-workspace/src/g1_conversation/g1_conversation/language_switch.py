"""Explicit spoken language-switch commands for the conversation node."""

import re


LANGUAGE_ALIASES = {
    "en": ("english",),
    "fr": ("french", "français", "francais"),
    "de": ("german", "deutsch"),
    "es": ("spanish", "español", "espanol"),
    "ru": ("russian", "русский", "русском"),
    "ja": ("japanese", "日本語"),
    "zh": ("chinese", "mandarin", "中文", "普通话"),
    "ko": ("korean", "한국어"),
    "hi": ("hindi", "हिंदी", "हिन्दी"),
    # Whisper may transliterate a spoken language name into the script of the
    # current language hint.  The Devanagari forms below are real outputs seen
    # while a Hindi session was being asked to switch to Sinhala.
    "si": (
        "sinhala", "singhala", "sinhalese", "සිංහල",
        # Observed large-v3 rendering of "speak in Sinhala". Keep the
        # complete phrase so the Malay word "singkat" alone never switches.
        "ispekin singkat",
        "सिंहला", "सिंखला", "सिंगला", "सिंगले", "सिंहली",
    ),
    "ta": ("tamil", "தமிழ்"),
}

SWITCH_MARKERS = (
    "speak", "talk", "switch", "change", "continue", "language", "prefer",
    "parler", "sprechen", "hablar", "говор", "話", "说", "말",
    "बोल", "भाषा", "लंगवेज", "ispekin", "කතා", "பேச",
)

LANGUAGE_CONFIRMATIONS = {
    "en": "Certainly. I will continue in English.",
    "fr": "Bien sûr. Je vais continuer en français.",
    "de": "Natürlich. Ich werde auf Deutsch fortfahren.",
    "es": "Claro. Continuaré en español.",
    "ru": "Конечно. Я продолжу говорить по-русски.",
    "ja": "もちろんです。これから日本語でお話しします。",
    "zh": "当然可以。我接下来会用中文交流。",
    "ko": "물론입니다. 이제부터 한국어로 말씀드리겠습니다.",
    "hi": "ज़रूर। अब मैं हिंदी में बात करूँगा।",
    "si": "හොඳයි. මම මෙතැන් සිට සිංහලෙන් කතා කරන්නම්.",
    "ta": "நிச்சயமாக. இனிமேல் நான் தமிழில் பேசுகிறேன்.",
}


def detect_language_switch(text: str) -> str | None:
    """Return a requested supported language code, if present."""
    normalized = re.sub(r"[^\w\s\-\u0080-\uffff]", " ", text.casefold())
    normalized = " ".join(normalized.split())

    for code, aliases in LANGUAGE_ALIASES.items():
        for alias in aliases:
            if alias.casefold() not in normalized:
                continue
            # A language name alone is a valid answer to the initial greeting.
            if normalized == alias.casefold() or any(
                marker in normalized for marker in SWITCH_MARKERS
            ):
                return code
    return None


def get_language_confirmation(lang_code: str) -> str:
    """Return a language-switch confirmation in the selected language."""
    return LANGUAGE_CONFIRMATIONS[lang_code]
