"""Pure contracts for Project-scoped user documents (V3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

MAX_PROJECT_NAME_LENGTH = 200
MAX_DOCUMENT_TITLE_LENGTH = 300


class ProjectDocumentStatus(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class ProjectDocumentFailureReason(StrEnum):
    INVALID_PDF = "invalid_pdf"
    INVALID_DOCX = "invalid_docx"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    EXTRACTION_FAILED = "extraction_failed"
    OCR_UNAVAILABLE = "ocr_unavailable"
    OCR_INCOMPLETE = "ocr_incomplete"
    EMPTY_DOCUMENT = "empty_document"
    EMBEDDING_FAILED = "embedding_failed"
    VECTOR_STORE_UNAVAILABLE = "vector_store_unavailable"
    INTERNAL_ERROR = "internal_error"


class ProjectDocumentMediaType(StrEnum):
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True, slots=True)
class ChatProject:
    project_id: str
    tenant_id: str
    user_id: str
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _required(self.project_id, "project_id")
        _required(self.tenant_id, "tenant_id")
        _required(self.user_id, "user_id")
        _bounded(self.name, "name", MAX_PROJECT_NAME_LENGTH)

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "is_default": self.is_default,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    document_id: str
    project_id: str
    tenant_id: str
    user_id: str
    title: str
    media_type: ProjectDocumentMediaType
    size_bytes: int
    sha256: str
    status: ProjectDocumentStatus
    reason_code: ProjectDocumentFailureReason | None
    page_count: int
    chunk_count: int
    ocr_page_count: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("document_id", "project_id", "tenant_id", "user_id", "sha256"):
            _required(getattr(self, name), name)
        _bounded(self.title, "title", MAX_DOCUMENT_TITLE_LENGTH)
        if self.size_bytes < 1:
            raise ValueError("size_bytes must be positive")
        if min(self.page_count, self.chunk_count, self.ocr_page_count) < 0:
            raise ValueError("document counts must be non-negative")
        if self.status is ProjectDocumentStatus.FAILED and self.reason_code is None:
            raise ValueError("failed documents require reason_code")
        if self.status is not ProjectDocumentStatus.FAILED and self.reason_code is not None:
            raise ValueError("reason_code is only valid for failed documents")

    def to_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "project_id": self.project_id,
            "title": self.title,
            "media_type": self.media_type.value,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "status": self.status.value,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "page_count": self.page_count,
            "chunk_count": self.chunk_count,
            "ocr_page_count": self.ocr_page_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ProjectDocumentChunk:
    chunk_id: str
    document_id: str
    project_id: str
    tenant_id: str
    user_id: str
    text: str
    page_start: int
    page_end: int
    section: str | None = None

    def __post_init__(self) -> None:
        for name in ("chunk_id", "document_id", "project_id", "tenant_id", "user_id", "text"):
            _required(getattr(self, name), name)
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("invalid page range")


@dataclass(frozen=True, slots=True)
class ProjectDocumentQuery:
    tenant_id: str
    user_id: str
    project_id: str
    query: str
    document_ids: tuple[str, ...] = ()
    top_k: int = 8
    min_score: float = 0.6
    timeout_ms: int = 3_000

    def __post_init__(self) -> None:
        for name in ("tenant_id", "user_id", "project_id", "query"):
            _required(getattr(self, name), name)
        if not 1 <= self.top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        if not 0 <= self.min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")
        if not 1 <= self.timeout_ms <= 10_000:
            raise ValueError("timeout_ms must be between 1 and 10000")
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("document_ids must be unique")


@dataclass(frozen=True, slots=True)
class ProjectDocumentEvidence:
    citation_id: str
    chunk_id: str
    document_id: str
    project_id: str
    title: str
    text: str
    page_start: int
    page_end: int
    section: str | None
    score: float


@dataclass(frozen=True, slots=True)
class ProjectDocumentResponse:
    evidence: tuple[ProjectDocumentEvidence, ...]
    degraded: bool = False
    reason_code: str | None = None


def deterministic_default_project_id(tenant_id: str, user_id: str) -> str:
    """Stable opaque default ID without exposing principal values."""
    from uuid import NAMESPACE_URL, uuid5

    _required(tenant_id, "tenant_id")
    _required(user_id, "user_id")
    return f"project_{uuid5(NAMESPACE_URL, f'cowork/default/{tenant_id}/{user_id}').hex}"


def can_transition_document(current: ProjectDocumentStatus, target: ProjectDocumentStatus) -> bool:
    if current is target:
        return True
    allowed = {
        ProjectDocumentStatus.RECEIVED: {
            ProjectDocumentStatus.EXTRACTING,
            ProjectDocumentStatus.FAILED,
            ProjectDocumentStatus.DELETING,
        },
        ProjectDocumentStatus.EXTRACTING: {
            ProjectDocumentStatus.INDEXING,
            ProjectDocumentStatus.FAILED,
            ProjectDocumentStatus.DELETING,
        },
        ProjectDocumentStatus.INDEXING: {
            ProjectDocumentStatus.READY,
            ProjectDocumentStatus.FAILED,
            ProjectDocumentStatus.DELETING,
        },
        ProjectDocumentStatus.READY: {ProjectDocumentStatus.DELETING},
        ProjectDocumentStatus.FAILED: {ProjectDocumentStatus.DELETING},
        ProjectDocumentStatus.DELETING: {ProjectDocumentStatus.DELETED},
        ProjectDocumentStatus.DELETED: set(),
    }
    return target in allowed[current]


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _bounded(value: object, name: str, maximum: int) -> str:
    result = _required(value, name)
    if len(result) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters")
    return result
