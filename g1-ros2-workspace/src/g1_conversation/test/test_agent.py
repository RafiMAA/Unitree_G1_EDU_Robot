"""Basic tests for the g1_conversation agent."""

import os
import pytest


def test_session_manager():
    """Test session lifecycle and PII cleanup."""
    from g1_conversation.session import SessionManager

    mgr = SessionManager()

    # No session initially
    assert mgr.current is None
    assert not mgr.has_active_session

    # Start session
    session = mgr.start_session("test-session-001")
    assert mgr.has_active_session
    assert session.session_id == "test-session-001"
    assert session.language == "en"
    assert session.turn_count == 0

    # Modify session
    session.passenger_name = "John Doe"
    session.phone_number = "+94771234567"
    session.turn_count = 5
    session.language = "fr"

    # End session — PII should be cleared
    mgr.end_session("test_complete")
    assert mgr.current is None
    assert not mgr.has_active_session


def test_session_replacement():
    """Starting a new session should end the previous one."""
    from g1_conversation.session import SessionManager

    mgr = SessionManager()
    session1 = mgr.start_session("session-1")
    session1.passenger_name = "Alice"

    session2 = mgr.start_session("session-2")
    assert mgr.current.session_id == "session-2"
    assert session1.passenger_name is None  # PII was cleared


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Abdul", "Abdul"),
        ("My name is Abdul Rafi", "Abdul Rafi"),
        ("Hi, my name is John.", "John"),
        ("my nameis john", "john"),
        ("Hello my nameis Abdul Rafi", "Abdul Rafi"),
        ("Hey, my name's Anne-Marie O'Neill.", "Anne-Marie O'Neill"),
        ("I'm Sarah", "Sarah"),
        ("I am Nimal", "Nimal"),
        ("Call me Nimal", "Nimal"),
        ("Jean-Luc Picard", "Jean-Luc Picard"),
        ("නිමල් පෙරේරා", "නිමල් පෙරේරා"),
        ("Hello", None),
        ("Hello there", None),
        ("Can you repeat that?", None),
        ("I am looking for a ride", None),
        ("I am Nimal from Kandy", None),
        ("I'm happy to be here", None),
        ("This is my first time here", None),
        ("Call me when the taxi arrives", None),
        ("My favorite app is PickMe", None),
        ("My name is", None),
        ("My name is John and I need a ride", None),
        ("My name is 1234", None),
        ("Thank you very much", None),
        ("This is a complete sentence with too many words", None),
    ],
)
def test_extract_passenger_name(utterance, expected):
    from g1_conversation.onboarding import extract_passenger_name

    assert extract_passenger_name(utterance) == expected


def test_pickme_onboarding_introduction_uses_name_and_language():
    from g1_conversation.onboarding import get_pickme_introduction

    introduction = get_pickme_introduction("si", "Abdul")
    assert "Abdul" in introduction
    assert "PickMe" in introduction
    assert "ශ්‍රී ලංකාවේ" in introduction


def test_prompts():
    """Test prompt template retrieval."""
    from g1_conversation.agent.prompts import (
        get_system_prompt,
        is_language_supported,
        INITIAL_GREETING,
    )

    # English prompt should contain key instructions
    en_prompt = get_system_prompt("en")
    assert "PickMe" in en_prompt
    assert "concierge" in en_prompt.lower() or "assistant" in en_prompt.lower()

    # French prompt should be in French
    fr_prompt = get_system_prompt("fr")
    assert "français" in fr_prompt.lower() or "Parlez" in fr_prompt

    # Unsupported language should fallback to English
    unsupported = get_system_prompt("xx")
    assert unsupported == en_prompt

    # Language support check
    assert is_language_supported("en")
    assert is_language_supported("fr")
    assert is_language_supported("ru")
    assert not is_language_supported("xx")

    # Greeting should exist
    assert "PickMe" in INITIAL_GREETING


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Speak in Sinhala", "si"),
        ("Kena ispekin singkat", "si"),
        # Actual faster-whisper output when the active session hint was Hindi.
        ("इस पेखें सिंखला लंगवेज", "si"),
        ("सिंहला भाषा में बोलो", "si"),
        ("Can you speak Russian?", "ru"),
        ("Can you continue in Russian?", "ru"),
        ("Continue in Chinese", "zh"),
        ("Switch to Tamil", "ta"),
        ("සිංහලෙන් කතා කරන්න", "si"),
        ("தமிழ்", "ta"),
        ("I speak to passengers every day", None),
        ("What languages do you support?", None),
    ],
)
def test_detect_language_switch(utterance, expected):
    from g1_conversation.language_switch import detect_language_switch

    assert detect_language_switch(utterance) == expected


