"""In-memory repositories for local execution and deterministic tests."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from cowork_agent.domain import (
    ActionFreshness,
    AttachmentWarning,
    DigestRun,
    ProcessedEmail,
    RunStatus,
)
from cowork_agent.domain.chat_contracts import (
    ChatMemoryScope,
    ChatTurn,
    ChatTurnStatus,
)
from cowork_agent.features.email_action_plan.ports import PersistedTask


class InMemoryChatHistoryRepository:
    """Process-local chat lifecycle store used when PostgreSQL is not configured."""

    def __init__(self) -> None:
        self._turns: dict[tuple[str, str], ChatTurn] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._owners: dict[str, tuple[str, str]] = {}
        self._titles: dict[str, str] = {}

    async def begin_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        idempotency_key: str,
        title: str,
    ) -> ChatTurn:
        self._validate_scope(scope, turn)
        if turn.idempotency_key is not None and turn.idempotency_key != idempotency_key:
            raise ValueError("chat turn idempotency key must match begin_turn")
        self._claim_owner(scope)
        idempotency_index = (scope.session_id, idempotency_key)
        existing_turn_id = self._idempotency.get(idempotency_index)
        if existing_turn_id is not None:
            existing = self._turns[(scope.session_id, existing_turn_id)]
            if existing.user_message != turn.user_message:
                raise ValueError("idempotency key was already used for another message")
            return existing
        stored = replace(turn, idempotency_key=idempotency_key)
        self._turns[(scope.session_id, stored.turn_id)] = stored
        self._idempotency[idempotency_index] = stored.turn_id
        self._titles.setdefault(scope.session_id, title)
        return stored

    async def update_turn(
        self,
        scope: ChatMemoryScope,
        turn: ChatTurn,
        *,
        title: str | None = None,
    ) -> ChatTurn:
        self._validate_scope(scope, turn)
        if not self._is_owner(scope):
            raise ValueError("chat turn was not found for its history scope")
        key = (scope.session_id, turn.turn_id)
        existing = self._turns.get(key)
        if existing is None:
            raise ValueError("chat turn was not found for its history scope")
        stored = replace(
            existing,
            assistant_message=turn.assistant_message,
            citation_coordinates=turn.citation_coordinates,
            rag_evidence=turn.rag_evidence,
            retrieval_status=turn.retrieval_status,
            mail_scan=turn.mail_scan,
            status=turn.status,
            error_code=turn.error_code,
            activities=turn.activities,
            completed_at=turn.completed_at,
        )
        self._turns[key] = stored
        if title is not None:
            self._titles[scope.session_id] = title
        return stored

    async def write_turn(
        self, scope: ChatMemoryScope, turn: ChatTurn, *, title: str
    ) -> None:
        if turn.assistant_message is None:
            raise ValueError("only completed assistant replies may enter chat history")
        completed = replace(
            turn,
            status=ChatTurnStatus.COMPLETED,
            idempotency_key=turn.idempotency_key or turn.turn_id,
            error_code=None,
        )
        await self.begin_turn(
            scope,
            completed,
            idempotency_key=completed.idempotency_key or completed.turn_id,
            title=title,
        )

    async def list_turns(
        self, scope: ChatMemoryScope, *, connection: object | None = None
    ) -> tuple[ChatTurn, ...]:
        del connection
        if not self._is_owner(scope):
            return ()
        matching = (
            turn
            for (session_id, _), turn in self._turns.items()
            if session_id == scope.session_id
        )
        return tuple(sorted(matching, key=lambda turn: (turn.created_at, turn.turn_id)))

    async def titles_for(self, scopes: Sequence[ChatMemoryScope]) -> Mapping[str, str]:
        return {
            scope.session_id: self._titles[scope.session_id]
            for scope in scopes
            if self._is_owner(scope) and scope.session_id in self._titles
        }

    async def latest_turns_for(
        self, scopes: Sequence[ChatMemoryScope]
    ) -> Mapping[str, ChatTurn]:
        latest: dict[str, ChatTurn] = {}
        for scope in scopes:
            turns = await self.list_turns(scope)
            if turns:
                latest[scope.session_id] = turns[-1]
        return latest

    def _claim_owner(self, scope: ChatMemoryScope) -> None:
        owner = (scope.tenant_id, scope.user_id)
        existing = self._owners.setdefault(scope.session_id, owner)
        if existing != owner:
            raise ValueError("chat session was not found for its history scope")

    def _is_owner(self, scope: ChatMemoryScope) -> bool:
        return self._owners.get(scope.session_id) == (scope.tenant_id, scope.user_id)

    @staticmethod
    def _validate_scope(scope: ChatMemoryScope, turn: ChatTurn) -> None:
        if turn.session_id != scope.session_id:
            raise ValueError("chat turn session must match its history scope")


class InMemoryRunRepository:
    def __init__(self) -> None:
        self.runs: dict[str, DigestRun] = {}
        self._idempotency: dict[tuple[str, str], str] = {}

    async def create(self, run: DigestRun) -> tuple[DigestRun, bool]:
        key = (run.user_id, run.idempotency_key)
        existing_id = self._idempotency.get(key)
        if existing_id:
            return self.runs[existing_id], False
        self.runs[run.id], self._idempotency[key] = run, run.id
        return run, True

    async def get(self, run_id: str) -> DigestRun | None:
        return self.runs.get(run_id)

    async def list_recent(
        self, *, user_id: str, mailbox_connection_id: str, limit: int
    ) -> tuple[DigestRun, ...]:
        matching = (
            run
            for run in self.runs.values()
            if run.user_id == user_id and run.mailbox_connection_id == mailbox_connection_id
        )
        return tuple(
            sorted(
                matching,
                key=lambda run: (
                    float("-inf") if run.created_at is None else run.created_at.timestamp(),
                    run.id,
                ),
                reverse=True,
            )[:limit]
        )

    async def claim(self, run_id: str, started_at: datetime) -> DigestRun | None:
        run = self.runs.get(run_id)
        if run is None or run.status is not RunStatus.QUEUED:
            return None
        run.status, run.started_at = RunStatus.RUNNING, started_at
        return run

    async def save(self, run: DigestRun) -> None:
        self.runs[run.id] = run

    async def list_stuck_runs(
        self, *, running_before: datetime, queued_before: datetime
    ) -> tuple[DigestRun, ...]:
        stuck: list[DigestRun] = []
        for run in self.runs.values():
            if (
                run.status is RunStatus.RUNNING
                and run.started_at is not None
                and run.started_at < running_before
            ) or (
                run.status is RunStatus.QUEUED
                and run.created_at is not None
                and run.created_at < queued_before
            ):
                stuck.append(run)
        return tuple(stuck)

    async def reset_stuck_run(self, run_id: str, *, started_before: datetime) -> bool:
        run = self.runs.get(run_id)
        if (
            run is None
            or run.status is not RunStatus.RUNNING
            or run.started_at is None
            or run.started_at >= started_before
        ):
            return False
        run.status, run.started_at = RunStatus.QUEUED, None
        return True


class InMemoryResultRepository:
    def __init__(self) -> None:
        self.warnings: dict[str, list[AttachmentWarning]] = {}
        self.processed_emails: dict[str, list[ProcessedEmail]] = {}

    async def save_warning(self, run_id: str, warning: AttachmentWarning) -> None:
        self.warnings.setdefault(run_id, []).append(warning)

    async def list_warnings(self, run_id: str) -> Sequence[AttachmentWarning]:
        return tuple(self.warnings.get(run_id, ()))

    async def save_processed_emails(self, run_id: str, emails: Sequence[ProcessedEmail]) -> None:
        self.processed_emails[run_id] = list(emails)

    async def list_processed_emails(self, run_id: str) -> Sequence[ProcessedEmail]:
        return tuple(self.processed_emails.get(run_id, ()))


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[tuple[str, str, str, str], PersistedTask] = {}
        self.run_links: dict[str, dict[tuple[str, str, str, str], ActionFreshness]] = {}

    async def save_task(
        self,
        record: PersistedTask,
        *,
        tenant_id: str,
        user_id: str,
        pipeline_version: str,
        run_id: str,
    ) -> None:
        key = (tenant_id, user_id, record.task.gmail_message_id, pipeline_version)
        seen = any(
            stored.pointer.mailbox_connection_id == record.pointer.mailbox_connection_id
            and stored.fingerprint == record.fingerprint
            for stored in self.tasks.values()
        )
        freshness = ActionFreshness.SEEN if seen else ActionFreshness.NEW
        self.tasks[key] = replace(record, freshness=freshness)
        self.run_links.setdefault(run_id, {})[key] = freshness

    async def list_for_run(self, run_id: str) -> Sequence[PersistedTask]:
        links = self.run_links.get(run_id, {})
        # Dict insertion order preserves the deterministic save order, which
        # the mapper's stable sort relies on for equal-key ties.
        return tuple(
            replace(self.tasks[key], freshness=freshness)
            for key, freshness in links.items()
            if key in self.tasks
        )
