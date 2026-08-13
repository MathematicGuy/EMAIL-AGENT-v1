from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from redis.exceptions import ResponseError

from cowork_agent.domain.project_documents import ProjectDocumentMediaType
from cowork_agent.orchestration.document_ingestion import (
    DocumentIngestionDispatcher,
    RedisDocumentIngestionConsumer,
)
from cowork_agent.persistence.repositories.project_documents import (
    InMemoryProjectDocumentRepository,
)

NOW = datetime.now(UTC)


async def _document(repository: InMemoryProjectDocumentRepository):
    return (
        await repository.create_or_get(
            document_id="document_1",
            project_id="project_1",
            tenant_id="tenant_1",
            user_id="user_1",
            title="Policy.pdf",
            media_type=ProjectDocumentMediaType.PDF,
            size_bytes=10,
            sha256="a" * 64,
            created_at=NOW,
            expires_at=NOW + timedelta(days=30),
        )
    )[0]


def test_redis_dispatch_payload_contains_only_ids_and_scope_metadata() -> None:
    class Redis:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        async def xadd(self, name, fields):
            self.calls.append((name, fields))

    async def scenario() -> None:
        repository = InMemoryProjectDocumentRepository()
        document = await _document(repository)
        redis = Redis()
        dispatcher = DocumentIngestionDispatcher(
            documents=repository,
            process=lambda item: asyncio.sleep(0),
            stream_name="documents",
            redis=redis,
        )
        await dispatcher.dispatch(document)
        assert redis.calls == [
            (
                "documents",
                {
                    "document_id": "document_1",
                    "project_id": "project_1",
                    "tenant_id": "tenant_1",
                    "user_id": "user_1",
                },
            )
        ]

    asyncio.run(scenario())


def test_redis_consumer_acks_success_and_relies_on_repository_claim() -> None:
    class Redis:
        def __init__(self) -> None:
            self.acks: list[str] = []

        async def xgroup_create(self, *_args, **_kwargs):
            raise ResponseError("BUSYGROUP Consumer Group name already exists")

        async def xpending_range(self, *_args, **_kwargs):
            return []

        async def xreadgroup(self, *_args, **_kwargs):
            return [("documents", [("message_1", {"document_id": "document_1"})])]

        async def xack(self, _stream, _group, message_id):
            self.acks.append(message_id)

    async def scenario() -> None:
        repository = InMemoryProjectDocumentRepository()
        await _document(repository)
        processed: list[str] = []

        async def process(document):
            processed.append(document.document_id)

        redis = Redis()
        consumer = RedisDocumentIngestionConsumer(
            redis=redis,
            documents=repository,
            process=process,
            stream_name="documents",
        )
        await consumer.ensure_group()
        assert await consumer.deliver_once() == 1
        assert processed == ["document_1"]
        assert redis.acks == ["message_1"]

    asyncio.run(scenario())
