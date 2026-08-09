"""Low-latency deterministic RAG for the PickMe concierge.

The previous implementation used a tool-calling AgentExecutor.  A factual
question required one Gemini request to select the FAISS tool and a second
request to write the answer.  This module retrieves from FAISS first and then
makes exactly one bounded Gemini request.
"""

from collections import deque
from dataclasses import dataclass, field
import re
import threading
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from ..rag.vector_store import build_vectorstore, get_retriever
from .prompts import get_system_prompt


DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MEMORY_WINDOW = 4
DEFAULT_REQUEST_TIMEOUT = 12.0
DEFAULT_SEARCH_K = 3
FALLBACK_RESPONSE = "I'm sorry, I didn't understand. Could you repeat that?"

_vectorstore = None
_vectorstore_lock = threading.Lock()


def _shared_vectorstore():
    """Load the persisted FAISS index once per process."""
    global _vectorstore
    if _vectorstore is None:
        with _vectorstore_lock:
            if _vectorstore is None:
                _vectorstore = build_vectorstore()
    return _vectorstore


def _content_to_text(content: Any, fallback: str = FALLBACK_RESPONSE) -> str:
    """Normalize Gemini/LangChain structured content into spoken text."""
    if isinstance(content, str):
        return content.strip() or fallback
    if isinstance(content, list):
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type", "text") == "text"
        ).strip()
        return text or fallback
    return str(content).strip() or fallback


def _stream_content_to_text(content: Any) -> str:
    """Flatten a streaming chunk without removing boundary whitespace."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type", "text") == "text"
        )
    return "" if content is None else str(content)


@dataclass
class DirectRAGAgent:
    """A retriever plus one LLM call and a small per-session history."""

    lang_code: str
    llm: Any
    retriever: Any
    memory_window: int = DEFAULT_MEMORY_WINDOW
    passenger_name: str | None = None
    history: deque = field(default_factory=deque)

    def _messages(self, question: str):
        documents = self.retriever.invoke(question)
        context = "\n\n".join(
            f"[{doc.metadata.get('source', 'knowledge base')}]\n{doc.page_content}"
            for doc in documents
        )

        system_prompt = (
            get_system_prompt(self.lang_code)
            + "\n\nThe relevant knowledge has already been retrieved below. "
            "Answer directly; do not request or describe a tool call. "
            "Use at most two short sentences and about 45 spoken words. "
            "Give only the next useful step when explaining a procedure.\n\n"
            "BRAND FACT: The official PickMe app logo has a yellow background "
            "with a black passenger figure. Never describe it as green.\n\n"
            f"RETRIEVED KNOWLEDGE:\n{context}"
        )
        if self.passenger_name:
            system_prompt += (
                f"\n\nThe passenger's name is {self.passenger_name}. "
                "Remember it during this session and use it naturally, but not in every reply."
            )
        messages = [SystemMessage(content=system_prompt)]
        for human_text, ai_text in self.history:
            messages.append(HumanMessage(content=human_text))
            messages.append(AIMessage(content=ai_text))
        messages.append(HumanMessage(content=question))
        return question, messages

    def invoke(self, inputs: dict[str, str]) -> dict[str, str]:
        question = inputs["input"].strip()
        question, messages = self._messages(question)

        response = self.llm.invoke(messages)
        text = _content_to_text(response.content)
        self.history.append((question, text))
        while len(self.history) > self.memory_window:
            self.history.popleft()
        return {"output": text}

    def stream_sentences(self, user_input: str):
        """Yield completed sentences while Gemini is still generating."""
        question, messages = self._messages(user_input.strip())
        buffer = ""
        complete_text = ""
        for chunk in self.llm.stream(messages):
            content = _stream_content_to_text(chunk.content)
            if not content:
                continue
            buffer += content
            while True:
                match = re.search(r"[.!?。！？](?:\s+|$)", buffer)
                if match is None:
                    break
                end = match.end()
                sentence = buffer[:end].strip()
                buffer = buffer[end:]
                if sentence:
                    complete_text += (" " if complete_text else "") + sentence
                    yield sentence

        if buffer.strip():
            sentence = buffer.strip()
            complete_text += (" " if complete_text else "") + sentence
            yield sentence

        if complete_text:
            self.history.append((question, complete_text))
            while len(self.history) > self.memory_window:
                self.history.popleft()


def create_agent(
    lang_code: str = "en",
    memory_window: int = DEFAULT_MEMORY_WINDOW,
    passenger_name: str | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    verbose: bool = False,
) -> DirectRAGAgent:
    """Create a fast per-passenger RAG session using one Gemini call/turn."""
    del verbose  # Kept for compatibility with the existing test harness.
    llm = ChatGoogleGenerativeAI(
        model=model,
        temperature=temperature,
        thinking_level="minimal",
        max_tokens=96,
        request_timeout=DEFAULT_REQUEST_TIMEOUT,
        retries=1,
    )
    retriever = get_retriever(
        vectorstore=_shared_vectorstore(), search_k=DEFAULT_SEARCH_K
    )
    return DirectRAGAgent(
        lang_code=lang_code,
        llm=llm,
        retriever=retriever,
        passenger_name=passenger_name,
        memory_window=memory_window,
    )


def invoke_agent(agent_executor: Any, user_input: str) -> str:
    """Invoke either DirectRAGAgent or a compatible test double."""
    try:
        result = agent_executor.invoke({"input": user_input})
    except Exception as exc:
        print(f"[RAG] Bounded request failed; using spoken fallback: {exc}")
        return FALLBACK_RESPONSE
    output = result.get("output", FALLBACK_RESPONSE)
    return _content_to_text(output)


def stream_agent_sentences(agent_executor: Any, user_input: str):
    """Stream sentences when supported, with a safe non-streaming fallback."""
    try:
        if hasattr(agent_executor, "stream_sentences"):
            yielded = False
            for sentence in agent_executor.stream_sentences(user_input):
                yielded = True
                yield sentence
            if not yielded:
                yield FALLBACK_RESPONSE
        else:
            yield invoke_agent(agent_executor, user_input)
    except Exception as exc:
        print(f"[RAG] Streaming request failed; using spoken fallback: {exc}")
        yield FALLBACK_RESPONSE
