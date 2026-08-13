import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from cowork_agent.orchestration.project_document_worker import ProjectDocumentIngestionWorker
from cowork_agent.persistence.repositories.projects import ProjectDocument

SOURCE = b"private project source"


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


class Storage:
    async def download_to(self, object_key: str, target: Path) -> None:
        assert object_key == "private/source"
        target.write_bytes(SOURCE)


class Extractor:
    def extract(self, path: Path, media_type: str) -> SimpleNamespace:
        assert path.suffix == ".pdf" and media_type == "application/pdf"
        return SimpleNamespace(page_count=2, chunks=(("first page", 1, 1), ("second page", 2, 2)))


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

    asyncio.run(scenario())
