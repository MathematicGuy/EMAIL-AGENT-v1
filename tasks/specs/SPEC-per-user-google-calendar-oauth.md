# Technical Spec — Per-user Google Calendar OAuth, chained to the mail connection

| Field | Value |
|---|---|
| Status | **Implemented.** P1–P8 landed on `claude/cowork-agent-tools-registry-b7ee98`. |
| Date | 2026-08-26 |
| Scope | Replace the shared `GOOGLE_CALENDAR_REFRESH_TOKEN` with a per-user grant, obtained in the same journey as the mail connection. |
| Decisions | [ADR-019](../adr/ADR-019-executable-chat-tools-run-under-a-per-user-grant.md) (per-user grant required), [ADR-020](../adr/ADR-020-google-grants-stay-separate.md) (two grants, chained consent) |
| Builds on | [`SPEC-chat-tools-registry.md`](SPEC-chat-tools-registry.md) §10 — this closes the debt that section records |
| Evidence | [`docs/evaluations/CHAT/PROGRESS.md`](../../docs/evaluations/CHAT/PROGRESS.md) — the QA that measured the write path, and §2 there for the gate this work re-measured |

---

## 1. Why this exists

Today every chat user shares one Google Calendar grant, read from
`GOOGLE_CALENDAR_REFRESH_TOKEN` in the environment. Whoever asks, the event
lands on whichever calendar that token belongs to. `SPEC-chat-tools-registry.md`
§10 names this as the largest piece of debt in the tool plane, and ADR-019 now
makes a per-user grant a precondition for enabling a writing tool at all.

The product requirement layered on top: a user who connects their email should
end up with calendar working too, without hunting for a second button.

One fact makes that cheap. **The Gmail callback is already the login** — it
resolves the principal and mints the session cookie (`api/mailboxes.py`). Work
chained after it runs with an authenticated session in place, which is what lets
the calendar callback identify the user from the session rather than from an
email address inside a token.

## 2. Scope

### In

- A `CalendarConnection` record: per-user, encrypted refresh token, its own table.
- A Google Calendar OAuth handshake — connect and callback — in its own router.
- Chaining: the Gmail callback redirects into the calendar consent instead of straight to the frontend.
- Resolving the calendar grant per turn from the connection, and degrading the turn when there is none.
- A combined connection status the frontend can render.

### Out

- **Merging the two grants.** ADR-020 decided against it. The guards at `config.py:379` and `gmail/provider.py:180` are not touched by this spec, and a change that touches them is not this spec.
- **Outlook or Microsoft calendars.** One provider, following the Gmail/Outlook precedent of adding providers one at a time.
- **Turning the flags on.** `CHAT_TOOL_AXIS_ENABLED` and `GOOGLE_CALENDAR_ENABLED` stay off by default. This spec makes enabling them defensible; it does not enable them.
- **F5, the ambiguous-hour guard.** Recorded in PROGRESS.md §5, owed separately. Named here so it is not assumed closed by this work.

## 3. Invariants

Numbered so a review or a failure can cite one.

**J1 — A write uses the turn's own user's grant, or no grant at all.** No
process-wide credential, no other user's, no environment fallback when a
principal is present.

**J2 — A missing grant degrades the turn.** The user is told the calendar is not
connected. Nothing is written and no other calendar is substituted (ADR-019 §2).

**J3 — Both Gmail scope guards hold unchanged.** `config.py:379` and
`gmail/provider.py:180` behave exactly as today. A Gmail grant returning
`calendar` is still an error.

**J4 — A refused calendar consent costs nothing already earned.** The session
cookie and the mail connection survive. The user lands on the frontend logged in,
mail connected, calendar denied.

**J5 — A calendar grant cannot log anyone in.** The calendar callback reads an
existing principal from the session. It never creates a principal and never
mints a session cookie.

**J6 — Either grant is independently revocable.** Deleting one leaves the other
working.

**J7 — A calendar connection is never mistaken for a mailbox.** Mail routing
iterates mailbox connections; a calendar record must not appear there.

## 4. Data

A new table and a new record, sibling to `MailboxConnection` rather than a
variant of it. J7 is the reason: `ProviderRoutingMailboxAdapter` iterates
mailbox connections to route mail, so a `provider="google_calendar"` row in
`mailbox_connections` becomes a mail-routing bug the first time someone adds a
provider branch.

