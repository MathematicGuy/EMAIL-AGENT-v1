"""Frozen legacy result shape, asserted against the mapper instead of the API.

Replaces the retired ``tests/compatibility`` suite. That suite booted a FastAPI
app and drove an ASGI client to reach ``legacy_result_shape`` -- 627 lines and
seconds of lifespan startup to test a stateless pure function. The invariants
below are the ones nothing else covered: the exact key set, the nextActions
slice, the empty-state message, and the sort order.
"""

from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain import (
    ActionFreshness,
    DigestRun,
    Priority,
    RunStatus,
    RunTrigger,
)
from cowork_agent.domain.target_contracts import (
    Actionability,
    PlanStep,
    Route,
    Task,
    ValidationStatus,
)
from cowork_agent.features.email_action_plan.compat_mapper import (
    action_item_from_task,
    action_item_sort_key,
    legacy_result_shape,
)
from cowork_agent.features.email_action_plan.ports import PersistedTask, TaskPointer

CLOCK = datetime(2026, 8, 3, 8, tzinfo=UTC)
EMPTY_STATE_MESSAGE = "Không có công việc cần xử lý"

#: Every key the legacy client reads. Adding one is a breaking change for the
#: frontend, so this set is asserted exactly rather than with `>=`.
FROZEN_RESULT_KEYS = {
    "run",
    "actionItems",
    "nextActions",
    "attachmentWarnings",
    "processedEmails",
    "message",
}


def _run() -> DigestRun:
    return DigestRun(
        id="run-compat",
        user_id="owner@example.com",
        mailbox_connection_id="mbx-compat",
        trigger=RunTrigger.ON_DEMAND,
        status=RunStatus.SUCCEEDED,
        query="is:unread in:inbox",
        idempotency_key="compat-1",
        max_emails=20,
    )


def _persisted(
    message_id: str,
    *,
    priority: Priority | None = Priority.MEDIUM,
    deadline: datetime | None = None,
    generation_confidence: float | None = 0.9,
) -> PersistedTask:
    task = Task(
        task_id=f"task_{message_id}",
        run_id="run-compat",
        gmail_message_id=message_id,
        gmail_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        source_message_ids=(message_id,),
        incident_key=None,
        title=f"Việc {message_id}",
        request_summary="Yêu cầu cần xử lý.",
        actionability=Actionability.ACTION_REQUIRED,
        route=Route.DIRECT_PLAN,
        priority=priority,
        deadline=deadline,
        action_plan=(PlanStep(1, f"Xử lý {message_id}", ()),),
        supporting_documents=(),
        missing_information=(),
        classifier_confidence=0.9,
        generation_confidence=generation_confidence,
        validation_status=ValidationStatus.SYSTEM_GENERATED,
        created_at=CLOCK,
    )
    pointer = TaskPointer(
        mailbox_connection_id="mbx-compat",
        provider_thread_id=f"thread-{message_id}",
        sender_name="Người Gửi",
        sender_address=f"{message_id}@example.com",
        email_subject=f"Chủ đề {message_id}",
        email_received_at=CLOCK,
    )
    return PersistedTask(task, pointer, f"fp-{message_id}", ActionFreshness.NEW)


def _shape(*persisted: PersistedTask) -> dict[str, object]:
    return legacy_result_shape(
        run=_run(),
        persisted=persisted,
        warnings=(),
        processed_emails=(),
        clock=CLOCK,
    )


def test_result_keys_are_exactly_the_frozen_set() -> None:
    assert set(_shape(_persisted("m1"))) == FROZEN_RESULT_KEYS


def test_next_actions_are_the_first_three_action_items() -> None:
    payload = _shape(*(_persisted(f"m{index}") for index in range(5)))
    items = payload["actionItems"]
    assert len(items) == 5
    assert payload["nextActions"] == items[:3]


def test_empty_run_carries_the_explicit_empty_state_message() -> None:
    payload = _shape()
    assert payload["message"] == EMPTY_STATE_MESSAGE
    assert payload["actionItems"] == []
    assert payload["nextActions"] == []


def test_populated_run_has_no_empty_state_message() -> None:
    assert _shape(_persisted("m1"))["message"] is None


def test_items_order_by_priority_then_deadline_presence_then_deadline() -> None:
    later = CLOCK + timedelta(days=2)
    sooner = CLOCK + timedelta(days=1)
    payload = _shape(
        _persisted("undated-medium", priority=Priority.MEDIUM),
        _persisted("later-medium", priority=Priority.MEDIUM, deadline=later),
        _persisted("sooner-medium", priority=Priority.MEDIUM, deadline=sooner),
        _persisted("urgent", priority=Priority.URGENT),
    )
    assert [item.provider_message_id for item in payload["actionItems"]] == [
        "urgent",
        "sooner-medium",
        "later-medium",
        "undated-medium",
    ]


def test_sort_key_ranks_dated_items_ahead_of_undated_at_equal_priority() -> None:
    dated = action_item_from_task(
        _persisted("dated", deadline=CLOCK + timedelta(days=1)),
        run_id="run-compat",
        clock=CLOCK,
    )
    undated = action_item_from_task(_persisted("undated"), run_id="run-compat", clock=CLOCK)
    assert action_item_sort_key(dated, CLOCK) < action_item_sort_key(undated, CLOCK)


def test_action_item_id_is_derived_from_the_fingerprint() -> None:
    item = action_item_from_task(_persisted("m1"), run_id="run-compat", clock=CLOCK)
    assert item.id == "act_fp-m1"
    assert item.fingerprint == "fp-m1"


@pytest.mark.parametrize(
    ("generation_confidence", "expected"),
    [
        (0.95, "high"),
        (0.8, "high"),
        (0.65, "medium"),
        (0.5, "medium"),
        (0.2, "low"),
        (None, "medium"),
    ],
)
def test_generation_confidence_maps_onto_legacy_confidence_bands(
    generation_confidence: float | None, expected: str
) -> None:
    item = action_item_from_task(
        _persisted("m1", generation_confidence=generation_confidence),
        run_id="run-compat",
        clock=CLOCK,
    )
    assert item.confidence.value == expected


def test_missing_priority_falls_back_to_the_deadline_policy() -> None:
    item = action_item_from_task(
        _persisted("m1", priority=None, deadline=CLOCK + timedelta(hours=2)),
        run_id="run-compat",
        clock=CLOCK,
    )
    assert item.priority_reason != "generated"


def test_generated_priority_is_passed_through_untouched() -> None:
    item = action_item_from_task(
        _persisted("m1", priority=Priority.LOW, deadline=CLOCK),
        run_id="run-compat",
        clock=CLOCK,
    )
    assert (item.priority, item.priority_reason) == (Priority.LOW, "generated")
