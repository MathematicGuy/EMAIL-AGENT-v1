"""Live CRUD smoke test for the Google Calendar tool plane.

Verifies the exact behaviours `tasks/specs/SPEC-chat-tools-registry.md` depends
on, against a real calendar. Every event it creates is prefixed and deleted
before exit, including on failure.

Credentials come from `.env` (gitignored):

    GOOGLE_CALENDAR_CLIENT_ID
    GOOGLE_CALENDAR_CLIENT_SECRET
    GOOGLE_CALENDAR_REFRESH_TOKEN
    GOOGLE_CALENDAR_ID          (default: primary)
    GOOGLE_CALENDAR_TIMEZONE    (default: Asia/Ho_Chi_Minh)

Run:

    uv run python scripts/smoke_test_google_calendar.py

Exit code 0 means every check passed. This talks to Google, so it is a manual
script, not part of `pytest`.
"""

from __future__ import annotations

import base64
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ("https://www.googleapis.com/auth/calendar",)
TEST_PREFIX = "[cowork-smoke]"

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


def section(title: str) -> None:
    print(f"\n--- {title} ---")


def google_event_id(seed: str) -> str:
    """Derive a valid Google event id from the chat turn's idempotency key.

    Google accepts base32hex only: lowercase `a`-`v` and `0`-`9`, 5-1024 chars.
    `w`, `x`, `y` and `z` are NOT allowed, so a literal prefix has to be checked
    against that alphabet too -- "cowork" is rejected because of its `w`.
    Anything outside the range returns 400 "Invalid resource id value."
    """
    digest = base64.b32hexencode(seed.encode()).decode().rstrip("=").lower()
    return f"coagent{digest}"[:1024]


