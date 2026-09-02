# Google Calendar API — verified behaviour notes

Findings from a live CRUD run against a real calendar on **2026-08-25**.
Everything below was observed, not read from documentation.

- Script: [`scripts/smoke_test_google_calendar.py`](../../scripts/smoke_test_google_calendar.py)
- Run: `uv run python scripts/smoke_test_google_calendar.py`
- Result at time of writing: **20 passed, 0 failed**
- Consumer: [`tasks/specs/SPEC-chat-tools-registry.md`](../../tasks/specs/SPEC-chat-tools-registry.md) §7

The script creates events prefixed `[cowork-smoke]` and deletes them in a
`finally` block. It talks to Google, so it is a manual script and is deliberately
not part of `pytest`.

---

## 1. Setup gotchas (both cost a debugging cycle)

**Enabling the API is separate from granting the scope.** The OAuth consent
screen will happily issue a token carrying
`https://www.googleapis.com/auth/calendar` for a Cloud project where the Calendar
API is not enabled. Nothing fails until the first API call, which returns
`403 accessNotConfigured`. Gmail API being enabled in the same project does not
help. Enable per-API, per-project.

**Windows has no IANA tz database.** `ZoneInfo("Asia/Ho_Chi_Minh")` raises
`ZoneInfoNotFoundError` until the `tzdata` package is installed. This is not
incidental — resolving "tomorrow at 3pm" requires the current time in the user's
IANA zone. Now pinned in [`pyproject.toml`](../../pyproject.toml) under a
`sys_platform == 'win32'` marker.

## 2. Event IDs: base32hex, and `w`/`x`/`y`/`z` are illegal

Client-supplied event ids accept **lowercase `a`–`v` and digits `0`–`9` only**,
length 5–1024. Anything outside that alphabet returns:

```text
400 "Invalid resource id value."
```

The last four letters of the alphabet being invalid is easy to miss. A literal
prefix has to be checked against the same rule — `"cowork"` is rejected for its
`w`; `"coagent"` is fine.

`base64.b32hexencode(...).lower()` maps arbitrary bytes into exactly this
alphabet, which is why it is the right encoding for deriving an id from a chat
turn's idempotency key.

## 3. Duplicate insert returns 409 — free retry protection

Inserting twice with the same client-supplied id returns **409**, not a second
event. A retried chat turn that reuses its idempotency key therefore cannot
create a duplicate. Treat `409` on insert as success.

This is the cheapest idempotency mechanism available and needs no local state.

## 4. What Google validates, and what it silently accepts

| Input | Result |
|---|---|
| `end` before `start` | **Rejected** (`400`) |
| Event starting one year in the past | **Accepted** |
| `dateTime` with no UTC offset, plus a `timeZone` field | **Accepted** |

Only the first is Google's problem. The other two are ours:

- A model that gets the year wrong — the classic rollover, writing January of the
  year that just ended — produces a silently valid event. **Validate the range
  locally.** The spec caps `start` at one year from now.
- An offset-less `dateTime` is interpreted using the accompanying `timeZone`.
  Convenient, but it means a missing offset is not an error signal, so a model
  omitting the offset while *also* meaning a different zone fails silently. Send
  both an explicit offset and `timeZone`.

## 5. Delete is a tombstone, not a removal

After `events().delete(...)`, fetching the same id **still returns 200** with
`status: "cancelled"`. It does not 404.

Existence checks must read `status`, not assume the call raises. A second delete
of the same id returns **410**.

`events().list(...)` does not include cancelled events by default, so listing
behaves the way you would expect even though `get` does not.

## 6. Confirmed working, no surprises

- Refresh-token → access-token exchange with `google.oauth2.credentials.Credentials`.
- `calendars().get` returns the calendar's own `timeZone` — worth reading at
  startup rather than trusting an env var to match.
- Timed events round-trip their start in the requested timezone
  (`2026-08-26T15:00:00+07:00`).
- All-day events via date-only `start.date` / `end.date`; the response carries no
  `dateTime`.
- `insert` returns `htmlLink`, suitable for linking the event in a chat reply.
- `patch` updates named fields and leaves the rest intact, including rescheduling
  by sending only `start`/`end`.
- `list` with `timeMin`/`timeMax` + `q` + `singleEvents=True` + `orderBy=startTime`.

## 7. Credentials

Read from `.env` (gitignored), never committed:

```text
GOOGLE_CALENDAR_CLIENT_ID
GOOGLE_CALENDAR_CLIENT_SECRET
GOOGLE_CALENDAR_REFRESH_TOKEN
GOOGLE_CALENDAR_ID          # default: primary
GOOGLE_CALENDAR_TIMEZONE    # default: Asia/Ho_Chi_Minh
```

The current grant is a **single service-level refresh token** shared by all chat
users — a demo shortcut, documented as the top item to replace in
`SPEC-chat-tools-registry.md` §10. It is separate from the Gmail connection by
necessity: [`config.py:380`](../../src/cowork_agent/config.py:380) and
[`provider.py:180`](../../src/cowork_agent/integrations/gmail/provider.py:180)
both hard-reject any Google scope other than `gmail.readonly`, and that guard
should stay.

Refresh tokens issued to a Cloud project still in **Testing** publishing status
expire after 7 days. If calls start failing with `invalid_grant`, re-run the
authorization rather than debugging the code.
