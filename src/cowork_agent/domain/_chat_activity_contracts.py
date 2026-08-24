"""User-facing, durable progress metadata for one chat turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

from ._chat_contracts_common import _as_datetime, _as_enum, _as_mapping, _to_dict

MAX_CHAT_ACTIVITIES = 8
MAX_CHAT_ACTIVITY_COUNT = 100_000


class ChatActivityCode(StrEnum):
    UNDERSTANDING_REQUEST = "understanding_request"
    SEARCHING_RELEVANT_INFORMATION = "searching_relevant_information"
    REVIEWING_CONTEXT = "reviewing_context"
    PREPARING_RESPONSE = "preparing_response"
    PREPARING_ACTION_PLAN = "preparing_action_plan"
    CHECKING_MAIL = "checking_mail"
    PROCESSING_EMAIL = "processing_email"
    PREPARING_MAIL_RESULTS = "preparing_mail_results"


class ChatActivityStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ChatActivityOutcome(StrEnum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    PARTIAL = "partial"
    DEGRADED = "degraded"


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or value > MAX_CHAT_ACTIVITY_COUNT:
        raise ValueError(f"{name} must be between 0 and {MAX_CHAT_ACTIVITY_COUNT}")
    return value


@dataclass(frozen=True, slots=True)
class ChatActivityDetail:
    """A bounded aggregate; deliberately incapable of carrying user content."""

    kind: str
    current: int
    total: int | None = None

    _KINDS = frozenset({"documents_found", "emails_processed", "action_items_prepared"})

    def __post_init__(self) -> None:
        if self.kind not in self._KINDS:
            raise ValueError("unsupported activity detail kind")
        _count(self.current, "current")
        if self.total is not None:
            _count(self.total, "total")
            if self.current > self.total:
                raise ValueError("current must not exceed total")

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        if set(data) - {"kind", "current", "total"}:
            raise ValueError("unexpected activity detail field")
        kind = data.get("kind")
        if not isinstance(kind, str):
            raise TypeError("kind must be a string")
        total = data.get("total")
        return cls(
            kind=kind,
            current=_count(data.get("current"), "current"),
            total=_count(total, "total") if total is not None else None,
        )


_TERMINAL_ACTIVITY_STATUSES = frozenset(
    {
        ChatActivityStatus.COMPLETED,
        ChatActivityStatus.FAILED,
        ChatActivityStatus.CANCELLED,
        ChatActivityStatus.SKIPPED,
    }
)


@dataclass(frozen=True, slots=True)
class ChatActivity:
    code: ChatActivityCode
    status: ChatActivityStatus = ChatActivityStatus.PENDING
    outcome: ChatActivityOutcome | None = None
    detail: ChatActivityDetail | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _as_enum(self.code, ChatActivityCode, "code"))
        status = _as_enum(self.status, ChatActivityStatus, "status")
        object.__setattr__(self, "status", status)
        if self.outcome is not None:
            object.__setattr__(
                self, "outcome", _as_enum(self.outcome, ChatActivityOutcome, "outcome")
            )
        if self.detail is not None and not isinstance(self.detail, ChatActivityDetail):
            raise TypeError("detail must be a ChatActivityDetail")
        for value, name in (
            (self.started_at, "started_at"),
            (self.completed_at, "completed_at"),
        ):
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.completed_at is not None and self.started_at is None:
            raise ValueError("completed_at requires started_at")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must not precede started_at")
        if status is ChatActivityStatus.PENDING and (
            self.started_at is not None or self.completed_at is not None or self.outcome is not None
        ):
            raise ValueError("pending activity cannot have timestamps or outcome")
        if status is ChatActivityStatus.RUNNING and self.completed_at is not None:
            raise ValueError("running activity cannot have completed_at")
        if status in _TERMINAL_ACTIVITY_STATUSES and self.completed_at is None:
            raise ValueError("terminal activity requires completed_at")
        if self.outcome is not None and status is not ChatActivityStatus.COMPLETED:
            raise ValueError("outcome is supported only on completed activity")

    @classmethod
    def pending(cls, code: ChatActivityCode) -> Self:
        return cls(code=code)

    def transition(
        self,
        status: ChatActivityStatus,
        *,
        at: datetime,
        outcome: ChatActivityOutcome | None = None,
        detail: ChatActivityDetail | None = None,
    ) -> Self:
        """Apply one monotonic server-stamped lifecycle transition."""

        status = _as_enum(status, ChatActivityStatus, "status")
        if at.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        allowed = {
            ChatActivityStatus.PENDING: {
                ChatActivityStatus.RUNNING,
                ChatActivityStatus.SKIPPED,
                ChatActivityStatus.CANCELLED,
            },
            ChatActivityStatus.RUNNING: {
                ChatActivityStatus.COMPLETED,
                ChatActivityStatus.FAILED,
                ChatActivityStatus.CANCELLED,
            },
        }.get(self.status, set())
        if status not in allowed:
            raise ValueError(f"invalid activity transition: {self.status.value} -> {status.value}")
        started_at = self.started_at
        if started_at is None:
            started_at = at
        completed_at = at if status in _TERMINAL_ACTIVITY_STATUSES else None
        return replace(
            self,
            status=status,
            outcome=outcome,
            detail=self.detail if detail is None else detail,
            started_at=started_at,
            completed_at=completed_at,
        )

    def to_dict(self) -> dict[str, object]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Self:
        unexpected = set(data) - {
            "code", "status", "outcome", "detail", "started_at", "completed_at"
        }
        if unexpected:
            raise ValueError(f"unexpected activity field(s): {sorted(unexpected)}")
        raw_detail = data.get("detail")
        raw_started = data.get("started_at")
        raw_completed = data.get("completed_at")
        raw_outcome = data.get("outcome")
        return cls(
            code=_as_enum(data.get("code"), ChatActivityCode, "code"),
            status=_as_enum(data.get("status"), ChatActivityStatus, "status"),
            outcome=(
                _as_enum(raw_outcome, ChatActivityOutcome, "outcome")
                if raw_outcome is not None
                else None
            ),
            detail=(
                ChatActivityDetail.from_dict(_as_mapping(raw_detail, "detail"))
                if raw_detail is not None
                else None
            ),
            started_at=(
                _as_datetime(raw_started, "started_at") if raw_started is not None else None
            ),
            completed_at=(
                _as_datetime(raw_completed, "completed_at")
                if raw_completed is not None
                else None
            ),
        )


def validate_chat_activities(value: Sequence[ChatActivity]) -> tuple[ChatActivity, ...]:
    activities = tuple(value)
    if len(activities) > MAX_CHAT_ACTIVITIES:
        raise ValueError(f"activities must not exceed {MAX_CHAT_ACTIVITIES} items")
    if not all(isinstance(item, ChatActivity) for item in activities):
        raise TypeError("activities items must be ChatActivity")
    codes = tuple(item.code for item in activities)
    if len(set(codes)) != len(codes):
        raise ValueError("activity codes must be unique")
    return activities


def transition_activity_snapshot(
    activities: Sequence[ChatActivity],
    code: ChatActivityCode,
    status: ChatActivityStatus,
    *,
    at: datetime,
    outcome: ChatActivityOutcome | None = None,
    detail: ChatActivityDetail | None = None,
) -> tuple[ChatActivity, ...]:
    """Transition one activity without changing snapshot order."""

    snapshot = validate_chat_activities(activities)
    code = _as_enum(code, ChatActivityCode, "code")
    for index, activity in enumerate(snapshot):
        if activity.code is code:
            transitioned = activity.transition(
                status, at=at, outcome=outcome, detail=detail
            )
            return (*snapshot[:index], transitioned, *snapshot[index + 1 :])
    raise ValueError(f"activity is absent from snapshot: {code.value}")