def test_document_loader():
    """Test knowledge base loading."""
    from g1_conversation.rag.document_loader import load_knowledge_base

    docs = load_knowledge_base()
    assert len(docs) > 0

    # Check metadata
    for doc in docs:
        assert "source" in doc.metadata
        assert "section" in doc.metadata
        assert len(doc.page_content) > 0

    # Check all 4 knowledge base files are loaded
    sources = set(doc.metadata["source"] for doc in docs)
    assert "pickme_services.md" in sources
    assert "sri_lanka_locations.md" in sources
    assert "app_installation.md" in sources
    assert "robot_concierge.md" in sources

    knowledge_text = "\n".join(doc.page_content for doc in docs).lower()
    assert "yellow" in knowledge_text
    assert "black passenger figure" in knowledge_text
    assert "green logo" not in knowledge_text


@pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set"
)
def test_agent_creation():
    """Test agent creation (requires Gemini API key)."""
    from g1_conversation.agent.rag_agent import create_agent

    agent = create_agent(lang_code="en", verbose=False)
    assert agent is not None


@pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set"
)
def test_agent_basic_conversation():
    """Test a basic agent conversation (requires Gemini API key)."""
    from g1_conversation.agent.rag_agent import create_agent, invoke_agent

    agent = create_agent(lang_code="en", verbose=False)
    response = invoke_agent(agent, "What is PickMe?")

    assert len(response) > 0
    assert "pickme" in response.lower() or "ride" in response.lower()


def test_faiss_cache_fingerprint():
    """Test that the KB fingerprint changes when files are modified."""
    from g1_conversation.rag.vector_store import _kb_fingerprint, DEFAULT_KB_PATH
    from g1_conversation.rag.document_loader import DEFAULT_KB_PATH as KB_PATH

    # Fingerprint should be deterministic for the same files
    fp1 = _kb_fingerprint(KB_PATH)
    fp2 = _kb_fingerprint(KB_PATH)
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex digest


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Plain response", "Plain response"),
        (
            [
                {"type": "text", "text": "First sentence. "},
                {"type": "text", "text": "Second sentence."},
            ],
            "First sentence. Second sentence.",
        ),
        ([{"type": "tool_result", "value": 1}],
         "I'm sorry, I didn't understand. Could you repeat that?"),
        (123, "123"),
    ],
)
def test_invoke_agent_normalizes_structured_output(output, expected):
    """Gemini structured content must be flattened before ROS/TTS use."""
    from g1_conversation.agent.rag_agent import invoke_agent

    class FakeAgentExecutor:
        def invoke(self, _inputs):
            return {"output": output}

    assert invoke_agent(FakeAgentExecutor(), "hello") == expected


def test_direct_rag_uses_one_retrieval_and_one_llm_call():
    """Deterministic RAG must never use a second tool-planning LLM call."""
    from langchain_core.documents import Document
    from g1_conversation.agent.rag_agent import DirectRAGAgent, invoke_agent

    class FakeRetriever:
        calls = 0

        def invoke(self, _query):
            self.calls += 1
            return [Document(page_content="PickMe offers cars.", metadata={})]

    class FakeResponse:
        content = "PickMe offers cars."

    class FakeLLM:
        calls = 0

        def invoke(self, _messages):
            self.calls += 1
            return FakeResponse()

    retriever = FakeRetriever()
    llm = FakeLLM()
    agent = DirectRAGAgent("en", llm, retriever)

    assert invoke_agent(agent, "What does PickMe offer?") == "PickMe offers cars."
    assert retriever.calls == 1
    assert llm.calls == 1


def test_direct_rag_streams_complete_sentences():
    from langchain_core.documents import Document
    from g1_conversation.agent.rag_agent import (
        DirectRAGAgent,
        stream_agent_sentences,
    )

    class FakeRetriever:
        def invoke(self, _query):
            return [Document(page_content="Context", metadata={})]

    class Chunk:
        def __init__(self, content):
            self.content = content

    class FakeStreamingLLM:
        def stream(self, _messages):
            yield Chunk("First short ")
            yield Chunk("sentence. Second ")
            yield Chunk("one!")

    agent = DirectRAGAgent("en", FakeStreamingLLM(), FakeRetriever())
    assert list(stream_agent_sentences(agent, "hello")) == [
        "First short sentence.",
        "Second one!",
    ]


