# PROGRESS — Google Calendar tool-use QA

Run date: **2026-08-26** · Branch: `claude/cowork-agent-tools-registry-b7ee98`
Spec: [`SPEC-calendar-tool-qa.md`](SPEC-calendar-tool-qa.md) · Plan: [`PLAN-calendar-tool-qa.md`](PLAN-calendar-tool-qa.md)

---

## 1. What shipped

| Task | Deliverable | State |
|---|---|---|
| T1 | [`tests/fixtures/tool_intent/loader.py`](../../../tests/fixtures/tool_intent/loader.py) + [`test_tool_intent_loader.py`](../../../tests/unit/fixtures/test_tool_intent_loader.py) | Done — 11 tests |
| T2–T4 | [`tests/unit/features/ai_chat/test_tool_intent_qa.py`](../../../tests/unit/features/ai_chat/test_tool_intent_qa.py) | Done — 102 tests (97 run, 5 skip) |
| T5 | Mutation pass, §4 below | Done — 4/4 mutations red |
| T6 | [`scripts/evaluate_tool_intent.py`](../../../scripts/evaluate_tool_intent.py) + [`test_evaluate_tool_intent.py`](../../../tests/unit/scripts/test_evaluate_tool_intent.py) | Built, dry-run green, **live run executed — 2 of 4 gates missed**, §5 F4 |
| T7 | This document | Done |

**121 new tests**, all offline, all in the default suite. No new marker, no
credentials, no network.

## 2. Gate

```
uv run pytest -q      2349 passed, 14 skipped, 0 failed   (23.6 s)
uv run ruff check .   All checks passed
uv run mypy src       Success: no issues found in 210 source files
route table           63 routes, byte-identical to the post-M0 baseline
```

Before this change the suite was 2233 passed / 9 skipped. The delta is exactly
the 121 tests added.

`src/` was not modified. That is the point: this is QA over shipped behaviour,
and a case that revealed a defect would be reported here, not silently patched
away.

## 3. Acceptance criteria

| | Criterion | Evidence |
|---|---|---|
| AC1 | Layer A runs in the default suite, offline | No marker, no network, no credentials. The three new files take 1.3 s run serially; under the parallel scheduler the whole-suite cost is below the run-to-run spread (22.2 s / 23.6 s across two runs) |
| AC2 | All 25 cases pass Layer A | `test_the_route_matches_the_story[tq-001…tq-025]` and siblings |
| AC3 | Each invariant has a test that goes red when broken | §4 |
| AC4 | `ruff` and `mypy` clean, `src/` untouched | §2, `git status` |
| AC5 | Route table unchanged | 63 routes, byte-identical |
| AC6 | Layer B built, unit-tested, reports the §6 metrics | 8 tests; dry-run scores 14/14 and exits 0. Live run executed 2026-08-26 against `gemini-3.5-flash-lite`: 2 gates met, 2 missed — see §5 F4 |

## 4. Mutation verification

A green parametrized suite that has never been shown to fail is decoration.
Each invariant was broken in `src/`, the suite re-run, and the change reverted.

| # | Mutation | Invariant | Result |
|---|---|---|---|
| 1 | Deleted the `TOOL_NOT_AVAILABLE` branch in `intent/resolver.py` | I1, I2 | **5 red** — `tq-022`, `tq-023` on both parametrized tests, plus `test_a_tool_the_registry_does_not_hold_is_narrowed_by_name` |
| 2 | `fill_arguments` returns `{}` instead of the refusal sentence | I3 | **1 red** — `test_a_model_that_declines_writes_nothing_and_says_why` |
| 3 | `MAX_DAYS_BEHIND` widened from `1` to `400` | I5 | **2 red** — `test_a_date_resolved_backwards_is_rejected`, `test_every_guard_message_names_the_problem` |
| 4 | `google_event_id` returns a constant, ignoring the seed | I6 | **1 red** — `test_a_retry_creates_one_event_and_a_second_intent_creates_another` |

`git status` clean after each revert. Mutation 4 is worth reading: the naive
mutation (`setdefault` → assignment in `InMemoryCalendar`) does *not* go red,
because a dict keyed by event id absorbs it. What actually carries I6 is that
the event id derives from the idempotency key, so that is what the mutation
targets.

## 5. Findings

### F1 — `tq-013`: the compound request is answered ungrounded *(product gap, not a defect)*

The user's own second story — *"Dựa vào tài liệu này, giúp tôi tạo lịch học"* —
routes to `TOOL`. `finalize_route` collapses `RAG_TOOL` → `TOOL` and clears
`effective_rag`, so an event is created with no document evidence behind it and
no signal that the document was never read.

`tq-014` shows the no-documents case produces a byte-identical outcome: the
`RAG && not has_ready_documents → CHAT` gate never fires, because the collapse
happened first. Two very different situations are indistinguishable to the user.

`tq-015` is the counterweight — with the tool axis **off**, which is every
deployment today, the same request degrades to pure `RAG` and the user gets a
document-grounded plan as text. Arguably the better answer. Worth deciding
before the flag is turned on, rather than discovering after.

**Not fixed here.** Changing the collapse is a routing-contract change and
belongs in its own increment.

### F2 — Delimiter neutralization confirmed working *(positive)*

`tq-024` ends with a literal `</untrusted_data>` followed by an instruction. If
that tag survived into the prompt, the instruction would sit *outside* the quoted
block — the whole difference between data the model reads and an order it obeys.

