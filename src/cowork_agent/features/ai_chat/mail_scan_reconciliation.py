"""Reconcile aggregate-only mail scan progress with durable chat turns."""

from dataclasses import dataclass, replace
from datetime import datetime

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
from cowork_agent.features.ai_chat.ports import ChatSessionBufferPort


@dataclass(frozen=True, slots=True)
class DesiredMailActivity:
    """One client-desired activity state without server-owned timestamps."""

    code: ChatActivityCode
    status: ChatActivityStatus
    outcome: ChatActivityOutcome | None = None
    detail: ChatActivityDetail | None = None


_MAIL_ACTIVITY_CODES = frozenset(
    {
        ChatActivityCode.CHECKING_MAIL,
        ChatActivityCode.PROCESSING_EMAIL,
        ChatActivityCode.PREPARING_MAIL_RESULTS,
    }
)


def validate_mail_turn_scan_status(turn_status: ChatTurnStatus, mail_scan: MailScanSummary) -> None:
    allowed = {
        ChatTurnStatus.GENERATING: {"connecting", "queued", "running"},
        ChatTurnStatus.COMPLETED: {"succeeded", "partial"},
        ChatTurnStatus.FAILED: {"failed"},
        ChatTurnStatus.CANCELLED: {
            "connecting",
            "queued",
            "running",
            "succeeded",
            "partial",
            "failed",
        },
    }
    if mail_scan.status not in allowed[turn_status]:
        raise ValueError("mail scan status does not match turn status")


def _terminalize_mail_activities(
    activities: tuple[ChatActivity, ...],
    turn_status: ChatTurnStatus,
    *,
    at: datetime,
) -> tuple[ChatActivity, ...]:
    if turn_status is ChatTurnStatus.GENERATING:
        return activities
    if turn_status is ChatTurnStatus.COMPLETED:
        if any(
            item.status not in {ChatActivityStatus.COMPLETED, ChatActivityStatus.SKIPPED}
            for item in activities
        ):
            raise ValueError("completed mail turn has unfinished activity")
        return activities
    terminal_activity_status = (
        ChatActivityStatus.FAILED
        if turn_status is ChatTurnStatus.FAILED
        else ChatActivityStatus.CANCELLED
    )
    result = activities
    for item in tuple(result):
        if item.status is ChatActivityStatus.RUNNING:
            result = tuple(
                current.transition(terminal_activity_status, at=at)
                if current.code is item.code
                else current
                for current in result
            )
        elif item.status is ChatActivityStatus.PENDING:
            result = tuple(
                current.transition(ChatActivityStatus.SKIPPED, at=at)
                if current.code is item.code
                else current
                for current in result
            )
    return result


def _transition_to_desired_activity(
    activity: ChatActivity,
    desired: DesiredMailActivity,
    *,
    at: datetime,
) -> ChatActivity:
    if desired.outcome is not None and desired.status is not ChatActivityStatus.COMPLETED:
        raise ValueError("activity outcome requires completed status")
    if activity.status is desired.status:
        if activity.status is ChatActivityStatus.PENDING:
            if desired.outcome is not None or desired.detail is not None:
                raise ValueError("pending mail activity cannot carry results")
            return activity
        return replace(
            activity,
            detail=desired.detail if desired.detail is not None else activity.detail,
            outcome=(desired.outcome if activity.status is ChatActivityStatus.COMPLETED else None),
        )
    if activity.status not in {ChatActivityStatus.PENDING, ChatActivityStatus.RUNNING}:
        raise ValueError("terminal mail activity cannot regress")
    if activity.status is ChatActivityStatus.PENDING and desired.status in {
        ChatActivityStatus.COMPLETED,
        ChatActivityStatus.FAILED,
    }:
        activity = activity.transition(ChatActivityStatus.RUNNING, at=at)
    return activity.transition(
        desired.status,
        at=at,
        outcome=desired.outcome,
        detail=desired.detail,
    )


