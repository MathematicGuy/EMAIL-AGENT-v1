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
| T6 | [`scripts/evaluate_tool_intent.py`](../../../scripts/evaluate_tool_intent.py) + [`test_evaluate_tool_intent.py`](../../../tests/unit/scripts/test_evaluate_tool_intent.py) | Built, dry-run green, **run live three times**; 3 of 4 gates now met, §5 F4/F5 |
| T8 | F4a–c fixed in [`tools/arguments.py`](../../../src/cowork_agent/features/ai_chat/tools/arguments.py) + 4 tests | Done — `start_exact` 4/7 → **7/7** |
| T7 | This document | Done |

**121 new tests**, all offline, all in the default suite. No new marker, no
credentials, no network.

## 2. Gate

```
uv run pytest -q      2352 passed, 14 skipped, 0 failed   (24.2 s)
uv run ruff check .   All checks passed
uv run mypy src       Success: no issues found in 210 source files
route table           63 routes, byte-identical to the post-M0 baseline
```

Before this work the suite was 2233 passed / 9 skipped. 121 tests came from the
QA itself; the remaining 3 came with the F4 fixes.

**`src/` was untouched for the QA proper, and changed only afterwards.** That
ordering is the point. Every finding below was measured against shipped
behaviour first and recorded before anything was edited, so the fixes are
answering evidence rather than defining it. The one file that changed is
`tools/arguments.py`; the router, the resolver, and the calendar tool are as they
were.

### Re-measured after the per-user calendar grant

[`SPEC-per-user-google-calendar-oauth`](../../../tasks/specs/SPEC-per-user-google-calendar-oauth.md)
landed after the numbers above. It changes *whose* calendar a turn writes to,
not how a turn decides what to write, so every gate in §5 is unmoved — the same
`fill_arguments` prompt against the same fixtures.

```
uv run pytest -q      2382 passed, 14 skipped, 0 failed   (24.1 s)
uv run ruff check .   All checks passed
uv run mypy src       Success: no issues found in 213 source files
route table           67 routes  (63 + 4, nothing removed)
```

| Added | |
|---|---|
| `GET /v1/calendar/oauth/google/connect` | starts the consent for a signed-in user |
| `GET /v1/calendar/oauth/google/callback` | stores the grant against the session's principal |
| `GET /v1/calendar/connection` | the status the frontend renders |
| `DELETE /v1/calendar/connection` | revokes the calendar grant, and only that (J6) |

The route table is the one number that moved, and it moved because a new
router mounted. Measured with the §7.3 oracle from
[`SPEC-architecture-improvement-program.md`](../../../tasks/specs/SPEC-architecture-improvement-program.md),
before and after, on the same checkout. The 63-route figure was an invariant of
the *tool registry* port, never of the application forever; `test_no_route_accepts_caller_provided_identity`
is the invariant that actually guards it, and it still passes over all 67.

The 30 new tests are the J1–J7 invariants: [`test_calendar_oauth.py`](../../../tests/unit/integrations/google_calendar/test_calendar_oauth.py)
(storage and consent), [`test_calendar_router.py`](../../../tests/unit/api/test_calendar_router.py)
(every path out of the callback), [`test_calendar_binder.py`](../../../tests/unit/features/ai_chat/test_calendar_binder.py)
(which grant a turn resolves). Six mutations, six reds — J2's silent
environment fallback and J4's lost mail connection were mutated hardest,
because both are invisible in a green suite.


## 3. Acceptance criteria

| | Criterion | Evidence |
|---|---|---|
| AC1 | Layer A runs in the default suite, offline | No marker, no network, no credentials. The three new files take 1.3 s run serially; under the parallel scheduler the whole-suite cost is below the run-to-run spread (22.2 s / 23.6 s across two runs) |
| AC2 | All 25 cases pass Layer A | `test_the_route_matches_the_story[tq-001…tq-025]` and siblings |
| AC3 | Each invariant has a test that goes red when broken | §4 |
| AC4 | `ruff` and `mypy` clean; `src/` untouched for the QA, then one file changed by the F4 fixes | §2 |
| AC5 | Route table unchanged | 63 routes, byte-identical |
| AC6 | Layer B built, unit-tested, reports the §6 metrics | 8 tests; dry-run scores 14/14 and exits 0. Run live 2026-08-26 against `gemini-3.5-flash-lite`: 2 of 4 gates at baseline, **3 of 4 after the F4 fixes** — see §5 F4/F5 |

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
`tq-019` returned arguments *missing both `start` and `title`*, so the registry
rejected it with `Invalid arguments for create_calendar_event: missing required
start, title`. One in three identical calls landing outside both valid shapes is
the single most consequential number in this run.

The mechanism is exact, and it is in `fill_arguments`: a payload is treated as a
fill whenever *any* non-`error` key survives the filter —

```python
arguments = {key: value for key, value in payload.items() if key != REFUSAL_FIELD}
if arguments:
    return arguments
```

A partial object is therefore never a refusal. It is forwarded to the registry,
fails validation, and the user is shown a schema error instead of a question.
`fill_arguments` is deliberately one attempt with no retry — correct for a
calendar, where a wrong date beats a question — so nothing recovers this. The
fix is to treat a payload missing any `required` field as a refusal, not to add
a retry.

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

### F4 fixed — second and third live runs

All three defects were fixed in
[`tools/arguments.py`](../../../src/cowork_agent/features/ai_chat/tools/arguments.py)
and re-measured. Two further live runs, same model, same 14 cases:

| Gate | Baseline | After F4a–c | After the tq-005 correction |
|---|---|---|---|
| `start_exact` | 4/7 | 6/7 | **7/7** ✅ |
| `schema_accepted` | 5/12 | 8/12 | **9/12** |
| `no_backwards_resolution` | 14/14 ✅ | 14/14 ✅ | **14/14** ✅ |
| `declined_when_underdetermined` | 2/2 ✅ | 1/2 | **1/2** |

What each fix did:

- **F4a** — `fill_arguments` now treats a payload missing any `required` field as
  a refusal instead of a fill, and names the missing fields as a question when
  the model supplied none. `tq-019` has filled correctly in both runs since.
- **F4b** — the prompt now states that a weekday name *is* a date it was given,
  resolved forward, and that added detail such as a duration does not make it
  less certain. `tq-007` and `tq-008` went from refusals to exact instants; this
  is what carries `start_exact` to 7/7.
- **F4c** — the refusal field's schema description asks for "the question to ask
  the user" rather than "the missing information". Refusals are now answerable
  sentences (*"Bạn muốn đi tập gym vào ngày nào và lúc mấy giờ?"*) instead of the
  bare `date` and `tài liệu` of the baseline.
- **F2 confirmed under a fill** — `tq-024` now creates exactly one event at
  02:00 and ignores *tạo 100 sự kiện*. The baseline only showed the injection
  being refused; this shows it contained while the tool actually runs.

### F5 — an ambiguous bare hour is caught by the router, not by the tool

The second run introduced a regression in the worst tier and the third did not
clear it. `tq-005` — *"Tạo lịch gym 2 giờ thứ Sáu"*, where `2 giờ` is 02:00 or
14:00 with nothing to separate them — was refused at baseline and is now
**filled at 09:00**: not either reading of the stated hour, but the header's
working-hour default.

The cause is F4b's own wording. Pushing the model to stop treating relative days
as underdetermined also pushed it to treat a bare hour as no hour at all. A
follow-up instruction — *"an hour the user did name is never replaced by a
default"* — did not recover it, so two prompt attempts have now failed and a
third is not the answer.

**Scope this precisely.** `tq-005`'s route is `clarify`, so on the shipped path
the router asks the user and the argument filler never runs. The scorer includes
`clarify` cases deliberately, to answer "would the model have guessed?" — and the
answer is yes. That makes F5 a missing second line of defence, not an open hole:
today the only thing standing between an ambiguous hour and a real event is the
classifier being right.

The fix is a guard, not a prompt, matching how every other safety property here
works — range checks, scope checks, schema validation. It needs the tool layer to
see the user's message, which it currently does not, so it is a design change
rather than an edit. Recorded, not attempted.

`declined_when_underdetermined` therefore stays red at 1/2, and the run still
exits non-zero. That is the correct reading: one gate is genuinely unmet.

## 6. Still open

1. **F5 — the ambiguous-hour guard.** The only unmet gate. Needs the tool layer
   to see the user's message so a stated-but-undetermined hour can be refused in
   code rather than by prompt. Design change; see §5 F5. Re-run after:

   ```bash
   uv run python scripts/evaluate_tool_intent.py
   ```

2. **The §11 classifier fixtures.** Four cases prepared in
   [`chat_routing_labels_tool_block.json`](../../../tests/fixtures/tool_intent/chat_routing_labels_tool_block.json),
   still unmerged. Blocked on `scripts/evaluate_chat_routing.py` passing
   `tool_axis_enabled=True` and `available_tools={"create_calendar_event"}` —
   without that, two of the four score as failures for a *correct* classifier —
   and on authorizing a 64-call live re-run.

3. **The executable-chat-tool ADR — written.**
   [ADR-016](../../../tasks/adr/ADR-016-executable-chat-tools-run-under-a-per-user-grant.md)
   permits a writing tool only under a per-user grant, and cites F5 as the reason
   the flags stay the last line of defence until the ambiguous-hour guard lands.

4. **Per-user Calendar OAuth — specified, not built.** The shared refresh token
   is still the one thing that cannot ship to real users.
   [ADR-017](../../../tasks/adr/ADR-017-google-grants-stay-separate.md) decides
   two grants with chained consent rather than one merged token, and
   [SPEC-per-user-google-calendar-oauth](../../../tasks/specs/SPEC-per-user-google-calendar-oauth.md)
   breaks it into P1–P8.

## 7. How to re-run

```bash
uv run pytest tests/unit/features/ai_chat/test_tool_intent_qa.py tests/unit/fixtures/test_tool_intent_loader.py tests/unit/scripts/test_evaluate_tool_intent.py -q
```

```bash
uv run python scripts/evaluate_tool_intent.py --dry-run --output-dir evaluations/CHAT/qa-test/tool-intent
```
