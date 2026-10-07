"""Offline regressions for passenger-assistance content and index isolation."""
import os
from pathlib import Path
import pytest

from g1_conversation.agent.prompts import get_system_prompt, INITIAL_GREETING, SYSTEM_PROMPT_TEMPLATE
from g1_conversation.onboarding import get_airport_introduction


@pytest.mark.parametrize('language', list(SYSTEM_PROMPT_TEMPLATE))
def test_all_languages_introduce_airport_assistance(language):
    prompt = get_system_prompt(language)
    introduction = get_airport_introduction(language, 'Alex')
    assert 'Alex' in introduction
    assert 'airport passenger assistant' in prompt
    assert 'Never invent gate numbers' in prompt
    assert 'navigation-system confirmation' in prompt
    for text in (prompt, introduction, INITIAL_GREETING):
        assert 'pickme' not in text.lower()
        assert 'ride-hailing' not in text.lower()


def test_cache_fingerprint_checks_content_even_when_size_and_mtime_match(tmp_path):
    from g1_conversation.rag.vector_store import _kb_fingerprint, DEFAULT_INDEX_DIR
    path = tmp_path / 'route.md'
    path.write_text('Gate A')
    stat = path.stat()
    before = _kb_fingerprint(str(tmp_path))
    path.write_text('Gate B')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert _kb_fingerprint(str(tmp_path)) != before
    assert Path(DEFAULT_INDEX_DIR).name == 'airport_faiss_index'


def test_real_faiss_build_reload_and_changed_content_rebuild_offline(tmp_path, monkeypatch):
    from langchain_core.embeddings import Embeddings
    from g1_conversation.rag import vector_store
    class LocalEmbeddings(Embeddings):
        calls = 0
        def embed_documents(self, texts):
            self.calls += 1
            return [self.embed_query(text) for text in texts]
        def embed_query(self, text):
            text = text.lower()
            return [float(text.count(word)) for word in ('washroom', 'baggage', 'gate')] + [1.]
    embeddings = LocalEmbeddings()
    monkeypatch.setattr(vector_store, 'GoogleGenerativeAIEmbeddings', lambda **kwargs: embeddings)
    kb, cache = tmp_path / 'kb', tmp_path / 'index'
    kb.mkdir()
    (kb / 'services.md').write_text('# Services\n## Washrooms\nAsk which terminal the passenger is in.\n')
    first = vector_store.build_vectorstore(str(kb), str(cache))
    assert first.index.ntotal > 0
    assert embeddings.calls == 1
    cached = vector_store.build_vectorstore(str(kb), str(cache))
    assert cached.index.ntotal == first.index.ntotal
    assert embeddings.calls == 1
    (kb / 'services.md').write_text('# Services\n## Baggage claim\nConfirm the carousel through official displays.\n')
    rebuilt = vector_store.build_vectorstore(str(kb), str(cache))
    assert embeddings.calls == 2
    result = rebuilt.similarity_search('baggage')[0]
    assert 'Baggage claim' in result.page_content
    assert 'Washrooms' not in result.page_content
