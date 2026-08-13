"""Content-based PDF/DOCX media validation."""

from __future__ import annotations

import io
import zipfile

from cowork_agent.domain.project_documents import ProjectDocumentMediaType


def sniff_media_type(data: bytes) -> ProjectDocumentMediaType:
    if data.startswith(b"%PDF-"):
        return ProjectDocumentMediaType.PDF
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                content_types = archive.read("[Content_Types].xml")
        except (KeyError, OSError, zipfile.BadZipFile) as exc:
            raise ValueError("invalid DOCX structure") from exc
        if "word/document.xml" in names and b"wordprocessingml.document.main+xml" in content_types:
            return ProjectDocumentMediaType.DOCX
    raise ValueError("unsupported document content")
