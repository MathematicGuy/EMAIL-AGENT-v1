# Technical Spec — Minimal Tools Registry + Google Calendar

| Field | Value |
|---|---|
| Status | M0-M4 implemented on current `dev`; flag-off. Outstanding: §11 fixtures. The executable-chat-tool ADR §9 required is now [ADR-019](../adr/ADR-019-executable-chat-tools-run-under-a-per-user-grant.md). |
| Date | 2026-08-26 (synchronized with `dev` at `bf2fdee`) |
| Scope | **Dev-grade slice.** One use case, one tool, flag-off by default. |
| Use case | "Create a todo on my Google Calendar" — the agent picks the tool from its own turn context and creates the event. |
| Reference | `docs/references/waku-agent/Waku-Agent-System-Design.md` §8 (`Tool` = name + description + schema + handler) |
| Architecture authority | [SPEC-architecture-improvement-program](SPEC-architecture-improvement-program.md), ADR-013/014/015, and current `dev` source. See §0 and §9. |

---

## 0. Mandatory integration with the current architecture

This specification was originally written and implemented in an uncommitted worktree based on
`04e0549`. Current `dev` is `bf2fdee`, 39 commits ahead of that base. The draft therefore cannot
be merged or resolved by taking its versions of shared files wholesale. Preserve the tool
behaviour described here, but port its deltas onto current `dev`.

The architecture improvement register is authoritative for the seams it closed. This work is a
new chat capability; it does **not** reopen, replace, or complete any C01-C10 workstream.

| Current `dev` decision | Required registry integration |
|---|---|
| **C02 / ADR-013:** boot-time composition is the frozen, typed `CoworkRuntime`; chat dependencies live in `ChatRuntime`. | Add `chat_tool_runner: ChatToolRunner | None` to `ChatRuntime`. Initialize it to `None` in `build_chat`, construct the runner in the provider-upgrade block, and publish it through the existing `dataclasses.replace(chat_runtime, ...)` call. The controller factory reads `chat.chat_tool_runner`. **Do not add `app.state.chat_tool_runner` or any other boot-time `app.state` key.** |
| **C03 / ADR-015:** `app.py` is the composition root and routers own transport. | Do not move chat routes back into `app.py`, restore deleted route closures, or bypass `api/chat.py`. This slice adds no route. |
| **C04 / ADR-014:** `stream_message` remains one orchestration generator; `TurnJournal`, `CancellationGuard`, and `TaskEpisodeSettler` own their settled policies. | Port the TOOL branch into the current generator without replacing the current file with the stale-base controller. Keep journal, cancellation, activity, and episode-settlement flows intact. Replace the draft's private `self._memory._read_active_turns()` call with C04's public `self._memory.read_active_turns()` interface. |
| **C05:** implicit disk reads in `Settings.from_env` remain parked. | Do not widen C05. After `load_runtime_environment()`, resolve `GoogleCalendarSettings` once in `create_app` and capture that typed value for `lifespan`; `_chat_tool_runner` receives it as an argument and must not reload `.env`. `ChatRuntime` stores only `ChatToolRunner | None`, not settings. |
| **C06:** the frontend mail-poll protocol is behind `runMailScanProtocol`. | No frontend change belongs to this slice. |
| **C07:** mail-scan reconciliation lives in `features/ai_chat/mail_scan_reconciliation.py`; `api/chat.py` keeps only transport and its six private chat seams. | Do not restore reconciliation helpers to `api/chat.py` or move the mail-scan route. The tool registry does not depend on that route. |
| **C08:** the PDF renderer is blocked on a human dependency decision. | Do not add or select a PDF dependency while landing this spec. |
| **C10:** the remaining `app.state` sites are accepted, bounded debt. | Do not create a fourth survivor. Tool composition must use `ChatRuntime` as stated above. |

Shared-file conflict rule: current `dev` owns the structure; the registry draft contributes only
the smallest behaviour delta. This is especially important for `app.py`, `controller.py`,
`intent/prompt.py`, `intent/resolver.py`, `chat_reply.py`, and their existing tests.

Before further implementation, preserve the current dirty registry work, integrate `dev`, and
then reapply or resolve each registry delta against the resulting files. A clean merge marker is
not enough: the typed-runtime and turn-journal invariants above must be proved by tests and diff
review.

### 0.1 M0 preservation and port checklist

