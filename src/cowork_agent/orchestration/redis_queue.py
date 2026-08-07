"""Redis Streams queue + DLQ for digest runs (V1-H T5.2).

The durable control plane pairs the queue with the PostgreSQL CAS claim:
the queue delivers ``run_id`` messages, and ``PostgresRunRepository.claim``
is the single source of truth for at-most-one execution, so duplicate or
redelivered messages are acked without re-running the pipeline.

Payloads are metadata-only by construction (invariant 1): a message carries
``run_id``, ``user_id``, ``tenant_id``, and ``enqueued_at`` — never email
bodies, attachment bytes, or OAuth tokens. The DLQ stream keeps the same
shape plus retry accounting.

Delivery semantics:
- producer ``XADD`` on the run stream (bounded via ``MAXLEN ~``);
- consumer group read; success acks, failure leaves the message pending;
- ``claim_stale`` reclaims messages idle past ``claim_min_idle_ms``;
  delivery counts come from ``XPENDING``;
- a message whose delivery count exceeds ``max_retries`` moves to the DLQ,
  is acked, and its run is marked failed with a safe error code.
"""

import asyncio
import logging
import socket
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from cowork_agent.domain import DigestCompletedEvent, DigestRun, RunStatus
from cowork_agent.features.email_action_plan.observability import (
    LifecycleEventPublisher,
)
from cowork_agent.features.email_action_plan.ports import (
    CompletionOutboxPort,
    RunRepository,
)
from cowork_agent.identity import LOCAL_TENANT_ID
from cowork_agent.orchestration.recovery import (
    DEFAULT_QUEUED_TIMEOUT,
    DEFAULT_RUNNING_TIMEOUT,
    sweep_stuck_runs,
)

logger = logging.getLogger(__name__)

DEFAULT_STREAM = "mail:digest-runs"
DEFAULT_DLQ_STREAM = "mail:digest-runs:dlq"
DEFAULT_GROUP = "digest-workers"
_STREAM_MAXLEN = 10_000


class RunExecutor(Protocol):
    async def execute(self, run_id: str) -> object: ...


class RedisRunQueue:
    """Producer side: enqueue run IDs for worker consumption."""

    def __init__(self, redis: Redis, *, stream: str = DEFAULT_STREAM) -> None:
        self._redis = redis
        self._stream = stream

    async def enqueue_digest_run(
        self, run_id: str, *, user_id: str, tenant_id: str
    ) -> None:
        await self._redis.xadd(
            self._stream,
            {
                "run_id": run_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "enqueued_at": datetime.now(UTC).isoformat(),
            },
            maxlen=_STREAM_MAXLEN,
            approximate=True,
        )


