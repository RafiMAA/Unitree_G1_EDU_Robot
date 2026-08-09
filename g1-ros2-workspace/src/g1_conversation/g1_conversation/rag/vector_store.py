"""FAISS vector store for the PickMe RAG knowledge base.

Builds an in-memory FAISS index from the knowledge base documents
using Google Gemini embeddings.  Persists the index to a stable
location (``~/.g1_conversation/faiss_index/``) so it survives
``colcon build`` and doesn't re-embed on every node startup.

The index is automatically rebuilt when any knowledge base file has
been modified since the last build (staleness check via mtime).
"""

import hashlib
import json
import os
from typing import Optional

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS

from .document_loader import load_knowledge_base, DEFAULT_KB_PATH


# Stable cache location — survives colcon build, unlike install/
DEFAULT_INDEX_DIR = os.path.join(
    os.path.expanduser("~"), ".g1_conversation", "faiss_index"
)
_MANIFEST_FILENAME = "_manifest.json"


def _kb_fingerprint(kb_path: str) -> str:
    """Compute a fingerprint of the knowledge base content.

    Combines the mtime and size of every .md file into a single hash.
    If any file is added, removed, or modified the fingerprint changes
    and the index will be rebuilt.
    """
    import glob as _glob

    md_files = sorted(_glob.glob(os.path.join(kb_path, "*.md")))
    hasher = hashlib.sha256()
    for fpath in md_files:
        stat = os.stat(fpath)
        hasher.update(f"{fpath}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    return hasher.hexdigest()


def _read_manifest(index_dir: str) -> Optional[dict]:
    """Read the manifest file that records the KB fingerprint at build time."""
    manifest_path = os.path.join(index_dir, _MANIFEST_FILENAME)
    if not os.path.exists(manifest_path):
        return None
    try:
        with open(manifest_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_manifest(index_dir: str, fingerprint: str, num_vectors: int) -> None:
    """Write a manifest file alongside the FAISS index."""
    manifest_path = os.path.join(index_dir, _MANIFEST_FILENAME)
    with open(manifest_path, "w") as f:
        json.dump(
            {"kb_fingerprint": fingerprint, "num_vectors": num_vectors},
            f,
            indent=2,
        )


def build_vectorstore(
    kb_path: Optional[str] = None,
    index_dir: Optional[str] = None,
    force_rebuild: bool = False,
) -> FAISS:
    """Build or load the FAISS vector store.

    Lifecycle:
    1. Compute a fingerprint of the knowledge base ``.md`` files.
    2. If a cached index exists at ``index_dir`` **and** the fingerprint
       matches, load it from disk (fast — no Gemini API call).
    3. Otherwise, embed all documents and persist the new index.

    Parameters
    ----------
    kb_path : str, optional
        Path to the knowledge base markdown files.
    index_dir : str, optional
        Directory to persist/load the FAISS index.
        Defaults to ``~/.g1_conversation/faiss_index/``.
    force_rebuild : bool
        If True, rebuild even if a valid cached index exists.

    Returns
    -------
    FAISS
        A LangChain FAISS vector store ready for retrieval.
    """
    if kb_path is None:
        kb_path = DEFAULT_KB_PATH
    if index_dir is None:
        index_dir = DEFAULT_INDEX_DIR

    kb_path = os.path.abspath(kb_path)
    embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")

    # ── Check cache ──────────────────────────────────────────────────
    current_fingerprint = _kb_fingerprint(kb_path)

    if not force_rebuild and os.path.isdir(index_dir):
        manifest = _read_manifest(index_dir)
        if manifest and manifest.get("kb_fingerprint") == current_fingerprint:
            print(f"[RAG] Cache hit — loading FAISS index from {index_dir}")
            try:
                vectorstore = FAISS.load_local(
                    index_dir,
                    embeddings,
                    allow_dangerous_deserialization=True,
                )
                print(
                    f"[RAG] FAISS index loaded "
                    f"({vectorstore.index.ntotal} vectors, cache valid)"
                )
                return vectorstore
            except Exception as e:
                print(f"[RAG] Cache load failed, rebuilding: {e}")
        else:
            if manifest:
                print("[RAG] Knowledge base changed — rebuilding index")
            else:
                print("[RAG] No manifest found — building index from scratch")

    # ── Build from scratch ───────────────────────────────────────────
    documents = load_knowledge_base(kb_path=kb_path)

    print(f"[RAG] Embedding {len(documents)} chunks (Gemini API call) ...")
    vectorstore = FAISS.from_documents(documents, embeddings)
    num_vectors = vectorstore.index.ntotal
    print(f"[RAG] FAISS index built ({num_vectors} vectors)")

    # ── Persist ──────────────────────────────────────────────────────
    os.makedirs(index_dir, exist_ok=True)
    vectorstore.save_local(index_dir)
    _write_manifest(index_dir, current_fingerprint, num_vectors)
    print(f"[RAG] Index + manifest persisted to {index_dir}")

    return vectorstore


def get_retriever(
    vectorstore: Optional[FAISS] = None,
    kb_path: Optional[str] = None,
    search_k: int = 4,
):
    """Get a LangChain retriever from the vector store.

    Parameters
    ----------
    vectorstore : FAISS, optional
        An existing FAISS vector store. If None, builds/loads one.
    kb_path : str, optional
        Path to the knowledge base (used if vectorstore is None).
    search_k : int
        Number of top documents to retrieve per query.

    Returns
    -------
    VectorStoreRetriever
        A LangChain retriever configured for the knowledge base.
    """
    if vectorstore is None:
        vectorstore = build_vectorstore(kb_path=kb_path)

    return vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": search_k},
    )
