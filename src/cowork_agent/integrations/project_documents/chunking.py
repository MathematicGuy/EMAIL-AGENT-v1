"""Deterministic page-aware chunking for one document at a time."""

from __future__ import annotations

import re
from collections.abc import Mapping
from uuid import NAMESPACE_URL, uuid5

from cowork_agent.domain.project_documents import ProjectDocumentChunk


def chunk_pages(
    *,
    document_id: str,
    project_id: str,
    tenant_id: str,
    user_id: str,
    pages: Mapping[int, str],
    max_chars: int = 1_800,
    overlap_chars: int = 180,
) -> tuple[ProjectDocumentChunk, ...]:
    if max_chars < 200 or not 0 <= overlap_chars < max_chars:
        raise ValueError("invalid chunk bounds")
    chunks: list[ProjectDocumentChunk] = []
    for page_number in sorted(pages):
        text = "\n".join(line.rstrip() for line in pages[page_number].splitlines()).strip()
        if not text:
            raise ValueError(f"page {page_number} is empty")
        section = _first_heading(text)
        start = 0
        ordinal = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            if end < len(text):
                boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
                if boundary > start + max_chars // 2:
                    end = boundary + 1
            content = text[start:end].strip()
            if content:
                stable = f"{document_id}:{page_number}:{ordinal}:{content}"
                chunks.append(
                    ProjectDocumentChunk(
                        chunk_id=f"chunk_{uuid5(NAMESPACE_URL, stable).hex}",
                        document_id=document_id,
                        project_id=project_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        text=content,
                        page_start=page_number,
                        page_end=page_number,
                        section=section,
                    )
                )
                ordinal += 1
            if end >= len(text):
                break
            start = max(start + 1, end - overlap_chars)
    if not chunks:
        raise ValueError("document extraction is empty")
    return tuple(chunks)


def render_pages(pages: Mapping[int, str]) -> str:
    return "\n\n".join(
        f"<!-- Page {number} -->\n{pages[number].strip()}" for number in sorted(pages)
    )


def _first_heading(text: str) -> str | None:
    match = re.search(r"^#{1,6}\s+(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip()[:300] if match else None