`neutralize_delimiters` rewrites it to `[delimiter-removed]`. Verified and now
asserted by `test_a_closing_delimiter_in_the_message_cannot_end_the_quoted_block`,
which checks the prompt contains exactly one closing tag and that the payload
text does not appear after it. This behaviour existed; it was not previously
covered by a test naming this attack.

### F3 — The fixture contains duplicate `current_message` values, by design

`tq-001`, `tq-019`, and `tq-020` share one message; so do `tq-013` and `tq-014`.
That is deliberate — a retry and a fresh intent *are* the same sentence, and the
only difference is the idempotency key.

It caught a bug in the first draft of the live scorer's dry-run fake, which
matched answers by locating the message inside the prompt and so collapsed three
stories into one. Any consumer that keys on message text will do the same. The
scorer now serves answers in call order and says why in a comment.

### F4 — The live run: the model under-writes rather than mis-writes

Run 2026-08-26, `gemini-3.5-flash-lite`, 14 cases, one call each. Report:
[`evaluations/CHAT/qa-test/tool-intent/tool-intent-eval-2026-08-26.json`](../../../evaluations/CHAT/qa-test/tool-intent/tool-intent-eval-2026-08-26.json).

| Gate | Result | |
|---|---|---|
| `no_backwards_resolution` | **14/14** | met |
| `declined_when_underdetermined` | **2/2** | met |
| `start_exact` | **4/7** | missed |
| `schema_accepted` | **5/12** | missed |

**Read the failures before the numbers.** Not one of the 14 calls produced an
event at the wrong time. Every miss is a refusal to act, never a wrong write —
which is the cheap direction of the asymmetry SPEC §1 is built on. The three
`start_exact` misses (`tq-007`, `tq-008`, `tq-024`) are declines, not wrong
instants; the guards were never the thing that saved them.

**F4a — the same sentence gets different answers.** `tq-001`, `tq-019`, and
`tq-020` are byte-identical messages against the same clock. Three calls, three
outcomes: a correct 02:00 event, a malformed object, and a correct 02:00 event.
`tq-019` returned arguments *missing both `start` and `title`* and did not use
the refusal field, so it was neither a fill nor a decline — the registry rejected
it with `Invalid arguments for create_calendar_event: missing required start,
title`. One in three identical calls landing outside both valid shapes is the
single most consequential number in this run, and `MAX_ARGUMENT_TURNS = 4` did
not recover it.

**F4b — "thứ Sáu" resolves, except when it doesn't.** The model resolved Friday
correctly in `tq-001` and `tq-002`, then declined `tq-008` — the *same* Friday,
same hour, with a duration added — saying "the specific date for Friday is
missing". Adding *tập khoảng 90 phút* to a sentence it had already resolved
turned a fill into a refusal. `tq-024` behaved the same way ("Which Friday?").
This is an over-refusal, and it is the reason `schema_accepted` sits at 5/12.

**F4c — the decline text is not a question.** The refusal string is shown to the
user. `tq-007` returned the single word `date`; `tq-013` returned `tài liệu`;
`tq-014` returned unaccented `tai lieu va thoi gian cu the`. A refusal that
cannot be read as a question defeats the purpose of having a refusal path at
all. This is a prompt defect in `fill_arguments`, not a model capability limit —
`tq-005` ("the specific date for Friday is missing") and `tq-006` ("when should
the gym session happen") show the same model producing usable text.

**F4d — `tq-016` may have the better answer, and the fixture the worse one.**
The fixture expects the recurring request to create one event. The model instead
declined with "recurring events are not supported in a single
create_calendar_event call, and a specific date was not provided" — which is
exactly what I7 wants a user to be told. The expectation is worth revisiting
before the refusal is treated as a failure.

**Not fixed here**, for the same reason as F1: `src/` is not modified by this QA.
F4a and F4c are both defects with an owner (`tools/arguments.py`), and both are
now measured rather than suspected.

## 6. Still open

1. **F4a and F4c are unfixed.** The malformed-arguments path and the one-word
   refusal both live in
   [`tools/arguments.py`](../../../src/cowork_agent/features/ai_chat/tools/arguments.py)
   and both need a source change plus a re-run to confirm. Re-run:

   ```bash
   uv run python scripts/evaluate_tool_intent.py
   ```

2. **The §11 classifier fixtures.** Four cases prepared in
   [`chat_routing_labels_tool_block.json`](../../../tests/fixtures/tool_intent/chat_routing_labels_tool_block.json),
   still unmerged. Blocked on `scripts/evaluate_chat_routing.py` passing
   `tool_axis_enabled=True` and `available_tools={"create_calendar_event"}` —
   without that, two of the four score as failures for a *correct* classifier —
   and on authorizing a 64-call live re-run.

3. **The executable-chat-tool ADR.** Required by `SPEC-chat-tools-registry.md`
   §9 before `GOOGLE_CALENDAR_ENABLED` or `CHAT_TOOL_AXIS_ENABLED` is turned on
   outside local development. Must not overload ADR-013 or weaken ADR-004's
   Gmail prohibition.

4. **Per-user Calendar OAuth.** The shared refresh token is the one thing that
   cannot ship to real users. Out of this QA's scope; recorded so it is not lost.

## 7. How to re-run

```bash
uv run pytest tests/unit/features/ai_chat/test_tool_intent_qa.py tests/unit/fixtures/test_tool_intent_loader.py tests/unit/scripts/test_evaluate_tool_intent.py -q
```

```bash
uv run python scripts/evaluate_tool_intent.py --dry-run --output-dir evaluations/CHAT/qa-test/tool-intent
```
