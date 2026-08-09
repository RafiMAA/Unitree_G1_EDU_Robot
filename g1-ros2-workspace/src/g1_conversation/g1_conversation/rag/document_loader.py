"""Load and chunk knowledge base markdown documents for RAG ingestion.

Reads all .md files from the knowledge_base directory, splits them into
manageable chunks with metadata (source file, section heading), and
returns LangChain Document objects ready for vector store ingestion.
"""

import os
import glob
from typing import Optional

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


# Default knowledge base path — relative to this file
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KB_PATH = os.path.join(_THIS_DIR, "..", "knowledge_base")

# Chunking parameters — tuned for conversational RAG retrieval
CHUNK_SIZE = 500         # characters per chunk
CHUNK_OVERLAP = 50       # overlap between adjacent chunks
SEPARATORS = ["\n## ", "\n### ", "\n\n", "\n", ". ", " "]


def load_knowledge_base(
    kb_path: Optional[str] = None,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> list[Document]:
    """Load and chunk all markdown files from the knowledge base.

    Parameters
    ----------
    kb_path : str, optional
        Path to the knowledge base directory. Defaults to the
        ``knowledge_base/`` directory next to this package.
    chunk_size : int
        Maximum number of characters per chunk.
    chunk_overlap : int
        Number of overlapping characters between adjacent chunks.

    Returns
    -------
    list[Document]
        LangChain Document objects with metadata including source file
        and a section hint.
    """
    if kb_path is None:
        kb_path = DEFAULT_KB_PATH

    kb_path = os.path.abspath(kb_path)

    md_files = sorted(glob.glob(os.path.join(kb_path, "*.md")))
    if not md_files:
        raise FileNotFoundError(
            f"No .md files found in knowledge base: {kb_path}"
        )

    print(f"[RAG] Loading {len(md_files)} knowledge base files from {kb_path}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=SEPARATORS,
        length_function=len,
        is_separator_regex=False,
    )

    all_documents: list[Document] = []

    for md_path in md_files:
        basename = os.path.basename(md_path)
        source_name = os.path.splitext(basename)[0].replace("_", " ").title()

        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Split into chunks
        chunks = splitter.create_documents(
            texts=[content],
            metadatas=[{
                "source": basename,
                "source_name": source_name,
                "file_path": md_path,
            }],
        )

        # Enrich metadata with section headings
        for chunk in chunks:
            chunk.metadata["section"] = _extract_section_heading(chunk.page_content)

        all_documents.extend(chunks)
        print(f"  ├── {basename}: {len(chunks)} chunks")

    print(f"[RAG] Total: {len(all_documents)} document chunks loaded")
    return all_documents


def _extract_section_heading(text: str) -> str:
    """Extract the most recent markdown heading from a chunk.

    Useful for giving the retriever additional context about what
    section of the knowledge base a chunk came from.
    """
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            # Remove markdown heading markers
            return stripped.lstrip("#").strip()
    return "General"
