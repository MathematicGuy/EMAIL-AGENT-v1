"""PostgreSQL project/document ownership integration tests."""

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterator

import pytest

try:
    import psycopg
    from psycopg_pool import AsyncConnectionPool
except ImportError:  # pragma: no cover - environment-dependent
    pytest.skip("psycopg is not installed (pip install '.[postgres]')", allow_module_level=True)

from cowork_agent.persistence.migrate import apply_migrations
from cowork_agent.persistence.repositories.identity import PostgresIdentityRepository
from cowork_agent.persistence.repositories.projects import PostgresProjectRepository

DATABASE_URL = os.getenv("PG_TEST_URL", "")


def _server_available() -> bool:
    if not DATABASE_URL:
        return False
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


if not _server_available():
    pytest.skip("PG_TEST_URL is not configured or reachable", allow_module_level=True)


@pytest.fixture(autouse=True)
def fresh_schema() -> Iterator[None]:
    with psycopg.connect(DATABASE_URL, connect_timeout=3) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    yield


async def _pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=2, open=False)
    await pool.open(wait=True)
    return pool


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def test_project_document_repository_isolates_owners_and_deduplicates_content_digest() -> None:
    async def scenario() -> None:
        pool = await _pool()
        try:
            await apply_migrations(pool)
            identities = PostgresIdentityRepository(pool)
            projects = PostgresProjectRepository(pool)
            owner = await identities.resolve_or_create_principal("owner@example.com")
            other = await identities.resolve_or_create_principal("other@example.com")

            default = await projects.default_project(owner)
            assert default.is_default is True
            assert await projects.list_for(owner) == (default,)

            document, created = await projects.create_or_get_document(
                principal=owner,
                project_id=default.id,
                filename="plan.pdf",
                media_type="application/pdf",
                byte_size=100,
                content_sha256="a" * 64,
                expires_in_seconds=2_592_000,
            )
            duplicate, created_again = await projects.create_or_get_document(
                principal=owner,
                project_id=default.id,
                filename="renamed.pdf",
                media_type="application/pdf",
                byte_size=100,
                content_sha256="a" * 64,
                expires_in_seconds=2_592_000,
            )

            assert created is True
            assert created_again is False
            assert duplicate.id == document.id
            assert await projects.require_document(other, default.id, document.id) is None

            queued = await projects.mark_upload_completed(owner, default.id, document.id)
            assert queued is not None
            assert queued.status == "queued"
            assert await projects.next_claimable_job() == document.id
            claimed = await projects.claim_job(document.id)
            assert claimed is not None
            assert claimed.id == document.id
            assert claimed.status == "extracting"
            assert await projects.transition_document(
                document.id,
                from_status="extracting",
                to_status="indexing",
            )
            assert await projects.transition_document(
                document.id,
                from_status="indexing",
                to_status="ready",
                page_count=1,
                chunk_count=2,
            )
            assert await projects.finish_job(document.id, status="completed")
            deleting = await projects.begin_deletion(owner, default.id, document.id)
            assert deleting is not None
            assert deleting.status == "deleting"
            await projects.record_deletion_audit(
                document.id,
                postgres_outcome="hidden",
                qdrant_outcome="deleted",
                storage_outcome="pending",
            )
        finally:
            await pool.close()

    _run(scenario)
