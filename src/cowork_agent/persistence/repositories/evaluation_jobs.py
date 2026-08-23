"""Durable, metadata-only SQLite storage for batch evaluation jobs."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cowork_agent.features.batch_evaluation.contracts import (
    AttemptState,
    EvaluationBudget,
    EvaluationRequest,
    EvaluationWarning,
    ExecutionMode,
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
    request: EvaluationRequest
    state: JobState
    requested_workers: int
    effective_workers: int
    warnings: tuple[EvaluationWarning, ...]
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvaluationAttempt:
    attempt_id: str
    job_id: str
    unit_id: str
    worker_id: str
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


@dataclass(frozen=True, slots=True)
class EvaluationUnit:
    job_id: str
    unit_id: str
    ordinal: int
    state: UnitState
    claimed_by: str | None
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class EvaluationRecovery:
    requeued_unit_ids: tuple[str, ...]
    unknown_attempt_ids: tuple[str, ...]
    blocked_unit_ids: tuple[str, ...]


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
    "job_id, request_json, warnings_json, state, requested_workers, effective_workers,"
    " cancel_requested_at, created_at, updated_at, completed_at"
)
_UNIT_COLUMNS = "job_id, unit_id, ordinal, state, claimed_by, safe_payload_json"
_ATTEMPT_COLUMNS = (
    "attempt_id, job_id, unit_id, worker_id, credential_alias, attempt_number, state,"
    " failure_class, started_at, completed_at"
)
_RECOVERABLE_JOB_STATES = (
    JobState.QUEUED,
    JobState.RUNNING,
    JobState.COLLECTING,
    JobState.CANCELLATION_REQUESTED,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
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
                    claimed_by TEXT,
                    safe_payload_json TEXT NOT NULL,
                    PRIMARY KEY(job_id, unit_id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
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
                CREATE UNIQUE INDEX IF NOT EXISTS evaluation_attempts_one_running_unit_idx
                    ON evaluation_attempts(job_id, unit_id)
                    WHERE state = 'running';
                """
            )
            _add_column_if_missing(database, "evaluation_units", "claimed_by TEXT")
            _add_column_if_missing(
                database, "evaluation_attempts", "worker_id TEXT NOT NULL DEFAULT ''"
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
        if str(row[10]) != request_hash:
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

    async def list_recoverable_jobs(self) -> tuple[EvaluationJob, ...]:
        return await asyncio.to_thread(self._list_recoverable_jobs_sync)

    def _list_recoverable_jobs_sync(self) -> tuple[EvaluationJob, ...]:
        placeholders = ", ".join("?" for _ in _RECOVERABLE_JOB_STATES)
        with self._connect() as database:
            rows = database.execute(
                f"""
                SELECT {_JOB_COLUMNS} FROM evaluation_jobs
                WHERE state IN ({placeholders})
                ORDER BY created_at, job_id
                """,
                tuple(state.value for state in _RECOVERABLE_JOB_STATES),
            ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    async def transition_job(
        self,
        job_id: str,
        state: JobState,
        *,
        effective_workers: int | None = None,
        warnings: Sequence[EvaluationWarning] | None = None,
    ) -> EvaluationJob:
        return await asyncio.to_thread(
            self._transition_job_sync, job_id, state, effective_workers, warnings
        )

    def _transition_job_sync(
        self,
        job_id: str,
        state: JobState,
        effective_workers: int | None,
        warnings: Sequence[EvaluationWarning] | None,
    ) -> EvaluationJob:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                f"SELECT {_JOB_COLUMNS} FROM evaluation_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = _job_from_row(row)
            if state is JobState.CANCELLED and job.state is JobState.CANCELLED:
                return job
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
                    _warnings_json(job.warnings if warnings is None else warnings),
                    now,
                    now if not _JOB_TRANSITIONS[state] else job.completed_at,
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
                        job_id, unit_id, ordinal, state, claimed_by, safe_payload_json
                    ) VALUES (?, ?, ?, ?, NULL, ?)
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
        _require_identifier(worker_id, "worker_id")
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
                UPDATE evaluation_units SET state = ?, claimed_by = ?
                WHERE job_id = ? AND unit_id = ? AND state = ?
                """,
                (
                    UnitState.RUNNING.value,
                    worker_id,
                    job_id,
                    str(row[0]),
                    UnitState.READY.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return WorkUnit(
            unit_id=str(row[0]), ordinal=int(row[1]), payload=_mapping_from_json(row[2])
        )

    async def get_unit(self, job_id: str, unit_id: str) -> EvaluationUnit | None:
        return await asyncio.to_thread(self._get_unit_sync, job_id, unit_id)

    def _get_unit_sync(self, job_id: str, unit_id: str) -> EvaluationUnit | None:
        with self._connect() as database:
            row = database.execute(
                f"SELECT {_UNIT_COLUMNS} FROM evaluation_units WHERE job_id = ? AND unit_id = ?",
                (job_id, unit_id),
            ).fetchone()
        return None if row is None else _unit_from_row(row)

    async def list_running_units(self, job_id: str) -> tuple[EvaluationUnit, ...]:
        return await asyncio.to_thread(self._list_running_units_sync, job_id)

    def _list_running_units_sync(self, job_id: str) -> tuple[EvaluationUnit, ...]:
        with self._connect() as database:
            rows = database.execute(
                f"""
                SELECT {_UNIT_COLUMNS} FROM evaluation_units
                WHERE job_id = ? AND state = ?
                ORDER BY ordinal, unit_id
                """,
                (job_id, UnitState.RUNNING.value),
            ).fetchall()
        return tuple(_unit_from_row(row) for row in rows)

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
        self, job_id: str, unit_id: str, worker_id: str, credential_alias: str
    ) -> EvaluationAttempt:
        return await asyncio.to_thread(
            self._start_attempt_sync, job_id, unit_id, worker_id, credential_alias
        )

    def _start_attempt_sync(
        self, job_id: str, unit_id: str, worker_id: str, credential_alias: str
    ) -> EvaluationAttempt:
        _require_identifier(worker_id, "worker_id")
        _require_identifier(credential_alias, "credential_alias")
        attempt_id = f"attempt-{uuid4().hex}"
        now = _now()
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT state, claimed_by FROM evaluation_units WHERE job_id = ? AND unit_id = ?",
                (job_id, unit_id),
            ).fetchone()
            if row is None or str(row[0]) != UnitState.RUNNING.value:
                raise InvalidStateTransition("attempt requires a running unit")
            if str(row[1]) != worker_id:
                raise InvalidStateTransition("attempt owner does not hold the unit claim")
            states = tuple(
                AttemptState(str(attempt_row[0]))
                for attempt_row in database.execute(
                    """
                    SELECT state FROM evaluation_attempts
                    WHERE job_id = ? AND unit_id = ?
                    ORDER BY attempt_number DESC
                    """,
                    (job_id, unit_id),
                ).fetchall()
            )
            if AttemptState.RUNNING in states:
                raise InvalidStateTransition("unit already has a running attempt")
            if AttemptState.UNKNOWN in states:
                raise InvalidStateTransition("unit has an unknown provider outcome")
            if states and states[0] is not AttemptState.FAILED:
                raise InvalidStateTransition(
                    "unit requires explicit recovery before another attempt"
                )
            attempt_number = int(
                database.execute(
                    "SELECT COUNT(*) FROM evaluation_attempts WHERE job_id = ? AND unit_id = ?",
                    (job_id, unit_id),
                ).fetchone()[0]
            ) + 1
            database.execute(
                """
                INSERT INTO evaluation_attempts (
                    attempt_id, job_id, unit_id, worker_id, credential_alias, attempt_number,
                    state, failure_class, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    attempt_id,
                    job_id,
                    unit_id,
                    worker_id,
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

    async def list_attempts(
        self, job_id: str, unit_id: str | None = None
    ) -> tuple[EvaluationAttempt, ...]:
        return await asyncio.to_thread(self._list_attempts_sync, job_id, unit_id)

    def _list_attempts_sync(
        self, job_id: str, unit_id: str | None
    ) -> tuple[EvaluationAttempt, ...]:
        with self._connect() as database:
            if unit_id is None:
                rows = database.execute(
                    f"""
                    SELECT {_ATTEMPT_COLUMNS} FROM evaluation_attempts
                    WHERE job_id = ? ORDER BY unit_id, attempt_number, attempt_id
                    """,
                    (job_id,),
                ).fetchall()
            else:
                rows = database.execute(
                    f"""
                    SELECT {_ATTEMPT_COLUMNS} FROM evaluation_attempts
                    WHERE job_id = ? AND unit_id = ? ORDER BY attempt_number, attempt_id
                    """,
                    (job_id, unit_id),
                ).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    async def finish_attempt(
        self,
        attempt_id: str,
        state: AttemptState,
        failure_class: FailureClass | None = None,
        *,
        worker_id: str,
    ) -> EvaluationAttempt:
        return await asyncio.to_thread(
            self._finish_attempt_sync, attempt_id, state, failure_class, worker_id
        )

    def _finish_attempt_sync(
        self,
        attempt_id: str,
        state: AttemptState,
        failure_class: FailureClass | None,
        worker_id: str,
    ) -> EvaluationAttempt:
        if state not in _ATTEMPT_TERMINAL_STATES:
            raise InvalidStateTransition("attempt must finish in a terminal state")
        _require_identifier(worker_id, "worker_id")
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            existing = database.execute(
                "SELECT worker_id, state FROM evaluation_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if existing is None or str(existing[1]) != AttemptState.RUNNING.value:
                raise InvalidStateTransition("attempt is not running")
            if str(existing[0]) != worker_id:
                raise InvalidStateTransition("attempt owner does not match worker")
            database.execute(
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

    async def recover_orphaned_attempts(self, job_id: str) -> EvaluationRecovery:
        return await asyncio.to_thread(self._recover_orphaned_attempts_sync, job_id)

    def _recover_orphaned_attempts_sync(self, job_id: str) -> EvaluationRecovery:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            rows = database.execute(
                """
                SELECT unit_id FROM evaluation_units
                WHERE job_id = ? AND state = ?
                ORDER BY ordinal, unit_id
                """,
                (job_id, UnitState.RUNNING.value),
            ).fetchall()
            requeued: list[str] = []
            unknown_attempts: list[str] = []
            blocked: list[str] = []
            for row in rows:
                unit_id = str(row[0])
                attempts = database.execute(
                    """
                    SELECT attempt_id, state FROM evaluation_attempts
                    WHERE job_id = ? AND unit_id = ?
                    ORDER BY attempt_number, attempt_id
                    """,
                    (job_id, unit_id),
                ).fetchall()
                if not attempts:
                    database.execute(
                        """
                        UPDATE evaluation_units SET state = ?, claimed_by = NULL
                        WHERE job_id = ? AND unit_id = ? AND state = ?
                        """,
                        (UnitState.READY.value, job_id, unit_id, UnitState.RUNNING.value),
                    )
                    requeued.append(unit_id)
                    continue
                blocked.append(unit_id)
                for attempt_id, state in attempts:
                    if str(state) != AttemptState.RUNNING.value:
                        continue
                    database.execute(
                        """
                        UPDATE evaluation_attempts
                        SET state = ?, failure_class = ?, completed_at = ?
                        WHERE attempt_id = ? AND state = ?
                        """,
                        (
                            AttemptState.UNKNOWN.value,
                            FailureClass.UNKNOWN.value,
                            _now(),
                            str(attempt_id),
                            AttemptState.RUNNING.value,
                        ),
                    )
                    unknown_attempts.append(str(attempt_id))
        return EvaluationRecovery(
            requeued_unit_ids=tuple(requeued),
            unknown_attempt_ids=tuple(unknown_attempts),
            blocked_unit_ids=tuple(blocked),
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self._path, timeout=30)
        database.row_factory = sqlite3.Row
        try:
            yield database
        except BaseException:
            database.rollback()
            raise
        else:
            database.commit()
        finally:
            database.close()


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
            "parameters": request.parameters,
        }
    )


def _warnings_json(warnings: Sequence[EvaluationWarning]) -> str:
    return _safe_json(
        [
            {"code": warning.code, "details": warning.details}
            for warning in warnings
        ]
    )


def _safe_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
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
    parsed = value if isinstance(value, Mapping) else json.loads(str(value))
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError("stored work metadata is invalid")
    return parsed


def _request_from_json(value: object) -> EvaluationRequest:
    parsed = _mapping_from_json(value)
    budget = _mapping_from_json(parsed.get("budget"))
    return EvaluationRequest(
        evaluation_type=str(parsed["evaluation_type"]),
        provider=str(parsed["provider"]),
        target_model=str(parsed["target_model"]),
        dataset_ref=str(parsed["dataset_ref"]),
        credential_pool=str(parsed["credential_pool"]),
        execution_mode=ExecutionMode(str(parsed["execution_mode"])),
        max_workers=_integer(parsed["max_workers"]),
        max_attempts_per_unit=_integer(parsed["max_attempts_per_unit"]),
        budget=EvaluationBudget(
            max_provider_requests=_integer(budget["max_provider_requests"]),
            max_total_tokens=_integer(budget["max_total_tokens"]),
        ),
        parameters=_mapping_from_json(parsed.get("parameters", "{}")),
    )


def _warnings_from_json(value: object) -> tuple[EvaluationWarning, ...]:
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError("stored warnings are invalid")
    warnings: list[EvaluationWarning] = []
    for item in parsed:
        if (
            not isinstance(item, dict)
            or not {"code", "details"} <= set(item)
            or set(item) - {"code", "details", "message"}
        ):
            raise ValueError("stored warning is invalid")
        details = item["details"]
        if not isinstance(details, dict) or not all(isinstance(key, str) for key in details):
            raise ValueError("stored warning details are invalid")
        warnings.append(EvaluationWarning(code=str(item["code"]), details=details))
    return tuple(warnings)


def _job_from_row(row: Sequence[object]) -> EvaluationJob:
    return EvaluationJob(
        job_id=str(row[0]),
        request=_request_from_json(row[1]),
        warnings=_warnings_from_json(row[2]),
        state=JobState(str(row[3])),
        requested_workers=_integer(row[4]),
        effective_workers=_integer(row[5]),
        cancel_requested_at=_optional_datetime(row[6]),
        created_at=_datetime(row[7]),
        updated_at=_datetime(row[8]),
        completed_at=_optional_datetime(row[9]),
    )


def _unit_from_row(row: Sequence[object]) -> EvaluationUnit:
    return EvaluationUnit(
        job_id=str(row[0]),
        unit_id=str(row[1]),
        ordinal=_integer(row[2]),
        state=UnitState(str(row[3])),
        claimed_by=None if row[4] is None else str(row[4]),
        payload=_mapping_from_json(row[5]),
    )


def _attempt_from_row(row: Sequence[object]) -> EvaluationAttempt:
    return EvaluationAttempt(
        attempt_id=str(row[0]),
        job_id=str(row[1]),
        unit_id=str(row[2]),
        worker_id=str(row[3]),
        credential_alias=str(row[4]),
        attempt_number=_integer(row[5]),
        state=AttemptState(str(row[6])),
        failure_class=None if row[7] is None else FailureClass(str(row[7])),
        started_at=_datetime(row[8]),
        completed_at=_optional_datetime(row[9]),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _integer(value: object) -> int:
    return int(str(value))


def _require_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a safe identifier")
    return value


def _add_column_if_missing(database: sqlite3.Connection, table: str, definition: str) -> None:
    column = definition.split(maxsplit=1)[0]
    columns = {str(row[1]) for row in database.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        database.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