def test_tts_sentence_split():
    from g1_conversation.tts_engine import TTSEngine

    assert TTSEngine._split_sentences("First. Second?") == ["First.", "Second?"]


def test_tts_combines_short_sentences_into_one_natural_utterance():
    from g1_conversation.tts_engine import TTSEngine

    text = "Hello! Ayubowan. Welcome to Sri Lanka. What's your name?"
    assert TTSEngine._chunk_text(text) == [text]


def test_tts_splits_long_text_without_losing_words():
    from g1_conversation.tts_engine import TTSEngine

    text = " ".join(["PickMe makes travel simple."] * 15)
    chunks = TTSEngine._chunk_text(text, max_characters=80)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert " ".join(chunks) == text


def test_tts_uses_friendly_local_voice_profiles():
    from g1_conversation.tts_engine import TTSEngine

    engine = object.__new__(TTSEngine)
    engine._preferred_backend = "edge"
    assert engine.get_profile("en").voice == "en-US-AvaMultilingualNeural"
    assert engine.get_profile("en").rate == "+8%"
    assert engine.get_profile("si").voice == "si-LK-ThiliniNeural"
    assert engine.get_profile("si").rate == "+0%"
    assert engine.get_profile("ta").voice == "ta-LK-SaranyaNeural"


def test_tts_cache_key_changes_with_prosody(tmp_path):
    from g1_conversation.tts_engine import TTSEngine, VoiceProfile

    engine = object.__new__(TTSEngine)
    engine._preferred_backend = "edge"
    engine._cache_dir = str(tmp_path)
    normal = VoiceProfile("voice", "+0%")
    faster = VoiceProfile("voice", "+8%")

    assert engine._cache_path("Hello", normal) != engine._cache_path(
        "Hello", faster
    )


def test_tts_sequence_prefetches_next_sentence_during_playback():
    import threading
    from g1_conversation.tts_engine import TTSEngine, _PreparedAudio

    engine = object.__new__(TTSEngine)
    engine._preferred_backend = "edge"
    engine._stop_requested = threading.Event()
    engine._speak_lock = threading.Lock()
    second_ready = threading.Event()
    played = []

    def prepare(text, _profile, _cache):
        if text == "Second.":
            second_ready.set()
        return _PreparedAudio(path=text, temporary=False)

    def play(path):
        if path == "First.":
            assert second_ready.wait(timeout=1.0)
        played.append(path)

    engine._prepare_audio = prepare
    engine._play_audio = play
    engine.speak_sequence(["First.", "Second."], lang_code="en")

    assert played == ["First.", "Second."]


# ── Text-mode dev harness ────────────────────────────────────────────
# Run with:  pytest -s -k test_text_mode_harness
# NOT run in CI — requires manual invocation and a Gemini API key.

@pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set"
)
@pytest.mark.skipif(
    not os.environ.get("G1_TEXT_HARNESS"),
    reason="Set G1_TEXT_HARNESS=1 to run the interactive text harness"
)
def test_text_mode_harness():
    """Interactive text-mode harness for testing the agent without a mic.

    This is the replacement for the removed ``ros2 run conversation_text``
    entry point.  It exercises the same agent + RAG pipeline but through
    stdin/stdout instead of mic/speaker.

    Invoke manually::

        G1_TEXT_HARNESS=1 OPENAI_API_KEY=sk-... pytest -s -k test_text_mode_harness
    """
    from g1_conversation.session import SessionManager
    from g1_conversation.agent.rag_agent import create_agent, invoke_agent
    from g1_conversation.agent.prompts import (
        INITIAL_GREETING,
        UNSUPPORTED_LANGUAGE_MESSAGE,
        is_language_supported,
    )

    session_mgr = SessionManager()

    print("\n" + "=" * 60)
    print("  PickMe Robotic Mobility Concierge — Text Harness")
    print("=" * 60)
    print(f"\n{INITIAL_GREETING}\n")

    session = session_mgr.start_session()
    lang_input = input("Your language (en/fr/de/es/ja/zh/ko/hi/si/ta) [en]: ").strip()
    if not lang_input:
        lang_input = "en"

    session.language = lang_input
    if not is_language_supported(lang_input):
        print(f"\n{UNSUPPORTED_LANGUAGE_MESSAGE}\n")
        session.language = "en"

    agent = create_agent(lang_code=session.language, verbose=True)
    print("\nAgent ready. Type messages (or 'quit' to exit):\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            break

        session.turn_count += 1
        response = invoke_agent(agent, user_input)
        print(f"\nRobot: {response}\n")

    session_mgr.end_session("harness_exit")
    print("Session ended.")
