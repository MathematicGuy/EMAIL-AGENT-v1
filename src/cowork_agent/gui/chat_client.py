"""AI Chat transport and typed-SSE accumulation for the demo frontend.

Scope is fixed by SPEC-Demo-Frontend §3.4: create one server chat session for
the active browser session, submit idempotent messages, and render the typed
events ``delta``, ``memory_citation``, ``completed``, and ``error``.

Nothing here parses assistant prose, stores memory, or infers lifecycle state:
unknown event types are dropped fail-closed, ``source_id`` stays opaque, and no
value is written to durable storage. Everything below the Streamlit boundary is
pure and unit-tested in ``tests/unit/gui/test_chat_client.py``.
"""

from __future__ import annotations

import html
import json
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

CHAT_SESSIONS_PATH = "/v1/cowork/chat/sessions"

#: Typed SSE variants the demo is allowed to render (SPEC §7.1).
KNOWN_EVENT_TYPES = frozenset(
    {"delta", "memory_citation", "task_proposal", "completed", "error"}
)

#: Memory citation kinds the backend may cite (`MemoryCitationType`).
KNOWN_MEMORY_TYPES = frozenset({"declarative", "episodic", "semantic"})

#: Error codes the backend emits as a non-terminal advisory: the turn still
#: streams deltas afterwards, so the UI warns instead of failing (SPEC §8.1).
ADVISORY_ERROR_CODES = frozenset({"optional_memory_degraded"})

#: Icon + i18n key per memory kind; color is never the only signal (§6.2).
MEMORY_BADGES: dict[str, dict[str, str]] = {
    "declarative": {"icon": "👤", "css": "memory-declarative", "key": "chat_badge_declarative"},
    "episodic": {"icon": "🗂", "css": "memory-episodic", "key": "chat_badge_episodic"},
    "semantic": {"icon": "📚", "css": "memory-semantic", "key": "chat_badge_semantic"},
}

_DEFAULT_STREAM_TIMEOUT_SECONDS = 90.0


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    """One typed chat event accepted from the backend stream."""

    event_type: str
    event_id: str
    session_id: str
    turn_id: str
    text: str | None = None
    memory_type: str | None = None
    source_id: str | None = None
    code: str | None = None
    safe_message: str | None = None
    proposal: Mapping[str, Any] | None = None


def parse_stream_event(payload: Mapping[str, Any]) -> ChatStreamEvent | None:
    """Build a typed event from one SSE ``data`` object, or drop it fail-closed.

    An event is dropped when its type is unknown or when the payload the UI
    needs for that variant is missing, so a malformed frame can never be
    rendered as assistant content.
    """

    event_type = payload.get("event_type")
    if event_type not in KNOWN_EVENT_TYPES:
        return None
    identity = {key: payload.get(key) for key in ("event_id", "session_id", "turn_id")}
    if not all(isinstance(value, str) and value for value in identity.values()):
        return None

    text = payload.get("text")
    memory_type = payload.get("memory_type")
    source_id = payload.get("source_id")
    code = payload.get("code")
    safe_message = payload.get("safe_message")
    proposal = payload.get("proposal")

    if event_type == "delta" and not isinstance(text, str):
        return None
    if event_type == "memory_citation" and (
        memory_type not in KNOWN_MEMORY_TYPES or not isinstance(source_id, str) or not source_id
    ):
        return None
    if event_type == "task_proposal" and not _is_valid_proposal(proposal):
        return None
    if event_type == "error" and not (isinstance(code, str) and isinstance(safe_message, str)):
        return None

    return ChatStreamEvent(
        event_type=str(event_type),
        event_id=str(identity["event_id"]),
        session_id=str(identity["session_id"]),
        turn_id=str(identity["turn_id"]),
        text=text if isinstance(text, str) else None,
        memory_type=str(memory_type) if isinstance(memory_type, str) else None,
        source_id=source_id if isinstance(source_id, str) else None,
        code=code if isinstance(code, str) else None,
        safe_message=safe_message if isinstance(safe_message, str) else None,
        proposal=proposal if event_type == "task_proposal" else None,
    )


def _is_valid_proposal(value: object) -> bool:
    """The card needs the episode handle and a title; anything less is dropped."""
    if not isinstance(value, Mapping):
        return False
    episode_id = value.get("episode_id")
    task_title = value.get("task_title")
    return (
        isinstance(episode_id, str)
        and bool(episode_id)
        and isinstance(task_title, str)
        and bool(task_title)
    )


def iter_sse_events(lines: Iterable[str]) -> Iterator[ChatStreamEvent]:
    """Yield typed events from raw ``text/event-stream`` lines.

    Frames are separated by a blank line; only the ``data:`` field is trusted,
    because the JSON body already carries its own ``event_type`` discriminant.
    """

    data_parts: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if line == "":
            event = _event_from_data("\n".join(data_parts))
            data_parts = []
            if event is not None:
                yield event
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_parts.append(line[len("data:") :].lstrip())
    trailing = _event_from_data("\n".join(data_parts))
    if trailing is not None:
        yield trailing


