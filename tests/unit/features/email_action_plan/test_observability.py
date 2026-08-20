"""T4.4 telemetry + T4.5 development trace + T5.5 lifecycle publication tests."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from cowork_agent.domain import DigestCompletedEvent, RunStatus
from cowork_agent.domain.target_contracts import TraceEvent, TraceLatency, TraceStatus
from cowork_agent.features.email_action_plan.observability import (
    DEV_TRACE_MARKER,
    EncryptedDevTraceSink,
    InMemoryTraceSink,
    LifecycleEventPublisher,
    ProductionTraceForbiddenError,
    dev_trace_sink_from_env,
    is_production_env,
)
from cowork_agent.orchestration.local import InMemoryOutbox

pytestmark = pytest.mark.extended

KEY = Fernet.generate_key().decode()

PROD_ENV = {"APP_ENV": "production"}
DEV_ENV = {"APP_ENV": "development"}


def test_is_production_env_recognizes_production_values() -> None:
    assert is_production_env({"APP_ENV": "production"})
    assert is_production_env({"APP_ENV": "Prod"})
    assert not is_production_env({"APP_ENV": "development"})
    assert not is_production_env({})


def test_dev_trace_sink_refuses_construction_in_production(tmp_path: Path) -> None:
    with pytest.raises(ProductionTraceForbiddenError):
        EncryptedDevTraceSink(
            tmp_path / "trace.enc", KEY, enabled=True, ttl_seconds=60, environ=PROD_ENV
        )


def test_dev_trace_write_read_round_trip_is_encrypted_markered_ttls(tmp_path: Path) -> None:
    path = tmp_path / "trace.enc"
    sink = EncryptedDevTraceSink(path, KEY, enabled=True, ttl_seconds=3600, environ=DEV_ENV)

    sink.write(run_id="run-1", kind="classifier_input", payload={"body": "Nội dung mật"})

    raw = path.read_text(encoding="utf-8")
    assert "Nội dung mật" not in raw  # encrypted at rest
    records = sink.read()
    assert len(records) == 1
    assert records[0]["marker"] == DEV_TRACE_MARKER
    assert records[0]["run_id"] == "run-1"
    assert records[0]["payload"] == {"body": "Nội dung mật"}


def test_dev_trace_expired_records_are_not_returned(tmp_path: Path) -> None:
    sink = EncryptedDevTraceSink(
        tmp_path / "trace.enc", KEY, enabled=True, ttl_seconds=-1, environ=DEV_ENV
    )
    sink.write(run_id="run-1", kind="classifier_input", payload={"x": 1})
    assert sink.read() == []


def test_dev_trace_disabled_flag_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "trace.enc"
    sink = EncryptedDevTraceSink(path, KEY, enabled=False, ttl_seconds=60, environ=DEV_ENV)
    sink.write(run_id="run-1", kind="classifier_input", payload={"x": 1})
    assert not path.exists()


def test_dev_trace_sink_from_env_honors_flag_and_production_guard(tmp_path: Path) -> None:
    assert dev_trace_sink_from_env(tmp_path, KEY, environ={}) is None
    assert (
        dev_trace_sink_from_env(tmp_path, KEY, environ=PROD_ENV | {"DEV_TRACE_ENABLED": "true"})
        is None
    )
    enabled = dev_trace_sink_from_env(
        tmp_path, KEY, environ=DEV_ENV | {"DEV_TRACE_ENABLED": "true"}
    )
    assert enabled is not None and enabled.enabled


def test_in_memory_trace_sink_records_json_safe_events() -> None:
    sink = InMemoryTraceSink()
    event = TraceEvent(
        run_id="run-1",
        user_id="u1",
        gmail_message_id=None,
        event_name="digest_run",
        status=TraceStatus.SUCCESS,
        route=None,
        reason_codes=(),
        classifier_confidence=None,
        rag_result_count=None,
        retrieval_status=None,
        generation_status=None,
        validation_status=None,
        latency_ms=TraceLatency(),
    )
    sink.record(event)
    assert sink.events == [event]
    json.dumps(event.to_dict())


def test_lifecycle_publisher_relays_and_marks_published() -> None:
    async def scenario() -> None:
        outbox = InMemoryOutbox()
        sink = InMemoryTraceSink()
        now = datetime(2026, 8, 8, 10, tzinfo=UTC)
        await outbox.add(
            DigestCompletedEvent(
                run_id="run-1", user_id="u1", status=RunStatus.SUCCEEDED, occurred_at=now
            )
        )
        await outbox.add(
            DigestCompletedEvent(
                run_id="run-2", user_id="u1", status=RunStatus.PARTIAL, occurred_at=now
            )
        )
        await outbox.add(
            DigestCompletedEvent(
                run_id="run-3", user_id="u1", status=RunStatus.FAILED, occurred_at=now
            )
        )
        publisher = LifecycleEventPublisher(outbox, sink)

        published = await publisher.publish_pending()

        assert published == 3
        assert [(e.run_id, e.status) for e in sink.events] == [
            ("run-1", TraceStatus.SUCCESS),
            ("run-2", TraceStatus.PARTIAL),
            ("run-3", TraceStatus.FAILED),
        ]
        assert all(e.event_name == LifecycleEventPublisher.EVENT_NAME for e in sink.events)
        assert await outbox.pending() == ()
        # Drained: a second pass relays nothing.
        assert await publisher.publish_pending() == 0

    asyncio.run(scenario())


def test_lifecycle_publisher_without_sink_still_drains_outbox() -> None:
    async def scenario() -> None:
        outbox = InMemoryOutbox()
        await outbox.add(
            DigestCompletedEvent(
                run_id="run-1",
                user_id="u1",
                status=RunStatus.SUCCEEDED,
                occurred_at=datetime(2026, 8, 8, 10, tzinfo=UTC),
            )
        )
        publisher = LifecycleEventPublisher(outbox, None)

        assert await publisher.publish_pending() == 1
        assert await outbox.pending() == ()

    asyncio.run(scenario())
