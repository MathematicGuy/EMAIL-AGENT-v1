# SPEC — Google Calendar tool-use QA

> **Status:** Layer A implemented and green. Layer B implemented and run — 2 of 4 gates missed, see [`PROGRESS.md`](PROGRESS.md) §5 F4.
> **Feature under test:** the chat tool plane from `tasks/specs/SPEC-chat-tools-registry.md` (M0–M4, flag-off).
> **Data:** [`tests/fixtures/tool_intent/`](../../../tests/fixtures/tool_intent/README.md)

---

## 1. Why this QA exists

The calendar tool writes to a real, shared, user-visible resource. Every other
failure in this codebase produces a wrong sentence; this one produces a wrong
*event*, which the user finds at 2 a.m. or does not find at all.

That asymmetry sets the whole priority order. A missed write costs one repeated
sentence. A wrong write costs trust, and it costs it silently.

The existing unit tests
([`test_calendar_tool.py`](../../../tests/unit/features/ai_chat/test_calendar_tool.py),
[`test_tool_arguments.py`](../../../tests/unit/features/ai_chat/test_tool_arguments.py),
[`test_controller_tool_route.py`](../../../tests/unit/features/ai_chat/test_controller_tool_route.py))
prove the *plumbing*: the runner binds, the registry validates, a failure
degrades the turn. They do not prove the *judgement* — whether a real sentence
in a real language ends up as the right event at the right time, or as no event
at all when it should be none.

This QA covers the judgement.

---

## 2. Scope

### In

| Layer | Boundary exercised | Determinism |
|---|---|---|
| **A. Gate & guard** | `finalize_route` → `ChatToolRunner` → `build_calendar_tool` handler → `InMemoryCalendar` | Fully deterministic, offline, no provider |
| **B. Argument judgement** | `fill_arguments` against the configured `ToolArgumentCompletion` | Live model, non-deterministic, scored not asserted |

### Out

- The Google Calendar HTTP adapter. `scripts/smoke_test_google_calendar.py` already owns that seam against a real calendar; duplicating it here would give two owners for one invariant (`tests/README.md` §3).
- The intent classifier's own accuracy. `evaluations/CHAT/qa-test/` and `scripts/evaluate_chat_routing.py` own that. This QA takes classifier output as **given input** and tests what the system does with it.
- Anything requiring `GOOGLE_CALENDAR_ENABLED=true`. The flag stays off; the executable-chat-tool ADR is still unwritten (`SPEC-chat-tools-registry.md` §9).

---

## 3. The requirement, stated as invariants

Numbered so a failure report can cite one.

**I1 — A tool turn narrows, never widens.** For every case, the route
`finalize_route` returns is the labelled route. Capability gates only ever
remove capability.

**I2 — A disabled or absent tool is reported once, in the right words.**
`tool_requested_but_disabled` when the axis is off; `tool_not_available` when
the name is not registered. Never both for one cause.

**I3 — No write without a time.** When the message does not determine a start,
the turn produces a question, not an event. `InMemoryCalendar.events` stays
empty.

**I4 — No write for talking about calendars.** Mentioning a schedule, asking
how a feature works, describing a past action, or explicitly declining one must
not create an event.

**I5 — A relative day resolves forward.** "thứ Hai" said on a Wednesday is the
*next* Monday. The one-sided past-date guard (`MAX_DAYS_BEHIND = 1`) is the
backstop, and it must fire when the resolution goes backwards.

**I6 — A retried turn creates one event.** Same idempotency key → one event.
Different keys → two, because those are two genuine intents.

**I7 — An unsupported verb creates nothing.** Move, cancel, and repeat have no
handler. The failure mode to prevent is not inaction; it is *creating a second
event* while the user believes the first was moved or removed.

**I8 — Quoted text is data.** Instructions inside the user's message or inside a
retrieved document never become tool calls.

---

## 4. Test data

25 cases in [`tool_intent_qa.json`](../../../tests/fixtures/tool_intent/tool_intent_qa.json),
anchored at `now = 2026-08-26T09:00:00+07:00`, a **Wednesday**.

| Tier | n | Invariant | Why it earns its place |
|---|---|---|---|
| `happy_path` | 4 | I1 | The feature works at all, VI and EN. |
| `silent_wrong_write` | 4 | I3, I5 | The most expensive failure in the system. |
| `false_positive_write` | 4 | I4 | A wrong write costs more than a missed one. |
| `compound_request` | 3 | I1 | "Based on this document, make me a schedule." |
| `unsupported_verb` | 3 | I7 | Move/cancel/repeat. |
| `duplicate_write` | 2 | I6 | Retry vs. two genuine turns. |
| `capability_gate` | 3 | I2 | Today's actual deployment: flag off. |
| `prompt_injection` | 2 | I8 | Message text and document text. |

The 80/20 the user asked for: `happy_path` is the 80% that must not break;
`silent_wrong_write` + `false_positive_write` + `unsupported_verb` are the 20%
that decide whether anyone trusts the feature after week one. Eleven of the 25
cases sit in those three tiers.

Every `expected_final_route` and `expected_appended_reason_codes` value was
**generated by running the real `finalize_route`**, not written by hand. They
record shipped behaviour, so a future change can genuinely disagree with them.

---

## 5. Layer A — gate & guard (offline)

**Runner:** `tests/unit/features/ai_chat/test_tool_intent_qa.py`, parametrized
over the 25 cases. Route: R2. No network, no provider, no marker — it joins the
default suite.

For each case:

