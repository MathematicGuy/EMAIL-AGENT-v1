"""In-memory repositories for local execution and deterministic tests."""

from collections.abc import Sequence
from datetime import datetime

from cowork_agent.domain import (
    ActionItem,
    AttachmentWarning,
    DigestRun,
    ProcessedEmail,
    RunStatus,
)
from cowork_agent.domain.target_contracts import Task


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

    async def claim(self, run_id: str, started_at: datetime) -> DigestRun | None:
        run = self.runs.get(run_id)
        if run is None or run.status is not RunStatus.QUEUED:
            return None
        run.status, run.started_at = RunStatus.RUNNING, started_at
        return run

    async def save(self, run: DigestRun) -> None:
        self.runs[run.id] = run


class InMemoryResultRepository:
    def __init__(self) -> None:
        self.items: dict[str, list[ActionItem]] = {}
        self.warnings: dict[str, list[AttachmentWarning]] = {}
        self.processed_emails: dict[str, list[ProcessedEmail]] = {}

    async def save_items(self, run_id: str, items: Sequence[ActionItem]) -> None:
        self.items[run_id] = list(items)

    async def list_items(self, run_id: str) -> Sequence[ActionItem]:
        return tuple(self.items.get(run_id, ()))

    async def save_warning(self, run_id: str, warning: AttachmentWarning) -> None:
        self.warnings.setdefault(run_id, []).append(warning)

    async def list_warnings(self, run_id: str) -> Sequence[AttachmentWarning]:
        return tuple(self.warnings.get(run_id, ()))

    async def fingerprint_seen(self, mailbox_id: str, fingerprint: str) -> bool:
        return any(
            item.mailbox_connection_id == mailbox_id and item.fingerprint == fingerprint
            for values in self.items.values()
            for item in values
        )

    async def save_processed_emails(self, run_id: str, emails: Sequence[ProcessedEmail]) -> None:
        self.processed_emails[run_id] = list(emails)

    async def list_processed_emails(self, run_id: str) -> Sequence[ProcessedEmail]:
        return tuple(self.processed_emails.get(run_id, ()))


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[tuple[str, str, str, str], Task] = {}

    async def save_task(
        self, *, tenant_id: str, user_id: str, pipeline_version: str, task: Task
    ) -> None:
        key = (tenant_id, user_id, task.gmail_message_id, pipeline_version)
        self.tasks[key] = task

    async def list_for_run(self, run_id: str) -> Sequence[Task]:
        return tuple(
            sorted(
                (task for task in self.tasks.values() if task.run_id == run_id),
                key=lambda task: (task.created_at, task.task_id),
            )
        )
