from datetime import UTC, datetime, timedelta

import pytest

from cowork_agent.domain.chat_contracts import (
    ChatActivity,
    ChatActivityCode,
    ChatActivityDetail,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatMemoryScope,
    ChatTurn,
    ChatTurnStatus,
    MailScanSummary,
    MemoryNamespace,
    MemoryType,
)
from cowork_agent.features.ai_chat.mail_scan_reconciliation import (
    DesiredMailActivity,
    reconcile_mail_activities,
    reconcile_mail_turn,
    upsert_buffer_mail_turn,
    validate_mail_turn_scan_status,
)
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
SCOPE = ChatMemoryScope(user_id="user-1", session_id="session-1")


def _scan(status: str) -> MailScanSummary:
    return MailScanSummary(
        status=status,
        emails_matched=2,
        emails_processed=2 if status in {"succeeded", "partial"} else 0,
        emails_to_process=2,
        action_items_count=1 if status in {"succeeded", "partial"} else None,
    )


def _turn(
    *,
    status: ChatTurnStatus,
    scan_status: str,
    assistant_message: str | None = None,
    activities: tuple[ChatActivity, ...] = (),
) -> ChatTurn:
    return ChatTurn(
        turn_id="turn-1",
        session_id="session-1",
        user_message="@mail summarize unread messages",
        assistant_message=assistant_message,
        created_at=NOW,
        mail_scan=_scan(scan_status),
        status=status,
        idempotency_key="mail-1",
        activities=activities,
        completed_at=NOW if status is not ChatTurnStatus.GENERATING else None,
    )


def test_reconcile_mail_activities_builds_a_server_stamped_terminal_snapshot() -> None:
    desired = (
        DesiredMailActivity(
            code=ChatActivityCode.CHECKING_MAIL,
            status=ChatActivityStatus.COMPLETED,
            outcome=ChatActivityOutcome.SUCCESS,
            detail=ChatActivityDetail(kind="emails_processed", current=2, total=2),
        ),
        DesiredMailActivity(
            code=ChatActivityCode.PROCESSING_EMAIL,
            status=ChatActivityStatus.SKIPPED,
        ),
    )

    activities = reconcile_mail_activities((), desired, ChatTurnStatus.COMPLETED, at=NOW)

    assert [item.status for item in activities] == [
        ChatActivityStatus.COMPLETED,
        ChatActivityStatus.SKIPPED,
    ]
    assert activities[0].started_at == NOW
    assert activities[0].completed_at == NOW
    assert activities[0].outcome is ChatActivityOutcome.SUCCESS
    assert activities[0].detail == ChatActivityDetail(kind="emails_processed", current=2, total=2)


def test_reconcile_mail_activities_terminalizes_unfinished_failure_work() -> None:
    running = ChatActivity.pending(ChatActivityCode.CHECKING_MAIL).transition(
        ChatActivityStatus.RUNNING, at=NOW
    )
    desired = (
        DesiredMailActivity(
            code=ChatActivityCode.CHECKING_MAIL,
            status=ChatActivityStatus.RUNNING,
        ),
        DesiredMailActivity(
            code=ChatActivityCode.PROCESSING_EMAIL,
            status=ChatActivityStatus.PENDING,
        ),
    )

    activities = reconcile_mail_activities(
        (running,), desired, ChatTurnStatus.FAILED, at=NOW + timedelta(seconds=1)
    )

    assert [item.status for item in activities] == [
        ChatActivityStatus.FAILED,
        ChatActivityStatus.SKIPPED,
    ]


def test_reconcile_mail_activities_terminalizes_cancelled_work_as_cancelled() -> None:
    running = ChatActivity.pending(ChatActivityCode.CHECKING_MAIL).transition(
        ChatActivityStatus.RUNNING, at=NOW
    )
    desired = (
        DesiredMailActivity(
            code=ChatActivityCode.CHECKING_MAIL,
            status=ChatActivityStatus.RUNNING,
        ),
    )

    activities = reconcile_mail_activities(
        (running,), desired, ChatTurnStatus.CANCELLED, at=NOW + timedelta(seconds=1)
    )

    assert activities[0].status is ChatActivityStatus.CANCELLED


