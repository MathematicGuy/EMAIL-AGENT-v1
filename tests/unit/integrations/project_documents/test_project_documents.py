from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from cowork_agent.domain.project_documents import (
    ProjectDocumentMediaType,
    ProjectDocumentQuery,
    ProjectDocumentStatus,
    can_transition_document,
)
from cowork_agent.features.ai_chat.graph.runner import build_chat_graph
from cowork_agent.integrations.knowledge_ingestion.models import ExtractionResult
from cowork_agent.integrations.project_documents.chunking import chunk_pages, render_pages
from cowork_agent.integrations.project_documents.encrypted_store import EncryptedDocumentStore
from cowork_agent.integrations.project_documents.ingestion import ProjectDocumentIngestionService
from cowork_agent.integrations.project_documents.qdrant_store import QdrantProjectDocumentStore
from cowork_agent.integrations.project_documents.sniffing import sniff_media_type
from cowork_agent.orchestration.document_retention import DocumentRetentionManager
from cowork_agent.persistence.repositories.project_documents import (
    InMemoryProjectDocumentRepository,
    InMemoryProjectRepository,
)

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _docx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Override ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/>',
        )
        archive.writestr("word/document.xml", "<document/>")
    return output.getvalue()


async def _create_document(
    repository: InMemoryProjectDocumentRepository,
    *,
    document_id: str = "document_1",
    project_id: str = "project_1",
    sha256: str = "a" * 64,
    expires_at: datetime | None = None,
):
    return await repository.create_or_get(
        document_id=document_id,
        project_id=project_id,
        tenant_id="tenant_1",
        user_id="user_1",
        title="Policy.docx",
        media_type=ProjectDocumentMediaType.DOCX,
        size_bytes=10,
        sha256=sha256,
        created_at=NOW,
        expires_at=expires_at or NOW + timedelta(days=30),
    )


def test_document_state_machine_is_fail_closed() -> None:
    assert can_transition_document(ProjectDocumentStatus.RECEIVED, ProjectDocumentStatus.EXTRACTING)
    assert can_transition_document(ProjectDocumentStatus.READY, ProjectDocumentStatus.DELETED)
    assert not can_transition_document(ProjectDocumentStatus.RECEIVED, ProjectDocumentStatus.READY)
    assert not can_transition_document(ProjectDocumentStatus.DELETED, ProjectDocumentStatus.READY)


def test_content_sniffing_uses_bytes_not_extension() -> None:
    assert sniff_media_type(b"%PDF-1.7\n") is ProjectDocumentMediaType.PDF
    assert sniff_media_type(_docx_bytes()) is ProjectDocumentMediaType.DOCX
    with pytest.raises(ValueError):
        sniff_media_type(b"PK\x03\x04not-a-docx")


def test_chunking_is_page_aware_and_deterministic() -> None:
    values = dict(
        document_id="document_1",
        project_id="project_1",
        tenant_id="tenant_1",
        user_id="user_1",
        pages={1: "# First\n" + "a" * 400, 2: "# Second\n" + "b" * 400},
        max_chars=250,
        overlap_chars=25,
    )
    first = chunk_pages(**values)
    assert first == chunk_pages(**values)
    assert {chunk.page_start for chunk in first} == {1, 2}
    assert all(chunk.page_start == chunk.page_end for chunk in first)
    assert "<!-- Page 1 -->" in render_pages(values["pages"])


def test_encrypted_store_uses_opaque_paths_and_ciphertext(tmp_path) -> None:
    store = EncryptedDocumentStore(tmp_path, Fernet.generate_key().decode("ascii"))
    store.put_source("document_1", b"private bytes")
    store.put_markdown("document_1", "private text")
    assert store.read_source("document_1") == b"private bytes"
    assert store.read_markdown("document_1") == "private text"
    assert b"private bytes" not in (tmp_path / "document_1" / "source.bin").read_bytes()
    with pytest.raises(ValueError):
        store.put_source("../escape", b"bad")


def test_repository_deduplicates_only_inside_same_owner_project() -> None:
    async def scenario() -> None:
        repository = InMemoryProjectDocumentRepository()
        first, created = await _create_document(repository)
        duplicate, duplicate_created = await _create_document(repository, document_id="document_2")
        other_project, other_created = await _create_document(
            repository, document_id="document_3", project_id="project_2"
        )
        assert created and not duplicate_created and duplicate == first
        assert other_created and other_project.document_id == "document_3"

    asyncio.run(scenario())


