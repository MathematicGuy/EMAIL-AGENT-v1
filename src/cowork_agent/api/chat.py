"""FastAPI adapter for V2-M4A chat sessions and typed SSE events."""

import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from langfuse import observe
from pydantic import BaseModel, ConfigDict, Field

from cowork_agent.composition import ChatRuntime, ControlPlane, runtime
from cowork_agent.domain.chat_contracts import (
    MAX_CHAT_ACTIVITIES,
    ChatActivity,
    ChatActivityCode,
    ChatActivityDetail,
    ChatActivityOutcome,
    ChatActivityStatus,
    ChatMemoryScope,
    ChatMessageRequest,
    ChatMessageStreamEvent,
    ChatTurn,
    ChatTurnStatus,
    DeclarativeProfile,
    MailScanSummary,
    MemoryNamespace,
    MemoryProvenance,
    MemoryProvenanceSource,
    MemoryType,
    TaskEpisode,
)
from cowork_agent.features.ai_chat.controller import (
    ChatController,
    ChatSessionAccessDenied,
    ChatSessionRegistryPort,
)
from cowork_agent.features.ai_chat.memory_gateway import MemorySourceUnavailableError
from cowork_agent.features.ai_chat.ports import (
    ChatHistoryPort,
    ChatSessionBufferPort,
    DeclarativeMemoryPort,
    EpisodicMemoryPort,
)
from cowork_agent.features.ai_chat.profile_policy import (
    ProfileWriteRejected,
    authorize_profile_write,
)
from cowork_agent.identity import VerifiedPrincipal
from cowork_agent.persistence.repositories.projects import Project, ProjectDocument

ControllerFactory = Callable[[ChatMemoryScope], ChatController]


def slim_listed_turn(
    payload: dict[str, object], *, include_content: bool
) -> dict[str, object]:
    """Drop ``rag_evidence.content`` from GET /messages unless the client asks."""
    if include_content:
        return payload
    evidence = payload.get("rag_evidence")
    if not isinstance(evidence, list):
        return payload
    slimmed: list[object] = []
    for item in evidence:
        if not isinstance(item, dict):
            slimmed.append(item)
            continue
        next_item = dict(item)
        next_item.pop("content", None)
        slimmed.append(next_item)
    return {**payload, "rag_evidence": slimmed}


async def load_owned_history(
    *,
    sessions: ChatSessionRegistryPort,
    history: ChatHistoryPort | None,
    buffer: ChatSessionBufferPort | None,
    principal: VerifiedPrincipal,
    session_id: str,
) -> tuple[ChatMemoryScope, tuple[ChatTurn, ...]]:
    """Load owned turns. Shared Postgres pools use one checkout for require + list."""
    session_pool = getattr(sessions, "_pool", None)
    history_pool = getattr(history, "_pool", None) if history is not None else None
    if session_pool is not None and session_pool is history_pool:
        async with cast(Any, session_pool).connection() as connection:
            scope = await sessions.require(
                session_id,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                connection=connection,
            )
            if history is None:
                return scope, ()
            turns = await history.list_turns(scope, connection=connection)
            return scope, turns
    scope = await sessions.require(
        session_id, tenant_id=principal.tenant_id, user_id=principal.user_id
    )
    namespace = MemoryNamespace(
        scope=scope,
        memory_type=MemoryType.SHORT_TERM,
        record_id=session_id,
        source_id=None,
    )
    if history is not None:
        return scope, await history.list_turns(scope)
    if buffer is None:
        return scope, ()
    return scope, buffer.read(namespace)


class _ChatMessagePayload(BaseModel):
    """Untrusted HTTP body kept separate from the pure chat contract."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    user_message: str
    idempotency_key: str = Field(min_length=1, max_length=128)
    document_ids: list[str] = []
    reasoning_mode: Literal["fast", "reasoning"] = "fast"


class _CreateSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None


class _CancelTurnPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)


class _MailScanPayload(BaseModel):
    """Aggregate-only @mail result; it deliberately accepts no email content."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["connecting", "queued", "running", "succeeded", "partial", "failed"]
    emails_matched: int = Field(ge=0)
    emails_processed: int = Field(ge=0)
    emails_to_process: int = Field(ge=0)
    action_items_count: int | None = Field(default=None, ge=0)