@pytest.mark.parametrize(
    ("existing", "desired", "message"),
    [
        (
            (),
            (
                DesiredMailActivity(ChatActivityCode.CHECKING_MAIL, ChatActivityStatus.PENDING),
                DesiredMailActivity(ChatActivityCode.CHECKING_MAIL, ChatActivityStatus.PENDING),
            ),
            "codes must be unique",
        ),
        (
            (ChatActivity.pending(ChatActivityCode.CHECKING_MAIL),),
            (DesiredMailActivity(ChatActivityCode.PROCESSING_EMAIL, ChatActivityStatus.PENDING),),
            "plan is append-only",
        ),
    ],
)
def test_reconcile_mail_activities_rejects_non_monotonic_plans(
    existing: tuple[ChatActivity, ...],
    desired: tuple[DesiredMailActivity, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        reconcile_mail_activities(existing, desired, ChatTurnStatus.GENERATING, at=NOW)


def test_reconcile_mail_turn_preserves_identity_and_lands_terminal_results() -> None:
    existing = _turn(
        status=ChatTurnStatus.GENERATING,
        scan_status="running",
        activities=(
            ChatActivity.pending(ChatActivityCode.CHECKING_MAIL).transition(
                ChatActivityStatus.RUNNING, at=NOW
            ),
        ),
    )
    incoming = _turn(
        status=ChatTurnStatus.COMPLETED,
        scan_status="succeeded",
        assistant_message="Prepared one action item.",
    )
    desired = (
        DesiredMailActivity(
            code=ChatActivityCode.CHECKING_MAIL,
            status=ChatActivityStatus.COMPLETED,
            outcome=ChatActivityOutcome.SUCCESS,
        ),
    )

    stored = reconcile_mail_turn(existing, incoming, desired, at=NOW + timedelta(seconds=1))

    assert stored.turn_id == existing.turn_id
    assert stored.created_at == existing.created_at
    assert stored.status is ChatTurnStatus.COMPLETED
    assert stored.assistant_message == "Prepared one action item."
    assert stored.activities[0].status is ChatActivityStatus.COMPLETED


def test_reconcile_mail_turn_rejects_an_idempotency_key_change() -> None:
    existing = _turn(status=ChatTurnStatus.GENERATING, scan_status="running")
    incoming = ChatTurn(
        turn_id="turn-1",
        session_id="session-1",
        user_message=existing.user_message,
        assistant_message=None,
        created_at=NOW,
        mail_scan=_scan("running"),
        status=ChatTurnStatus.GENERATING,
        idempotency_key="mail-2",
    )

    with pytest.raises(ValueError, match="idempotency key cannot change"):
        reconcile_mail_turn(existing, incoming, (), at=NOW)


def test_upsert_buffer_mail_turn_rejects_idempotency_reuse_for_another_request() -> None:
    buffer = InMemoryChatSessionBuffer(max_turns=4, ttl_seconds=60)
    namespace = MemoryNamespace(
        scope=SCOPE,
        memory_type=MemoryType.SHORT_TERM,
        record_id=SCOPE.session_id,
        source_id=None,
    )
    buffer.append(
        namespace,
        _turn(status=ChatTurnStatus.GENERATING, scan_status="running"),
    )
    conflicting = ChatTurn(
        turn_id="turn-2",
        session_id="session-1",
        user_message="@mail a different request",
        assistant_message=None,
        created_at=NOW,
        mail_scan=_scan("running"),
        status=ChatTurnStatus.GENERATING,
        idempotency_key="mail-1",
    )

    with pytest.raises(ValueError, match="another mail request"):
        upsert_buffer_mail_turn(buffer, SCOPE, conflicting, (), at=NOW)


@pytest.mark.parametrize(
    ("turn_status", "scan_status"),
    [
        (ChatTurnStatus.GENERATING, "succeeded"),
        (ChatTurnStatus.COMPLETED, "running"),
        (ChatTurnStatus.FAILED, "partial"),
    ],
)
def test_validate_mail_turn_scan_status_rejects_mismatched_terminal_state(
    turn_status: ChatTurnStatus, scan_status: str
) -> None:
    with pytest.raises(ValueError, match="does not match"):
        validate_mail_turn_scan_status(turn_status, _scan(scan_status))


def test_validate_mail_turn_scan_status_accepts_partial_completed_scan() -> None:
    validate_mail_turn_scan_status(ChatTurnStatus.COMPLETED, _scan("partial"))
