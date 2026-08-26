# ADR-017 — Google grants stay separate; consent is chained, not merged

- Status: Accepted — implemented 2026-08-26 by [`SPEC-per-user-google-calendar-oauth.md`](../specs/SPEC-per-user-google-calendar-oauth.md)
- Date: 2026-08-26
- Decision makers: Product/Engineering team
- Relates to: `src/cowork_agent/config.py`, `src/cowork_agent/integrations/gmail/`, `src/cowork_agent/api/mailboxes.py`; partners [ADR-004](ADR-004-chat-native-task-episodes.md), [ADR-006](ADR-006-supabase-managed-data-with-gmail-sessions.md), [ADR-016](ADR-016-executable-chat-tools-run-under-a-per-user-grant.md)

## Context

[ADR-016](ADR-016-executable-chat-tools-run-under-a-per-user-grant.md) requires a
per-user Google Calendar grant. The product requirement on top of it is that
connecting email should leave the user with calendar working too — one journey,
not a second button to discover.

Google will issue both scopes in a single authorization request, so the
obvious implementation is one consent screen granting `gmail.readonly` and
`calendar` together, producing one refresh token carrying both.

Two facts make that less obvious than it looks.

**The codebase enforces the opposite, twice, on purpose.** `config.py:379`
rejects any Gmail scope set other than exactly `(gmail.readonly,)`.
`gmail/provider.py:180` re-checks what Google actually granted at callback and
raises if it differs. The calendar adapter's own docstring names this decision:
its grant is *"deliberately separate from the Gmail connection, whose read-only
scope guard must not be loosened to carry this."* Merging scopes is not an
oversight to correct; it is a guard to overrule or keep.

**The two scopes are not the same kind of permission.** `gmail.readonly` cannot
change anything. `calendar` writes. A combined token means every mailbox
credential in the database — encrypted at rest, but decrypted on every fetch —
is also a calendar-writing credential. The blast radius of a token compromise
changes shape, and it changes for the read-only capability that ADR-004
deliberately kept narrow.

There is also a fact about the existing flow that makes chaining cheap:
**the Gmail callback is the login.** It resolves the principal and mints the
session cookie (`api/mailboxes.py`). Anything chained after it therefore runs
with an authenticated session already in place.

## Decision

**Two grants, two tokens, two connection records — presented as one journey.**

The Gmail consent completes exactly as it does today, including the session
cookie. Instead of redirecting to the frontend, it redirects into the calendar
consent, which completes and then redirects to the frontend with the outcome of
both. The user presses Allow twice and never looks for a second button.

Four properties this must have:

1. **Both scope guards stay.** `config.py:379` and `gmail/provider.py:180` are
   unchanged. A Gmail grant that comes back carrying `calendar` is still an
   error, because that would mean the consent screens were merged after all.
2. **The calendar callback identifies the user from the session, not the token.**
   The Gmail callback binds identity from the verified grant because no session
   exists yet. The calendar callback runs after the cookie is set, so it reads
   the principal from the session. A calendar grant never creates a principal
   and never mints a session — it cannot be used to log in.
3. **A refused calendar consent must not cost the user their mail connection or
   their session.** Denial at the second step redirects to the frontend as
   `gmail=connected&calendar=denied`. This is the single largest risk of
   chaining and the one the implementation is most likely to get wrong.
4. **The calendar connection is a separate record in a separate table.** Not a
   `MailboxConnection` with `provider="google_calendar"`: mail routing iterates
   mailbox connections, so a calendar row in that table becomes a mail-routing
   bug the first time someone adds a provider loop.

Either grant can be established, revoked, and re-established without touching
the other. Connecting calendar alone stays possible for a user who already has a
session.

## Consequences

The user experiences one flow and two consent screens. That is the visible cost,
and it is the whole cost — Google shows both screens in sequence with no
intermediate stop on our side.

Revocation gets sharper rather than blunter: disconnecting calendar leaves mail
working, and disconnecting mail does not silently strip calendar. With a merged
token neither is possible; the user revokes both or neither.

Two records mean two refresh flows and two failure modes to render in the UI.
The frontend needs a combined status — connected / mail-only / neither — rather
than a boolean.

`gmail.readonly` stays provably read-only. That is the property being bought,
and it is worth stating plainly: no amount of compromise of the mail path can
write to a calendar, and no compromise of the calendar path can read mail.

## Alternatives considered

**One consent, one token carrying both scopes.** Genuinely one click, and less
code — one record, one refresh path, one status. Rejected because it requires
deleting both scope guards and converts every mailbox token into a
calendar-writing token. The saving is one click; the cost is the read-only
guarantee ADR-004 and the Gmail settings were built to make true. If this is
ever revisited, it needs its own ADR superseding this one, not an edit to the
guard.

**Incremental authorization** (`include_granted_scopes=true`). Google's own
answer to this shape: the second consent returns a token covering both scopes.
Rejected for the same reason as above — the end state is still one token with
both scopes, arrived at more subtly.

**Leave the flows independent, unify only the frontend.** Cheapest, and no OAuth
work at all. Rejected because it does not deliver the requirement: the user still
performs two distinct connect actions, and the shared environment token stays,
which ADR-016 forbids for a writing tool.
