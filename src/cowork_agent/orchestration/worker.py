"""Separate worker process polling durable Supabase Postgres jobs."""

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
from qdrant_client import AsyncQdrantClient

from cowork_agent.config import (
    FaucetSettings,
    GeminiEmbeddingSettings,
    GeminiSettings,
    GmailSettings,
    GroqSettings,
    JinaEmbeddingSettings,
    QdrantSettings,
    UserDocumentsSettings,
    database_url,
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
    from cowork_agent.persistence.repositories.identity import (
        PostgresMailboxConnectionRepository,
    )
    from cowork_agent.persistence.repositories.postgres import (
        PostgresOutboxRepository,
        PostgresRunRepository,
        PostgresTaskRepository,
    )
    from cowork_agent.persistence.repositories.projects import PostgresProjectRepository

    pool = AsyncConnectionPool(database_url(), min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    storage_client: httpx.AsyncClient | None = None
    qdrant_client: AsyncQdrantClient | None = None
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
            from cowork_agent.integrations.rag.project_documents import ProjectDocumentVectorStore
            from cowork_agent.integrations.storage.supabase import SupabasePrivateStorage

            try:
                qdrant = QdrantSettings.from_env()
                if not qdrant.enabled:
                    raise ValueError("Qdrant is not configured")
                storage = SupabaseStorageSettings.from_env()
                embedding = GeminiEmbeddingSettings.from_env()
                storage_client = httpx.AsyncClient(timeout=30.0)
                qdrant_client = AsyncQdrantClient(url=qdrant.url, api_key=qdrant.api_key or None)
                private_storage = SupabasePrivateStorage(
                    storage.url, storage.secret_key, storage.bucket, storage_client
                )
                document_vectors = ProjectDocumentVectorStore(
                    qdrant_client,
                    qdrant.project_collection_name,
                    GeminiEmbeddingAdapter(embedding),
                    vector_size=embedding.dimensions,
                )
                await asyncio.wait_for(
                    document_vectors.ensure_collection(),
                    timeout=document_settings.retrieval_timeout_ms / 1000,
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
        if qdrant_client is not None:
            await qdrant_client.close()
        await pool.close()


def main() -> None:
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
    if sys.platform == "win32":
        # psycopg async cannot run on Windows' ProactorEventLoop.
        from asyncio import windows_events

        asyncio.set_event_loop_policy(windows_events.WindowsSelectorEventLoopPolicy())
    if not database_url():
        raise SystemExit("mail-todo-worker requires DATABASE_URL")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
