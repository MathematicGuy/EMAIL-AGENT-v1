"""Knowledge corpus loading and chunking for the in-repo RAG adapter.

The corpus is a directory of markdown knowledge documents (V1-M3:
``data/extracted/``). Email content is never ingested (PRD-v1 invariant).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cowork_agent.integrations.knowledge_ingestion.text_sanitizer import split_frontmatter

from .markdown_chunking import chunk_markdown_pages, split_markdown_pages

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
    page_start: int | None = None
    page_end: int | None = None


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
    is the file stem, ``title`` the first H1 heading in the body (fallback:
    stem), and ``source_url`` the POSIX path relative to the repository
    root. A leading closed frontmatter block is stripped before title and
    chunking so YAML keys are never indexed. Page comments become
    ``page_start`` / ``page_end`` (both ``None`` when unmarked). Chunks
    follow H1/H2 sections (fallback: the whole document), split further
    on paragraph boundaries near ``_MAX_CHUNK_CHARS``.

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
        _fields, body = split_frontmatter(raw_text)
        document_id = path.stem
        title_match = _H1_PATTERN.search(body)
        title = title_match.group(1).strip() if title_match else document_id
        if repo_root is not None:
            source_url = path.resolve().relative_to(repo_root).as_posix()
        else:
            source_url = path.name
        chunks: list[KnowledgeChunk] = []
        pages = split_markdown_pages(body)
        for part in chunk_markdown_pages(pages):
            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"{document_id}#{len(chunks)}",
                    document_id=document_id,
                    document_title=title,
                    section=part.section,
                    text=part.text,
                    source_url=source_url,
                    page_start=part.page_start,
                    page_end=part.page_end,
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
