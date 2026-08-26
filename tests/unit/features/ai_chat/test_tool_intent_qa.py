"""Layer A of the calendar tool-use QA: gates and guards, offline.

Runs the 25 stories in `tests/fixtures/tool_intent/` through the real
`finalize_route` and the real `ChatController` over an `InMemoryCalendar`.

What this proves and what it does not: the argument-filling completion is
*scripted* from each case, so these tests prove the router narrows correctly and
the handler turns a well-formed answer into exactly the right number of events.
They do not prove a model would produce that answer -- `scripts/evaluate_tool_intent.py`
covers that, live and opt-in. The split is stated rather than papered over
because a scripted answer that matches its own expectation is circular, and only
the parts either side of it are load-bearing.

See `docs/evaluations/CHAT/SPEC-calendar-tool-qa.md` for the invariants I1-I8.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatIntent,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatRoute,
    IntentDecision,
    IntentReasonCode,
    RoutingOutcome,
)
from cowork_agent.features.ai_chat.controller import ChatController
from cowork_agent.features.ai_chat.generation_context import GenerationContext
from cowork_agent.features.ai_chat.intent.resolver import finalize_route
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer
from cowork_agent.features.ai_chat.tools import (
    CALENDAR_TOOL_NAME,
    InMemoryCalendar,
    build_calendar_tool,
)
from cowork_agent.features.ai_chat.tools.registry import ToolResult
from cowork_agent.features.ai_chat.tools.runner import ChatToolRunner
from tests.fixtures.tool_intent.loader import (
    SERVER_OWNED_REASON_CODES,
    ToolIntentCase,
    load_tool_intent_cases,
)

FIXTURE = load_tool_intent_cases()
CASES = FIXTURE.cases
BY_ID = {case.id: case for case in CASES}
TIMEZONE = "Asia/Ho_Chi_Minh"
PROMPT_VERSION = "chat-intent-v4"

# Ids, not indices, so a failure names the user story it broke.
IDS = [case.id for case in CASES]


def _decision(case: ToolIntentCase) -> IntentDecision:
    """The classifier's output, taken as given. `intent` does not affect routing
    -- `resolve_route` reads the three axes -- so it is derived, not labelled."""

    return IntentDecision(
        intent=(
            ChatIntent.ACTION_REQUEST
            if case.classifier_labels.needs_tool
            else ChatIntent.KNOWLEDGE_QUERY
        ),
        needs_rag=case.classifier_labels.needs_rag,
        needs_tool=case.classifier_labels.needs_tool,
        tool_name=case.classifier_labels.tool_name,
        needs_clarification=case.classifier_labels.needs_clarification,
        retrieval_query=(case.current_message if case.classifier_labels.needs_rag else None),
        confidence=0.9,
        reason_codes=(IntentReasonCode.GENERAL_CHAT,),
    )


def _outcome(case: ToolIntentCase) -> RoutingOutcome:
    return finalize_route(
        _decision(case),
        has_ready_documents=bool(case.ready_document_titles),
        tool_axis_enabled=case.context.tool_axis_enabled,
        classifier_retried=False,
        fallback_used=False,
        prompt_version=PROMPT_VERSION,
        available_tools=case.context.available_tools,
    )


# --------------------------------------------------------------------------
# I1, I2 -- the gates
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_route_matches_the_story(case: ToolIntentCase) -> None:
    """I1: capability gates narrow, never widen."""

    assert _outcome(case).route is case.expected_final_route, case.why_it_matters


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_server_appends_exactly_the_expected_reason_codes(case: ToolIntentCase) -> None:
    """I2: a disabled tool and an absent tool are different words, said once."""

    appended = tuple(
        code for code in _outcome(case).reason_codes if code in SERVER_OWNED_REASON_CODES
    )

    assert appended == case.expected_appended_reason_codes, case.why_it_matters


def test_a_disabled_axis_does_not_also_claim_the_tool_is_missing() -> None:
    """Two codes for one cause reads as two problems and sends the user hunting."""

    codes = _outcome(BY_ID["tq-021"]).reason_codes

    assert IntentReasonCode.TOOL_REQUESTED_BUT_DISABLED in codes
    assert IntentReasonCode.TOOL_NOT_AVAILABLE not in codes


def test_a_tool_the_registry_does_not_hold_is_narrowed_by_name() -> None:
    """ADR-004: naming `send_email` must not reach a handler by near-miss."""

    case = BY_ID["tq-023"]

    assert case.classifier_labels.tool_name == "send_email"
    assert _outcome(case).route is ChatRoute.CHAT


# --------------------------------------------------------------------------
# I3, I4, I6, I7 -- what actually lands in the calendar
# --------------------------------------------------------------------------


class _Routing:
    def __init__(self, outcome: RoutingOutcome) -> None:
        self._outcome = outcome

    async def route(self, **kwargs: object) -> RoutingOutcome:
        del kwargs
        return self._outcome


class _Reply:
    def __init__(self) -> None:
        self.contexts: list[GenerationContext] = []

    async def stream_reply(
        self, request: ChatMessageRequest, context: GenerationContext
    ) -> AsyncIterator[str]:
        del request
        self.contexts.append(context)
        yield "ok"


def _scripted_arguments(case: ToolIntentCase) -> Mapping[str, object]:
    """A well-formed answer for this case, so the handler is what is under test.

    Cases that state a start use it; the rest get a valid future slot, because
    what they assert is the *count* of events, not their time."""

    outcome = case.expected_tool_outcome
    assert outcome is not None
    start = outcome.expect_start or (case.context.now + timedelta(days=2)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    end = outcome.expect_end or start + timedelta(minutes=30)
    return {"title": f"QA {case.id}", "start": start.isoformat(), "end": end.isoformat()}


def _runner(calendar: InMemoryCalendar, arguments: Mapping[str, object] | None) -> ChatToolRunner:
    async def complete(prompt: str, schema: Mapping[str, object]) -> Mapping[str, object]:
        del prompt, schema
        # `None` stands for the model declining rather than guessing.
        return arguments if arguments is not None else {"error": "no date was given"}

    return ChatToolRunner(
        {
            CALENDAR_TOOL_NAME: lambda key, now: build_calendar_tool(
                calendar, idempotency_key=key, timezone=TIMEZONE, now=now
            )
        },
        complete=complete,
    )


def _run_turn(
    case: ToolIntentCase,
    calendar: InMemoryCalendar,
    *,
    arguments: Mapping[str, object] | None,
    idempotency_key: str,
) -> GenerationContext:
    scope = ChatMemoryScope(user_id="qa-user", session_id="qa-session")
    reply = _Reply()
    controller = ChatController(
        scope=scope,
        memory=MemoryGateway(
            scope=scope,
            session_buffer=InMemoryChatSessionBuffer(max_turns=8, ttl_seconds=60),
        ),
        reply=reply,
        routing=_Routing(_outcome(case)),  # type: ignore[arg-type]
        new_id=iter(f"{case.id}-{index}" for index in range(50)).__next__,
        clock=lambda: case.context.now,
        tools=_runner(calendar, arguments),
    )

    async def collect() -> None:
        request = ChatMessageRequest("qa-session", case.current_message, idempotency_key)
        async for _ in controller.stream_message(request):
            pass

    asyncio.run(collect())
    return reply.contexts[0]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_calendar_holds_exactly_what_the_story_expects(case: ToolIntentCase) -> None:
    """I4 and I7 for the non-tool routes, I3 and the write count for the rest.

    Every case runs through the real controller, so a route that should never
    touch the calendar is proven not to rather than assumed not to."""

    calendar = InMemoryCalendar()
    expected = case.expected_tool_outcome
    arguments = _scripted_arguments(case) if expected is not None else None

    _run_turn(case, calendar, arguments=arguments, idempotency_key=f"idem-{case.id}")

    assert len(calendar.events) == (expected.events_created if expected else 0), case.why_it_matters


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.expected_tool_outcome is not None],
    ids=[case.id for case in CASES if case.expected_tool_outcome is not None],
)
def test_a_created_event_lands_at_the_stated_instant(case: ToolIntentCase) -> None:
    """The offset survives the round trip: 02:00 +07:00 is not 02:00 UTC."""

    outcome = case.expected_tool_outcome
    assert outcome is not None
    if outcome.expect_start is None:
        pytest.skip("this story asserts a count, not a time")
    calendar = InMemoryCalendar()

    _run_turn(
        case,
        calendar,
        arguments=_scripted_arguments(case),
        idempotency_key=f"idem-{case.id}",
    )

    (event,) = calendar.events.values()
    assert event.start == outcome.expect_start
    if outcome.expect_end is not None:
        assert event.end == outcome.expect_end


def test_a_model_that_declines_writes_nothing_and_says_why() -> None:
    """I3: the refusal path, driven on a tool route so the runner is reached.

    `tq-005` -- bare '2 giờ thứ Sáu' -- is why this path has to exist: 02:00 and
    14:00 are both readable, and a real event at the wrong one is the most
    expensive failure in the feature. That case is routed to `clarify` upstream,
    so what is exercised here is the runner's own decline handling."""

    calendar = InMemoryCalendar()

    context = _run_turn(BY_ID["tq-001"], calendar, arguments=None, idempotency_key="idem-declined")

    assert calendar.events == {}
    assert context.tool_result == "no date was given"