The next agent must treat every existing dirty file as user-owned input until it has positively
identified the registry delta. At this synchronization point, the registry draft consists of:

- modified tracked files: `pyproject.toml`, `uv.lock`, `app.py`,
  `_chat_routing_contracts.py`, `controller.py`, `generation_context.py`,
  `intent/prompt.py`, `intent/resolver.py`, `intent/service.py`, `chat_intent.py`,
  `chat_reply.py`, `provider_factory.py`, `test_intent_prompt.py`, and
  `test_intent_resolver.py`;
- untracked registry files: `docs/references/google-calendar-api-notes.md`,
  `scripts/smoke_test_google_calendar.py`, the complete
  `features/ai_chat/tools/` and `integrations/google_calendar/` trees,
  `integrations/llm/tool_arguments.py`, the four new feature tests
  (`test_calendar_tool.py`, `test_controller_tool_route.py`,
  `test_tool_arguments.py`, `test_tool_registry.py`), and
  `tests/unit/integrations/google_calendar/test_google_calendar_provider.py`;
- `docs/references/architecture-review-20260825-022000.html` is **not** registry work. Leave it
  untouched and out of registry commits.

Use this non-destructive sequence:

1. Capture `git status --short`, `git diff --binary`, and the untracked-file inventory from
   `git ls-files --others --exclude-standard` as handoff evidence; inspect every registry-owned
   file for credentials.
2. Stash the tracked and untracked draft with `git stash push --include-untracked`, then integrate
   current `dev` into the branch. Do not use a destructive reset or delete the worktree.
3. Use `git stash apply`, not `pop`, so the preservation copy remains until the port is committed
   and verified. Resolve shared files by retaining current `dev` structure and applying only the
   registry behaviour delta; add new registry-owned files intact where they do not conflict.
4. Prove `app.state.chat_tool_runner`, `_read_active_turns`, conflict markers, and stale copies of
   removed route/reconciliation code are absent. Then run the narrow and full gates in §11.
5. Commit the port before dropping the preservation stash. Drop it only after the commit and
   post-commit diff prove every inventoried registry path is represented.

---

## 1. Goal and non-goals

**Goal:** the shortest path from today's disabled action axis to a chat turn that
reliably creates a Google Calendar event. Everything else is deferred.

Not in this spec: multi-tenant tool scoping, per-user OAuth, redaction policy,
observability sinks, allowlists, result truncation policy, tool phases, artifact
refs, a `TOOL_RESULT` precedence rank, a second seed tool, Google Tasks, event
update/delete, recurrence, attendees, or reminders.

Two tools would prove the seam is real. One tool proves the use case works. This
spec picks the use case, and §10 says what to revisit when the second tool lands.

## 2. What already exists

On current `dev`, the action axis is fully contracted and switched off:

- `ChatRoute.TOOL`, `IntentDecision.needs_tool`, `IntentDecision.tool_name` and
  `IntentReasonCode.TOOL_REQUESTED_BUT_DISABLED` all live in
  [`_chat_routing_contracts.py`](../../src/cowork_agent/domain/_chat_routing_contracts.py).
- `finalize_route()` forces `effective_needs_tool = False` while
  `tool_axis_enabled` is off.
- [`controller.py`](../../src/cowork_agent/features/ai_chat/controller.py) branches
  only on `RAG` and `CLARIFY`, while retaining the single-generator shape and
  the journal/cancellation/settlement modules established by ADR-014.
- `google-api-python-client`, `google-auth-oauthlib` are already dependencies.

Current composition is materially newer than the original registry draft:

- [`composition.py`](../../src/cowork_agent/composition.py) defines frozen
  `CoworkRuntime` groups, including `ChatRuntime`.
- [`app.py`](../../src/cowork_agent/app.py) assembles those groups once and
  mounts routers; it is not the home for a new untyped tool-runner key.
- [`api/chat.py`](../../src/cowork_agent/api/chat.py) owns chat transport, while
  mail-scan policy lives below transport in
  [`mail_scan_reconciliation.py`](../../src/cowork_agent/features/ai_chat/mail_scan_reconciliation.py).

**The classifier contract needs no change.** `tool_name` is already a field.
Selection costs one prompt line (§5), not a schema migration.

## 3. Credentials: a separate, single-user Google connection

