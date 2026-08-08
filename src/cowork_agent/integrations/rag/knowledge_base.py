"""Knowledge corpus loading and chunking for the in-repo RAG adapter.

The corpus is a directory of markdown knowledge documents (V1-M3:
``data/extracted/``). Email content is never ingested (PRD-v1 invariant).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Soft per-chunk size cap; splits happen on paragraph boundaries.
_MAX_CHUNK_CHARS = 1200

_H1_PATTERN = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SECTION_PATTERN = re.compile(r"^#{1,2}\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """One chunked slice of a knowledge document, stamped for ACL."""

    chunk_id: str
    document_id: str
    document_title: str
    section: str | None
    text: str
    source_url: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """One loaded knowledge document with its ordered chunks."""

    document_id: str
    title: str
    source_url: str
    chunks: tuple[KnowledgeChunk, ...]


def load_corpus(corpus_dir: Path, *, tenant_id: str) -> tuple[KnowledgeDocument, ...]:
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
        sections = _split_sections(raw_text)
        chunks: list[KnowledgeChunk] = []
        for section, section_text in sections:
            for part in _split_long_text(section_text):
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"{document_id}#{len(chunks)}",
                        document_id=document_id,
                        document_title=title,
                        section=section,
                        text=part,
                        source_url=source_url,
                        tenant_id=tenant_id,
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


def _split_sections(raw_text: str) -> list[tuple[str | None, str]]:
    """Split a document into (section title, body) pairs by H1/H2 headings."""
    matches = list(_SECTION_PATTERN.finditer(raw_text))
    if not matches:
        body = raw_text.strip()
        return [(None, body)] if body else []
    sections: list[tuple[str | None, str]] = []
    preamble = raw_text[: matches[0].start()].strip()
    if preamble:
        sections.append((None, preamble))
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(raw_text)
        body = raw_text[match.end() : end].strip()
        if body:
            sections.append((match.group(1).strip(), body))
    return sections


def _split_long_text(text: str) -> list[str]:
    """Split oversize section text on paragraph boundaries."""
    if len(text) <= _MAX_CHUNK_CHARS:
        return [text]
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > _MAX_CHUNK_CHARS:
            parts.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts
