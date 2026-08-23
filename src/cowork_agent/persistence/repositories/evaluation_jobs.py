"""Durable, metadata-only SQLite storage for batch evaluation jobs."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cowork_agent.features.batch_evaluation.contracts import (
    AttemptState,
    EvaluationRequest,
    EvaluationWarning,
    FailureClass,
    JobState,
    StepState,
    UnitState,
    WorkUnit,
    WorkUnitOutcome,
)


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is replayed with another request hash."""


class InvalidStateTransition(ValueError):
    """Raised when durable job, attempt, or step state would regress."""


@dataclass(frozen=True, slots=True)
class EvaluationJob:
    job_id: str
    state: JobState
    requested_workers: int
    effective_workers: int
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvaluationAttempt:
    attempt_id: str
    job_id: str
    unit_id: str
    credential_alias: str
    attempt_number: int
    state: AttemptState
    failure_class: FailureClass | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvaluationStep:
    attempt_id: str
    step_id: str
    ordinal: int
    state: StepState


_JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.ACCEPTED: frozenset({JobState.VALIDATING, JobState.CANCELLATION_REQUESTED}),
    JobState.VALIDATING: frozenset(
        {JobState.QUEUED, JobState.FAILED, JobState.CANCELLATION_REQUESTED}
    ),
    JobState.QUEUED: frozenset(
        {JobState.RUNNING, JobState.FAILED, JobState.CANCELLATION_REQUESTED}
    ),
    JobState.RUNNING: frozenset(
        {JobState.COLLECTING, JobState.FAILED, JobState.CANCELLATION_REQUESTED}
    ),
    JobState.COLLECTING: frozenset(
        {
            JobState.SUCCEEDED,
            JobState.PARTIALLY_SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLATION_REQUESTED,
        }
    ),
    JobState.CANCELLATION_REQUESTED: frozenset({JobState.CANCELLED}),
    JobState.SUCCEEDED: frozenset(),
    JobState.PARTIALLY_SUCCEEDED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
}
_STEP_TRANSITIONS: dict[StepState, frozenset[StepState]] = {
    StepState.PENDING: frozenset(
        {StepState.RUNNING, StepState.SUCCEEDED, StepState.FAILED, StepState.SKIPPED}
    ),
    StepState.RUNNING: frozenset({StepState.SUCCEEDED, StepState.FAILED, StepState.SKIPPED}),
    StepState.SUCCEEDED: frozenset(),
    StepState.FAILED: frozenset(),
    StepState.SKIPPED: frozenset(),
}
_UNIT_TERMINAL_STATES = frozenset({UnitState.SUCCEEDED, UnitState.FAILED, UnitState.CANCELLED})
_ATTEMPT_TERMINAL_STATES = frozenset(
    {AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.UNKNOWN, AttemptState.CANCELLED}
)
_JOB_COLUMNS = (
    "job_id, state, requested_workers, effective_workers, cancel_requested_at,"
    " created_at, updated_at, completed_at"
)
_ATTEMPT_COLUMNS = (
    "attempt_id, job_id, unit_id, credential_alias, attempt_number, state, failure_class,"
    " started_at, completed_at"
)
_PRIVATE_METADATA_KEY_PARTS = frozenset(
    {
        "authorization",
        "content",
        "credential",
        "dataset",
        "error",
        "message",
        "password",
        "prompt",
        "question",
        "reply",
        "secret",
        "token",
        "traceback",
    }
)
_PRIVATE_METADATA_KEY_COMPACTS = frozenset({"apikey", "accesstoken"})


class SQLiteEvaluationJobRepository:
    """Persist evaluation control metadata without retaining dataset or provider content."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute("PRAGMA journal_mode = WAL")
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS evaluation_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    requested_workers INTEGER NOT NULL,
                    effective_workers INTEGER NOT NULL,
                    warnings_json TEXT NOT NULL,
                    cancel_requested_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS evaluation_units (
                    job_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    safe_payload_json TEXT NOT NULL,
                    PRIMARY KEY(job_id, unit_id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    credential_alias TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    failure_class TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(job_id, unit_id, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS evaluation_steps (
                    attempt_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    safe_metadata_json TEXT NOT NULL,
                    PRIMARY KEY(attempt_id, step_id)
                );
                CREATE INDEX IF NOT EXISTS evaluation_units_ready_idx
                    ON evaluation_units(job_id, state, ordinal, unit_id);
                CREATE INDEX IF NOT EXISTS evaluation_attempts_job_state_idx
                    ON evaluation_attempts(job_id, state);
                """
            )

    async def create_or_get(
        self, request: EvaluationRequest, idempotency_key: str, request_hash: str
    ) -> tuple[EvaluationJob, bool]:
        return await asyncio.to_thread(
            self._create_or_get_sync, request, idempotency_key, request_hash
        )

    def _create_or_get_sync(
        self, request: EvaluationRequest, idempotency_key: str, request_hash: str
    ) -> tuple[EvaluationJob, bool]:
        now = _now()
        job_id = f"job-{uuid4().hex}"
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            cursor = database.execute(
                """
                INSERT OR IGNORE INTO evaluation_jobs (
                    job_id, idempotency_key, request_hash, request_json, state,
                    requested_workers, effective_workers, warnings_json,
                    cancel_requested_at, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
                """,
                (
                    job_id,
                    idempotency_key,
                    request_hash,
                    _request_json(request),
                    JobState.ACCEPTED.value,
                    request.max_workers,
                    0,
                    "[]",
                    now,
                    now,
                ),
            )
            row = database.execute(
                f"SELECT {_JOB_COLUMNS}, request_hash FROM evaluation_jobs"
                " WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        assert row is not None
        if str(row[8]) != request_hash:
            raise IdempotencyConflict("idempotency key belongs to another request")
        return _job_from_row(row), cursor.rowcount == 1

    async def get_job(self, job_id: str) -> EvaluationJob | None:
        return await asyncio.to_thread(self._get_job_sync, job_id)

    def _get_job_sync(self, job_id: str) -> EvaluationJob | None:
        with self._connect() as database:
            row = database.execute(
                f"SELECT {_JOB_COLUMNS} FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return None if row is None else _job_from_row(row)

    async def transition_job(
        self,
        job_id: str,
        state: JobState,
        *,
        effective_workers: int | None = None,
        warnings: Sequence[EvaluationWarning] = (),
    ) -> EvaluationJob:
        return await asyncio.to_thread(
            self._transition_job_sync, job_id, state, effective_workers, warnings
        )

    def _transition_job_sync(
        self,
        job_id: str,
        state: JobState,
        effective_workers: int | None,
        warnings: Sequence[EvaluationWarning],
    ) -> EvaluationJob:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                f"SELECT {_JOB_COLUMNS} FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = _job_from_row(row)
            if state not in _JOB_TRANSITIONS[job.state]:
                raise InvalidStateTransition(f"cannot move job from {job.state.value}")
            now = _now()
            database.execute(
                """
                UPDATE evaluation_jobs
                SET state = ?, effective_workers = ?, warnings_json = ?, updated_at = ?,
                    completed_at = ?
                WHERE job_id = ?
                """,
                (
                    state.value,
                    job.effective_workers if effective_workers is None else effective_workers,
                    _warnings_json(warnings),
                    now,
                    now if state in _JOB_TRANSITIONS and not _JOB_TRANSITIONS[state] else None,
                    job_id,
                ),
            )
            updated = database.execute(
                f"SELECT {_JOB_COLUMNS} FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        assert updated is not None
        return _job_from_row(updated)

    async def request_cancellation(self, job_id: str) -> EvaluationJob:
        return await asyncio.to_thread(self._request_cancellation_sync, job_id)

    def _request_cancellation_sync(self, job_id: str) -> EvaluationJob:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                f"SELECT {_JOB_COLUMNS} FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = _job_from_row(row)
            if job.state is JobState.CANCELLATION_REQUESTED:
                return job
            if JobState.CANCELLATION_REQUESTED not in _JOB_TRANSITIONS[job.state]:
                raise InvalidStateTransition(f"cannot cancel job from {job.state.value}")
            now = _now()
            database.execute(
                """
                UPDATE evaluation_jobs
                SET state = ?, cancel_requested_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (JobState.CANCELLATION_REQUESTED.value, now, now, job_id),
            )
            updated = database.execute(
                f"SELECT {_JOB_COLUMNS} FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        assert updated is not None
        return _job_from_row(updated)

    async def add_units(self, job_id: str, units: Sequence[WorkUnit]) -> None:
        await asyncio.to_thread(self._add_units_sync, job_id, units)

    def _add_units_sync(self, job_id: str, units: Sequence[WorkUnit]) -> None:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            for unit in units:
                database.execute(
                    """
                    INSERT INTO evaluation_units (
                        job_id, unit_id, ordinal, state, safe_payload_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        unit.unit_id,
                        unit.ordinal,
                        UnitState.READY.value,
                        _safe_json(unit.payload),
                    ),
                )

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None:
        return await asyncio.to_thread(self._claim_ready_unit_sync, job_id, worker_id)

    def _claim_ready_unit_sync(self, job_id: str, worker_id: str) -> WorkUnit | None:
        del worker_id
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                """
                SELECT unit_id, ordinal, safe_payload_json
                FROM evaluation_units
                WHERE job_id = ? AND state = ?
                ORDER BY ordinal, unit_id
                LIMIT 1
                """,
                (job_id, UnitState.READY.value),
            ).fetchone()
            if row is None:
                return None
            cursor = database.execute(
                """
                UPDATE evaluation_units SET state = ?
                WHERE job_id = ? AND unit_id = ? AND state = ?
                """,
                (UnitState.RUNNING.value, job_id, str(row[0]), UnitState.READY.value),
            )
            if cursor.rowcount != 1:
                return None
        return WorkUnit(
            unit_id=str(row[0]), ordinal=int(row[1]), payload=_mapping_from_json(row[2])
        )

    async def complete_unit(self, job_id: str, outcome: WorkUnitOutcome) -> None:
        await asyncio.to_thread(self._complete_unit_sync, job_id, outcome)

    def _complete_unit_sync(self, job_id: str, outcome: WorkUnitOutcome) -> None:
        if outcome.state not in _UNIT_TERMINAL_STATES:
            raise InvalidStateTransition("work units may only complete in a terminal state")
        with self._connect() as database:
            cursor = database.execute(
                """
                UPDATE evaluation_units SET state = ?
                WHERE job_id = ? AND unit_id = ? AND ordinal = ? AND state = ?
                """,
                (
                    outcome.state.value,
                    job_id,
                    outcome.unit_id,
                    outcome.ordinal,
                    UnitState.RUNNING.value,
                ),
            )
        if cursor.rowcount != 1:
            raise InvalidStateTransition("unit is not running")

    async def start_attempt(
        self, job_id: str, unit_id: str, credential_alias: str
    ) -> EvaluationAttempt:
        return await asyncio.to_thread(self._start_attempt_sync, job_id, unit_id, credential_alias)

    def _start_attempt_sync(
        self, job_id: str, unit_id: str, credential_alias: str
    ) -> EvaluationAttempt:
        attempt_id = f"attempt-{uuid4().hex}"
        now = _now()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT state FROM evaluation_units WHERE job_id = ? AND unit_id = ?",
                (job_id, unit_id),
            ).fetchone()
            if row is None or str(row[0]) != UnitState.RUNNING.value:
                raise InvalidStateTransition("attempt requires a running unit")
            attempt_number = int(
                database.execute(
                    "SELECT COUNT(*) FROM evaluation_attempts WHERE job_id = ? AND unit_id = ?",
                    (job_id, unit_id),
                ).fetchone()[0]
            ) + 1
            database.execute(
                """
                INSERT INTO evaluation_attempts (
                    attempt_id, job_id, unit_id, credential_alias, attempt_number,
                    state, failure_class, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    attempt_id,
                    job_id,
                    unit_id,
                    credential_alias,
                    attempt_number,
                    AttemptState.RUNNING.value,
                    now,
                ),
            )
            created = database.execute(
                f"SELECT {_ATTEMPT_COLUMNS} FROM evaluation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        assert created is not None
        return _attempt_from_row(created)

    async def get_attempt(self, attempt_id: str) -> EvaluationAttempt | None:
        return await asyncio.to_thread(self._get_attempt_sync, attempt_id)

    def _get_attempt_sync(self, attempt_id: str) -> EvaluationAttempt | None:
        with self._connect() as database:
            row = database.execute(
                f"SELECT {_ATTEMPT_COLUMNS} FROM evaluation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return None if row is None else _attempt_from_row(row)

    async def finish_attempt(
        self,
        attempt_id: str,
        state: AttemptState,
        failure_class: FailureClass | None = None,
    ) -> EvaluationAttempt:
        return await asyncio.to_thread(self._finish_attempt_sync, attempt_id, state, failure_class)

    def _finish_attempt_sync(
        self, attempt_id: str, state: AttemptState, failure_class: FailureClass | None
    ) -> EvaluationAttempt:
        if state not in _ATTEMPT_TERMINAL_STATES:
            raise InvalidStateTransition("attempt must finish in a terminal state")
        with self._connect() as database:
            cursor = database.execute(
                """
                UPDATE evaluation_attempts
                SET state = ?, failure_class = ?, completed_at = ?
                WHERE attempt_id = ? AND state = ?
                """,
                (
                    state.value,
                    None if failure_class is None else failure_class.value,
                    _now(),
                    attempt_id,
                    AttemptState.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise InvalidStateTransition("attempt is not running")
            row = database.execute(
                f"SELECT {_ATTEMPT_COLUMNS} FROM evaluation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        assert row is not None
        return _attempt_from_row(row)

    async def write_step(
        self,
        attempt_id: str,
        *,
        step_id: str,
        ordinal: int,
        state: StepState,
        safe_metadata: Mapping[str, object],
    ) -> EvaluationStep:
        return await asyncio.to_thread(
            self._write_step_sync, attempt_id, step_id, ordinal, state, safe_metadata
        )

    def _write_step_sync(
        self,
        attempt_id: str,
        step_id: str,
        ordinal: int,
        state: StepState,
        safe_metadata: Mapping[str, object],
    ) -> EvaluationStep:
        metadata_json = _safe_metadata_json(safe_metadata)
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT ordinal, state FROM evaluation_steps WHERE attempt_id = ? AND step_id = ?",
                (attempt_id, step_id),
            ).fetchone()
            if row is None:
                database.execute(
                    """
                    INSERT INTO evaluation_steps (
                        attempt_id, step_id, ordinal, state, safe_metadata_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (attempt_id, step_id, ordinal, state.value, metadata_json),
                )
            else:
                current = StepState(str(row[1]))
                if int(row[0]) != ordinal or (
                    state is not current and state not in _STEP_TRANSITIONS[current]
                ):
                    raise InvalidStateTransition("step state cannot regress")
                database.execute(
                    """
                    UPDATE evaluation_steps SET state = ?, safe_metadata_json = ?
                    WHERE attempt_id = ? AND step_id = ?
                    """,
                    (state.value, metadata_json, attempt_id, step_id),
                )
        return EvaluationStep(attempt_id=attempt_id, step_id=step_id, ordinal=ordinal, state=state)

    async def recover_orphaned_attempts(self, job_id: str) -> tuple[str, ...]:
        return await asyncio.to_thread(self._recover_orphaned_attempts_sync, job_id)

    def _recover_orphaned_attempts_sync(self, job_id: str) -> tuple[str, ...]:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            rows = database.execute(
                """
                SELECT attempt_id FROM evaluation_attempts
                WHERE job_id = ? AND state = ?
                ORDER BY attempt_number, attempt_id
                """,
                (job_id, AttemptState.RUNNING.value),
            ).fetchall()
            identifiers = tuple(str(row[0]) for row in rows)
            if identifiers:
                database.execute(
                    """
                    UPDATE evaluation_attempts
                    SET state = ?, failure_class = ?, completed_at = ?
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        AttemptState.UNKNOWN.value,
                        FailureClass.UNKNOWN.value,
                        _now(),
                        job_id,
                        AttemptState.RUNNING.value,
                    ),
                )
        return identifiers

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path, timeout=30)
        database.row_factory = sqlite3.Row
        return database


def _request_json(request: EvaluationRequest) -> str:
    return _safe_json(
        {
            "evaluation_type": request.evaluation_type,
            "provider": request.provider,
            "target_model": request.target_model,
            "dataset_ref": request.dataset_ref,
            "credential_pool": request.credential_pool,
            "execution_mode": request.execution_mode.value,
            "max_workers": request.max_workers,
            "max_attempts_per_unit": request.max_attempts_per_unit,
            "budget": {
                "max_provider_requests": request.budget.max_provider_requests,
                "max_total_tokens": request.budget.max_total_tokens,
            },
        }
    )


def _warnings_json(warnings: Sequence[EvaluationWarning]) -> str:
    return _safe_json(
        [
            {"code": warning.code, "message": warning.message, "details": warning.details}
            for warning in warnings
        ]
    )


def _safe_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )


def _safe_metadata_json(value: Mapping[str, object]) -> str:
    _validate_safe_metadata(value)
    return _safe_json(value)


def _validate_safe_metadata(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or _is_private_metadata_key(key):
                raise ValueError("step metadata contains a private key")
            _validate_safe_metadata(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _validate_safe_metadata(nested)
        return
    if isinstance(value, Path) or (isinstance(value, str) and Path(value).is_absolute()):
        raise ValueError("step metadata cannot contain absolute paths")
    if value is None or isinstance(value, str | int | float | bool):
        return
    raise ValueError("step metadata must be JSON-compatible")


def _is_private_metadata_key(key: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    compact = normalized.replace("_", "")
    return any(marker in compact for marker in _PRIVATE_METADATA_KEY_COMPACTS) or bool(
        frozenset(part for part in normalized.split("_") if part) & _PRIVATE_METADATA_KEY_PARTS
    )


def _json_default(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError("evaluation persistence accepts JSON-compatible safe metadata only")


def _mapping_from_json(value: object) -> Mapping[str, object]:
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError("stored work metadata is invalid")
    return parsed


def _job_from_row(row: Sequence[object]) -> EvaluationJob:
    return EvaluationJob(
        job_id=str(row[0]),
        state=JobState(str(row[1])),
        requested_workers=_integer(row[2]),
        effective_workers=_integer(row[3]),
        cancel_requested_at=_optional_datetime(row[4]),
        created_at=_datetime(row[5]),
        updated_at=_datetime(row[6]),
        completed_at=_optional_datetime(row[7]),
    )


def _attempt_from_row(row: Sequence[object]) -> EvaluationAttempt:
    return EvaluationAttempt(
        attempt_id=str(row[0]),
        job_id=str(row[1]),
        unit_id=str(row[2]),
        credential_alias=str(row[3]),
        attempt_number=_integer(row[4]),
        state=AttemptState(str(row[5])),
        failure_class=None if row[6] is None else FailureClass(str(row[6])),
        started_at=_datetime(row[7]),
        completed_at=_optional_datetime(row[8]),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _integer(value: object) -> int:
    return int(str(value))