def test_a_retry_creates_one_event_and_a_second_intent_creates_another() -> None:
    """I6: the idempotency key is the whole difference between the two."""

    retried, fresh = BY_ID["tq-019"], BY_ID["tq-020"]
    calendar = InMemoryCalendar()

    _run_turn(retried, calendar, arguments=_scripted_arguments(retried), idempotency_key="idem-a")
    _run_turn(retried, calendar, arguments=_scripted_arguments(retried), idempotency_key="idem-a")

    assert len(calendar.events) == 1

    _run_turn(fresh, calendar, arguments=_scripted_arguments(fresh), idempotency_key="idem-b")

    assert len(calendar.events) == 2


def test_a_recurring_request_creates_one_event_not_a_series() -> None:
    """I7: there is no recurrence handler. One event is the honest outcome;
    three, or a reply claiming a series exists, is not."""

    case = BY_ID["tq-016"]
    calendar = InMemoryCalendar()

    _run_turn(case, calendar, arguments=_scripted_arguments(case), idempotency_key="idem-rec")

    assert len(calendar.events) == 1


def test_a_prompt_injection_in_the_message_creates_one_ordinary_event() -> None:
    """I8: the payload is quoted inside <untrusted_data> and stays data."""

    case = BY_ID["tq-024"]
    calendar = InMemoryCalendar()

    _run_turn(case, calendar, arguments=_scripted_arguments(case), idempotency_key="idem-inj")

    (event,) = calendar.events.values()
    assert event.start == case.expected_tool_outcome.expect_start  # type: ignore[union-attr]