class _ActivityDetailPayload(BaseModel):
    """One aggregate-only UI detail; arbitrary labels are deliberately forbidden."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["documents_found", "emails_processed", "action_items_prepared"]
    current: int = Field(ge=0, le=100_000)
    total: int | None = Field(default=None, ge=0, le=100_000)


class _ActivitySnapshotPayload(BaseModel):
    """Desired lifecycle snapshot; the server owns all timestamps."""

    model_config = ConfigDict(extra="forbid")

    code: ChatActivityCode
    status: ChatActivityStatus
    outcome: ChatActivityOutcome | None = None
    detail: _ActivityDetailPayload | None = None


class _PersistMailScanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    user_message: str
    assistant_message: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    turn_status: Literal["generating", "completed", "failed", "cancelled"] = "completed"
    mail_scan: _MailScanPayload
    activities: list[_ActivitySnapshotPayload] = Field(
        default_factory=list, max_length=MAX_CHAT_ACTIVITIES
    )


class CanonicalProjectRepository(Protocol):
    async def default_project(self, principal: VerifiedPrincipal) -> Project: ...

    async def require_project(
        self, principal: VerifiedPrincipal, project_id: str
    ) -> Project | None: ...

    async def require_document(
        self, principal: VerifiedPrincipal, project_id: str, document_id: str
    ) -> ProjectDocument | None: ...


class _ChatProfilePayload(BaseModel):
    """Explicit preference edit; bounds mirror profile_policy (FR-05)."""

    model_config = ConfigDict(extra="forbid")

    language: str | None = None
    timezone: str | None = None
    assistant_persona: str | None = None
    response_tone: str | None = None


def create_chat_router() -> APIRouter:
    """Create the transport-only router; runtime dependencies live on the composed runtime."""

    router = APIRouter(prefix="/v1/cowork/chat", tags=["chat"])

    @router.post("/guest-session", status_code=204, response_model=None)
    async def create_guest_session(request: Request, response: Response) -> None:
        chat = runtime(request).chat
        issuer = chat.chat_guest_session_issuer if chat is not None else None
        if issuer is None:
            raise HTTPException(status_code=503, detail="Guest chat is unavailable")
        await issuer(request, response)

    @router.post("/sessions", status_code=201)
    async def create_session(
        request: Request, payload: _CreateSessionPayload | None = None
    ) -> dict[str, str]:
        principal = await _verified_principal(request)
        requested_project_id = payload.project_id if payload else None
        control_plane = runtime(request).control_plane
        project_repository = control_plane.project_repository if control_plane is not None else None
        if project_repository is None and requested_project_id is None:
            project_id = "default-project"
        elif project_repository is None:
            raise HTTPException(status_code=404, detail="Project not found")
        elif requested_project_id is None:
            project = await cast(CanonicalProjectRepository, project_repository).default_project(
                principal
            )
            project_id = project.id
        else:
            owned_project = await cast(
                CanonicalProjectRepository, project_repository
            ).require_project(principal, requested_project_id)
            if owned_project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            project_id = owned_project.id
        sessions = _sessions(request)
        if project_id == "default-project":
            scope = await sessions.create(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
        else:
            scope = await sessions.create(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                project_id=project_id,
            )
        controller = _controller_factory(request)(scope)
        _controllers(request)[scope.session_id] = controller
        response = {
            "session_id": scope.session_id,
            "feature": scope.feature,
        }
        if scope.project_id != "default-project":
            response["project_id"] = scope.project_id
        return response

    @router.post("/sessions/{session_id}/messages", response_class=StreamingResponse)
    @observe(name="api_chat_create_message")
    async def create_message(
        session_id: str,
        payload: _ChatMessagePayload,
        request: Request,
    ) -> Any:
        if payload.session_id != session_id:
            raise HTTPException(
                status_code=422,
                detail="Payload session_id must match the path session_id",
            )
        try:
            message = ChatMessageRequest.from_dict(payload.model_dump())
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid chat message") from exc
        principal = await _verified_principal(request)
        try:
            scope = await _require_session(request, principal, session_id)
        except ChatSessionAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        if message.document_ids:
            # The document gate spans three groups: the settings and routing
            # provider (chat) plus the vector plane (email-rag). An uncomposed
            # group degrades to the same 503 the old missing keys produced.
            chat = runtime(request).chat
            settings = chat.user_documents_settings if chat is not None else None
            if settings is None or not bool(getattr(settings, "enabled", False)):
                raise HTTPException(status_code=503, detail="User documents are disabled")
            email_rag = runtime(request).email_rag
            if (
                email_rag is None
                or email_rag.project_document_vectors is None
            ):
                raise HTTPException(
                    status_code=503,
                    detail="Project document retrieval unavailable",
                )
            if chat is None or chat.chat_routing_service is None:
                raise HTTPException(
                    status_code=503,
                    detail="Project document routing unavailable",
                )
            control_plane = runtime(request).control_plane
            repository = control_plane.project_repository if control_plane is not None else None
            if repository is None:
                raise HTTPException(status_code=404, detail="Document not found")
            for document_id in message.document_ids:
                if (
                    document := await cast(
                        CanonicalProjectRepository, repository
                    ).require_document(principal, scope.project_id, document_id)
                ) is None:
                    raise HTTPException(status_code=404, detail="Document not found")
                if document.status != "ready":
                    raise HTTPException(status_code=409, detail="document_not_ready")
        controller = _controllers(request).get(session_id)
        if controller is None:
            controller = _controller_factory(request)(scope)
            _controllers(request)[session_id] = controller
        return StreamingResponse(
            _sse_events(controller, message, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/sessions/{session_id}/mail-scans", status_code=201)
    async def persist_mail_scan(
        session_id: str, payload: _PersistMailScanPayload, request: Request
    ) -> dict[str, object]:
        """Upsert one aggregate-only @mail lifecycle without invoking AI Chat."""

        principal = await _verified_principal(request)
        try:
            scope = await _require_session(request, principal, session_id)
        except ChatSessionAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        now = datetime.now(UTC)
        idempotency_key = payload.idempotency_key or payload.turn_id
        turn_status = ChatTurnStatus(payload.turn_status)
        try:
            mail_scan = MailScanSummary.from_dict(payload.mail_scan.model_dump())
            _validate_mail_turn_scan_status(turn_status, mail_scan)
            desired_activities = tuple(payload.activities)
            activities = _merge_mail_activity_snapshot((), desired_activities, at=now)
            activities = _terminalize_mail_activities(activities, turn_status, at=now)
            turn = ChatTurn(
                turn_id=payload.turn_id,
                session_id=session_id,
                user_message=payload.user_message,
                assistant_message=payload.assistant_message,
                created_at=now,
                mail_scan=mail_scan,
                status=turn_status,
                idempotency_key=idempotency_key,
                activities=activities,
                completed_at=(now if turn_status is not ChatTurnStatus.GENERATING else None),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid mail scan result") from exc
        history = _history_repository(request)
        if history is not None:
            try:
                existing = await history.begin_turn(
                    scope,
                    turn,
                    idempotency_key=idempotency_key,
                    title="@mail",
                )
                turn = _merge_mail_turn(existing, turn, desired_activities, at=now)
                turn = await history.update_turn(scope, turn, title="@mail")
            except ValueError as exc:
                raise HTTPException(
                    status_code=409, detail="Invalid mail activity transition"
                ) from exc
        else:
            try:
                turn = _upsert_buffer_mail_turn(
                    _buffer(request), scope, turn, desired_activities, at=now
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=409, detail="Invalid mail activity transition"
                ) from exc
        return turn.to_dict()

    @router.get("/sessions")
    async def list_sessions(
        request: Request, project_id: str | None = Query(default=None)
    ) -> dict[str, object]:
        principal = await _verified_principal(request)
        if project_id is None:
            scopes = await _sessions(request).list_for(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
            )
        else:
            scopes = await _sessions(request).list_for(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                project_id=project_id,
            )
        history = _history_repository(request)
        titles = await history.titles_for(scopes) if history is not None else {}
        latest_turns = await history.latest_turns_for(scopes) if history is not None else {}
        return {
            "sessions": [
                _session_response(
                    scope,
                    title=titles.get(scope.session_id),
                    latest_turn=latest_turns.get(scope.session_id),
                )
                for scope in scopes
            ]
        }

    @router.get("/sessions/{session_id}/messages")
    async def list_messages(
        session_id: str,
        request: Request,
        include_content: bool = Query(False),
    ) -> dict[str, object]:
        principal = await _verified_principal(request)
        history = _history_repository(request)
        try:
            _scope, turns = await load_owned_history(
                sessions=_sessions(request),
                history=history,
                buffer=_buffer(request),
                principal=principal,
                session_id=session_id,
            )
        except ChatSessionAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        serialized = [
            slim_listed_turn(turn.to_dict(), include_content=include_content)
            for turn in turns
        ]
        return {"session_id": session_id, "turns": serialized}

    @router.post(
        "/sessions/{session_id}/turns/{turn_id}/cancel",
        status_code=204,
        response_model=None,
    )
    async def cancel_turn(session_id: str, turn_id: str, request: Request) -> None:
        controller = await _owned_controller(request, session_id)
        if not await controller.cancel_turn(turn_id):
            raise HTTPException(status_code=404, detail="Active chat turn not found")

    @router.post(
        "/sessions/{session_id}/turns/cancel",
        status_code=204,
        response_model=None,
    )
    async def cancel_turn_by_idempotency_key(
        session_id: str,
        payload: _CancelTurnPayload,
        request: Request,
    ) -> None:
        controller = await _owned_controller(request, session_id)
        if not await controller.cancel_turn_by_idempotency_key(payload.idempotency_key):
            raise HTTPException(status_code=404, detail="Active chat turn not found")

    @router.delete("/sessions/{session_id}", status_code=204, response_model=None)
    async def delete_session(session_id: str, request: Request) -> None:
        principal = await _verified_principal(request)
        try:
            scope = await _require_session(request, principal, session_id)
        except ChatSessionAccessDenied as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        deleted = await _sessions(request).delete(
            session_id, tenant_id=principal.tenant_id, user_id=principal.user_id
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat session not found")
        _buffer(request).clear(
            MemoryNamespace(
                scope=scope,
                memory_type=MemoryType.SHORT_TERM,
                record_id=session_id,
                source_id=None,
            )
        )
        _controllers(request).pop(session_id, None)

    @router.get("/episodes")
    async def list_episodes(request: Request) -> dict[str, object]:
        principal = await _verified_principal(request)
        repository = _episodic_repository(request)
        try:
            episodes = await repository.list_episodes(
                _user_namespace(principal, MemoryType.EPISODIC)
            )
        except MemorySourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc
        return {"episodes": [episode.to_dict() for episode in episodes]}

    @router.get("/profile")
    async def get_profile(request: Request) -> dict[str, object]:
        principal = await _verified_principal(request)
        repository = _profile_repository(request)
        try:
            profile = await repository.read_profile(
                _user_namespace(principal, MemoryType.LONG_TERM)
            )
        except MemorySourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc
        if profile is None:
            raise HTTPException(status_code=404, detail="Chat profile not found")
        return profile.to_dict()

    @router.post("/profile", status_code=201)
    async def create_profile(payload: _ChatProfilePayload, request: Request) -> dict[str, object]:
        return await _write_profile(payload, request)

    @router.put("/profile")
    async def update_profile(payload: _ChatProfilePayload, request: Request) -> dict[str, object]:
        return await _write_profile(payload, request)

    @router.delete("/profile", status_code=204, response_model=None)
    async def delete_profile(request: Request) -> None:
        principal = await _verified_principal(request)
        repository = _profile_repository(request)
        try:
            await repository.delete_profile(_user_namespace(principal, MemoryType.LONG_TERM))
        except MemorySourceUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc

    @router.post("/sessions/{session_id}/task-episodes/{episode_id}/approve")
    async def approve_task_episode(
        session_id: str, episode_id: str, request: Request
    ) -> dict[str, object]:
        return await _task_episode_action(request, session_id, episode_id, "approve")

    @router.post("/sessions/{session_id}/task-episodes/{episode_id}/complete")
    async def complete_task_episode(
        session_id: str, episode_id: str, request: Request
    ) -> dict[str, object]:
        return await _task_episode_action(request, session_id, episode_id, "complete")

    @router.post("/sessions/{session_id}/task-episodes/{episode_id}/reject")
    async def reject_task_episode(
        session_id: str, episode_id: str, request: Request
    ) -> dict[str, object]:
        return await _task_episode_action(request, session_id, episode_id, "reject")

    @router.delete(
        "/sessions/{session_id}/task-episodes/{episode_id}",
        status_code=204,
        response_model=None,
    )
    async def delete_task_episode(session_id: str, episode_id: str, request: Request) -> None:
        controller = await _owned_controller(request, session_id)
        if not await controller.delete_task_episode(episode_id):
            raise HTTPException(status_code=404, detail="Chat task episode not found")

    return router


async def _sse_events(
    controller: ChatController,
    payload: ChatMessageRequest,
    request: Request,
) -> AsyncIterator[str]:
    async for event in controller.stream_message(
        payload,
        is_cancelled=request.is_disconnected,
    ):
        yield _serialize_sse(event)


def _serialize_sse(event: ChatMessageStreamEvent) -> str:
    data = json.dumps(
        event.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.event_id}\nevent: {event.event_type.value}\ndata: {data}\n\n"


_MAIL_ACTIVITY_CODES = frozenset(
    {
        ChatActivityCode.CHECKING_MAIL,
        ChatActivityCode.PROCESSING_EMAIL,
        ChatActivityCode.PREPARING_MAIL_RESULTS,
    }
)


def _validate_mail_turn_scan_status(
    turn_status: ChatTurnStatus, mail_scan: MailScanSummary
) -> None:
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


def _activity_detail(payload: _ActivityDetailPayload | None) -> ChatActivityDetail | None:
    if payload is None:
        return None
    return ChatActivityDetail(
        kind=payload.kind,
        current=payload.current,
        total=payload.total,
    )


def _transition_to_desired_activity(
    activity: ChatActivity,
    desired: _ActivitySnapshotPayload,
    *,
    at: datetime,
) -> ChatActivity:
    detail = _activity_detail(desired.detail)
    if (
        desired.outcome is not None
        and desired.status is not ChatActivityStatus.COMPLETED
    ):
        raise ValueError("activity outcome requires completed status")
    if activity.status is desired.status:
        if activity.status is ChatActivityStatus.PENDING:
            if desired.outcome is not None or detail is not None:
                raise ValueError("pending mail activity cannot carry results")
            return activity
        return replace(
            activity,
            detail=detail if detail is not None else activity.detail,
            outcome=(
                desired.outcome
                if activity.status is ChatActivityStatus.COMPLETED
                else None
            ),
        )
    if activity.status not in {ChatActivityStatus.PENDING, ChatActivityStatus.RUNNING}:
        raise ValueError("terminal mail activity cannot regress")
    if (
        activity.status is ChatActivityStatus.PENDING
        and desired.status in {ChatActivityStatus.COMPLETED, ChatActivityStatus.FAILED}
    ):
        activity = activity.transition(ChatActivityStatus.RUNNING, at=at)
    return activity.transition(
        desired.status,
        at=at,
        outcome=desired.outcome,
        detail=detail,
    )


def _merge_mail_activity_snapshot(
    existing: tuple[ChatActivity, ...],
    desired: tuple[_ActivitySnapshotPayload, ...],
    *,
    at: datetime,
) -> tuple[ChatActivity, ...]:
    if not desired:
        return existing
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
        activity = (
            existing[index]
            if index < len(existing)
            else ChatActivity.pending(item.code)
        )
        merged.append(_transition_to_desired_activity(activity, item, at=at))
    return tuple(merged)


def _merge_mail_turn(
    existing: ChatTurn,
    incoming: ChatTurn,
    desired_activities: tuple[_ActivitySnapshotPayload, ...],
    *,
    at: datetime,
) -> ChatTurn:
    if existing.user_message != incoming.user_message:
        raise ValueError("idempotency key was already used for another mail request")
    if existing.idempotency_key not in {None, incoming.idempotency_key}:
        raise ValueError("mail turn idempotency key cannot change")
    if existing.status is not ChatTurnStatus.GENERATING and incoming.status is not existing.status:
        raise ValueError("terminal mail turn cannot regress")
    if existing.status is not ChatTurnStatus.GENERATING:
        return existing
    if (
        existing.status is ChatTurnStatus.GENERATING
        and incoming.status
        not in {
            ChatTurnStatus.GENERATING,
            ChatTurnStatus.COMPLETED,
            ChatTurnStatus.FAILED,
            ChatTurnStatus.CANCELLED,
        }
    ):
        raise ValueError("unsupported mail turn transition")
    terminal = incoming.status is not ChatTurnStatus.GENERATING
    activities = _merge_mail_activity_snapshot(
        existing.activities, desired_activities, at=at
    )
    activities = _terminalize_mail_activities(activities, incoming.status, at=at)
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


def _upsert_buffer_mail_turn(
    buffer: ChatSessionBufferPort,
    scope: ChatMemoryScope,
    incoming: ChatTurn,
    desired_activities: tuple[_ActivitySnapshotPayload, ...],
    *,
    at: datetime,
) -> ChatTurn:
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
            if turn.turn_id == incoming.turn_id
            or turn.idempotency_key == incoming.idempotency_key
        ),
        None,
    )
    if index is None:
        stored = incoming
        turns.append(stored)
    else:
        stored = _merge_mail_turn(turns[index], incoming, desired_activities, at=at)
        turns[index] = stored
    buffer.clear(namespace)
    for turn in turns:
        buffer.append(namespace, turn)
    return stored


async def _verified_principal(request: Request) -> VerifiedPrincipal:
    chat = runtime(request).chat
    resolver = chat.chat_principal_resolver if chat is not None else None
    if resolver is None:
        raise HTTPException(status_code=503, detail="Chat identity is unavailable")
    principal = await resolver(request)
    if not isinstance(principal, VerifiedPrincipal):
        raise HTTPException(status_code=503, detail="Chat identity is unavailable")
    return principal


async def _owned_controller(request: Request, session_id: str) -> ChatController:
    principal = await _verified_principal(request)
    try:
        scope = await _require_session(request, principal, session_id)
    except ChatSessionAccessDenied as exc:
        raise HTTPException(status_code=404, detail="Chat session not found") from exc
    controller = _controllers(request).get(session_id)
    if controller is None:
        controller = _controller_factory(request)(scope)
        _controllers(request)[session_id] = controller
    return controller


async def _task_episode_action(
    request: Request, session_id: str, episode_id: str, action: str
) -> dict[str, object]:
    controller = await _owned_controller(request, session_id)
    operation = {
        "approve": controller.approve_task_episode,
        "complete": controller.complete_task_episode,
        "reject": controller.reject_task_episode,
    }[action]
    episode = await operation(episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Chat task episode not found")
    return _task_episode_response(episode)


def _task_episode_response(episode: TaskEpisode) -> dict[str, object]:
    return {
        "episode_id": episode.episode_id,
        "validation_status": episode.validation_status.value,
        "retrieval_eligible": episode.retrieval_eligible,
    }


async def _write_profile(payload: _ChatProfilePayload, request: Request) -> dict[str, object]:
    principal = await _verified_principal(request)
    repository = _profile_repository(request)
    namespace = _user_namespace(principal, MemoryType.LONG_TERM)
    try:
        existing = await repository.read_profile(namespace)
    except MemorySourceUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc
    now = datetime.now(UTC)
    profile = DeclarativeProfile(
        profile_id=existing.profile_id if existing else f"prof_{uuid.uuid4().hex}",
        user_id=principal.user_id,
        language=payload.language,
        timezone=payload.timezone,
        assistant_persona=payload.assistant_persona,
        response_tone=payload.response_tone,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    provenance = MemoryProvenance(
        source_type=MemoryProvenanceSource.EXPLICIT_USER_CONFIG,
        source_id="demo-profile-editor",
        chat_turn_id=None,
        pipeline_version=None,
        model_id=None,
        prompt_version=None,
    )
    try:
        authorize_profile_write(namespace, profile, provenance)
    except ProfileWriteRejected as exc:
        raise HTTPException(status_code=422, detail="Invalid profile preference") from exc
    try:
        stored = await repository.write_profile(namespace, profile)
    except MemorySourceUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Chat memory store unavailable") from exc
    return stored.to_dict()


def _user_namespace(principal: VerifiedPrincipal, memory_type: MemoryType) -> MemoryNamespace:
    # User-level administration namespace: storage keys are user/session/feature,
    # so the session component is a stable placeholder, not a live chat session.
    return MemoryNamespace(
        scope=ChatMemoryScope(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id="memory-admin",
        ),
        memory_type=memory_type,
        record_id=None,
        source_id=None,
    )


def _chat_group(request: Request) -> ChatRuntime:
    # Typed read through the runtime seam (ADR-013). The old direct attribute
    # reads crashed on a missing key; an uncomposed group fails just as loudly.
    chat = runtime(request).chat
    if chat is None:
        raise RuntimeError("the chat group is not composed")
    return chat


def _control_plane(request: Request) -> ControlPlane | None:
    return runtime(request).control_plane


def _buffer(request: Request) -> ChatSessionBufferPort:
    return _chat_group(request).chat_session_buffer


def _profile_repository(request: Request) -> DeclarativeMemoryPort:
    control_plane = _control_plane(request)
    repository = control_plane.chat_profile_repository if control_plane is not None else None
    if repository is None:
        raise HTTPException(status_code=503, detail="Chat memory store unavailable")
    return repository


def _episodic_repository(request: Request) -> EpisodicMemoryPort:
    control_plane = _control_plane(request)
    repository = (
        control_plane.chat_task_episode_repository if control_plane is not None else None
    )
    if repository is None:
        raise HTTPException(status_code=503, detail="Chat memory store unavailable")
    return repository  # type: ignore[return-value]


async def _require_session(
    request: Request, principal: VerifiedPrincipal, session_id: str
) -> ChatMemoryScope:
    return await _sessions(request).require(
        session_id, tenant_id=principal.tenant_id, user_id=principal.user_id
    )


def _controllers(request: Request) -> dict[str, ChatController]:
    # Request-time memoization cache. Deliberately NOT on the frozen runtime:
    # a frozen dataclass cannot hold a mutable per-request cache (ADR-013),
    # so this one write stays on ``app.state`` until a proper seam exists.
    controllers = getattr(request.app.state, "chat_controllers", None)
    if controllers is None:
        controllers = {}
        request.app.state.chat_controllers = controllers
    return cast(dict[str, ChatController], controllers)


def _sessions(request: Request) -> ChatSessionRegistryPort:
    return _chat_group(request).chat_sessions


def _controller_factory(request: Request) -> ControllerFactory:
    # A documented ``app.state`` survivor (ADR-013, slice 02-8): the factory
    # is published once after the single runtime assembly and reads the
    # composed runtime at controller-creation time, so it is not a group
    # field — this cache's request-time readers reach it here.
    return cast(ControllerFactory, request.app.state.chat_controller_factory)


def _session_response(
    scope: ChatMemoryScope,
    *,
    title: str | None = None,
    latest_turn: ChatTurn | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"session_id": scope.session_id, "feature": scope.feature}
    if scope.project_id != "default-project":
        payload["project_id"] = scope.project_id
    if title is not None:
        payload["title"] = title
    if latest_turn is not None:
        payload["latest_turn_status"] = latest_turn.status.value
        payload["latest_turn_id"] = latest_turn.turn_id
        if latest_turn.idempotency_key is not None:
            payload["latest_turn_idempotency_key"] = latest_turn.idempotency_key
        if latest_turn.error_code is not None:
            payload["latest_turn_error_code"] = latest_turn.error_code
    return payload


def _history_repository(request: Request) -> ChatHistoryPort | None:
    control_plane = _control_plane(request)
    repository = control_plane.chat_history_repository if control_plane is not None else None
    return cast(ChatHistoryPort | None, repository)


__all__ = ["create_chat_router"]