The Gmail OAuth path cannot carry the Calendar scope. Two guards reject it:
[`config.py:380`](../../src/cowork_agent/config.py:380) raises "Gmail v1 must use
only the gmail.readonly scope", and
[`provider.py:180`](../../src/cowork_agent/integrations/gmail/provider.py:180)
raises on any unexpected granted scope. Both are deliberate — AGENTS.md states
Gmail is read-only as a boundary. **Do not loosen either.**

Minimal alternative: one service-level refresh token in `.env`, minted once by a
developer, used for every turn.

```text
GOOGLE_CALENDAR_ENABLED=false
GOOGLE_CALENDAR_REFRESH_TOKEN=...
GOOGLE_CALENDAR_ID=primary
GOOGLE_CALENDAR_TIMEZONE=Asia/Ho_Chi_Minh
```

Client id and secret are reused from `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET`
(same Google Cloud project, different consent grant). A one-off
`scripts/authorize_calendar.py` runs the installed-app flow for
`https://www.googleapis.com/auth/calendar.events` and prints the refresh token.

This is a **single-user shortcut**: every chat user writes to the same calendar.
That is correct for a demo and wrong for anything else. It is the single largest
piece of debt in this spec, and §10 names its replacement.

## 4. The registry

Waku's `Tool` is name + description + JSON schema + handler, and the registry
"exposes schemas to the model and dispatches calls by name." Copy that, drop the
rest.

```python
# src/cowork_agent/features/ai_chat/tools/registry.py


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    text: str  # what the model reads back; on failure, why it failed


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str  # one line, shown to the classifier
    parameters: Mapping[str, object]  # JSON Schema, object type
    handler: Callable[[Mapping[str, object]], Awaitable[ToolResult]]


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool]) -> None: ...

    def specs(self) -> tuple[Tool, ...]:
        """Registered tools, stable name order. Feeds the prompt (§5, §6)."""

    async def run(self, name: str, arguments: Mapping[str, object]) -> ToolResult:
        """Validate against `parameters`, dispatch, and never raise."""
```

Two methods. The one rule that matters: **`run` never raises.** Unknown name,
schema violation, handler exception, Google API error — all come back as
`ToolResult(ok=False, text=...)`. The controller is streaming when it calls this
and must not die; a later ReAct loop must be able to read the failure and decide
whether to retry. That property is why this is a module and not a dict lookup.

Behind `run`: name lookup, `jsonschema` validation against `parameters`, a 15s
`asyncio.wait_for`, and `except Exception` → `ok=False`. That is the entire
implementation.

`parameters` is JSON Schema from the start even though nothing needs it to be —
it is exactly what a provider's native tool-calling API wants, so §10 costs
nothing.

## 5. Selection: one prompt line, no contract change

[`intent/prompt.py`](../../src/cowork_agent/features/ai_chat/intent/prompt.py)
gains a short tier before TIER 5, rendered from `registry.specs()`:

```text
TIER 4.5 — AVAILABLE ACTIONS
Set needs_tool=true and tool_name only when the user asks you to perform one of
these actions. Asking *about* a calendar is not asking you to create an event.
- create_calendar_event: create an event or todo on the user's Google Calendar
```

Trusted system text, rendered outside the `<untrusted_data>` block.

Bump `INTENT_PROMPT_VERSION` to `chat-intent-v4`. TIER 5's JSON schema is
**unchanged** — `IntentDecision.from_dict` uses `_require_exact_fields`, so
touching it would break the contract, the fixtures, and every scripted classifier
in the suite. Nothing here needs it touched.

## 6. Arguments: a second, dedicated call

**This is where the use case is won or lost, and it is where my previous draft was
wrong.** That draft had the registry derive arguments from typed turn context so
no model JSON reached a handler. A calendar event needs a title, a start and an
end. None of those are derivable from context — the model has to produce them.

So: when the route is `TOOL`, one more LLM call fills the arguments.

```python
async def fill_arguments(
    tool: Tool, user_message: str, recent_turns: tuple[ChatTurn, ...], now: datetime
) -> Mapping[str, object]
```

Prompt: the tool's `description` and `parameters`, the current message, the last
few turns, and — non-negotiable — **the current datetime with its IANA timezone**.
Without `now`, "tạo todo họp team ngày mai 3 giờ chiều" is unanswerable; with it,
it is arithmetic. Waku §5.1 injects "the current local date, time, timezone" into
every system prompt for exactly this reason.