def build_service() -> Any:
    load_dotenv()
    missing = [
        name
        for name in (
            "GOOGLE_CALENDAR_CLIENT_ID",
            "GOOGLE_CALENDAR_CLIENT_SECRET",
            "GOOGLE_CALENDAR_REFRESH_TOKEN",
        )
        if not os.environ.get(name)
    ]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")

    credentials = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_CALENDAR_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_CALENDAR_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CALENDAR_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=list(SCOPES),
    )
    credentials.refresh(Request())
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def main() -> int:
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    tz_name = os.environ.get("GOOGLE_CALENDAR_TIMEZONE", "Asia/Ho_Chi_Minh")
    tz = ZoneInfo(tz_name)
    created: list[str] = []

    section("auth")
    service = build_service()
    check("refresh token exchanged for an access token", True)

    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    meta = service.calendars().get(calendarId=calendar_id).execute()
    check("calendars().get reachable", bool(meta.get("id")), meta.get("id", ""))
    check(
        "calendar timezone matches GOOGLE_CALENDAR_TIMEZONE",
        meta.get("timeZone") == tz_name,
        f"calendar={meta.get('timeZone')} env={tz_name}",
    )

    run_id = uuid.uuid4().hex[:8]
    now_local = datetime.now(tz)

    try:
        # ------------------------------------------------------------------
        section("CREATE - timed event (the 'todo at a time' path)")
        start = (now_local + timedelta(days=1)).replace(
            hour=15, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(minutes=30)
        timed_id = google_event_id(f"turn-{run_id}-timed")
        body = {
            "id": timed_id,
            "summary": f"{TEST_PREFIX} timed {run_id}",
            "description": "created by scripts/smoke_test_google_calendar.py",
            "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
        }
        event = service.events().insert(calendarId=calendar_id, body=body).execute()
        created.append(event["id"])
        check("insert returns the client-supplied id", event["id"] == timed_id)
        check("insert returns htmlLink", bool(event.get("htmlLink")))
        check(
            "start round-trips in the requested timezone",
            event["start"]["dateTime"].startswith(start.strftime("%Y-%m-%dT%H:%M:%S")),
            event["start"]["dateTime"],
        )
        check("status is confirmed", event.get("status") == "confirmed")

        # ------------------------------------------------------------------
        section("CREATE - duplicate id (retry protection)")
        duplicate_rejected = False
        error_status = None
        try:
            service.events().insert(calendarId=calendar_id, body=body).execute()
        except HttpError as exc:
            duplicate_rejected = True
            error_status = exc.resp.status
        check(
            "re-inserting the same id is rejected, not duplicated",
            duplicate_rejected and error_status == 409,
            f"status={error_status}",
        )

        # ------------------------------------------------------------------
        section("CREATE - all-day event (the 'todo on a date' path)")
        day = (now_local + timedelta(days=2)).date()
        allday_id = google_event_id(f"turn-{run_id}-allday")
        allday = (
            service.events()
            .insert(
                calendarId=calendar_id,
                body={
                    "id": allday_id,
                    "summary": f"{TEST_PREFIX} all-day {run_id}",
                    "start": {"date": day.isoformat()},
                    "end": {"date": (day + timedelta(days=1)).isoformat()},
                },
            )
            .execute()
        )
        created.append(allday["id"])
        check(
            "all-day event accepts date-only bounds",
            allday["start"].get("date") == day.isoformat(),
        )
        check("all-day event has no dateTime", "dateTime" not in allday["start"])

        # ------------------------------------------------------------------
        section("READ - get and list")
        fetched = service.events().get(calendarId=calendar_id, eventId=timed_id).execute()
        check("get by id returns the event", fetched["id"] == timed_id)

        window_start = (now_local - timedelta(days=1)).astimezone(UTC)
        window_end = (now_local + timedelta(days=7)).astimezone(UTC)
        listed = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=window_start.isoformat().replace("+00:00", "Z"),
                timeMax=window_end.isoformat().replace("+00:00", "Z"),
                q=TEST_PREFIX,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        ids = {item["id"] for item in listed.get("items", ())}
        check(
            "list with time window finds both events",
            {timed_id, allday_id} <= ids,
            f"{len(ids)} hit(s)",
        )

        # ------------------------------------------------------------------
        section("UPDATE - patch")
        patched = (
            service.events()
            .patch(
                calendarId=calendar_id,
                eventId=timed_id,
                body={"summary": f"{TEST_PREFIX} timed {run_id} (updated)"},
            )
            .execute()
        )
        check("patch changes summary", patched["summary"].endswith("(updated)"))
        check(
            "patch leaves untouched fields intact",
            patched["start"]["dateTime"] == fetched["start"]["dateTime"],
        )

        moved_start = start + timedelta(hours=2)
        moved = (
            service.events()
            .patch(
                calendarId=calendar_id,
                eventId=timed_id,
                body={
                    "start": {"dateTime": moved_start.isoformat(), "timeZone": tz_name},
                    "end": {
                        "dateTime": (moved_start + timedelta(minutes=30)).isoformat(),
                        "timeZone": tz_name,
                    },
                },
            )
            .execute()
        )
        check(
            "patch can reschedule",
            moved["start"]["dateTime"].startswith(moved_start.strftime("%Y-%m-%dT%H:%M:%S")),
            moved["start"]["dateTime"],
        )

        # ------------------------------------------------------------------
        section("VALIDATION - what Google rejects vs. silently accepts")
        end_before_start = False
        try:
            service.events().insert(
                calendarId=calendar_id,
                body={
                    "summary": f"{TEST_PREFIX} invalid {run_id}",
                    "start": {"dateTime": end.isoformat(), "timeZone": tz_name},
                    "end": {"dateTime": start.isoformat(), "timeZone": tz_name},
                },
            ).execute()
        except HttpError:
            end_before_start = True
        check("end before start is rejected by the API", end_before_start)

        far_past_accepted = False
        past_id = None
        try:
            stale = (
                service.events()
                .insert(
                    calendarId=calendar_id,
                    body={
                        "summary": f"{TEST_PREFIX} past {run_id}",
                        "start": {
                            "dateTime": start.replace(year=start.year - 1).isoformat(),
                            "timeZone": tz_name,
                        },
                        "end": {
                            "dateTime": end.replace(year=end.year - 1).isoformat(),
                            "timeZone": tz_name,
                        },
                    },
                )
                .execute()
            )
            past_id = stale["id"]
            created.append(past_id)
            far_past_accepted = True
        except HttpError:
            pass
        check(
            "a year-in-the-past event is ACCEPTED (so we must validate it ourselves)",
            far_past_accepted,
        )

        naive_accepted = False
        try:
            naive = (
                service.events()
                .insert(
                    calendarId=calendar_id,
                    body={
                        "summary": f"{TEST_PREFIX} naive {run_id}",
                        "start": {
                            "dateTime": start.replace(tzinfo=None).isoformat(),
                            "timeZone": tz_name,
                        },
                        "end": {
                            "dateTime": end.replace(tzinfo=None).isoformat(),
                            "timeZone": tz_name,
                        },
                    },
                )
                .execute()
            )
            created.append(naive["id"])
            naive_accepted = True
        except HttpError as exc:
            print(f"        offset-less dateTime rejected: {exc.resp.status}")
        check(
            "offset-less dateTime + timeZone is accepted",
            naive_accepted,
            "model may omit the offset",
        )

        # ------------------------------------------------------------------
        section("DELETE")
        service.events().delete(calendarId=calendar_id, eventId=allday_id).execute()
        created.remove(allday_id)
        # A deleted event is still retrievable by id -- Google tombstones it as
        # `status: cancelled` rather than returning 404. Existence checks must
        # therefore read `status`, not rely on the call failing.
        after_delete_status = None
        get_raised = None
        try:
            after_delete = service.events().get(calendarId=calendar_id, eventId=allday_id).execute()
            after_delete_status = after_delete.get("status")
        except HttpError as exc:
            get_raised = exc.resp.status
        check(
            "deleted event reads back as cancelled, not 404",
            after_delete_status == "cancelled",
            f"status={after_delete_status} http={get_raised}",
        )

        repeat_delete_status = None
        try:
            service.events().delete(calendarId=calendar_id, eventId=allday_id).execute()
        except HttpError as exc:
            repeat_delete_status = exc.resp.status
        check(
            "deleting twice returns 410/404, not a crash",
            repeat_delete_status in {404, 410},
            f"status={repeat_delete_status}",
        )

    finally:
        section("cleanup")
        for event_id in list(created):
            try:
                service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
                print(f"  deleted {event_id}")
            except HttpError as exc:
                print(f"  could not delete {event_id}: {exc.resp.status}")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
