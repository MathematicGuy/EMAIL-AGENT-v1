"""Separate worker process polling durable Supabase Postgres jobs."""

import asyncio
import logging
import os
import sys

import httpx
from qdrant_client import AsyncQdrantClient

from cowork_agent.config import (
    FaucetSettings,
    GeminiSettings,
    GmailSettings,
    GroqSettings,
    QdrantSettings,
    database_url,
)
from cowork_agent.features.email_action_plan.observability import (
    LoggingTraceSink,
    dev_trace_sink_from_env,
)
from cowork_agent.features.email_action_plan.ports import (
    ActionPlanGeneratorPort,
    RouteClassifierPort,
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
from cowork_agent.persistence.repositories.local import InMemoryResultRepository

logger = logging.getLogger(__name__)

async def run_worker() -> None:
    # Lazy imports: the durable extras are optional, so the friendly URL
    # check in main() must run even without them installed.
    from psycopg_pool import AsyncConnectionPool

    from cowork_agent.orchestration.postgres_poller import CallableJobSource, PostgresPoller
    from cowork_agent.orchestration.project_document_worker import ProjectDocumentIngestionWorker
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
            semantic_memory = await build_semantic_memory(gemini_settings)
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
        digest_poller = PostgresPoller(CallableJobSource(runs.next_claimable_run), digest_worker)
        document_poller = None
        if provider == "gemini" and os.getenv("SUPABASE_URL", "").strip():
            from cowork_agent.config import SupabaseStorageSettings
            from cowork_agent.integrations.knowledge_ingestion.project_documents import (
                ProjectDocumentExtractor,
            )
            from cowork_agent.integrations.rag.embeddings import GeminiEmbeddingAdapter
            from cowork_agent.integrations.rag.project_documents import ProjectDocumentVectorStore
            from cowork_agent.integrations.storage.supabase import SupabasePrivateStorage

            qdrant = QdrantSettings.from_env()
            if qdrant.enabled:
                storage = SupabaseStorageSettings.from_env()
                storage_client = httpx.AsyncClient(timeout=30.0)
                qdrant_client = AsyncQdrantClient(url=qdrant.url, api_key=qdrant.api_key or None)
                document_worker = ProjectDocumentIngestionWorker(
                    PostgresProjectRepository(pool),
                    SupabasePrivateStorage(
                        storage.url, storage.secret_key, storage.bucket, storage_client
                    ),
                    ProjectDocumentExtractor(),
                    ProjectDocumentVectorStore(
                        qdrant_client,
                        qdrant.project_collection_name,
                        GeminiEmbeddingAdapter(gemini_settings),
                    ),
                )
                document_poller = PostgresPoller(
                    CallableJobSource(PostgresProjectRepository(pool).next_claimable_job),
                    document_worker,
                )
            else:
                logger.warning(
                    "Project document polling is disabled because Qdrant is not configured"
                )
        logger.info("Worker ready; polling durable Postgres jobs")
        if document_poller is None:
            await digest_poller.run_forever()
        else:
            await asyncio.gather(digest_poller.run_forever(), document_poller.run_forever())
    finally:
        if storage_client is not None:
            await storage_client.aclose()
        if qdrant_client is not None:
            await qdrant_client.close()
        await pool.close()


def main() -> None:
    # See app.main(): INFO records are dropped without a root handler, and the
    # trace sink plus lifecycle publication are INFO-only.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    if sys.platform == "win32":
        # psycopg async cannot run on Windows' ProactorEventLoop.
        from asyncio import windows_events

        asyncio.set_event_loop_policy(windows_events.WindowsSelectorEventLoopPolicy())
    if not database_url():
        raise SystemExit("mail-todo-worker requires DATABASE_URL")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
