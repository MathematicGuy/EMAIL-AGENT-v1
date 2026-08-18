import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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

    async def index(self, **kwargs: object) -> int:
        self.calls.append(kwargs)
        return len(kwargs["chunks"])  # type: ignore[arg-type]


def test_worker_verifies_then_indexes_private_document_without_persisting_text() -> None:
    async def scenario() -> None:
        repository = Repository()
        vectors = Vectors()
        worker = ProjectDocumentIngestionWorker(repository, Storage(), Extractor(), vectors)
        await worker.execute("document-1")

        assert [item["to_status"] for item in repository.transitions] == ["indexing", "ready"]
        assert repository.finished == [{"document_id": "document-1", "status": "completed"}]
        assert len(vectors.calls[0]["chunks"]) == 2  # type: ignore[arg-type]
        # ADR-008: nothing publishes readiness into the vector store any more.
        # The retrieval ACL joins project_documents.status, so the transition
        # to "ready" above is the only thing that makes the document visible.
        assert not hasattr(vectors, "ready_calls")

    asyncio.run(scenario())


def test_worker_logs_metadata_safe_stage_timings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        worker = ProjectDocumentIngestionWorker(
            Repository(), Storage(), Extractor(), Vectors()
        )
        with caplog.at_level(logging.INFO):
            await worker.execute("document-1")

        messages = [record.getMessage() for record in caplog.records]
        timing = [
            message
            for message in messages
            if message.startswith("project_document_ingestion_timing ")
        ]
        assert [message.split()[1] for message in timing] == [
            "stage=source_download",
            "stage=extraction_chunking",
            "stage=ready_transition",
            "stage=worker_execution",
        ]
        assert all(" document_id=document-1" in message for message in timing)
        assert all(" duration_ms=" in message for message in timing)
        assert all(" outcome=success" in message for message in timing)
        assert all("project_id=" not in message for message in timing)
        assert all(
            sensitive not in "\n".join(timing)
            for sensitive in ("source.pdf", "private/source", "first page", "second page")
        )

    asyncio.run(scenario())


def test_failed_download_logs_the_reached_stage_and_worker_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingStorage:
        async def download_to(self, object_key: str, target: Path) -> None:
            del object_key, target
            raise OSError("signed-url=must-not-be-logged")

    async def scenario() -> None:
        with caplog.at_level(logging.INFO):
            await ProjectDocumentIngestionWorker(
                Repository(), FailingStorage(), Extractor(), Vectors()
            ).execute("document-1")

        timing = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("project_document_ingestion_timing ")
        ]
        assert [message.split()[1] for message in timing] == [
            "stage=source_download",
            "stage=worker_execution",
        ]
        assert all(" outcome=error" in message for message in timing)
        assert "signed-url" not in "\n".join(timing)

    asyncio.run(scenario())


def test_source_verification_is_not_counted_as_extraction_chunking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class CorruptStorage:
        async def download_to(self, object_key: str, target: Path) -> None:
            del object_key
            target.write_bytes(SOURCE + b"corrupt")

    async def scenario() -> None:
        with caplog.at_level(logging.INFO):
            await ProjectDocumentIngestionWorker(
                Repository(), CorruptStorage(), Extractor(), Vectors()
            ).execute("document-1")

        timing = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("project_document_ingestion_timing ")
        ]
        assert [message.split()[1] for message in timing] == [
            "stage=source_download",
            "stage=worker_execution",
        ]
        assert " outcome=success" in timing[0]
        assert " outcome=error" in timing[1]

    asyncio.run(scenario())


def test_failed_ready_transition_logs_stage_and_worker_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class StaleRepository(Repository):
        async def transition_document(self, document_id: str, **kwargs: object) -> bool:
            self.transitions.append({"document_id": document_id, **kwargs})
            return kwargs["to_status"] != "ready"

    async def scenario() -> None:
        with caplog.at_level(logging.INFO):
            await ProjectDocumentIngestionWorker(
                StaleRepository(), Storage(), Extractor(), Vectors()
            ).execute("document-1")

        timing = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("project_document_ingestion_timing ")
        ]
        assert [message.split()[1] for message in timing] == [
            "stage=source_download",
            "stage=extraction_chunking",
            "stage=ready_transition",
            "stage=worker_execution",
        ]
        assert [" outcome=error" in message for message in timing] == [
            False,
            False,
            True,
            True,
        ]

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
            raise OSError("index unavailable")

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


def test_worker_preserves_the_safe_native_extraction_failure_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingExtractor:
        def extract(self, path: Path, media_type: str) -> ExtractedProjectDocument:
            del path, media_type
            raise ProjectDocumentExtractionError("native_extraction_failed")

    async def scenario() -> None:
        repository = Repository()
        worker = ProjectDocumentIngestionWorker(
            repository, Storage(), FailingExtractor(), Vectors()
        )

        with caplog.at_level(logging.INFO):
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
        timing = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("project_document_ingestion_timing ")
        ]
        assert [message.split()[1] for message in timing] == [
            "stage=source_download",
            "stage=extraction_chunking",
            "stage=worker_execution",
        ]
        assert [" outcome=error" in message for message in timing] == [False, True, True]

    asyncio.run(scenario())


def test_worker_execution_timer_starts_after_successful_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class RecordingRepository(Repository):
        async def claim_job(self, document_id: str) -> ProjectDocument | None:
            events.append("claim")
            return await super().claim_job(document_id)

    def clock() -> float:
        events.append("clock")
        return 1.0

    async def scenario() -> None:
        monkeypatch.setattr(
            "cowork_agent.orchestration.project_document_worker.perf_counter", clock
        )
        await ProjectDocumentIngestionWorker(
            RecordingRepository(), Storage(), Extractor(), Vectors()
        ).execute("document-1")
        assert events[:2] == ["claim", "clock"]

    asyncio.run(scenario())
