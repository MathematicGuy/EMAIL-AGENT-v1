"""Separate worker process polling durable Supabase Postgres jobs."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx

from cowork_agent.config import (
    FaucetSettings,
    GeminiEmbeddingSettings,
    GeminiSettings,
    GmailSettings,
    GroqSettings,
    JinaEmbeddingSettings,
    UserDocumentsSettings,
    database_url,
    load_runtime_environment,
)
from cowork_agent.features.email_action_plan.observability import (
    LifecycleEventPublisher,
    LoggingTraceSink,
    dev_trace_sink_from_env,
)
from cowork_agent.features.email_action_plan.ports import (
    ActionPlanGeneratorPort,
    RouteClassifierPort,
    RunRepository,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore
from cowork_agent.features.email_action_plan.workflow import DigestWorker
from cowork_agent.integrations.gmail.auth import TokenCipher
from cowork_agent.integrations.gmail.fakes import SafeTextAttachmentExtractor
from cowork_agent.integrations.gmail.provider import GmailMailboxAdapter
from cowork_agent.integrations.llm.providers.faucet import (
    FaucetActionPlanGenerator,
    FaucetRouteClassifier,
)
from cowork_agent.integrations.llm.providers.gemini import (
    GeminiActionPlanGenerator,
    GeminiRouteClassifier,
)
from cowork_agent.integrations.llm.providers.groq import (
    GroqActionPlanGenerator,
    GroqRouteClassifier,
)
from cowork_agent.integrations.rag.bootstrap import build_semantic_memory
from cowork_agent.integrations.rag.null_memory import NullSemanticMemory
from cowork_agent.orchestration.document_recovery import (
    ProjectDocumentLeaseRepository,
    recover_stale_document_jobs,
)
from cowork_agent.orchestration.recovery import sweep_stuck_runs
from cowork_agent.persistence.repositories.local import InMemoryResultRepository
from cowork_agent.runtime import configure_windows_event_loop_policy

logger = logging.getLogger(__name__)


class PostgresWorkerMaintenance:
    """Recover expired claims and publish durable lifecycle events."""

    def __init__(
        self,
        runs: RunRepository,
        documents: ProjectDocumentLeaseRepository,
        publisher: LifecycleEventPublisher,
    ) -> None:
        self._runs = runs
        self._documents = documents
        self._publisher = publisher

    async def run(self) -> None:
        now = datetime.now(UTC)
        await sweep_stuck_runs(self._runs, now=now)
        await recover_stale_document_jobs(self._documents, now=now)
        await self._publisher.publish_pending()


class HeartbeatRepository(Protocol):
    async def record_document_worker_heartbeat(self) -> None: ...


class ProjectDocumentWorkerHeartbeat:
    """Publish liveness only from a fully composed document poller."""

    def __init__(self, projects: HeartbeatRepository) -> None:
        self._projects = projects

    async def run(self) -> None:
        await self._projects.record_document_worker_heartbeat()

async def run_worker() -> None:
    # Lazy imports: the durable extras are optional, so the friendly URL
    # check in main() must run even without them installed.
    from psycopg_pool import AsyncConnectionPool

    from cowork_agent.orchestration.postgres_poller import CallableJobSource, PostgresPoller
    from cowork_agent.orchestration.project_document_worker import (
        ProjectDocumentCleanupWorker,
        ProjectDocumentIngestionWorker,
    )
    from cowork_agent.persistence.migrate import apply_migrations
    from cowork_agent.persistence.pool import control_plane_pool_kwargs
    from cowork_agent.persistence.repositories.identity import (
        PostgresMailboxConnectionRepository,
    )
    from cowork_agent.persistence.repositories.postgres import (
        PostgresOutboxRepository,
        PostgresRunRepository,
        PostgresTaskRepository,
    )
    from cowork_agent.persistence.repositories.projects import PostgresProjectRepository

    pool = AsyncConnectionPool(database_url(), **control_plane_pool_kwargs())
    await pool.open(wait=True)
    storage_client: httpx.AsyncClient | None = None
    try:
        await apply_migrations(pool)
        runs = PostgresRunRepository(pool)
        tasks = PostgresTaskRepository(pool)
        outbox = PostgresOutboxRepository(pool)
        settings = GmailSettings.from_env()
        connection_repository = PostgresMailboxConnectionRepository(pool)
        provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
        classifier: RouteClassifierPort
        generator: ActionPlanGeneratorPort
        if provider == "gemini":
            gemini_settings = GeminiSettings.from_env()
            classifier = GeminiRouteClassifier(gemini_settings)
            generator = GeminiActionPlanGenerator(gemini_settings)
            jina_embedding_settings = JinaEmbeddingSettings.from_env()
            semantic_memory = await build_semantic_memory(jina_embedding_settings)
        elif provider == "groq":
            groq_settings = GroqSettings.from_env()
            classifier = GroqRouteClassifier(groq_settings)
            generator = GroqActionPlanGenerator(groq_settings)
            semantic_memory = NullSemanticMemory()
        elif provider == "faucet":
            faucet_settings = FaucetSettings.from_env()
            classifier = FaucetRouteClassifier(faucet_settings)
            generator = FaucetActionPlanGenerator(faucet_settings)
            semantic_memory = NullSemanticMemory()
        else:
            raise ValueError("LLM_PROVIDER must be 'gemini', 'groq', or 'faucet'")
        digest_worker = DigestWorker(
            runs,
            InMemoryResultRepository(),
            GmailMailboxAdapter(
                settings, connection_repository, TokenCipher(settings.token_encryption_key)
            ),
            SafeTextAttachmentExtractor(),
            classifier,
            generator,
            ShortTermStore(),
            tasks,
            semantic_memory=semantic_memory,
            trace_sink=LoggingTraceSink(),
            dev_trace=dev_trace_sink_from_env(
                settings.connection_db_path.parent, settings.token_encryption_key
            ),
            completion_outbox=outbox,
        )
        projects = PostgresProjectRepository(pool)
        maintenance = PostgresWorkerMaintenance(
            runs,
            projects,
            LifecycleEventPublisher(outbox, LoggingTraceSink()),
        )
        digest_poller = PostgresPoller(
            CallableJobSource(runs.next_claimable_run),
            digest_worker,
            maintenance=maintenance,
        )
        document_poller = None
        cleanup_poller = None
        document_settings = UserDocumentsSettings.from_env()
        if document_settings.enabled:
            from cowork_agent.config import SupabaseStorageSettings
            from cowork_agent.integrations.knowledge_ingestion.project_documents import (
                ProjectDocumentExtractor,
            )
            from cowork_agent.integrations.rag.embeddings import GeminiEmbeddingAdapter
            from cowork_agent.integrations.rag.project_documents import (
                HybridProjectDocumentStore,
            )
            from cowork_agent.integrations.rag.project_index import TurbovecProjectIndexStore
            from cowork_agent.integrations.storage.supabase import SupabasePrivateStorage
            from cowork_agent.persistence.repositories.project_document_chunks import (
                PostgresProjectDocumentChunkRepository,
            )

            try:
                storage = SupabaseStorageSettings.from_env()
                embedding = GeminiEmbeddingSettings.from_env()
                storage_client = httpx.AsyncClient(timeout=30.0)
                private_storage = SupabasePrivateStorage(
                    storage.url, storage.secret_key, storage.bucket, storage_client
                )
                # This process is the only writer of a project .tvim; the API
                # reads the snapshots it publishes to the same private bucket.
                document_vectors = HybridProjectDocumentStore(
                    PostgresProjectDocumentChunkRepository(pool),
                    TurbovecProjectIndexStore(
                        document_settings.index_root,
                        storage=private_storage,
                        vector_size=embedding.dimensions,
                    ),
                    GeminiEmbeddingAdapter(embedding),
                    vector_size=embedding.dimensions,
                )
                document_worker = ProjectDocumentIngestionWorker(
                    projects,
                    private_storage,
                    ProjectDocumentExtractor(),
                    document_vectors,
                    max_pages=document_settings.max_pages,
                )
                cleanup_worker = ProjectDocumentCleanupWorker(
                    projects, private_storage, document_vectors
                )
                document_poller = PostgresPoller(
                    CallableJobSource(projects.next_claimable_job),
                    document_worker,
                    maintenance=ProjectDocumentWorkerHeartbeat(projects),
                )
                cleanup_poller = PostgresPoller(
                    CallableJobSource(projects.next_claimable_cleanup),
                    cleanup_worker,
                )
            except Exception:
                logger.exception(
                    "Project document polling is degraded; digest polling remains online"
                )
        logger.info("Worker ready; polling durable Postgres jobs")
        pollers = [digest_poller.run_forever()]
        if document_poller is not None:
            pollers.append(document_poller.run_forever())
        if cleanup_poller is not None:
            pollers.append(cleanup_poller.run_forever())
        await asyncio.gather(*pollers)
    finally:
        if storage_client is not None:
            await storage_client.aclose()
        await pool.close()


async def run_sqlite_worker() -> None:
    """Poll the single-machine SQLite document queue without Postgres extras."""
    from cowork_agent.integrations.knowledge_ingestion.project_documents import (
        ProjectDocumentExtractor,
    )
    from cowork_agent.integrations.rag.embeddings import GeminiEmbeddingAdapter
    from cowork_agent.integrations.rag.project_documents import HybridProjectDocumentStore
    from cowork_agent.integrations.rag.project_index import TurbovecProjectIndexStore
    from cowork_agent.integrations.storage.local import LocalPrivateStorage
    from cowork_agent.orchestration.project_document_worker import ProjectDocumentIngestionWorker
    from cowork_agent.persistence.repositories.sqlite_project_document_chunks import (
        SQLiteProjectDocumentChunkRepository,
    )
    from cowork_agent.persistence.repositories.sqlite_projects import SQLiteProjectRepository

    settings = GmailSettings.from_env()
    document_settings = UserDocumentsSettings.from_env()
    if not document_settings.enabled:
        logger.info("User documents are disabled; SQLite worker is idle")
        while True:
            await asyncio.sleep(60)
    root = settings.connection_db_path.parent
    projects = SQLiteProjectRepository(root / "projects.db")
    chunks = SQLiteProjectDocumentChunkRepository(root / "project_chunks.db", root / "projects.db")
    embedding = GeminiEmbeddingSettings.from_env()
    vectors = HybridProjectDocumentStore(
        chunks,
        TurbovecProjectIndexStore(
            document_settings.index_root,
            vector_size=embedding.dimensions,
        ),
        GeminiEmbeddingAdapter(embedding),
        vector_size=embedding.dimensions,
    )
    await projects.initialize()
    await chunks.initialize()
    worker = ProjectDocumentIngestionWorker(
        projects,
        LocalPrivateStorage(root / "project-documents"),
        ProjectDocumentExtractor(),
        vectors,
        max_pages=document_settings.max_pages,
    )
    while True:
        await projects.record_document_worker_heartbeat()
        document_id = await projects.next_claimable_job()
        if document_id is None:
            await asyncio.sleep(1)
            continue
        await worker.execute(document_id)


def main() -> None:
    load_runtime_environment()
    # See app.main(): INFO records are dropped without a root handler, and the
    # trace sink plus lifecycle publication are INFO-only.
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE", ".data/worker.log")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    configure_windows_event_loop_policy()
    asyncio.run(run_worker() if database_url() else run_sqlite_worker())


if __name__ == "__main__":
    main()
