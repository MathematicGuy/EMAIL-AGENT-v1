"""Framework-neutral handlers matching the documented HTTP contract."""

from dataclasses import asdict
from enum import Enum
from typing import Any

from mail_todo.application.services import CreateDigestRun, GetDigestResult
from mail_todo.domain import DigestRun


class MailTodoApi:
    def __init__(self, create_run: CreateDigestRun, get_result: GetDigestResult) -> None:
        self._create_run, self._get_result = create_run, get_result

    async def post_run(
        self,
        *,
        user_id: str,
        mailbox_connection_id: str,
        idempotency_key: str,
        query: str | None = None,
        max_emails: int = 200,
    ) -> tuple[int, dict[str, Any]]:
        run = await self._create_run.execute(
            user_id=user_id,
            mailbox_connection_id=mailbox_connection_id,
            idempotency_key=idempotency_key,
            query=query,
            max_emails=max_emails,
        )
        return 202, {
            "id": run.id,
            "status": run.status.value,
            "statusUrl": f"/v1/mail-todo/runs/{run.id}",
        }

    async def get_run(self, run: DigestRun) -> tuple[int, dict[str, Any]]:
        error = (
            {"code": run.error_code, "message": run.error_message_safe} if run.error_code else None
        )
        return 200, {
            "id": run.id,
            "status": run.status.value,
            "progress": {
                "emailsMatched": run.emails_matched,
                "emailsProcessed": run.emails_processed,
            },
            "error": error,
        }

    async def get_result(self, run_id: str) -> tuple[int, dict[str, Any]]:
        return 200, _jsonable(await self._get_result.execute(run_id))


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