def test_qdrant_authorization_denial_happens_before_embedding() -> None:
    class DeniedProjects:
        async def get_owned(self, *_args):
            return None

    class ExplodingEmbedder:
        async def embed(self, _texts):
            raise AssertionError("embedding I/O must not run")

    async def scenario() -> None:
        store = QdrantProjectDocumentStore(
            client=object(),  # type: ignore[arg-type]
            collection_name="project_documents",
            embedder=ExplodingEmbedder(),
            projects=DeniedProjects(),  # type: ignore[arg-type]
            documents=InMemoryProjectDocumentRepository(),
        )
        result = await store.retrieve(
            ProjectDocumentQuery("tenant_1", "user_1", "project_1", "question")
        )
        assert result.reason_code == "authorization_denied"
        assert result.evidence == ()

    asyncio.run(scenario())


def test_ingestion_activates_vectors_before_committing_ready(tmp_path) -> None:
    events: list[str] = []

    class Extractor:
        def extract(self, _path):
            return ExtractionResult("# Policy\nGrounded content", 1)

    class Vectors:
        async def upsert_chunks(self, chunks, *, expires_at):
            del expires_at
            events.append("upsert")
            return len(chunks)

        async def activate(self, document_id):
            del document_id
            events.append("activate")

        async def delete_document(self, document_id):
            del document_id

    async def scenario() -> None:
        repository = InMemoryProjectDocumentRepository()
        document, _ = await _create_document(repository)
        store = EncryptedDocumentStore(tmp_path, Fernet.generate_key().decode("ascii"))
        store.put_source(document.document_id, _docx_bytes())
        service = ProjectDocumentIngestionService(
            documents=repository,
            store=store,
            vectors=Vectors(),
            docx_extractor=Extractor(),  # type: ignore[arg-type]
        )
        ready = await service.process(document, worker_id="worker_1")
        assert ready is not None and ready.status is ProjectDocumentStatus.READY
        assert events == ["upsert", "activate"]

    asyncio.run(scenario())


def test_retention_marks_expired_ineligible_and_retries_physical_cleanup(tmp_path) -> None:
    deleted: list[str] = []

    class Vectors:
        async def delete_document(self, document_id: str) -> None:
            deleted.append(document_id)

    async def scenario() -> None:
        repository = InMemoryProjectDocumentRepository()
        document, _ = await _create_document(repository, expires_at=NOW - timedelta(seconds=1))
        store = EncryptedDocumentStore(tmp_path, Fernet.generate_key().decode("ascii"))
        store.put_source(document.document_id, b"source")
        manager = DocumentRetentionManager(documents=repository, store=store, vectors=Vectors())
        assert await manager.run_once() == 1
        assert await repository.get_job(document.document_id) is None
        assert deleted == [document.document_id]
        assert await manager.run_once() == 0

    asyncio.run(scenario())


def test_default_project_id_is_stable_and_owner_scoped() -> None:
    async def scenario() -> None:
        repository = InMemoryProjectRepository()
        first = await repository.resolve_default("tenant_1", "user_1")
        retry = await repository.resolve_default("tenant_1", "user_1")
        other = await repository.resolve_default("tenant_1", "user_2")
        assert first == retry
        assert first.project_id != other.project_id

    asyncio.run(scenario())


def test_langgraph_flow_keeps_only_lean_state_and_branches_on_route() -> None:
    visited: list[str] = []

    async def classify(state):
        visited.append("classify")
        return {**state, "route": "rag", "retrieval_query": state["user_message"]}

    async def retrieve(state):
        visited.append("retrieve")
        return {**state, "citation_ids": ["citation_1"]}

    async def assemble(state):
        visited.append("assemble")
        return state

    async def generate(state):
        visited.append("generate")
        return state

    async def persist(state):
        visited.append("persist")
        return {**state, "completed": True}

    async def scenario() -> None:
        graph = build_chat_graph(
            classify=classify,
            retrieve=retrieve,
            assemble=assemble,
            generate_or_clarify=generate,
            persist=persist,
        )
        result = await graph.ainvoke(
            {
                "tenant_id": "tenant_1",
                "user_id": "user_1",
                "project_id": "project_1",
                "session_id": "session_1",
                "turn_id": "turn_1",
                "user_message": "Question",
                "document_ids": [],
            }
        )
        assert visited == ["classify", "retrieve", "assemble", "generate", "persist"]
        assert result["completed"] is True
        assert not {"document_text", "chunks", "assembled_prompt", "bytes"} & set(result)

    asyncio.run(scenario())
