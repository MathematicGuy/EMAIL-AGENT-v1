import pytest

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_observability import (
    MemoryOperation,
    MemoryOperationEvent,
    MemoryOutcome,
    RecordingMemoryOperationSink,
)


def test_metadata_only_event_never_exposes_sensitive_sentinels_and_bounds_counts() -> None:
    event = MemoryOperationEvent(
        memory_type=MemoryType.SEMANTIC,
        operation=MemoryOperation.READ,
        outcome=MemoryOutcome.SUCCESS,
        result_count=7,
        filtered_count=2,
        latency_ms=3,
        reason_code=None,
    )
    sink = RecordingMemoryOperationSink()
    sink.emit(event)

    rendered = f"{event!r}{event.to_dict()}{tuple(event.__dataclass_fields__)}"
    for sentinel in (
        "raw-email-body",
        "user@example.com",
        "session-1",
        "https://private.example",
        "exception message",
    ):
        assert sentinel not in rendered
    assert sink.events == (event,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("result_count", -1),
        ("filtered_count", 10001),
        ("latency_ms", -1),
    ],
    ids=["negative_results", "unbounded_filtered", "negative_latency"],
)
def test_event_rejects_counts_outside_the_bounded_nonnegative_range(field: str, value: int) -> None:
    kwargs: dict[str, object] = {
        "memory_type": MemoryType.LONG_TERM,
        "operation": MemoryOperation.DELETE,
        "outcome": MemoryOutcome.SUCCESS,
        "result_count": 0,
        "filtered_count": 0,
        "latency_ms": 0,
        "reason_code": "configured",
        field: value,
    }

    with pytest.raises(ValueError, match=f"{field} must be a bounded nonnegative integer"):
        MemoryOperationEvent(**kwargs)  # type: ignore[arg-type]


def test_event_rejects_a_reason_code_that_is_not_a_short_safe_identifier() -> None:
    with pytest.raises(ValueError, match="reason_code must be a short safe identifier"):
        MemoryOperationEvent(
            memory_type=MemoryType.LONG_TERM,
            operation=MemoryOperation.READ,
            outcome=MemoryOutcome.DEGRADED,
            reason_code="unsafe reason text",
        )


def test_logging_sink_logs_only_metadata_and_never_raises() -> None:
    import logging

    from cowork_agent.features.ai_chat.memory_observability import (
        LoggingMemoryOperationSink,
        MemoryOperationMetrics,
    )

    captured: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = CapturingHandler()
    logger = logging.getLogger("test_memory_observability_metadata")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    metrics = MemoryOperationMetrics()
    sink = LoggingMemoryOperationSink(logger=logger, metrics=metrics)

    event = MemoryOperationEvent(
        memory_type=MemoryType.SEMANTIC,
        operation=MemoryOperation.READ,
        outcome=MemoryOutcome.SUCCESS,
        result_count=5,
        filtered_count=1,
        latency_ms=12,
        reason_code=None,
    )
    sink.emit(event)

    assert len(captured) == 1
    record = captured[0]
    assert record.levelno == logging.INFO
    assert "memory_operation" in record.getMessage()
    rendered = record.getMessage()
    for sentinel in ("user@example.com", "session-1", "https://", "exception"):
        assert sentinel not in rendered

    event_dict = event.to_dict()
    assert set(event_dict.keys()) == {
        "feature",
        "memory_type",
        "operation",
        "outcome",
        "result_count",
        "filtered_count",
        "latency_ms",
        "reason_code",
    }
    assert len(event_dict) == 8

    logger.removeHandler(handler)