1. Build an `IntentDecision` from `classifier_labels`.
2. Run `finalize_route` with that case's `context` (`tool_axis_enabled`, `available_tools`).
3. Assert route == `expected_final_route` (**I1**).
4. Assert the server-owned reason codes == `expected_appended_reason_codes` (**I2**). Classifier-owned codes vary by model and are deliberately not asserted.
5. When the route is `tool`, drive a real `ChatToolRunner` over an `InMemoryCalendar` with a **scripted** completion derived from the case, and assert `events_created` and, where stated, the resolved `start`/`end` (**I3, I6, I7**).

Layer A's honest limit: step 5 scripts the model's answer, so it proves the
*handler* accepts a well-formed answer and produces exactly one event — not that
a model would produce that answer. That is Layer B's job, and the split is
stated here rather than papered over.

Layer A additionally asserts the guards directly, without a model in the loop:
end-before-start, all-day/timed mismatch, a backwards-resolved date, and a date
more than a year out. Those are I5's backstop.

## 6. Layer B — argument judgement (live, opt-in)

**Runner:** `scripts/evaluate_tool_intent.py`. Not a pytest test — it spends
money, and `tests/README.md` §1 keeps the suite offline by construction.

For the 12 `tool`-route cases plus the two `clarify` cases, call the configured
`ToolArgumentCompletion` through the real `fill_arguments` and score:

| Metric | Definition | Target |
|---|---|---|
| `start_exact` | resolved start == `expect_start` | 12/12 |
| `refusal_when_underdetermined` | returns a string, not arguments, for `tq-005`/`tq-006` | 2/2 |
| `no_backwards_resolution` | no case resolves before `now` | 14/14 |
| `injection_ignored` | `tq-024`/`tq-025` produce the legitimate event or none | 2/2 |

Output: JSON into `evaluations/CHAT/qa-test/tool-intent/`, matching the
convention the other eval CLIs already use.

**Cost:** ~14 structured completions on the `ChatIntentSettings.model` tier.
Small, but real, and it hits a live provider — so it runs on an explicit
instruction, never as a default.

---

## 7. Acceptance criteria

- [x] **AC1** — Layer A runs in the default `uv run pytest -q` with no new marker, no network, no credentials.
- [x] **AC2** — All 25 cases pass Layer A.
- [x] **AC3** — Each of I1–I8 is asserted by at least one named test, and breaking the corresponding source line turns that test red (verified by mutation, §8).
- [x] **AC4** — `ruff check .` and `mypy src` stay clean; no change to `src/` behaviour.
- [x] **AC5** — Route table unchanged: 63 byte-identical routes (ADR-015 invariant).
- [x] **AC6** — Layer B script exists, is unit-tested against a fake completion under R9, and reports the §6 metrics. *(run 2026-08-26; the run itself is red — that is a finding about the model, not an unmet criterion)*

---

## 8. Verification, not assertion

A parametrized suite that passes proves nothing until it has been shown to
fail. Before this SPEC is marked done, each invariant is mutated in `src/` and
the corresponding test must go red:

| Invariant | Mutation | Expected red |
|---|---|---|
| I1/I2 | delete the `TOOL_NOT_AVAILABLE` branch in `resolver.py` | capability-gate cases |
| I3 | make `fill_arguments` return `{}` instead of a refusal string | `tq-005`, `tq-006` |
| I5 | widen `MAX_DAYS_BEHIND` to 400 | backwards-date guard test |
| I6 | make `google_event_id` ignore its seed | the retry/fresh-key pair |

Note on I6: the obvious mutation — `setdefault` → assignment in
`InMemoryCalendar` — does *not* go red, because a dict keyed by event id absorbs
it. What actually carries the invariant is that the id derives from the
idempotency key, so that is what gets broken.

Results recorded in [`PROGRESS.md`](PROGRESS.md) §4.

---

## 9. Recommended skills

Applied in this order, and the reason each earns its place:

| Phase | Skill | What it contributed |
|---|---|---|
| Define | `agent-skills:spec-driven-development` | This document — invariants before a runner. |
| Plan | `agent-skills:planning-and-task-breakdown` | [`PLAN-calendar-tool-qa.md`](PLAN-calendar-tool-qa.md), tasks with acceptance criteria. |
| Build | `claude-code-qa` | Behaviour-not-implementation, determinism, the anti-pattern list. |
| Build | `agent-skills:test-driven-development` | §8 — prove red before claiming green. |
| Build | `mattpocock-skills:codebase-design` | Kept the harness a deep module: one loader interface over 25 cases. |
| Review | `agent-skills:security-and-hardening` | The `prompt_injection` tier and the untrusted-data boundary. |
| Ship | `agent-skills:documentation-and-adrs` | `PROGRESS.md`, and the still-open executable-chat-tool ADR. |

Not used, deliberately: `browser-testing-with-devtools` (no UI in scope),
`performance-optimization` (the suite is 25 offline cases).

---

## 10. Known gaps this QA documents but does not fix

1. **`tq-013` — the compound-request gap.** `finalize_route` collapses
   `RAG_TOOL` → `TOOL` and clears `effective_rag`, so "based on this document,
   create a learning schedule" produces an event with no document evidence
   behind it, and no signal that the document was never read. `tq-014` shows
   the no-documents case is byte-identical. This is a product decision, not a
   defect; it is recorded so it is decided rather than discovered.
2. **The §11 classifier fixtures are still unmerged.** Blocked on
   `scripts/evaluate_chat_routing.py` passing `tool_axis_enabled=True` and on a
   64-call live re-run. See the fixture README.
3. **The executable-chat-tool ADR is unwritten.** Required before either flag
   is enabled outside local development.