A second call rather than widening the classifier, because the classifier prompt
is five tuned tiers gated on a labelled fixture set. Datetime extraction is an
unrelated job, and mixing it in means re-tuning a prompt that currently works. The
extra call only fires on tool turns, so the ~95% of turns that are `CHAT`/`RAG`
pay nothing.

Invalid or unparseable output ⇒ no retry, `ToolResult(ok=False)`, the reply says
what it could not determine. Fail closed: a wrong date on a real calendar is
worse than a question.

## 7. The tool: `create_calendar_event`

```json
{
  "type": "object",
  "properties": {
    "title":       {"type": "string", "maxLength": 200},
    "start":       {"type": "string", "description": "RFC3339 with offset, or YYYY-MM-DD for all-day"},
    "end":         {"type": "string", "description": "RFC3339 with offset, or YYYY-MM-DD for all-day"},
    "description": {"type": "string", "maxLength": 2000}
  },
  "required": ["title", "start", "end"],
  "additionalProperties": false
}
```

Handler rules, in order:

1. Parse `start`/`end`. A bare `YYYY-MM-DD` on both ⇒ all-day event. Mixed
   formats ⇒ `ok=False`.
2. `end > start`, and `start` within one year of now. Otherwise `ok=False` — this
   catches the classic year-rollover failure where the model writes last January.