class RedisRunConsumer:
    """Consumer side: executor-driven execution with bounded retry and a DLQ.

    The executor owns the CAS claim; the consumer only manages delivery,
    retry accounting, and dead-lettering."""

    def __init__(
        self,
        redis: Redis,
        runs: RunRepository,
        executor: RunExecutor,
        *,
        stream: str = DEFAULT_STREAM,
        dlq_stream: str = DEFAULT_DLQ_STREAM,
        group: str = DEFAULT_GROUP,
        consumer_name: str | None = None,
        max_retries: int = 3,
        block_ms: int = 500,
        claim_min_idle_ms: int = 30_000,
        sweep_interval_seconds: float = 300.0,
        running_timeout: timedelta = DEFAULT_RUNNING_TIMEOUT,
        queued_timeout: timedelta = DEFAULT_QUEUED_TIMEOUT,
        completion_outbox: CompletionOutboxPort | None = None,
        publisher: LifecycleEventPublisher | None = None,
    ) -> None:
        self._redis = redis
        self._runs = runs
        self._executor = executor
        self._stream = stream
        self._dlq_stream = dlq_stream
        self._group = group
        self._consumer = consumer_name or f"{socket.gethostname()}-{uuid4().hex[:8]}"
        self._max_retries = max_retries
        self._block_ms = block_ms
        self._claim_min_idle_ms = claim_min_idle_ms
        self._sweep_interval_seconds = sweep_interval_seconds
        self._running_timeout = running_timeout
        self._queued_timeout = queued_timeout
        self._queue = RedisRunQueue(redis, stream=stream)
        self._completion_outbox = completion_outbox
        self._publisher = publisher

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                self._stream, self._group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def deliver_once(self) -> int:
        """One consumption cycle; returns messages processed or dead-lettered."""
        handled = await self.claim_stale()
        response = await self._redis.xreadgroup(
            self._group,
            self._consumer,
            {self._stream: ">"},
            count=1,
            block=self._block_ms,
        )
        for _stream, messages in response or []:
            for message_id, fields in messages:
                await self._handle(message_id, fields)
                handled += 1
        return handled

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        await self.ensure_group()
        next_sweep = time.monotonic() + self._sweep_interval_seconds
        while stop is None or not stop.is_set():
            if time.monotonic() >= next_sweep:
                try:
                    await self.sweep_stuck()
                except Exception:
                    # Maintenance must never kill the consumer; the next
                    # interval retries.
                    logger.exception("Stuck-run sweep failed; retrying next interval")
                if self._publisher is not None:
                    try:
                        await self._publisher.publish_pending()
                    except Exception:
                        # Independent of the sweep: durable completion
                        # events keep flowing even if Redis is degraded.
                        logger.exception(
                            "Lifecycle publication failed; retrying next interval"
                        )
                next_sweep = time.monotonic() + self._sweep_interval_seconds
            await self.deliver_once()
        logger.info("Run consumer %s stopped", self._consumer)

    async def sweep_stuck(self) -> int:
        """T5.4: recover crashed-RUNNING and orphaned-QUEUED runs."""
        return await sweep_stuck_runs(
            self._runs,
            now=datetime.now(UTC),
            requeue=self._reenqueue,
            running_timeout=self._running_timeout,
            queued_timeout=self._queued_timeout,
        )

    async def _reenqueue(self, run: DigestRun) -> None:
        # Idempotent by design: if the original message merely arrived
        # late, the CAS claim keeps execution single.
        await self._queue.enqueue_digest_run(
            run.id, user_id=run.user_id, tenant_id=LOCAL_TENANT_ID
        )

    async def claim_stale(self) -> int:
        """Reclaim messages idle past the threshold; dead-letter exhausted ones."""
        pending = await self._redis.xpending_range(
            self._stream, self._group, min="-", max="+", count=100
        )
        handled = 0
        for entry in pending:
            if entry["time_since_delivered"] < self._claim_min_idle_ms:
                continue
            message_id = entry["message_id"]
            # Delivery count read before XCLAIM == execution attempts so far;
            # the upcoming reclaim would make it attempts + 1.
            deliveries = int(entry["times_delivered"])
            claimed = await self._redis.xclaim(
                self._stream,
                self._group,
                self._consumer,
                min_idle_time=self._claim_min_idle_ms,
                message_ids=(message_id,),
            )
            for claimed_id, fields in claimed:
                if fields is None:
                    # Message vanished from the stream; clear its PEL entry.
                    await self._redis.xack(self._stream, self._group, claimed_id)
                    continue
                if deliveries > self._max_retries:
                    await self._dead_letter(claimed_id, fields, deliveries)
                else:
                    await self._handle(claimed_id, fields)
                handled += 1
        return handled

    async def _handle(self, message_id: str, fields: Mapping[str, str]) -> None:
        run_id = str(fields.get("run_id", ""))
        if not run_id:
            # Undecodable poison message: ack to keep it out of the PEL.
            logger.warning("Dropping queue message %s without run_id", message_id)
            await self._redis.xack(self._stream, self._group, message_id)
            return
        # No claim here: the executor's own CAS claim (DigestWorker.execute)
        # is the single execution authority. Duplicate or stale redeliveries
        # of a running/terminal run return normally and get acked below.
        try:
            await self._executor.execute(run_id)
        except Exception:
            # Controlled failure: reset to QUEUED so the redelivery can
            # claim again. (A hard process crash leaves RUNNING; stuck-run
            # recovery arrives with the T5.4 timeout budgets.)
            logger.exception("Run %s failed in worker; queued for retry", run_id)
            await self._requeue(run_id)
            return
        await self._redis.xack(self._stream, self._group, message_id)

    async def _requeue(self, run_id: str) -> None:
        run: DigestRun | None = await self._runs.get(run_id)
        if run is None or run.status is not RunStatus.RUNNING:
            return
        run.status = RunStatus.QUEUED
        run.started_at = None
        await self._runs.save(run)

    async def _dead_letter(
        self, message_id: str, fields: Mapping[str, str], deliveries: int
    ) -> None:
        run_id = str(fields.get("run_id", ""))
        await self._redis.xadd(
            self._dlq_stream,
            {
                "run_id": run_id,
                "user_id": str(fields.get("user_id", "")),
                "tenant_id": str(fields.get("tenant_id", "")),
                "attempts": str(deliveries),
                "error_code": "RETRY_EXHAUSTED",
                "failed_at": datetime.now(UTC).isoformat(),
            },
        )
        await self._redis.xack(self._stream, self._group, message_id)
        await self._mark_run_failed(run_id)
        logger.error(
            "Run %s moved to DLQ after %d deliveries (metadata-only payload)",
            run_id,
            deliveries,
        )

    async def _mark_run_failed(self, run_id: str) -> None:
        run: DigestRun | None = await self._runs.get(run_id)
        if run is None or run.status in {
            RunStatus.SUCCEEDED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
        }:
            return
        run.status = RunStatus.FAILED
        run.error_code = "RETRY_EXHAUSTED"
        run.error_message_safe = "Run processing exhausted its retry budget."
        run.completed_at = datetime.now(UTC)
        await self._runs.save(run)
        if self._completion_outbox is not None:
            try:
                await self._completion_outbox.add(
                    DigestCompletedEvent(
                        run_id=run.id,
                        user_id=run.user_id,
                        status=run.status,
                        occurred_at=run.completed_at,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Completion event for DLQ run %s could not be appended (%s)",
                    run.id,
                    type(exc).__name__,
                )

    async def dlq_entries(self) -> Sequence[Mapping[str, str]]:
        """Inspect the DLQ (operations + tests); entries are metadata-only."""
        response = await self._redis.xrange(self._dlq_stream)
        return tuple(dict(fields) for _message_id, fields in response)