```python
@dataclass(frozen=True, slots=True)
class CalendarConnection:
    id: str  # cal_<uuid4hex>
    user_id: str  # the principal, from the session
    provider: str  # "google_calendar"
    external_account_id: str  # the Google account the grant belongs to
    calendar_id: str  # "primary" unless the user picks another
    encrypted_refresh_token: str  # same TokenCipher as the mailbox path
    scopes: tuple[str, ...]
    timezone: str
    status: str  # "active" | "revoked"
    created_at: datetime
    updated_at: datetime
```

Repository interface mirrors `MailboxConnectionRepository`: `upsert`, `get`,
`get_for_user`, `delete`, `initialize`. SQLite and Postgres implementations, in
that order, matching how the mailbox repositories are already paired.

`get_for_user` rather than `list_for_user`: one active Google Calendar grant per
user. A second connect replaces the first.

## 5. The chained flow

```
GET /v1/mail-todo/oauth/gmail/connect
      -> Google consent  (gmail.readonly)
GET /v1/mail-todo/oauth/gmail/callback
      -> MailboxConnection, principal resolved, session cookie set   [unchanged]
      -> redirect to the calendar connect route                      [changed]
GET /v1/calendar/oauth/google/connect
      -> Google consent  (calendar)
GET /v1/calendar/oauth/google/callback
      -> CalendarConnection for the session's principal
      -> redirect to the frontend with the outcome of both
```

The only change to the existing Gmail path is its final redirect. Everything
before it — the PKCE verifier, the granted-scope check, the principal
resolution, the session cookie — stays as written.

### Outcomes the frontend must be given

| Situation | Redirect carries |
|---|---|
| Both consents granted | `gmail=connected&calendar=connected` |
| Mail granted, calendar denied or failed | `gmail=connected&calendar=denied` |
| Mail denied | `gmail=denied` — the chain never starts |
| Calendar connected on its own, session already present | `calendar=connected` |

**J4 is the failure this design is most likely to get wrong.** The second leg
must not be able to unwind the first. Every error path in the calendar callback
redirects with `gmail=connected`, and none of them clears the cookie or touches
the mailbox record.

### Direct entry

`/v1/calendar/oauth/google/connect` is reachable on its own for a user who
already has a session — reconnecting after revocation, or connecting calendar
later. With no session it redirects to the frontend rather than starting a
consent, because there would be no principal to attach the result to (J5).

## 6. Routing and composition

Per ADR-015, this is a new router module — `api/calendars.py`, a
`create_calendar_router()` factory following the `api/mailboxes.py` shape —
mounted in `create_app` alongside the others. No route moves into `app.py`.

The route table grows by three (`connect`, `callback`, and the status read),
which is expected: the 63-route baseline is an invariant of the *tool registry*
port, not of the application forever. The new count is recorded when this lands.

`GoogleCalendarSettings` splits along the line ADR-019 draws:

| Value | Resolved | Why |
|---|---|---|
| `client_id`, `client_secret`, `redirect_uri` | Once, after `load_runtime_environment()` in `create_app` | Application identity. Does not vary by user. Keeps the tool-registry handoff's rule that no turn reloads `.env`. |
| `refresh_token`, `calendar_id`, `timezone` | Per turn, from `CalendarConnection` | Per-user. Read from the repository, never the environment. |

`GOOGLE_CALENDAR_REFRESH_TOKEN` is demoted to a local-development convenience:
used only when no principal is present, ignored otherwise. It is not a fallback
for a signed-in user with no connection — that is J2's refusal.

## 7. The binder seam

`ToolBinder = Callable[[str, datetime], Tool]` binds a tool to a turn's
idempotency key and clock. A per-user credential does not fit through it, so the
seam widens to carry who the turn belongs to:

```python
ToolBinder = Callable[[ToolTurnContext], Awaitable[Tool]]


@dataclass(frozen=True, slots=True)
class ToolTurnContext:
    idempotency_key: str
    now: datetime
    user_id: str | None = None
```

The binder is **async**, which this spec did not originally say: resolving the
grant is a repository read, so it cannot happen synchronously at bind time.

`ChatToolRunner.run_for_turn` gains `user_id` and passes it through. The binder
resolves the grant and returns either a bound calendar tool or one whose handler
refuses with the not-connected message (J2).