3. Insert with a client-supplied event `id` derived from the turn's
   `idempotency_key` (lowercase base32, Google's id charset). A retried turn
   returns `409`, which is treated as **success** — free duplicate protection for
   one line of code.
4. `events().insert(calendarId=GOOGLE_CALENDAR_ID, body=...)` via
   `asyncio.to_thread`.
5. Return `ok=True` with the title, the resolved local start time, and the
   response's `htmlLink` so the reply can link the event.

Behind a `CalendarPort` with a deterministic fake, per AGENTS.md — so every test
above runs without network or credentials.

## 8. Controller wiring

`ChatController` takes an optional `tools: ChatToolRunner | None`. Absent, or axis
off, and behaviour is identical to today.

```text
route == TOOL   -> fill_arguments -> registry.run -> generate with the result
route == RAG    -> unchanged
route == CLARIFY-> unchanged
route == CHAT   -> unchanged
```

`RAG_TOOL` is **not implemented**. `finalize_route()` downgrades it to `TOOL`:
creating a calendar event never needs document evidence, and the combined path is
untested surface for zero benefit here.

`finalize_route()` also gains one narrowing rule, matching the existing
`tool_requested_but_disabled` pattern: if `tool_name` is not in
`registry.specs()`, force `needs_tool = False` and fall to `CHAT`. No fuzzy name
matching — a near-miss on a tool that writes to a real calendar creates the wrong
event.

`GenerationContext` gains one optional field:

```python
tool_result: str | None = None
```

No new `ContextSource` member, no precedence-table rework. The reply adapter
renders it as a labelled block above the instruction. On `ok=False` the failure
text goes in the same field, so the model says the event was not created instead
of claiming it was.

Activity codes and the execution-trace field are **deferred**. The assistant's
reply reporting the created event with its link is sufficient feedback for this
slice.

### 8.1 Typed composition on current `dev`

The draft implementation added `app.state.chat_tool_runner`; that is stale and must not survive
the architecture port. The final composition is:

```text
create_app
  -> resolve Calendar settings once and capture for lifespan
provider upgrade block
  -> receive the captured settings
  -> build ChatToolRunner | None
  -> replace(chat_runtime, chat_tool_runner=..., chat_routing_service=...)
  -> CoworkRuntime(chat=chat_runtime)
  -> _chat_controller_factory reads chat.chat_tool_runner
  -> ChatController(tools=...)
```

`ChatToolRunner` is a concrete chat-feature module, not a new cross-application abstraction.
There is one runner and one executable tool, so introducing a separate application-wide port
would be a hypothetical seam. The existing `ChatRuntime` interface is the correct composition
seam and keeps the deletion test strong: removing it would spread optional boot state back into
the composition root and controller factory.

## 9. Architecture debt, stated once

TARGET-ARCHITECTURE §21.5 says "There is still no executable in-chat tool" and
§21.15 lists "any executable in-chat tool" as out of scope. This spec makes that
false.

That is fine while `GOOGLE_CALENDAR_ENABLED=false` and
`CHAT_TOOL_AXIS_ENABLED=false` in every deployed environment — the statement stays
true of the running system. **Before either flag is turned on outside a
developer's machine, write a new ADR for executable chat tools and update the target
architecture.** ADR-013 remains the composition decision and should not be overloaded with a
new product-capability decision. The new ADR must explicitly amend the no-executable-tool target
without weakening ADR-004's Email/Gmail prohibition.

That ADR is now written: [ADR-019](../adr/ADR-019-executable-chat-tools-run-under-a-per-user-grant.md)
permits a writing tool only under a grant belonging to the turn's own user, and leaves ADR-004's
prohibition intact. It carries a precondition of its own — the shared refresh token §10 records
must be replaced first, specified in
[SPEC-per-user-google-calendar-oauth](SPEC-per-user-google-calendar-oauth.md) and decided by
[ADR-020](../adr/ADR-020-google-grants-stay-separate.md).

ADR-004's actual constraint — no `@Email`, no Gmail from chat, no `tool_choices`
request field — is untouched. Tool selection stays a server-side routing decision,
and Gmail's read-only guard is left exactly as it is (§3).

The implementation change must also update the four live Level 1 documents named by the
architecture improvement program: `current-architectures/README.md`,
`02-ai-chat-and-typed-memory.md`, `03-control-plane-persistence-and-uis.md`, and
`04-overall-architecture.md`. Those updates describe the new tool path and typed composition;
they must not rewrite the C01-C10 statuses.

## 10. Path to ReAct, and what to fix first

Nothing here blocks the loop:

- `run(name, arguments)` takes one call and returns one result. Iteration count is
  the caller's policy, so ReAct replaces the call site in §8 and nothing else.
- `parameters` is already JSON Schema, so `specs()` feeds a provider's `tools`
  parameter with no translation.
- `fill_arguments` disappears once providers emit tool calls natively. The
  registry's schema validation, written for §4, is what receives them.

Fix in this order when the slice proves out:

1. ~~**Per-user OAuth** (§3). The shared refresh token is the one thing that cannot
   ship to real users.~~ **Done** — [`SPEC-per-user-google-calendar-oauth.md`](SPEC-per-user-google-calendar-oauth.md),
   2026-08-26. The grant is per user, chained to the mail consent, and a turn
   with no grant refuses rather than borrowing one.
2. ~~**A second tool.** One adapter is a hypothetical seam. The second is what
   reveals whether `Tool` and `ToolResult` are actually the right shapes — expect
   to change them, and prefer changing them then over guessing now.~~ **Done** —
   `list_calendar_events`, 2026-08-27. A *read*, deliberately: a second write
   would have re-used every shape unchanged and proven nothing. `Tool` and
   `ToolResult` both survived without a change, including under a tool that
   returns a list. What did move was one layer down — `parse_range` and
   `CalendarError` to `calendar_core.py`, `InMemoryCalendar` to
   `fake_calendar.py`, and `_not_connected_tool` from four literals to four
   parameters. `_validate_range` deliberately did **not** move: what is safe to
   write and what is sensible to read are different questions. Findings, including
   the three shapes that turned out to be write-specific rather than general, in
   [`PROGRESS.md` F9](../../docs/evaluations/CHAT/PROGRESS.md).
3. Activity code, trace entry, and observability events.
4. `RAG_TOOL`, once a tool exists that genuinely needs retrieved evidence.

## 11. Verification

Narrow routes per `tests/README.md`: **R2** (registry, controller, intent) and
**R4** (prompt). After integrating current `dev`, widen to **R15** plus
`ruff check .`, `mypy src`, and the full `pytest -q` gate at the end. The route-table oracle in
the architecture program must remain at 63 byte-identical routes because this slice adds no
transport endpoint.

| File | Asserts |
|---|---|
| `tests/unit/features/ai_chat/test_tool_registry.py` | `run` never raises: unknown name, schema violation, handler exception, timeout |
| `tests/unit/features/ai_chat/test_calendar_tool.py` | All-day vs timed; `end <= start` rejected; out-of-range year rejected; `409` treated as success; `htmlLink` in the result — all against the fake |
| `tests/unit/features/ai_chat/test_agenda_tool.py` | Ordering, overlap not containment, the truncation notice, the empty-window text; the read-side window rules that are *not* the write-side ones (past allowed, no lower bound) |
| `tests/unit/features/ai_chat/test_intent_resolver.py` *(extend)* | Unknown `tool_name` ⇒ `CHAT`; `RAG_TOOL` ⇒ `TOOL` |
| `tests/unit/features/ai_chat/test_controller_tool_route.py` | `TOOL` route runs the tool once; `ok=False` degrades the turn without failing it |
| `tests/unit/features/ai_chat/test_intent_prompt.py` *(extend)* | TIER 4.5 renders from `specs()`; empty registry omits it |

**Re-run `tests/fixtures/chat_routing/chat_routing_labels.json` once.** TIER 4.5
changes the prompt for every turn, not just tool turns. The schema is untouched,
so this is a spot-check for routing drift rather than a re-label — but a
calendar-tool gain paid for with a retrieval-routing loss is still a failed change.

Add two fixture cases: one asking to create an event, one *mentioning* a calendar
without asking for anything ("I checked my calendar earlier — explain X"). The
second is the failure mode that matters, because it writes to a real calendar.

## 12. Increments

| # | Scope | Exit | Status |
|---|---|---|---|
| M0 | Port the dirty worktree onto current `dev` without regressing C02/C03/C04/C07/C10 | Typed runtime used; no new `app.state`; current architecture tests green | **Done** — see §14 |
| M1 | `ToolRegistry`, `ToolResult`, `Tool`, tests with a dummy tool | R2 green, nothing composed | **Done** — reverified after M0 |
| M2 | `CalendarPort` + fake + Google adapter | Event created against a real test calendar, manually, once | **Done** — reverified after M0; live event created and deleted before the port |
| M3 | `chat-intent-v4` TIER 4.5, resolver narrowing, `fill_arguments` | R2+R4 green, fixtures spot-checked | **Done** except the §11 fixture cases, which need a human decision (§13) |
| M4 | Controller `TOOL` route, `GenerationContext.tool_result`, typed `ChatRuntime` composition | R15 + full backend gate green; end-to-end in dev with both flags on | **Done** — composed through `ChatRuntime.chat_tool_runner`, not `app.state` |


---

## 13. What the implementation changed, and why

Written after M1-M4 were drafted in the dirty worktree. Everything here is a deviation from the
sections above; where they disagree, this section describes the draft behaviour, while §0 is
binding for how that behaviour must be integrated with current `dev`.

**Credentials are their own pair, not Gmail's.** §3 proposed reusing
`GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET`. The working grant was minted through the
OAuth Playground and carries its own client, so the adapter reads
`GOOGLE_CALENDAR_CLIENT_ID` and `GOOGLE_CALENDAR_CLIENT_SECRET`. That also keeps
the two connections independent, which is the point of §3 anyway.
`scripts/authorize_calendar.py` was never written -- the Playground already
produced the refresh token, and a second way to mint one is a maintenance cost
with no user.

**`TOOL_NOT_AVAILABLE` is a new reason code.** §8's narrowing rule needed
somewhere to record itself. It is server-owned: it is absent from
`CLASSIFIER_REASON_CODES`, so a classifier cannot emit it, matching how
`TOOL_REQUESTED_BUT_DISABLED` already worked.

**Schema validation is hand-rolled, not `jsonschema`.** §4 assumed the library;
it is not a dependency and the enforced subset is `type`, `properties`,
`required`, `additionalProperties`, `enum` and `maxLength`. `validate_arguments`
documents that a schema growing past that subset silently loses enforcement.

**`fill_arguments` sees a widened schema.** Held to the tool's own schema, a
provider has no way to say it could not work out a date -- so it invents one. The
response schema is therefore the tool's properties plus an `error` field and
nothing required, while `ToolRegistry.run` still enforces the strict schema. A
cheap model sometimes returns both real arguments *and* an error, or fills the
error with the schema's own description text, so the refusal is detected
structurally: no arguments means a refusal, arguments always win.

**The completion takes the schema per call**, unlike the classifier's, whose
schema is fixed. `ChatProviderBundle.tool_arguments` supplies it for all four
providers via `integrations/llm/tool_arguments.py`.

**The date guard is one-sided, not a window.** §7 said "`start` within one year
of now". The failure it names -- a model resolving "next January" against the
year that just ended -- lands only months in the past when the current month is
August, so a symmetric window waves it through. The check is
`now - 1 day <= start <= now + 365 days`.

**The reply prompt gains a carve-out, but only on tool turns.** The reply system
instruction says not to mention tools, and a test asserts it. Both the extra
instruction and the `tool_result` key are added only when a tool actually ran, so
a turn with the flags off sends exactly the payload it sent before this work.

### Verified

- On the stale pre-architecture base, the full unit + integration suite was green,
  `ruff check .` and `mypy src` were clean, other
  than three fixture/corpus failures and three unused-import warnings that are
  present on a stashed baseline too. This is historical evidence, not proof that the current
  `dev` integration passes.
- Live, through the composed runner: real Gemini resolved "họp team ngày mai 3
  giờ chiều" to `2026-08-26T15:00:00+07:00`, the real calendar accepted it, a
  retry with the same idempotency key returned the same link rather than a second
  event, and a past-dated request was refused locally.

### Not done

The M0 architecture port and all post-port verification are outstanding. In particular, the
draft's `app.state.chat_tool_runner` must be replaced with typed `ChatRuntime` composition and
the shared-file deltas must be reapplied without losing current `dev` behaviour.

§11's two fixture cases and the label re-run are also outstanding. The fixture's
loader test asserts all four groups hold exactly `len(cases) // 4` cases, so two
cases cannot be added without either four filler cases or a fifth group -- and
re-running the 60 labelled cases spends 60 live classifier calls. Both are
decisions for a human, not defaults to pick.

---

## 14. The M0 port, as performed

`dev` was merged into the registry branch and the preserved draft re-applied on top. Only
`app.py` and `controller.py` conflicted; both were reset to `dev`'s version and the registry
delta re-applied by hand, so no part of the stale worktree's structure survived. Every other
file merged three-way cleanly, keeping both sides — `dev`'s classifier tier-2 rewrite and
langfuse-import removal sit alongside the draft's TIER 4.5 and `tool_result` carve-out.

What changed relative to the draft, and why:

- **`ChatRuntime.chat_tool_runner`, not `app.state.chat_tool_runner`.** The runner is a
  provider-upgrade slot exactly like `chat_reply`: it boots `None` in `build_chat` and is set in
  the same `replace` after the provider block resolves, because filling a tool's arguments needs
  those providers. C10 permits no new `app.state` survivor, and the controller factory reads it
  as `chat.chat_tool_runner`.
- **Calendar settings resolve once in `create_app`** and reach `lifespan` as a closure capture,
  matching how `EvaluationSettings` is handled. `_chat_tool_runner` now takes the settings as a
  parameter instead of calling `from_env()` itself, so no turn re-reads the environment.
- **`self._memory.read_active_turns()`**, the public interface, replaces the draft's private
  `_read_active_turns()` call.
- **`tool_result` is threaded into the real `assemble_generation_context` only.** `dev` grew a
  second, throwaway call inside the `searches_information` branch that exists to count evidence;
  passing the tool result there would change nothing and imply it matters.
- **Three test `ChatRuntime` constructions** gained `chat_tool_runner=None`. Giving the field a
  default would have been less churn, but the other three upgrade-slot fields have no defaults
  either, and a default is how the next added field gets silently forgotten.

Verified on the ported tree:

- `uv run pytest -q` — **2233 passed, 9 skipped, 0 failed**. The three corpus/fixture failures
  that were baseline before the port are fixed on `dev` and no longer appear.
- `uv run ruff check .` — clean. The three `chat_reply.py` F401s that were baseline are gone;
  `dev` removed those imports.
- `uv run mypy src` — clean, 210 files.
- Route-table oracle (§7.3 of the architecture program): **63 routes, byte-identical before and
  after**, SHA-256 `17923647a91b1d7c179ed4f7c3ea6cafe182337de8eaaaa700bcc750cb04d5ee` on both
  sides. This slice adds no route.
- No `app.state.chat_tool_runner`, no private `_read_active_turns`, no conflict markers, one
  `stream_message`, `mail_scan_reconciliation.py` untouched.

Still outstanding, unchanged by the port: §11's two fixture cases and their live label re-run.
The executable-chat-tool ADR that §9 required is written — [ADR-019](../adr/ADR-019-executable-chat-tools-run-under-a-per-user-grant.md)
— and it makes replacing §10's shared refresh token a precondition rather than a wish; see
[SPEC-per-user-google-calendar-oauth](SPEC-per-user-google-calendar-oauth.md).
