# Tier B verification harness

The real backend, the real chat UI, only Google's HTTP call faked.

## Why this exists

A live end-to-end proof of the calendar tool needs a Google OAuth client, a real
per-user grant, and a real calendar to write into. Tier B removes all three
without removing anything else: the intent router, `finalize_route`, the
per-user binder in `app._chat_tool_runner`, `fill_arguments`, the range guards,
`event_body`, the 409-as-success idempotency branch, the SQLite calendar
repository and the Fernet cipher all run as they do in production. A failure
here is a product failure.

Two seams are swapped, and only two:

1. `GoogleCalendar(settings, service=...)` gets a fake Google API service. The
   adapter's own code paths still run.
2. The OAuth handshake is replaced by `POST /__testing__/calendar-grant`, which
   writes a real `CalendarConnection` row for **the caller's own principal** —
   so J1 stays under test rather than assumed.

`POSTGRES_MODE` is forced to `off`, so this never reaches the Supabase URL that
`.env` carries, and the unapplied migration `017_calendar_connections.sql` is
not needed.

## Running it

```bash
TIER_B=1 pnpm exec playwright test --project=calendar-tier-b
```

Playwright starts the harness on `127.0.0.1:8123` (deliberately not 8000, which
a dev backend usually holds) and points Vite's proxy at it through
`BACKEND_ORIGIN`. Video, screenshots and traces land in `test-results/`.

To drive it by hand:

```bash
uv run python e2e/harness/tier_b_server.py
```

Then `GET /__testing__/whoami`, `POST /__testing__/calendar-grant`,
`GET /__testing__/events`.

## What it costs

Every turn calls a real classifier and a real `fill_arguments`. Keep the case
list short.

## What it cannot cover

- `tq-013`, `tq-014`, `tq-015`, `tq-025` need ready project documents seeded.
- `tq-021`, `tq-022` need a second boot with the tool axis off.
- The real Google contract. Tier B proves the request body we would send is the
  one we intend; it cannot prove Google accepts it. That is
  `scripts/smoke_test_google_calendar.py`'s job.