def _event_from_data(data: str) -> ChatStreamEvent | None:
    if not data.strip():
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    return parse_stream_event(payload)


@dataclass(slots=True)
class ChatTurnAccumulator:
    """Ordered, deduplicated view of one assistant turn.

    Streamlit reruns replay events, and a reconnect can repeat a frame, so the
    accumulator is idempotent per ``event_id``.
    """

    text: str = ""
    citations: list[tuple[str, str]] = field(default_factory=list)
    completed: bool = False
    error_code: str | None = None
    error_message: str | None = None
    advisory_code: str | None = None
    advisory_message: str | None = None
    proposal: Mapping[str, Any] | None = None
    _seen_event_ids: set[str] = field(default_factory=set)

    def apply(self, event: ChatStreamEvent) -> bool:
        """Fold one event in; return ``False`` when it was a duplicate."""

        if event.event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event.event_id)
        if event.event_type == "delta":
            self.text += event.text or ""
        elif event.event_type == "memory_citation":
            citation = (str(event.memory_type), str(event.source_id))
            if citation not in self.citations:
                self.citations.append(citation)
        elif event.event_type == "task_proposal":
            if event.proposal is not None:
                self.proposal = event.proposal
        elif event.event_type == "completed":
            self.completed = True
        elif event.event_type == "error":
            if event.code in ADVISORY_ERROR_CODES:
                self.advisory_code = event.code
                self.advisory_message = event.safe_message
            else:
                self.error_code = event.code
                self.error_message = event.safe_message
        return True

    @property
    def is_terminal(self) -> bool:
        return self.completed or self.error_code is not None

    def to_message(self) -> dict[str, Any]:
        """Project the turn into the browser-session message shape."""

        return {
            "role": "assistant",
            "text": self.text,
            "citations": list(self.citations),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "advisory_code": self.advisory_code,
            "advisory_message": self.advisory_message,
            "proposal": self.proposal,
        }


def new_idempotency_key() -> str:
    """Return a fresh key; a retry must reuse the previous one instead (§6.7)."""

    return str(uuid.uuid4())


def memory_badge_html(memory_type: str, label: str) -> str:
    """Render one memory badge as icon + text, never color alone (§6.2, §6.12)."""

    badge = MEMORY_BADGES.get(memory_type)
    if badge is None:
        return ""
    return f'<span class="memory-badge {badge["css"]}">{badge["icon"]} {html.escape(label)}</span>'


def create_chat_session(client: httpx.Client, base_url: str) -> tuple[int, str | None]:
    """Create one server chat session; returns ``(status_code, session_id)``."""

    url = f"{base_url.rstrip('/')}{CHAT_SESSIONS_PATH}"
    try:
        response = client.post(url, json={})
    except httpx.HTTPError:
        return 0, None
    if response.status_code != 201:
        return response.status_code, None
    try:
        body = response.json()
    except ValueError:
        return response.status_code, None
    session_id = body.get("session_id") if isinstance(body, Mapping) else None
    if not isinstance(session_id, str) or not session_id:
        return response.status_code, None
    return response.status_code, session_id


def stream_chat_turn(
    client: httpx.Client,
    base_url: str,
    *,
    session_id: str,
    user_message: str,
    idempotency_key: str,
    timeout_seconds: float = _DEFAULT_STREAM_TIMEOUT_SECONDS,
) -> Iterator[ChatStreamEvent]:
    """Stream one turn, yielding typed events in arrival order.

    Transport failures are surfaced as a synthetic ``error`` event carrying a
    code but no ``safe_message``, so the caller renders its own localized copy
    and an exception never reaches the DOM.
    """

    url = f"{base_url.rstrip('/')}{CHAT_SESSIONS_PATH}/{session_id}/messages"
    payload = {
        "session_id": session_id,
        "user_message": user_message,
        "idempotency_key": idempotency_key,
    }
    try:
        with client.stream(
            "POST",
            url,
            json=payload,
            headers={"Accept": "text/event-stream"},
            timeout=timeout_seconds,
        ) as response:
            if response.status_code != 200:
                response.read()
                yield _transport_error(
                    session_id,
                    idempotency_key,
                    code=f"http_{response.status_code}",
                )
                return
            yield from iter_sse_events(response.iter_lines())
    except httpx.HTTPError:
        yield _transport_error(session_id, idempotency_key, code="stream_unavailable")


def _transport_error(session_id: str, idempotency_key: str, *, code: str) -> ChatStreamEvent:
    return ChatStreamEvent(
        event_type="error",
        event_id=f"client-{uuid.uuid4()}",
        session_id=session_id,
        turn_id=idempotency_key,
        code=code,
        safe_message=None,
    )


__all__ = [
    "ADVISORY_ERROR_CODES",
    "CHAT_SESSIONS_PATH",
    "KNOWN_EVENT_TYPES",
    "KNOWN_MEMORY_TYPES",
    "MEMORY_BADGES",
    "ChatStreamEvent",
    "ChatTurnAccumulator",
    "create_chat_session",
    "iter_sse_events",
    "memory_badge_html",
    "new_idempotency_key",
    "parse_stream_event",
    "stream_chat_turn",
]