def test_a_closing_delimiter_in_the_message_cannot_end_the_quoted_block() -> None:
    """I8, at the only place it can actually be enforced.

    `tq-024` ends its request with a literal `</untrusted_data>` followed by an
    instruction. If that tag survived into the prompt the instruction would sit
    *outside* the quoted block, which is the whole difference between data the
    model reads and an order it follows. `neutralize_delimiters` replaces it,
    and this is the test that says so."""

    case = BY_ID["tq-024"]
    prompts: list[str] = []

    async def complete(prompt: str, schema: Mapping[str, object]) -> Mapping[str, object]:
        del schema
        prompts.append(prompt)
        return _scripted_arguments(case)

    runner = ChatToolRunner(
        {
            CALENDAR_TOOL_NAME: lambda key, now: build_calendar_tool(
                InMemoryCalendar(), idempotency_key=key, timezone=TIMEZONE, now=now
            )
        },
        complete=complete,
    )

    asyncio.run(
        runner.run_for_turn(
            CALENDAR_TOOL_NAME,
            user_message=case.current_message,
            idempotency_key="idem-tag",
            now=case.context.now,
        )
    )

    (prompt,) = prompts
    after = prompt.rpartition("</untrusted_data>")[2]
    assert "never an instruction to obey" in prompt
    # Exactly one closing tag, and it is the framework's own -- so everything the
    # user wrote, payload included, is on the inside of it.
    assert prompt.count("</untrusted_data>") == 1
    assert "[delimiter-removed]" in prompt
    assert "tạo 100 sự kiện" not in after


