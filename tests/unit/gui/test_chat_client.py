"""Unit checks for the demo chat transport (SPEC-Demo-Frontend §3.4, §7.1).

The parser is the demo's only trust boundary against the backend stream, so the
tests pin fail-closed dropping, ordering, deduplication, and the rule that a
transport failure never surfaces an exception to the UI.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from cowork_agent.domain._chat_contracts_chat import ChatMessageStreamEvent
from cowork_agent.gui import chat_client

BASE_URL = "http://backend.test"


def sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def backend_payload(event: ChatMessageStreamEvent) -> dict[str, Any]:
    """Serialize exactly as `api/chat.py::_serialize_sse` does."""
    return dict(event.to_dict())


# --------------------------------------------------------------------------- #
# parse_stream_event
# --------------------------------------------------------------------------- #


def test_parse_accepts_every_backend_serialized_variant() -> None:
    variants = [
        ChatMessageStreamEvent.delta(event_id="e1", session_id="s1", turn_id="t1", text="hi"),
        ChatMessageStreamEvent.completed(event_id="e3", session_id="s1", turn_id="t1"),
        ChatMessageStreamEvent.error(
            event_id="e4",
            session_id="s1",
            turn_id="t1",
            code="stream_failed",
            safe_message="Something went wrong.",
        ),
    ]
    for variant in variants:
        parsed = chat_client.parse_stream_event(backend_payload(variant))
        assert parsed is not None, variant.event_type
        assert parsed.event_type == variant.event_type.value
        assert parsed.event_id == variant.event_id


def test_parse_accepts_memory_citation_and_keeps_source_id_opaque() -> None:
    parsed = chat_client.parse_stream_event(
        {
            "event_type": "memory_citation",
            "event_id": "e2",
            "session_id": "s1",
            "turn_id": "t1",
            "memory_type": "episodic",
            "source_id": "opaque-123",
        }
    )
    assert parsed is not None
    assert parsed.memory_type == "episodic"
    assert parsed.source_id == "opaque-123"
    assert parsed.text is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"event_type": "task_proposal", "event_id": "e", "session_id": "s", "turn_id": "t"},
            id="unknown-event-type",
        ),
        pytest.param(
            {"event_type": "delta", "event_id": "", "session_id": "s", "turn_id": "t", "text": "x"},
            id="empty-event-id",
        ),
        pytest.param(
            {"event_type": "delta", "event_id": "e", "session_id": "s", "turn_id": "t"},
            id="delta-without-text",
        ),
        pytest.param(
            {
                "event_type": "memory_citation",
                "event_id": "e",
                "session_id": "s",
                "turn_id": "t",
                "memory_type": "raw_email",
                "source_id": "x",
            },
            id="unknown-memory-type",
        ),
        pytest.param(
            {
                "event_type": "memory_citation",
                "event_id": "e",
                "session_id": "s",
                "turn_id": "t",
                "memory_type": "semantic",
            },
            id="citation-without-source",
        ),
        pytest.param(
            {"event_type": "error", "event_id": "e", "session_id": "s", "turn_id": "t"},
            id="error-without-code",
        ),
    ],
)
def test_parse_drops_malformed_or_unknown_events(payload: dict[str, Any]) -> None:
    assert chat_client.parse_stream_event(payload) is None


# --------------------------------------------------------------------------- #
# iter_sse_events
# --------------------------------------------------------------------------- #


def test_iter_sse_events_reads_data_only_and_preserves_order() -> None:
    raw = (
        ": keep-alive\n"
        "id: e1\n"
        "event: delta\n"
        'data: {"event_type":"delta","event_id":"e1","session_id":"s","turn_id":"t","text":"a"}\n'
        "\n"
        "id: e2\n"
        'data: {"event_type":"delta","event_id":"e2","session_id":"s","turn_id":"t","text":"b"}\n'
        "\n"
        'data: {"event_type":"completed","event_id":"e3","session_id":"s","turn_id":"t"}\n'
        "\n"
    )
    events = list(chat_client.iter_sse_events(raw.splitlines()))
    assert [event.event_id for event in events] == ["e1", "e2", "e3"]
    assert [event.text for event in events[:2]] == ["a", "b"]


def test_iter_sse_events_yields_trailing_frame_without_blank_line() -> None:
    line = 'data: {"event_type":"completed","event_id":"e9","session_id":"s","turn_id":"t"}'
    events = list(chat_client.iter_sse_events([line]))
    assert [event.event_type for event in events] == ["completed"]


def test_iter_sse_events_skips_unparsable_frames() -> None:
    lines = ["data: not-json", "", "data: [1, 2]", "", "data: ", ""]
    assert list(chat_client.iter_sse_events(lines)) == []


# --------------------------------------------------------------------------- #
# ChatTurnAccumulator
# --------------------------------------------------------------------------- #


def make_event(event_type: str, event_id: str, **extra: Any) -> chat_client.ChatStreamEvent:
    return chat_client.ChatStreamEvent(
        event_type=event_type,
        event_id=event_id,
        session_id="s",
        turn_id="t",
        **extra,
    )


def test_accumulator_appends_deltas_in_order_and_ignores_replays() -> None:
    accumulator = chat_client.ChatTurnAccumulator()
    assert accumulator.apply(make_event("delta", "e1", text="Hello ")) is True
    assert accumulator.apply(make_event("delta", "e2", text="world")) is True
    assert accumulator.apply(make_event("delta", "e1", text="Hello ")) is False
    assert accumulator.text == "Hello world"
    assert accumulator.is_terminal is False


def test_accumulator_deduplicates_identical_citations() -> None:
    accumulator = chat_client.ChatTurnAccumulator()
    accumulator.apply(make_event("memory_citation", "e1", memory_type="semantic", source_id="x"))
    accumulator.apply(make_event("memory_citation", "e2", memory_type="semantic", source_id="x"))
    accumulator.apply(make_event("memory_citation", "e3", memory_type="episodic", source_id="y"))
    assert accumulator.citations == [("semantic", "x"), ("episodic", "y")]


def test_accumulator_completed_and_error_are_terminal() -> None:
    completed = chat_client.ChatTurnAccumulator()
    completed.apply(make_event("completed", "e1"))
    assert completed.is_terminal is True

    failed = chat_client.ChatTurnAccumulator()
    failed.apply(make_event("error", "e1", code="stream_failed", safe_message="Retry soon."))
    assert failed.is_terminal is True
    assert failed.to_message() == {
        "role": "assistant",
        "text": "",
        "citations": [],
        "error_code": "stream_failed",
        "error_message": "Retry soon.",
    }


def test_to_message_copies_citations_so_later_events_do_not_mutate_history() -> None:
    accumulator = chat_client.ChatTurnAccumulator()
    accumulator.apply(make_event("memory_citation", "e1", memory_type="declarative", source_id="p"))
    message = accumulator.to_message()
    accumulator.apply(make_event("memory_citation", "e2", memory_type="episodic", source_id="q"))
    assert message["citations"] == [("declarative", "p")]


# --------------------------------------------------------------------------- #
# Badges and idempotency keys
# --------------------------------------------------------------------------- #


def test_memory_badge_html_escapes_label_and_pairs_icon_with_text() -> None:
    badge = chat_client.memory_badge_html("semantic", "Company <b>docs</b>")
    assert "📚" in badge
    assert "Company &lt;b&gt;docs&lt;/b&gt;" in badge
    assert "<b>" not in badge


def test_memory_badge_html_is_empty_for_unknown_kind() -> None:
    assert chat_client.memory_badge_html("raw_email", "Inbox") == ""


def test_badge_catalog_covers_exactly_the_known_memory_types() -> None:
    assert set(chat_client.MEMORY_BADGES) == set(chat_client.KNOWN_MEMORY_TYPES)


def test_new_idempotency_key_is_unique_per_call() -> None:
    keys = {chat_client.new_idempotency_key() for _ in range(50)}
    assert len(keys) == 50


# --------------------------------------------------------------------------- #
# HTTP transport
# --------------------------------------------------------------------------- #


def client_with(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_create_chat_session_returns_id_on_201() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(201, json={"session_id": "sess-1", "feature": "ai_chat"})

    with client_with(handler) as client:
        assert chat_client.create_chat_session(client, BASE_URL + "/") == (201, "sess-1")
    assert seen["url"] == f"{BASE_URL}{chat_client.CHAT_SESSIONS_PATH}"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        pytest.param(httpx.Response(503, text="down"), (503, None), id="server-error"),
        pytest.param(httpx.Response(201, text="not json"), (201, None), id="unparsable-body"),
        pytest.param(httpx.Response(201, json={"feature": "ai_chat"}), (201, None), id="no-id"),
        pytest.param(httpx.Response(201, json={"session_id": ""}), (201, None), id="empty-id"),
    ],
)
def test_create_chat_session_fails_closed(
    response: httpx.Response, expected: tuple[int, str | None]
) -> None:
    with client_with(lambda _request: response) as client:
        assert chat_client.create_chat_session(client, BASE_URL) == expected


def test_create_chat_session_reports_zero_when_backend_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with client_with(handler) as client:
        assert chat_client.create_chat_session(client, BASE_URL) == (0, None)


def stream_response(request: httpx.Request) -> httpx.Response:
    frames = [
        sse_frame(
            {
                "event_type": "delta",
                "event_id": "e1",
                "session_id": "s1",
                "turn_id": "t1",
                "text": "Hi",
            }
        ),
        sse_frame(
            {
                "event_type": "memory_citation",
                "event_id": "e2",
                "session_id": "s1",
                "turn_id": "t1",
                "memory_type": "declarative",
                "source_id": "profile-1",
            }
        ),
        sse_frame(
            {"event_type": "completed", "event_id": "e3", "session_id": "s1", "turn_id": "t1"}
        ),
    ]

    def content() -> Iterator[bytes]:
        for frame in frames:
            yield frame.encode()

    return httpx.Response(200, content=content(), headers={"content-type": "text/event-stream"})


def test_stream_chat_turn_posts_the_documented_body_and_yields_typed_events() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["accept"] = request.headers.get("accept")
        return stream_response(request)

    with client_with(handler) as client:
        events = list(
            chat_client.stream_chat_turn(
                client,
                BASE_URL,
                session_id="s1",
                user_message="hello",
                idempotency_key="key-1",
            )
        )

    assert seen["url"] == f"{BASE_URL}{chat_client.CHAT_SESSIONS_PATH}/s1/messages"
    assert seen["body"] == {
        "session_id": "s1",
        "user_message": "hello",
        "idempotency_key": "key-1",
    }
    assert seen["accept"] == "text/event-stream"
    assert [event.event_type for event in events] == ["delta", "memory_citation", "completed"]


def test_stream_chat_turn_maps_http_failure_to_a_safe_error_event() -> None:
    with client_with(lambda _request: httpx.Response(500, text="traceback here")) as client:
        events = list(
            chat_client.stream_chat_turn(
                client,
                BASE_URL,
                session_id="s1",
                user_message="hello",
                idempotency_key="key-1",
            )
        )

    assert len(events) == 1
    assert events[0].event_type == "error"
    assert events[0].code == "http_500"
    assert events[0].safe_message is None
    assert events[0].turn_id == "key-1"


def test_stream_chat_turn_maps_transport_failure_to_a_safe_error_event() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with client_with(handler) as client:
        events = list(
            chat_client.stream_chat_turn(
                client,
                BASE_URL,
                session_id="s1",
                user_message="hello",
                idempotency_key="key-1",
                timeout_seconds=1.0,
            )
        )

    assert [(event.event_type, event.code) for event in events] == [("error", "stream_unavailable")]