def reconcile_mail_activities(
    existing: tuple[ChatActivity, ...],
    desired: tuple[DesiredMailActivity, ...],
    turn_status: ChatTurnStatus,
    *,
    at: datetime,
) -> tuple[ChatActivity, ...]:
    """Merge an append-only desired snapshot and settle it with the turn."""

    if not desired:
        return _terminalize_mail_activities(existing, turn_status, at=at)
    desired_codes = tuple(item.code for item in desired)
    if len(set(desired_codes)) != len(desired_codes):
        raise ValueError("mail activity codes must be unique")
    if any(code not in _MAIL_ACTIVITY_CODES for code in desired_codes):
        raise ValueError("mail lifecycle accepts only mail activity codes")
    existing_codes = tuple(item.code for item in existing)
    if desired_codes[: len(existing_codes)] != existing_codes:
        raise ValueError("mail activity plan is append-only")

    merged: list[ChatActivity] = []
    for index, item in enumerate(desired):
        activity = existing[index] if index < len(existing) else ChatActivity.pending(item.code)
        merged.append(_transition_to_desired_activity(activity, item, at=at))
    return _terminalize_mail_activities(tuple(merged), turn_status, at=at)


def reconcile_mail_turn(
    existing: ChatTurn,
    incoming: ChatTurn,
    desired_activities: tuple[DesiredMailActivity, ...],
    *,
    at: datetime,
) -> ChatTurn:
    """Merge an idempotent mail update into its existing durable turn."""

    if existing.user_message != incoming.user_message:
        raise ValueError("idempotency key was already used for another mail request")
    if existing.idempotency_key not in {None, incoming.idempotency_key}:
        raise ValueError("mail turn idempotency key cannot change")
    if existing.status is not ChatTurnStatus.GENERATING and incoming.status is not existing.status:
        raise ValueError("terminal mail turn cannot regress")
    if existing.status is not ChatTurnStatus.GENERATING:
        return existing
    if existing.status is ChatTurnStatus.GENERATING and incoming.status not in {
        ChatTurnStatus.GENERATING,
        ChatTurnStatus.COMPLETED,
        ChatTurnStatus.FAILED,
        ChatTurnStatus.CANCELLED,
    }:
        raise ValueError("unsupported mail turn transition")
    terminal = incoming.status is not ChatTurnStatus.GENERATING
    activities = reconcile_mail_activities(
        existing.activities,
        desired_activities,
        incoming.status,
        at=at,
    )
    return replace(
        existing,
        assistant_message=(
            incoming.assistant_message
            if incoming.assistant_message is not None
            else existing.assistant_message
        ),
        mail_scan=incoming.mail_scan,
        status=incoming.status,
        activities=activities,
        completed_at=(existing.completed_at or at) if terminal else None,
    )


def upsert_buffer_mail_turn(
    buffer: ChatSessionBufferPort,
    scope: ChatMemoryScope,
    incoming: ChatTurn,
    desired_activities: tuple[DesiredMailActivity, ...],
    *,
    at: datetime,
) -> ChatTurn:
    """Upsert a mail turn in the short-term buffer through one reconciliation path."""

    namespace = MemoryNamespace(
        scope=scope,
        memory_type=MemoryType.SHORT_TERM,
        record_id=scope.session_id,
        source_id=None,
    )
    turns = list(buffer.read(namespace))
    index = next(
        (
            position
            for position, turn in enumerate(turns)
            if turn.turn_id == incoming.turn_id or turn.idempotency_key == incoming.idempotency_key
        ),
        None,
    )
    if index is None:
        stored = incoming
        turns.append(stored)
    else:
        stored = reconcile_mail_turn(turns[index], incoming, desired_activities, at=at)
        turns[index] = stored
    buffer.clear(namespace)
    for turn in turns:
        buffer.append(namespace, turn)
    return stored