One parameter object rather than three positional arguments, because the next
writing tool will want the same three and the signature should not grow again.
`user_id: str | None` is explicit: `None` is local development, and the binder
decides what that means rather than the runner guessing.

**`ChatToolRunner.names` stays independent of the user.** The router narrows on
tool names before any binding happens, and a name that appears or disappears per
user would make routing depend on connection state. Whether the tool *runs* is
per-user; whether it *exists* is not.

## 8. Task breakdown

| | Task | Done when | State |
|---|---|---|---|
| P1 | `CalendarConnection`, repository protocol, SQLite implementation | Round-trips through `upsert`/`get_for_user`/`delete`; token encrypted at rest | Done |
| P2 | Postgres implementation | Same tests, both backends | Done — migration `017_calendar_connections.sql` |
| P3 | `GoogleCalendarConnectionService` — `begin()`, `complete(state, response, user_id)` | PKCE and single-use state reused from `OAuthStateManager`; granted scope checked, as Gmail does | Done |
| P4 | `api/calendars.py` — connect, callback, status | J4 and J5 asserted per error path | Done — 15 tests |
| P5 | Chain the Gmail callback's final redirect | Gmail path otherwise byte-identical; J3 unchanged and still asserted | Done |
| P6 | Widen the binder to `ToolTurnContext`; resolve the grant per turn | J1, J2; `names` proven user-independent | Done — 6 tests |
| P7 | Frontend combined status | Three states rendered: connected, mail-only, neither | Done — 157 frontend tests pass |
| P8 | Re-run the tool-intent QA; record the new route count | PROGRESS.md updated; suite, ruff, mypy green | Done — 63 → 67 routes |

P1–P4 are independent of the chat plane and can land before P5. P6 is the only
task that touches `features/ai_chat/`.

## 9. Test plan

Offline by construction, following `tests/README.md` §1. The OAuth driver is
faked at the same seam `GmailOAuthDriver` already is, so no test reaches Google.

| Invariant | Test |
|---|---|
| J1 | Two users, two grants; each turn writes through its own. A turn for user B never resolves A's token. |
| J2 | A signed-in user with no connection: the tool refuses, the calendar stays empty, and the environment token is *not* used even when set. |
| J3 | The existing Gmail scope-guard tests still pass, untouched. A Gmail grant returning `calendar` still raises. |
| J4 | Calendar denial, calendar exchange failure, and calendar state reuse: each redirects `gmail=connected&calendar=denied`, the cookie survives, the mailbox record survives. |
| J5 | A calendar callback with no session sets no cookie and creates no principal. |
| J6 | Deleting either connection leaves the other resolvable. |
| J7 | A calendar connection does not appear in `list_for_user` on the mailbox repository, and mail routing does not see it. |

Mutation pass before this is called done, matching the QA's §8: break each guard
in `src/`, confirm the named test goes red, revert. J2 and J4 are the two worth
mutating hardest — a silent environment fallback and a lost mail connection are
both invisible in a green suite.

**Result — nine mutations, nine reds.** Six in `src/`: J2's silent environment
fallback, J1 ignoring the user's grant, J4 dropping the mail outcome, J5
identifying from the token, J3 accepting a widened grant, and narrowing the
callback's `except Exception` to `except ValueError` (which is how a
`CalendarReauthRequiredError` becomes a 500 instead of a redirect). Three in the
frontend: always showing the connect offer, reporting only the calendar half of
the journey, and letting a calendar read failure surface as a mail error.

## 10. Risks

**J4 is the one that bites.** Chaining means a second network round trip can
fail after the user is already logged in. Every path out of the calendar
callback must preserve the session and the mailbox record, and the tests have to
enumerate those paths rather than sample them.

**Two consent screens read as a bug to some users.** Google shows them back to
back with no stop on our side, but the second screen appears after the user
believes they have finished. The frontend copy should say two steps are coming
before the first redirect.

**Revocation outside the app.** A user revoking access in their Google account
leaves a stale `active` record. The refresh failure has to mark it `revoked` and
degrade the turn per J2, not retry.

**Scope creep back toward merging.** The cheapest answer to any friction here is
"just put both scopes on one consent". ADR-020 rejected that with reasons; if it
is revisited, it is a superseding ADR, not an edit to a guard.
