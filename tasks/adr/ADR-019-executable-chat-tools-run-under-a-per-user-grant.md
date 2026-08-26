# ADR-019 — Executable chat tools run under a per-user grant

- Status: Accepted — implemented 2026-08-26 by [`SPEC-per-user-google-calendar-oauth.md`](../specs/SPEC-per-user-google-calendar-oauth.md)
- Date: 2026-08-26
- Decision makers: Product/Engineering team
- Relates to: `src/cowork_agent/features/ai_chat/tools/`, `src/cowork_agent/integrations/google_calendar/`; required by [`SPEC-chat-tools-registry.md`](../specs/SPEC-chat-tools-registry.md) §9; partners [ADR-004](ADR-004-chat-native-task-episodes.md), [ADR-013](ADR-013-composition-as-typed-value.md), [ADR-020](ADR-020-google-grants-stay-separate.md)

## Context

Every chat capability shipped so far reads. The tool registry (M0–M4) added the
first one that **writes to a resource outside this system** — a Google Calendar
event that appears on a real person's phone.

`SPEC-chat-tools-registry.md` §9 made this ADR a precondition for turning
`GOOGLE_CALENDAR_ENABLED` or `CHAT_TOOL_AXIS_ENABLED` on outside local
development, and deliberately refused to let ADR-013 absorb the question.
Both flags are off in every deployment today, so nothing here is a
retrospective justification: the decision is being made before the capability
is reachable.

Two things have since been measured rather than assumed, and both inform this.

The QA in [`docs/evaluations/CHAT/PROGRESS.md`](../../docs/evaluations/CHAT/PROGRESS.md)
ran 25 stories offline and 14 against a live model. The router narrows
correctly, the guards hold, and no live call has ever produced an event at the
wrong time. But F5 records a live case where an ambiguous hour was filled with a
working-hour default rather than a question, and it is the **classifier**, not
the tool, that keeps that off the shipped path today. The write path is
therefore correct but thinly defended.

Separately, the calendar adapter authenticates with a single
`GOOGLE_CALENDAR_REFRESH_TOKEN` read from the environment — one grant, shared by
every user of the system. That is fine for a demo with one user and is
disqualifying for anything else: user A's request writes into the calendar
whose token happens to be in `.env`.

## Decision

**A chat tool that writes runs only under a grant belonging to the user whose
turn it is. No writing tool executes against a process-wide credential.**

Four conditions, all of which must hold before a writing tool is reachable in a
deployment:

1. **Per-user grant.** The credential comes from a connection record owned by
   the authenticated principal. `GOOGLE_CALENDAR_REFRESH_TOKEN` is demoted to a
   local-development convenience and is ignored whenever a principal is present.
   [ADR-020](ADR-020-google-grants-stay-separate.md) decides how that grant is
   obtained; this ADR only requires that it exists.
2. **Absent grant degrades the turn, never substitutes.** A user with no
   calendar connection gets a reply saying the calendar is not connected. It is
   never served from another user's grant, and never from the environment's.
3. **One write per turn, keyed by the turn.** `ChatToolRunner` runs at most one
   tool per turn and the created resource's id derives from the turn's
   idempotency key, so a retried turn converges on one event. This already
   holds; it is recorded here because it is a safety property, not an
   implementation detail.
4. **The flag stays a flag.** `CHAT_TOOL_AXIS_ENABLED` and the per-tool flag
   both remain, and both remain off by default. A capability gate that only
   narrows is the last thing standing between a routing regression and a real
   event.

**This does not extend to Email or Gmail.** ADR-004's prohibition is untouched:
chat may not read or act on mail through a tool. The Gmail grant stays
`gmail.readonly` and stays unreachable from the chat tool plane.

## Consequences

The binder seam widens. `ToolBinder = Callable[[str, datetime], Tool]` carries a
turn's idempotency key and clock; it must now also carry who the turn belongs
to, because the credential is per-user. `ChatToolRunner.run_for_turn` gains the
principal and the binder resolves the grant. The alternative — resolving the
grant at composition time — is what produced the shared token, so it is exactly
what this decision rejects.

`GoogleCalendarSettings` splits. The client id and secret stay resolved once
after `load_runtime_environment()` in `create_app`, as the tool registry handoff
required: they are application identity and do not vary by user. Only the
refresh token moves per-user, and it is read from the repository rather than
from `.env`, so no turn reloads the environment.

A per-user grant makes revocation meaningful. Today, disconnecting cannot remove
calendar access because there is nothing user-scoped to disconnect. Afterwards,
deleting the connection ends it.

The write path stays thinly defended until F5 is closed. This ADR permits the
capability under a per-user grant; it does not claim the argument filler is
trustworthy. The ambiguous-hour guard is still owed, and the flags are what hold
the line until it lands.

**Amended 2026-08-27.** The guard landed:
[`tools/ambiguous_hour.py`](../../src/cowork_agent/features/ai_chat/tools/ambiguous_hour.py),
reached from the handler because `ToolTurnContext` now carries the turn's
message. The classifier is no longer the only thing between an undetermined hour
and a real event. This does not change the decision — the flags and the per-user
grant stay exactly as decided — it removes the caveat the decision was made
under. The live re-measurement is still owed; see PROGRESS.md §5 "F5/F7 fixed".

## Alternatives considered

**Keep the shared service token and scope it to a dedicated calendar.** Simplest,
and genuinely safe against cross-user writes, because there is only one calendar.
Rejected because the feature people asked for is an event on *their* calendar;
a shared one nobody looks at is not the feature.

**Per-user grant, but fall back to the environment token when absent.** Removes
a failure mode the user sees. Rejected because the fallback is silent and
cross-user: a user with no connection would get a confirmation naming an event
they will never find. A refusal they can act on is strictly better than a
successful write to the wrong calendar.

**Defer the ADR until a second writing tool exists.** Rejected because the flag
would be turned on first, and the point of §9's precondition is that the
decision precedes the capability.
