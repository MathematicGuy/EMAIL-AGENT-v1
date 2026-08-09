"""Separate worker process consuming the durable run queue (V1-H T5.2).

Run as ``mail-todo-worker`` (requires ``DATABASE_URL`` and ``REDIS_URL``).
The PostgreSQL CAS claim is the single-execution authority: this process
may be restarted or run alongside peers without double-processing a run,
and nothing is lost because undelivered/failed messages stay in the
stream's pending list until claimed or dead-lettered.
"""

import asyncio
import logging
import os
import sys

from cowork_agent.config import (
    FaucetSettings,
    GeminiSettings,
    GmailSettings,
    GroqSettings,
    database_url,
    redis_url,
)
from cowork_agent.features.email_action_plan.observability import (
    LifecycleEventPublisher,
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
from cowork_agent.persistence.repositories.mailbox_connections import (
    SQLiteMailboxConnectionRepository,
)

logger = logging.getLogger(__name__)

async def run_worker() -> None:
    # Lazy imports: the durable extras are optional, so the friendly URL
    # check in main() must run even without them installed.
    from psycopg_pool import AsyncConnectionPool
    from redis.asyncio import Redis as AsyncRedis

    from cowork_agent.orchestration.redis_queue import RedisRunConsumer
    from cowork_agent.persistence.migrate import apply_migrations
    from cowork_agent.persistence.repositories.postgres import (
        PostgresOutboxRepository,
        PostgresRunRepository,
        PostgresTaskRepository,
    )

    pool = AsyncConnectionPool(database_url(), min_size=1, max_size=4, open=False)
    await pool.open(wait=True)
    redis_client = AsyncRedis.from_url(redis_url(), decode_responses=True)
    try:
        await apply_migrations(pool)
        runs = PostgresRunRepository(pool)
        tasks = PostgresTaskRepository(pool)
        outbox = PostgresOutboxRepository(pool)
        settings = GmailSettings.from_env()
        connection_repository = SQLiteMailboxConnectionRepository(
            settings.connection_db_path
        )
        await connection_repository.initialize()
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
        consumer = RedisRunConsumer(
            redis_client,
            runs,
            digest_worker,
            completion_outbox=outbox,
            publisher=LifecycleEventPublisher(outbox, LoggingTraceSink()),
        )
        logger.info("Worker ready; consuming the durable run queue")
        await consumer.run_forever()
    finally:
        await redis_client.aclose()
        await pool.close()


def main() -> None:
    # See app.main(): INFO records are dropped without a root handler, and the
    # trace sink plus lifecycle publication are INFO-only.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    if sys.platform == "win32":
        # psycopg async cannot run on Windows' ProactorEventLoop.
        from asyncio import windows_events

        asyncio.set_event_loop_policy(windows_events.WindowsSelectorEventLoopPolicy())
    if not database_url() or not redis_url():
        raise SystemExit("mail-todo-worker requires DATABASE_URL and REDIS_URL")
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
