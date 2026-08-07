"""Redis Streams queue + DLQ integration tests (V1-H T5.2).

Run against a real Redis 7 server (dev container:
``docker run -d --name cowork-redis -p 6379:6379 redis:7-alpine``).
Override the target with ``REDIS_TEST_URL``; tests use database index 15
and flush it per test. The whole module skips when redis-py is not
installed or no server answers, so the default suite stays green without
Redis.
"""

import asyncio
import os
import selectors
import sys
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime

import pytest

REDIS_URL = os.getenv("REDIS_TEST_URL", "redis://127.0.0.1:6379/15")
SECRET_BODY = "Nội dung email tuyệt mật không được rò rỉ."
NOW = datetime(2026, 8, 8, 9, tzinfo=UTC)

try:
    import redis
    from redis.asyncio import Redis as AsyncRedis
except ImportError:  # pragma: no cover - environment-dependent
    pytest.skip("redis is not installed (pip install '.[redis]')", allow_module_level=True)

from cowork_agent.domain import DigestRun, RunStatus, RunTrigger  # noqa: E402
from cowork_agent.domain.target_contracts import (  # noqa: E402
    Actionability,
    BodyFormat,
    EphemeralEmailEnvelope,
    FetchStatus,
    PlanStep,
    Route,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.short_term import ShortTermStore  # noqa: E402
from cowork_agent.features.email_action_plan.workflow import (  # noqa: E402
    CreateDigestRun,
    DigestWorker,
)
from cowork_agent.integrations.gmail.fakes import (  # noqa: E402
    FakeMailbox,
    SafeTextAttachmentExtractor,
)
from cowork_agent.integrations.llm.fakes import (  # noqa: E402
    FakePlanGenerator,
    FakeRouteClassifier,
)
from cowork_agent.orchestration.redis_queue import (  # noqa: E402
    DEFAULT_DLQ_STREAM,
    DEFAULT_STREAM,
    RedisRunConsumer,
    RedisRunQueue,
)
from cowork_agent.persistence.repositories.local import (  # noqa: E402
    InMemoryResultRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)


def _run_scenario(scenario: Callable[[], Coroutine[object, object, None]]) -> None:
    # Windows' default ProactorEventLoop is avoided for parity with the
    # PostgreSQL suite; selector loops work everywhere.
    if sys.version_info >= (3, 12):
        asyncio.run(
            scenario(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
        return
    original_policy = asyncio.get_event_loop_policy()
    if sys.platform == "win32":
        from asyncio import windows_events

        asyncio.set_event_loop_policy(windows_events.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(scenario())
    finally:
        asyncio.set_event_loop_policy(original_policy)


def _server_available() -> bool:
    try:
        client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=3)
        return bool(client.ping())
    except redis.RedisError:
        return False


if not _server_available():
    pytest.skip(
        f"no Redis server at {REDIS_URL} (set REDIS_TEST_URL or start cowork-redis)",
        allow_module_level=True,
    )


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    client = redis.Redis.from_url(REDIS_URL)
    client.flushdb()
    yield
    client.flushdb()


class StubRunRepository:
    """Claim/get/save stub mirroring the CAS semantics of the PG adapter."""

    def __init__(self, run_ids: tuple[str, ...]) -> None:
        self.runs = {
            run_id: DigestRun(
                id=run_id,
                user_id="u1",
                mailbox_connection_id="mbx1",
                trigger=RunTrigger.ON_DEMAND,
                status=RunStatus.QUEUED,
                query="is:unread in:inbox",
                idempotency_key=run_id,
                max_emails=50,
            )
            for run_id in run_ids
        }

    async def create(self, run: DigestRun) -> tuple[DigestRun, bool]:
        raise AssertionError("not used in queue tests")

    async def get(self, run_id: str) -> DigestRun | None:
        return self.runs.get(run_id)

    async def claim(self, run_id: str, started_at: datetime) -> DigestRun | None:
        run = self.runs.get(run_id)
        if run is None or run.status is not RunStatus.QUEUED:
            return None
        run.status, run.started_at = RunStatus.RUNNING, started_at
        return run

    async def save(self, run: DigestRun) -> None:
        self.runs[run.id] = run


class RecordingExecutor:
    """Executor stub mirroring the DigestWorker contract: it owns the CAS
    claim and no-ops on a failed claim (duplicate/terminal delivery)."""

    def __init__(
        self, runs: StubRunRepository, fail_times: dict[str, int] | None = None
    ) -> None:
        self.calls: list[str] = []
        self._runs = runs
        self._fail_times = dict(fail_times or {})

    async def execute(self, run_id: str) -> None:
        claimed = await self._runs.claim(run_id, NOW)
        if claimed is None:
            return
        self.calls.append(run_id)
        if self._fail_times.get(run_id, 0) > 0:
            self._fail_times[run_id] -= 1
            raise RuntimeError("simulated worker crash")


def _consumer(
    client: AsyncRedis,
    runs: object,
    executor: object,
    *,
    consumer_name: str = "worker-a",
    max_retries: int = 3,
) -> RedisRunConsumer:
    return RedisRunConsumer(
        client,
        runs,
        executor,
        group="test-group",
        consumer_name=consumer_name,
        max_retries=max_retries,
        block_ms=10,
        claim_min_idle_ms=0,
    )


def test_enqueue_payload_is_metadata_only() -> None:
    async def scenario() -> None:
        client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
        try:
            queue = RedisRunQueue(client)
            await queue.enqueue_digest_run("run_1", user_id="u1", tenant_id="local")
            entries = await client.xrange(DEFAULT_STREAM)
            assert len(entries) == 1
            fields = entries[0][1]
            assert fields["run_id"] == "run_1"
            assert fields["user_id"] == "u1"
            assert fields["tenant_id"] == "local"
            assert "enqueued_at" in fields
            dump = repr(fields)
            assert SECRET_BODY not in dump
            assert "token" not in dump.lower()
        finally:
            await client.aclose()

    _run_scenario(scenario)


def test_consumer_executes_and_acks() -> None:
    async def scenario() -> None:
        client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
        try:
            queue = RedisRunQueue(client)
            runs = StubRunRepository(("run_1",))
            executor = RecordingExecutor(runs)
            consumer = _consumer(client, runs, executor)
            await consumer.ensure_group()
            await queue.enqueue_digest_run("run_1", user_id="u1", tenant_id="local")

            processed = await consumer.deliver_once()

            assert processed == 1
            assert executor.calls == ["run_1"]
            assert runs.runs["run_1"].status is RunStatus.RUNNING
            pending = await client.xpending(DEFAULT_STREAM, "test-group")
            assert pending["pending"] == 0
        finally:
            await client.aclose()

    _run_scenario(scenario)


def test_duplicate_delivery_is_acked_without_rerun() -> None:
    async def scenario() -> None:
        client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
        try:
            queue = RedisRunQueue(client)
            runs = StubRunRepository(("run_1",))
            executor = RecordingExecutor(runs)
            consumer = _consumer(client, runs, executor)
            await consumer.ensure_group()
            # Idempotent API retries enqueue the same run twice.
            await queue.enqueue_digest_run("run_1", user_id="u1", tenant_id="local")
            await queue.enqueue_digest_run("run_1", user_id="u1", tenant_id="local")

            await consumer.deliver_once()
            await consumer.deliver_once()

            # The CAS claim is the single-execution authority.
            assert executor.calls == ["run_1"]
            pending = await client.xpending(DEFAULT_STREAM, "test-group")
            assert pending["pending"] == 0
        finally:
            await client.aclose()

    _run_scenario(scenario)


def test_failed_execution_retries_then_dead_letters_metadata_only() -> None:
    async def scenario() -> None:
        client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
        try:
            queue = RedisRunQueue(client)
            runs = StubRunRepository(("run_1",))
            executor = RecordingExecutor(runs, fail_times={"run_1": 99})
            consumer = _consumer(client, runs, executor, max_retries=2)
            await consumer.ensure_group()
            await queue.enqueue_digest_run("run_1", user_id="u1", tenant_id="local")

            await consumer.deliver_once()
            for _ in range(4):
                await consumer.claim_stale()

            dlq = await consumer.dlq_entries()
            assert len(dlq) == 1
            entry = dlq[0]
            assert entry["run_id"] == "run_1"
            assert entry["error_code"] == "RETRY_EXHAUSTED"
            assert int(entry["attempts"]) >= 3
            dump = repr(entry)
            assert SECRET_BODY not in dump
            assert "token" not in dump.lower()

            # The run is terminal and the PEL is drained.
            assert runs.runs["run_1"].status is RunStatus.FAILED
            assert runs.runs["run_1"].error_code == "RETRY_EXHAUSTED"
            pending = await client.xpending(DEFAULT_STREAM, "test-group")
            assert pending["pending"] == 0
        finally:
            await client.aclose()

    _run_scenario(scenario)


def test_recovery_after_worker_crash_claims_stale_message() -> None:
    async def scenario() -> None:
        client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
        try:
            queue = RedisRunQueue(client)
            runs = StubRunRepository(("run_1",))
            crashed = _consumer(
                client,
                runs,
                RecordingExecutor(runs, fail_times={"run_1": 1}),
                consumer_name="worker-a",
            )
            await crashed.ensure_group()
            await queue.enqueue_digest_run("run_1", user_id="u1", tenant_id="local")
            await crashed.deliver_once()  # fails; message stays pending

            # A second process joins the same group after the crash.
            recovered_executor = RecordingExecutor(runs)
            recovered = _consumer(client, runs, recovered_executor, consumer_name="worker-b")
            await recovered.claim_stale()

            assert recovered_executor.calls == ["run_1"]
            pending = await client.xpending(DEFAULT_STREAM, "test-group")
            assert pending["pending"] == 0
            assert await consumer_dlq_empty(client)
        finally:
            await client.aclose()

    _run_scenario(scenario)


def test_ensure_group_is_idempotent() -> None:
    async def scenario() -> None:
        client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
        try:
            runs = StubRunRepository(())
            consumer = _consumer(client, runs, RecordingExecutor(runs))
            await consumer.ensure_group()
            await consumer.ensure_group()
        finally:
            await client.aclose()

    _run_scenario(scenario)


def test_consumer_drives_real_digest_worker_end_to_end() -> None:
    """Regression guard: the consumer must NOT claim before invoking the
    executor — DigestWorker.execute owns the CAS claim, and a consumer-side
    claim would leave every run stuck in RUNNING."""

    async def scenario() -> None:
        client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
        try:
            runs = InMemoryRunRepository()
            tasks = InMemoryTaskRepository()
            envelope = EphemeralEmailEnvelope(
                run_id="",
                tenant_id="",
                user_id="",
                gmail_message_id="m1",
                gmail_thread_id="t1",
                gmail_url="https://mail.google.com/mail/u/0/#inbox/m1",
                sender_name="Nguyễn An",
                sender_email="an@example.com",
                recipients=(),
                subject="Gửi báo cáo",
                received_at=NOW,
                labels=(),
                normalized_body="Nội dung thân email.",
                body_format=BodyFormat.TEXT,
                attachments_present=False,
                fetch_status=FetchStatus.COMPLETE,
            )
            canned_task = Task(
                task_id="task_m1",
                run_id="ignored",
                gmail_message_id="m1",
                gmail_url="https://mail.google.com/mail/u/0/#inbox/m1",
                source_message_ids=("m1",),
                incident_key=None,
                title="Gửi báo cáo",
                request_summary="Yêu cầu cần được xử lý.",
                actionability=Actionability.ACTION_REQUIRED,
                route=Route.DIRECT_PLAN,
                priority=None,
                deadline=None,
                action_plan=(PlanStep(1, "Gửi báo cáo", ()),),
                supporting_documents=(),
                missing_information=(),
                classifier_confidence=0.9,
                generation_confidence=0.9,
                validation_status=ValidationStatus.SYSTEM_GENERATED,
                created_at=NOW,
            )
            worker = DigestWorker(
                runs,
                InMemoryResultRepository(),
                FakeMailbox([envelope]),
                SafeTextAttachmentExtractor(),
                FakeRouteClassifier(),
                FakePlanGenerator((canned_task,)),
                ShortTermStore(),
                task_repository=tasks,
            )
            run = await CreateDigestRun(runs).execute(
                user_id="u1", mailbox_connection_id="mbx1", idempotency_key="k1"
            )
            consumer = _consumer(client, runs, worker)

            await consumer.ensure_group()
            await RedisRunQueue(client).enqueue_digest_run(
                run.id, user_id="u1", tenant_id="local"
            )
            await consumer.deliver_once()

            completed = await runs.get(run.id)
            assert completed is not None
            assert completed.status is RunStatus.SUCCEEDED
            assert completed.emails_processed == 1
            assert len(await tasks.list_for_run(run.id)) == 1
            pending = await client.xpending(DEFAULT_STREAM, "test-group")
            assert pending["pending"] == 0
        finally:
            await client.aclose()

    _run_scenario(scenario)


async def consumer_dlq_empty(client: AsyncRedis) -> bool:
    return await client.xlen(DEFAULT_DLQ_STREAM) == 0
