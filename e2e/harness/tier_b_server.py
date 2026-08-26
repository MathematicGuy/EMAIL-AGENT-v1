"""Tier B verification server: the real app, with only Google's network faked.

What this is for
----------------
Proving that a chat prompt reaches Google Calendar end to end, without a real
Google grant. Everything between the browser and `googleapiclient` runs
untouched: the intent router, `finalize_route`, the per-user binder in
`app._chat_tool_runner`, `fill_arguments`, the range guards, `event_body`, the
409-as-success idempotency branch, the SQLite calendar repository, and the
Fernet cipher. Only the HTTP call to Google is replaced.

Two seams, and nothing else
---------------------------
1. `GoogleCalendar(settings, service=...)` already accepts an injected Google
   API service. We inject a fake one, so `_insert`, `_existing_link` and the
   409 branch are the production code paths, not stubs.
2. The OAuth handshake is replaced by a seeding endpoint under `/__testing__`,
   which writes a real `CalendarConnection` row for the *caller's own*
   principal. That is the one thing a browser cannot do without a Google
   client, and it is deliberately per-caller so J1 (each turn writes through
   its own user's grant) is still under test rather than assumed.

The recorder stores a fingerprint of the refresh token each event was written
with. That is the evidence for J1: two users must produce two fingerprints.

Never point this at a real database. It forces `POSTGRES_MODE=off`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Forced before anything imports settings. `load_runtime_environment` uses
#: `load_dotenv(override=False)`, so a value set here beats `.env` -- which is
#: what keeps this harness off the Supabase URL that `.env` carries.
ENV: dict[str, str] = {
    "POSTGRES_MODE": "off",
    # The axis flag is named for the plane it shipped with, not for tools.
    # `CHAT_TOOL_AXIS_ENABLED` looks right and does nothing.
    "USER_DOCUMENTS_TOOL_AXIS_ENABLED": "true",
    "GOOGLE_CALENDAR_ENABLED": "true",
    # Any non-empty triple satisfies `GoogleCalendarOAuthSettings.from_env`.
    # The handshake never runs here, so these are placeholders by design; a
    # real client id would be a secret in a file that does not need one.
    "GOOGLE_CALENDAR_CLIENT_ID": "tier-b.apps.googleusercontent.com",
    "GOOGLE_CALENDAR_CLIENT_SECRET": "tier-b-placeholder-not-a-secret",
    "GOOGLE_CALENDAR_REDIRECT_URI": "http://127.0.0.1:8123/v1/calendar/oauth/google/callback",
    # Present so `GoogleCalendarSettings.from_env` composes. A turn that used
    # it would be a J2 failure, and the recorder would show the fingerprint.
    "GOOGLE_CALENDAR_REFRESH_TOKEN": "tier-b-environment-token-never-expected",
    "GOOGLE_CALENDAR_TIMEZONE": "Asia/Ho_Chi_Minh",
    "FRONTEND_URL": "http://127.0.0.1:5173",
}

EVENT_LOG = REPO_ROOT / "test-results" / "tier-b-calendar-events.jsonl"


def fingerprint(token: str) -> str:
    """A stable, non-reversible label for "which grant wrote this"."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


class Recorder:
    """Every event the fake Google accepted, in order."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []
        self.store: dict[tuple[str, str], dict[str, Any]] = {}
        self.conflicts = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def record(self, calendar_id: str, body: dict[str, Any], link: str, token: str) -> None:
        entry = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "calendar_id": calendar_id,
            "event_id": body.get("id"),
            "summary": body.get("summary"),
            "start": body.get("start"),
            "end": body.get("end"),
            "description": body.get("description"),
            "html_link": link,
            "grant_fingerprint": fingerprint(token),
        }
        self.events.append(entry)
        self.store[(calendar_id, str(body.get("id")))] = {"link": link, "status": "confirmed"}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def reset(self) -> None:
        self.events.clear()
        self.store.clear()
        self.conflicts = 0
        self.path.write_text("", encoding="utf-8")


RECORDER = Recorder(EVENT_LOG)


# --- the fake Google service -------------------------------------------------


class _Response:
    """The two attributes `googleapiclient`'s error path reads off a response."""

    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason

    def get(self, key: str, default: Any = None) -> Any:
        return {"status": self.status, "reason": self.reason}.get(key, default)


class _Deferred:
    def __init__(self, run: Any) -> None:
        self._run = run

    def execute(self) -> Any:
        return self._run()


class _Events:
    def __init__(self, recorder: Recorder, refresh_token: str) -> None:
        self._recorder = recorder
        self._token = refresh_token

    def insert(self, calendarId: str, body: dict[str, Any]) -> _Deferred:  # noqa: N803
        def run() -> dict[str, Any]:
            from googleapiclient.errors import HttpError

            key = (calendarId, str(body.get("id")))
            if key in self._recorder.store:
                # The real conflict Google returns for a re-used event id. The
                # 409-as-success branch in `GoogleCalendar._insert` is what we
                # want exercised, so raise rather than short-circuit.
                self._recorder.conflicts += 1
                raise HttpError(
                    resp=_Response(409, "Conflict"),
                    content=b'{"error": {"message": "The requested identifier already exists."}}',
                )
            link = f"https://calendar.google.com/calendar/event?eid={body.get('id')}"
            self._recorder.record(calendarId, body, link, self._token)
            return {"htmlLink": link, "status": "confirmed", "id": body.get("id")}

        return _Deferred(run)

    def get(self, calendarId: str, eventId: str) -> _Deferred:  # noqa: N803
        def run() -> dict[str, Any]:
            from googleapiclient.errors import HttpError

            existing = self._recorder.store.get((calendarId, eventId))
            if existing is None:
                raise HttpError(
                    resp=_Response(404, "Not Found"),
                    content=b'{"error": {"message": "Not Found"}}',
                )
            return {"htmlLink": existing["link"], "status": existing["status"]}

        return _Deferred(run)