def test_logging_sink_denied_logs_error_and_increments_safety_incidents() -> None:
    import logging

    from cowork_agent.features.ai_chat.memory_observability import (
        LoggingMemoryOperationSink,
        MemoryOperationMetrics,
    )

    captured: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = CapturingHandler()
    logger = logging.getLogger("test_memory_observability_denied")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    metrics = MemoryOperationMetrics()
    sink = LoggingMemoryOperationSink(logger=logger, metrics=metrics)

    denied_event = MemoryOperationEvent(
        memory_type=MemoryType.LONG_TERM,
        operation=MemoryOperation.WRITE,
        outcome=MemoryOutcome.DENIED,
        reason_code="policy_violation",
    )
    sink.emit(denied_event)

    success_event = MemoryOperationEvent(
        memory_type=MemoryType.EPISODIC,
        operation=MemoryOperation.READ,
        outcome=MemoryOutcome.SUCCESS,
    )
    sink.emit(success_event)

    assert len(captured) == 2
    assert captured[0].levelno == logging.ERROR
    assert "memory_safety_incident" in captured[0].getMessage()
    assert captured[1].levelno == logging.INFO
    assert "memory_operation" in captured[1].getMessage()

    incidents = metrics.safety_incidents()
    assert incidents == {"policy_violation": 1}

    logger.removeHandler(handler)


def test_metrics_snapshot_counts_and_latency_summary() -> None:
    from cowork_agent.features.ai_chat.memory_observability import (
        MemoryOperationMetrics,
    )

    metrics = MemoryOperationMetrics()

    event1 = MemoryOperationEvent(
        memory_type=MemoryType.SEMANTIC,
        operation=MemoryOperation.READ,
        outcome=MemoryOutcome.SUCCESS,
        latency_ms=10,
    )
    event2 = MemoryOperationEvent(
        memory_type=MemoryType.SEMANTIC,
        operation=MemoryOperation.READ,
        outcome=MemoryOutcome.SUCCESS,
        latency_ms=20,
    )
    event3 = MemoryOperationEvent(
        memory_type=MemoryType.LONG_TERM,
        operation=MemoryOperation.WRITE,
        outcome=MemoryOutcome.DEGRADED,
        latency_ms=0,
    )

    metrics.record(event1)
    metrics.record(event2)
    metrics.record(event3)

    snapshot = metrics.snapshot()
    assert snapshot[("semantic", "read", "success")] == 2
    assert snapshot[("long_term", "write", "degraded")] == 1

    latency = metrics.latency_summary()
    assert latency[("semantic", "read", "success")] == (30, 2)
    assert latency[("long_term", "write", "degraded")] == (0, 0)


def test_sink_never_raises_even_when_logger_fails() -> None:
    """A broken log handler must not take a memory operation down with it.

    The old version of this test proved less than it looked like: it asserted
    nothing, so it would have passed just as happily if the sink had stopped
    logging altogether and never touched the failing handler at all. It also
    restored the handler only on the success path, so the one failure it
    guards against would have leaked a raising handler into every later test
    on the same xdist worker.
    """
    import logging

    from cowork_agent.features.ai_chat.memory_observability import (
        LoggingMemoryOperationSink,
    )

    emit_attempts: list[logging.LogRecord] = []

    class RaisingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            emit_attempts.append(record)
            raise RuntimeError("handler failure")

        def handleError(self, record: logging.LogRecord) -> None:
            # Default behaviour prints "--- Logging error ---" plus a traceback
            # to stderr. Swallow it: the raise is the fixture here, not a
            # surprise, and the noise buries real failures in a 1000-test run.
            return

    handler = RaisingHandler()
    logger = logging.getLogger("test_memory_observability_raising")
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    try:
        sink = LoggingMemoryOperationSink(logger=logger)
        event = MemoryOperationEvent(
            memory_type=MemoryType.SEMANTIC,
            operation=MemoryOperation.READ,
            outcome=MemoryOutcome.SUCCESS,
        )

        sink.emit(event)  # must not propagate RuntimeError("handler failure")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    # The handler really was reached, so the swallowed failure is the one under
    # test rather than an emit that silently never happened.
    assert len(emit_attempts) == 1
    assert emit_attempts[0].levelno == logging.INFO
