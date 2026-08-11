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
