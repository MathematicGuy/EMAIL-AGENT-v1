"""Knowledge corpus loading and chunking for the in-repo RAG adapter.

The corpus is a directory of markdown knowledge documents (V1-M3:
``data/extracted/``). Email content is never ingested (PRD-v1 invariant).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cowork_agent.domain.target_contracts import RetrievalFilters
from cowork_agent.integrations.knowledge_ingestion.manifest import ManifestStore
from cowork_agent.integrations.knowledge_ingestion.text_sanitizer import split_frontmatter

from .markdown_chunking import chunk_markdown_pages, split_markdown_pages

_MANIFEST_NAME = "ingestion-manifest.json"

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
    document_date: date | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """One loaded knowledge document with its ordered chunks."""

    document_id: str
    title: str
    source_url: str
    chunks: tuple[KnowledgeChunk, ...]


def allowed_chunk_indices(
    chunks: Sequence[KnowledgeChunk], filters: RetrievalFilters
) -> tuple[int, ...]:
    """Return corpus indexes that pass query-time metadata filters.

    Empty ``document_ids`` / ``years`` / ``months`` leave that key unconstrained.
    Provided keys are ANDed. ``document_status`` is ignored (company corpus has
    no status payload). A missing ``document_date`` fails a year or month filter.
    """
    id_filter = set(filters.document_ids) if filters.document_ids else None
    year_filter = set(filters.years) if filters.years else None
    month_filter = set(filters.months) if filters.months else None
    allowed: list[int] = []
    for index, chunk in enumerate(chunks):
        if id_filter is not None and chunk.document_id not in id_filter:
            continue
        if year_filter is not None and (
            chunk.document_date is None or chunk.document_date.year not in year_filter
        ):
            continue
        if month_filter is not None and (
            chunk.document_date is None or chunk.document_date.month not in month_filter
        ):
            continue
        allowed.append(index)
    return tuple(allowed)


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
    dates_by_stem = _document_dates_by_stem(corpus_dir)
    documents: list[KnowledgeDocument] = []
    for path in paths:
        raw_text = path.read_text(encoding="utf-8")
        _fields, body = split_frontmatter(raw_text)
        document_id = path.stem
        document_date = dates_by_stem.get(document_id)
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
                    document_date=document_date,
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


def _document_dates_by_stem(corpus_dir: Path) -> dict[str, date | None]:
    manifest_path = corpus_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        return {}
    return {
        Path(entry.output).stem: _parse_iso_date(entry.document_date)
        for entry in ManifestStore(manifest_path).load().values()
    }


def _parse_iso_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None
