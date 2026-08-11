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


def test_event_rejects_negative_or_unbounded_counts_and_unsafe_reason() -> None:
    for field, value in (("result_count", -1), ("filtered_count", 10001), ("latency_ms", -1)):
        kwargs = dict(
            memory_type=MemoryType.LONG_TERM,
            operation=MemoryOperation.DELETE,
            outcome=MemoryOutcome.SUCCESS,
            result_count=0,
            filtered_count=0,
            latency_ms=0,
            reason_code="configured",
        )
        kwargs[field] = value
        try:
            MemoryOperationEvent(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{field} must be rejected")

    try:
        MemoryOperationEvent(
            memory_type=MemoryType.LONG_TERM,
            operation=MemoryOperation.READ,
            outcome=MemoryOutcome.DEGRADED,
            reason_code="unsafe reason text",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe reason_code must be rejected")


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
    import logging

    from cowork_agent.features.ai_chat.memory_observability import (
        LoggingMemoryOperationSink,
    )

    class RaisingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise RuntimeError("handler failure")

    handler = RaisingHandler()
    logger = logging.getLogger("test_memory_observability_raising")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    sink = LoggingMemoryOperationSink(logger=logger)

    event = MemoryOperationEvent(
        memory_type=MemoryType.SEMANTIC,
        operation=MemoryOperation.READ,
        outcome=MemoryOutcome.SUCCESS,
    )
    sink.emit(event)

    logger.removeHandler(handler)