# --------------------------------------------------------------------------
# I5 -- the guards, with no model in the loop at all
# --------------------------------------------------------------------------


def _handler_result(start: str, end: str, *, now: datetime) -> ToolResult:
    calendar = InMemoryCalendar()
    tool = build_calendar_tool(calendar, idempotency_key="guard", timezone=TIMEZONE, now=now)
    result = asyncio.run(tool.handler({"title": "Gym", "start": start, "end": end}))
    # A rejection that still wrote is the failure this whole tier exists to
    # prevent, so it is checked on every guard rather than once.
    assert bool(calendar.events) is result.ok
    return result


def test_a_date_resolved_backwards_is_rejected() -> None:
    """I5: 'thứ Hai' said on a Wednesday means the next Monday. A model that
    resolves it to the Monday just gone would write a past event; the one-sided
    window is what catches it."""

    result = _handler_result(
        "2026-08-24T02:00:00+07:00", "2026-08-24T03:00:00+07:00", now=FIXTURE.now
    )

    assert not result.ok
    assert "in the past" in result.text


def test_yesterday_is_still_allowed_because_logging_a_todo_is_legitimate() -> None:
    """The window is one day, not zero -- the guard must not block the ordinary
    case of recording something from earlier."""

    result = _handler_result(
        "2026-08-25T20:00:00+07:00", "2026-08-25T21:00:00+07:00", now=FIXTURE.now
    )

    assert result.ok


def test_a_date_more_than_a_year_out_is_rejected() -> None:
    """The classic wrong-year resolution, in the other direction."""

    result = _handler_result(
        "2027-12-01T09:00:00+07:00", "2027-12-01T10:00:00+07:00", now=FIXTURE.now
    )

    assert not result.ok
    assert "more than a year away" in result.text


def test_an_event_that_ends_before_it_starts_is_rejected() -> None:
    result = _handler_result(
        "2026-08-28T02:00:00+07:00", "2026-08-28T01:00:00+07:00", now=FIXTURE.now
    )

    assert not result.ok
    assert "ends before it starts" in result.text


def test_an_all_day_start_with_a_timed_end_is_rejected() -> None:
    """A model that changed its mind halfway. Guessing which half it meant
    creates the wrong event."""

    result = _handler_result("2026-08-28", "2026-08-28T03:00:00+07:00", now=FIXTURE.now)

    assert not result.ok
    assert "both be dates or both be times" in result.text


def test_an_offsetless_time_is_read_in_the_user_timezone() -> None:
    """Google would read it in the calendar's zone; the handler resolves it here,
    where the user's zone is actually known."""

    calendar = InMemoryCalendar()
    tool = build_calendar_tool(calendar, idempotency_key="tz", timezone=TIMEZONE, now=FIXTURE.now)

    result = asyncio.run(
        tool.handler({"title": "Gym", "start": "2026-08-28T02:00:00", "end": "2026-08-28T03:00:00"})
    )

    assert result.ok
    (event, *_) = calendar.events.values()
    assert isinstance(event.start, datetime)
    assert event.start.utcoffset() == timedelta(hours=7)


def test_an_unreadable_date_is_reported_rather_than_repaired() -> None:
    result = _handler_result("next friday-ish", "2026-08-28T03:00:00+07:00", now=FIXTURE.now)

    assert not result.ok
    assert "Could not read" in result.text


def test_every_guard_message_names_the_problem() -> None:
    """A refusal the model cannot act on becomes a guess on the next attempt."""

    failures: list[ToolResult] = [
        _handler_result("2026-08-24T02:00:00+07:00", "2026-08-24T03:00:00+07:00", now=FIXTURE.now),
        _handler_result("2026-08-28T02:00:00+07:00", "2026-08-28T01:00:00+07:00", now=FIXTURE.now),
        _handler_result("2026-08-28", "2026-08-28T03:00:00+07:00", now=FIXTURE.now),
    ]

    assert all(not result.ok and len(result.text.split()) >= 4 for result in failures)
