"""FastAPI transport for mail-digest runs.

One run is created, polled, and then read twice — once in the legacy result
shape and once as the persisted §6.6 tasks. Every read re-verifies that the
caller still owns the run's mailbox connection: a run id is guessable and the
repository does not filter by principal, so `_ensure_run_connection_owned`
guards each of the three reads.

`/v1/conversations` rides along as the one surviving legacy stub. It returns an
empty page so old clients get a 200 rather than a 404.
"""

from __future__ import annotations

import os
from typing import Any, cast

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from cowork_agent.composition import runtime
from cowork_agent.domain import DigestRun
from cowork_agent.features.email_action_plan.policies import DEFAULT_QUERY
from cowork_agent.features.email_action_plan.workflow import DigestWorker

from .dependencies import (
    connection_principal,
    control_plane_required,
    owned_connection,
    require_owned_connection,
)
from .handlers import _jsonable


class CreateRunRequest(BaseModel):
    mailbox_connection_id: str = Field(alias="mailboxConnectionId")
    query: str = DEFAULT_QUERY
    max_emails: int = Field(default=10, alias="maxEmails", ge=1, le=500)


async def _ensure_run_connection_owned(request: Request, run: DigestRun, *, detail: str) -> None:
    """Verify Run → Mailbox Connection ownership integrity; 404 on any mismatch."""
    connection = await owned_connection(request, run.mailbox_connection_id, detail)
    principal = await connection_principal(request, connection)
    if principal.user_id != run.user_id:
        raise HTTPException(status_code=404, detail=detail)
    require_owned_connection(principal, connection, detail=detail)


def _digest_worker(request: Request) -> DigestWorker | None:
    email_rag = runtime(request).email_rag
    return email_rag.digest_worker if email_rag is not None else None


def _is_development() -> bool:
    return os.getenv("APP_ENV", "development").lower() in {"development", "dev", "local"}


def _run_history_item(run: DigestRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "mailboxConnectionId": run.mailbox_connection_id,
        "status": run.status.value,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None,
        "progress": {
            "emailsMatched": run.emails_matched,
            "emailsProcessed": run.emails_processed,
            "emailsToProcess": min(run.emails_matched, run.max_emails),
            "maxEmails": run.max_emails,
            "actionItemsCount": run.action_items_count,
        },
        "error": (
            {"code": run.error_code, "message": run.error_message_safe} if run.error_code else None
        ),
    }


def create_digest_router() -> APIRouter:
    """Mount digest-run creation, polling and result reads."""

    router = APIRouter(tags=["digest-runs"])

    @router.get("/v1/conversations")
    async def legacy_list_conversations() -> dict[str, list[object]]:
        return {"items": []}

    @router.get("/v1/mail-todo/runs")
    async def list_digest_runs(
        request: Request,
        mailbox_connection_id: str = Query(alias="mailboxConnectionId", min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        connection = await owned_connection(
            request, mailbox_connection_id, "Mailbox connection not found"
        )
        principal = await connection_principal(request, connection)
        repository = control_plane_required(request).run_repository
        runs = await repository.list_recent(
            user_id=principal.user_id,
            mailbox_connection_id=mailbox_connection_id,
            limit=limit,
        )
        return {"runs": [_run_history_item(run) for run in runs]}

    @router.post("/v1/mail-todo/runs", status_code=202)
    async def create_digest_run(
        payload: CreateRunRequest,
        request: Request,
        background_tasks: BackgroundTasks,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    ) -> dict[str, str]:
        connection = await owned_connection(
            request, payload.mailbox_connection_id, "Mailbox connection not found"
        )
        principal = await connection_principal(request, connection)
        worker = _digest_worker(request)
        if worker is None:
            email_rag = runtime(request).email_rag
            label = email_rag.llm_provider_label if email_rag is not None else "the LLM provider"
            error = (
                email_rag.llm_configuration_error
                if email_rag is not None
                else "the email-RAG group is not composed"
            )
            raise HTTPException(
                status_code=503,
                detail=f"{label} is not configured: {error}",
            )
        creator = control_plane_required(request).create_run
        run = await creator.execute(
            user_id=principal.user_id,
            mailbox_connection_id=payload.mailbox_connection_id,
            idempotency_key=idempotency_key,
            query=payload.query,
            max_emails=payload.max_emails,
        )
        control_plane = runtime(request).control_plane
        run_queue = control_plane.run_queue if control_plane is not None else None
        if run_queue is not None:
            await cast(Any, run_queue).enqueue_digest_run(run.id, user_id=principal.user_id)
        else:
            background_tasks.add_task(worker.execute, run.id)
        return {
            "id": run.id,
            "status": run.status.value,
            "statusUrl": f"/v1/mail-todo/runs/{run.id}",
        }

    @router.get("/v1/mail-todo/runs/{run_id}")
    async def get_digest_run(run_id: str, request: Request) -> dict[str, Any]:
        repository = control_plane_required(request).run_repository
        run = await repository.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Digest run not found")
        await _ensure_run_connection_owned(request, run, detail="Digest run not found")
        response: dict[str, Any] = {
            "id": run.id,
            "status": run.status.value,
            "progress": {
                "emailsMatched": run.emails_matched,
                "emailsProcessed": run.emails_processed,
                "emailsToProcess": min(run.emails_matched, run.max_emails),
                "maxEmails": run.max_emails,
                "filteredSummary": run.filtered_summary,
            },
            "error": (
                {"code": run.error_code, "message": run.error_message_safe}
                if run.error_code
                else None
            ),
        }
        if _is_development():
            results = control_plane_required(request).result_repository
            processed = await results.list_processed_emails(run_id)
            response["processedEmails"] = [
                {
                    "messageId": item.provider_message_id,
                    "threadId": item.provider_thread_id,
                    "subject": item.subject,
                    "sender": item.sender_address,
                    "receivedAt": item.received_at.isoformat(),
                }
                for item in processed
            ]
        return response

    @router.get("/v1/mail-todo/runs/{run_id}/result")
    async def get_digest_result(run_id: str, request: Request) -> dict[str, Any]:
        control_plane = control_plane_required(request)
        repository = control_plane.run_repository
        run = await repository.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Digest run not found")
        await _ensure_run_connection_owned(request, run, detail="Digest run not found")
        if run.status.value in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="RUN_NOT_COMPLETE")
        result_service = control_plane.get_result
        payload = cast(dict[str, Any], _jsonable(await result_service.execute(run_id)))
        if not _is_development():
            payload.pop("processedEmails", None)
        return payload

    @router.get("/v1/mail-todo/runs/{run_id}/tasks")
    async def get_digest_tasks(run_id: str, request: Request) -> dict[str, Any]:
        """Persisted §6.6 Tasks for presentation (T4.3): citations, missing
        information, and confidences that the legacy result shape drops."""
        control_plane = control_plane_required(request)
        repository = control_plane.run_repository
        run = await repository.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Digest run not found")
        await _ensure_run_connection_owned(request, run, detail="Digest run not found")
        if run.status.value in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="RUN_NOT_COMPLETE")
        task_repository = control_plane.task_repository
        records = await task_repository.list_for_run(run_id)
        return {"tasks": [record.task.to_dict() for record in records]}

    return router


__all__ = ["CreateRunRequest", "create_digest_router"]
