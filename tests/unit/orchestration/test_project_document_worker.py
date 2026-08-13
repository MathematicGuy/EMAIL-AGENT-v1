import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cowork_agent.integrations.knowledge_ingestion.project_documents import (
    ExtractedProjectDocument,
    ExtractedProjectDocumentChunk,
    ProjectDocumentExtractionError,
)
from cowork_agent.orchestration.project_document_worker import ProjectDocumentIngestionWorker
from cowork_agent.persistence.repositories.projects import ProjectDocument

SOURCE = b"%PDF-1.7\nprivate project source"


class Repository:
    def __init__(self) -> None:
        self.transitions: list[dict[str, object]] = []
        self.finished: list[dict[str, object]] = []

    async def claim_job(self, document_id: str) -> ProjectDocument | None:
        assert document_id == "document-1"
        return ProjectDocument(
            "document-1", "project-1", "workspace-1", "user-1", "source.pdf",
            "application/pdf", len(SOURCE), hashlib.sha256(SOURCE).hexdigest(), "private/source",
            "received", datetime.now(UTC) + timedelta(days=1),
        )

    async def transition_document(self, document_id: str, **kwargs: object) -> bool:
        self.transitions.append({"document_id": document_id, **kwargs})
        return True

    async def finish_job(self, document_id: str, **kwargs: object) -> bool:
        self.finished.append({"document_id": document_id, **kwargs})
        return True

    async def retry_job(self, document_id: str, **kwargs: object) -> bool:
        raise AssertionError(f"unexpected retry for {document_id}: {kwargs}")


class Storage:
    async def download_to(self, object_key: str, target: Path) -> None:
        assert object_key == "private/source"
        target.write_bytes(SOURCE)


class Extractor:
    def extract(self, path: Path, media_type: str) -> ExtractedProjectDocument:
        assert path.suffix == ".pdf" and media_type == "application/pdf"
        return ExtractedProjectDocument(
            page_count=2,
            chunks=(
                ExtractedProjectDocumentChunk("first page", 1, 1, None),
                ExtractedProjectDocumentChunk("second page", 2, 2, None),
            ),
        )


class Vectors:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.ready_calls: list[dict[str, object]] = []

    async def index(self, **kwargs: object) -> int:
        self.calls.append(kwargs)
        return len(kwargs["chunks"])  # type: ignore[arg-type]

    async def mark_document_ready(self, **kwargs: object) -> None:
        self.ready_calls.append(kwargs)


def test_worker_verifies_then_indexes_private_document_without_persisting_text() -> None:
    async def scenario() -> None:
        repository = Repository()
        vectors = Vectors()
        worker = ProjectDocumentIngestionWorker(repository, Storage(), Extractor(), vectors)
        await worker.execute("document-1")

        assert [item["to_status"] for item in repository.transitions] == ["indexing", "ready"]
        assert repository.finished == [{"document_id": "document-1", "status": "completed"}]
        assert len(vectors.calls[0]["chunks"]) == 2  # type: ignore[arg-type]
        assert vectors.ready_calls == [{
            "workspace_id": "workspace-1",
            "user_id": "user-1",
            "project_id": "project-1",
            "document_id": "document-1",
        }]

    asyncio.run(scenario())


def test_worker_requeues_a_transient_index_failure_with_bounded_retry() -> None:
    class RetryRepository(Repository):
        def __init__(self) -> None:
            super().__init__()
            self.retries: list[dict[str, object]] = []

        async def retry_job(self, document_id: str, **kwargs: object) -> bool:
            self.retries.append({"document_id": document_id, **kwargs})
            return True

    class FailingVectors(Vectors):
        async def index(self, **kwargs: object) -> int:
            del kwargs
            raise OSError("qdrant unavailable")

    async def scenario() -> None:
        repository = RetryRepository()
        worker = ProjectDocumentIngestionWorker(
            repository, Storage(), Extractor(), FailingVectors()
        )
        await worker.execute("document-1")
        assert repository.retries == [{
            "document_id": "document-1",
            "from_status": "indexing",
            "error_code": "index_unavailable",
            "max_attempts": 3,
            "delay_seconds": 30,
        }]
        assert repository.finished == []

    asyncio.run(scenario())


def test_worker_preserves_the_safe_native_extraction_failure_code() -> None:
    class FailingExtractor:
        def extract(self, path: Path, media_type: str) -> ExtractedProjectDocument:
            del path, media_type
            raise ProjectDocumentExtractionError("native_extraction_failed")

    async def scenario() -> None:
        repository = Repository()
        worker = ProjectDocumentIngestionWorker(
            repository, Storage(), FailingExtractor(), Vectors()
        )

        await worker.execute("document-1")

        assert repository.transitions == [{
            "document_id": "document-1",
            "from_status": "extracting",
            "to_status": "failed",
            "error_code": "native_extraction_failed",
        }]
        assert repository.finished == [{
            "document_id": "document-1",
            "status": "failed",
            "error_code": "native_extraction_failed",
        }]

    asyncio.run(scenario())
