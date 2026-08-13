"""Knowledge corpus loading and chunking for the in-repo RAG adapter.

The corpus is a directory of markdown knowledge documents (V1-M3:
``data/extracted/``). Email content is never ingested (PRD-v1 invariant).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .markdown_chunking import chunk_markdown

_H1_PATTERN = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """One chunked slice of a knowledge document."""

    chunk_id: str
    document_id: str
    document_title: str
    section: str | None
    text: str
    source_url: str


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """One loaded knowledge document with its ordered chunks."""

    document_id: str
    title: str
    source_url: str
    chunks: tuple[KnowledgeChunk, ...]


def load_corpus(corpus_dir: Path, *, tenant_id: str | None = None) -> tuple[KnowledgeDocument, ...]:
    """Load every ``*.md`` document under ``corpus_dir`` into chunked form.

    Documents are read sorted by filename for determinism; ``document_id``
    is the file stem, ``title`` the first H1 heading (fallback: stem), and
    ``source_url`` the POSIX path relative to the repository root. Chunks
    follow H1/H2 sections (fallback: the whole document), split further on
    paragraph boundaries near ``_MAX_CHUNK_CHARS``.

    Raises:
        ValueError: when ``corpus_dir`` is missing, unreadable, or contains
            no markdown documents.
    """
    if not corpus_dir.is_dir():
        raise ValueError(f"Knowledge corpus directory not found: {corpus_dir}")
    paths = sorted(corpus_dir.glob("*.md"))
    if not paths:
        raise ValueError(f"Knowledge corpus has no markdown documents: {corpus_dir}")

    repo_root = corpus_dir.resolve().parents[1] if corpus_dir.name == "extracted" else None
    documents: list[KnowledgeDocument] = []
    for path in paths:
        raw_text = path.read_text(encoding="utf-8")
        document_id = path.stem
        title_match = _H1_PATTERN.search(raw_text)
        title = title_match.group(1).strip() if title_match else document_id
        if repo_root is not None:
            source_url = path.resolve().relative_to(repo_root).as_posix()
        else:
            source_url = path.name
        chunks: list[KnowledgeChunk] = []
        for part in chunk_markdown(raw_text):
            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"{document_id}#{len(chunks)}",
                    document_id=document_id,
                    document_title=title,
                    section=part.section,
                    text=part.text,
                    source_url=source_url,
                )
            )
        documents.append(
            KnowledgeDocument(
                document_id=document_id,
                title=title,
                source_url=source_url,
                chunks=tuple(chunks),
            )
        )
    return tuple(documents)