class _Service:
    def __init__(self, recorder: Recorder, refresh_token: str) -> None:
        self._events = _Events(recorder, refresh_token)

    def events(self) -> _Events:
        return self._events


# --- the /__testing__ plane --------------------------------------------------


def _testing_router() -> Any:
    from cowork_agent.api.dependencies import authenticated_chat_principal
    from cowork_agent.composition import runtime
    from cowork_agent.domain import CalendarConnection
    from cowork_agent.integrations.google_calendar.provider import CALENDAR_SCOPE

    router = APIRouter(prefix="/__testing__", tags=["tier-b"])

    @router.post("/calendar-grant")
    async def seed_grant(request: Request) -> dict[str, Any]:
        """Stand in for the OAuth callback, for the caller's own principal.

        Deliberately not "seed a grant for user X": the caller's cookie decides
        whose row this is, so a spec cannot accidentally prove J1 by writing
        the grant it later claims to have resolved.
        """

        principal = await authenticated_chat_principal(request, required=True)
        if principal is None:  # pragma: no cover - required=True raises first
            raise HTTPException(status_code=401, detail="No chat session")
        plane = runtime(request).calendar
        if plane is None:
            raise HTTPException(status_code=503, detail="Calendar plane is not composed")
        payload: dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - an empty body is the common case
            payload = {}
        token = str(payload.get("refresh_token") or f"tier-b-grant-{principal.user_id}")
        calendar_id = str(payload.get("calendar_id") or "primary")
        now = datetime.now(UTC)
        existing = await plane.repository.get_for_user(principal.user_id)
        await plane.repository.upsert(
            CalendarConnection(
                id=existing.id if existing else f"tier-b-{fingerprint(principal.user_id)}",
                user_id=principal.user_id,
                provider="google_calendar",
                external_account_id=str(
                    payload.get("account") or f"{fingerprint(principal.user_id)}@tier-b.invalid"
                ),
                calendar_id=calendar_id,
                encrypted_refresh_token=plane.cipher.encrypt(token),
                scopes=(CALENDAR_SCOPE,),
                timezone=str(payload.get("timezone") or ENV["GOOGLE_CALENDAR_TIMEZONE"]),
                status="active",
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
        )
        return {
            "user_id": principal.user_id,
            "calendar_id": calendar_id,
            "grant_fingerprint": fingerprint(token),
        }

    @router.delete("/calendar-grant", status_code=204)
    async def drop_grant(request: Request) -> None:
        principal = await authenticated_chat_principal(request, required=True)
        plane = runtime(request).calendar
        if principal is not None and plane is not None:
            await plane.repository.delete_for_user(principal.user_id)

    @router.get("/events")
    async def read_events() -> dict[str, Any]:
        return {
            "count": len(RECORDER.events),
            "conflicts": RECORDER.conflicts,
            "events": RECORDER.events,
        }

    @router.delete("/events", status_code=204)
    async def clear_events() -> None:
        RECORDER.reset()

    @router.get("/whoami")
    async def whoami(request: Request) -> dict[str, Any]:
        principal = await authenticated_chat_principal(request, required=False)
        plane = runtime(request).calendar
        connection = (
            await plane.repository.get_for_user(principal.user_id)
            if plane is not None and principal is not None
            else None
        )
        chat = runtime(request).chat
        runner = chat.chat_tool_runner if chat is not None else None
        return {
            "user_id": principal.user_id if principal else None,
            "has_calendar_grant": connection is not None,
            "composed_tools": sorted(runner.names) if runner is not None else [],
        }

    return router


def build() -> Any:
    """The real app, with the two seams swapped and `/__testing__` mounted."""

    for key, value in ENV.items():
        os.environ[key] = value
    sys.path.insert(0, str(REPO_ROOT / "src"))

    from cowork_agent import app as app_module
    from cowork_agent.integrations.google_calendar import GoogleCalendarSettings
    from cowork_agent.integrations.google_calendar.provider import GoogleCalendar

    def fake_calendar(settings: GoogleCalendarSettings) -> GoogleCalendar:
        return GoogleCalendar(settings, service=_Service(RECORDER, settings.refresh_token))

    app_module.GoogleCalendar = fake_calendar  # type: ignore[assignment]

    application = app_module.create_app()
    application.include_router(_testing_router())
    return application


def main() -> None:
    import uvicorn

    # Not 8000. A developer's own backend usually holds that port, and taking
    # it from them to run a test is not a trade this harness gets to make.
    port = int(os.environ.get("TIER_B_PORT", "8123"))
    os.chdir(REPO_ROOT)
    application = build()
    print(f"[tier-b] events -> {EVENT_LOG}", flush=True)
    print(f"[tier-b] listening on http://127.0.0.1:{port}", flush=True)
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
