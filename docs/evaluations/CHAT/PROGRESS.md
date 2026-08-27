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

### F6 — every successful write carries the wrong UTC offset *(silent wrong write)*

Found by the Tier B run on 2026-08-26 (`e2e/harness/tier_b_server.py`), which is
the first harness to record the **Google request body** rather than only the
tool's return value. Layer A and the three earlier live runs could not have seen
this: they assert on the reply and on `ok`, and both are correct here.

Four of four happy-path cases, plus `tq-024`, produced:

| case | recorded `start.dateTime` | `start.timeZone` | QA expects |
|---|---|---|---|
| `tq-001` | `2026-08-28T02:00:00+00:00` | `Asia/Ho_Chi_Minh` | `2026-08-28T02:00:00+07:00` |
| `tq-002` | `2026-08-28T02:00:00+00:00` | `Asia/Ho_Chi_Minh` | `2026-08-28T02:00:00+07:00` |
| `tq-003` | `2026-08-27T15:00:00+00:00` | `Asia/Ho_Chi_Minh` | `2026-08-27T15:00:00+07:00` |
| `tq-004` | `2026-09-03T09:00:00+00:00` | `Asia/Ho_Chi_Minh` | `2026-09-03T09:00:00+07:00` |
| `tq-024` | `2026-08-28T02:00:00+00:00` | `Asia/Ho_Chi_Minh` | `2026-08-28T02:00:00+07:00` |

The wall-clock digits are right and the reply tells the user the right hour. The
offset is UTC. Google honours an explicit offset over `timeZone`, so a 2AM
request is filed at **09:00 local** — seven hours off, with nothing anywhere
saying so. That is the exact failure class the `silent_wrong_write` tier exists
to catch, and it is 5 for 5.

**Where it comes from.** Not the parser: `_parse_moment`
(`features/ai_chat/tools/calendar.py:160`) attaches the calendar's zone only to
a *naive* timestamp, and `ZoneInfo("Asia/Ho_Chi_Minh")` cannot produce `+00:00`.
So `fill_arguments` is emitting an explicit `Z`/`+00:00`. Its prompt already
says *"Use its timezone offset in any timestamp you write"*
(`tools/arguments.py:26`); `mimo-v2.5-pro` does not. Nothing downstream compares
the offset it wrote against the zone the event is filed in.

**The decision this needs.** A guard in the tool layer that reconciles the two is
cheap, but which one wins is a product call:

* *Wall-clock wins* — reinterpret the digits in the calendar's zone. Correct for
  every case here, wrong for a user who genuinely means another zone.
* *Refuse on mismatch* — never writes the wrong hour, but rejects legitimate
  cross-zone input.
* *Send no offset* — `_bound` already notes Google reads an offset-less
  `dateTime` in the named `timeZone`. Smallest diff; same trade-off as the first.

Recorded, not fixed: changing which zone a write lands in is not a change to make
unilaterally. `e2e/calendar-tool-live.spec.ts` carries a `test.fail()`-marked
case asserting `+07:00`, so the suite turns red the day this starts passing.

### F7 — `tq-005` now writes rather than defaults

The same run re-measured F5. The behaviour has moved: the ambiguous bare hour no
longer falls back to a 09:00 working hour, it resolves to **14:00** and writes.
`expected=0 got=1`. The router still sends this case to `clarify` in the offline
labels, so production is covered by routing — but the tool layer's missing guard
(F5) is now a wrong write rather than a wrong default, which is worse.

### F5/F7 fixed — the ambiguous-hour guard, in code

Closed 2026-08-27 by
[`tools/ambiguous_hour.py`](../../../src/cowork_agent/features/ai_chat/tools/ambiguous_hour.py),
the design change F5 said it needed rather than the third prompt attempt it said
would not work.

**What changed.** `ToolTurnContext` now carries the user's message, so
`build_calendar_tool` can read it. Before a timed event is created, the handler
asks whether the message names an hour it does not determine; if it does, the
turn returns *"The hour is undetermined: 02:00 or 14:00. Confirm which one was
meant."* and nothing reaches the calendar. It sits beside `_validate_range` —
the same shape as every other guard here.

**Why the message and not the arguments.** The filler always resolves to *some*
hour. `start: 2026-08-28T14:00:00+07:00` is indistinguishable whether the user
said `2 giờ chiều` or `2 giờ`; only the message separates them.

**Where the line is drawn.** An hour of 1–12 with no `sáng`/`chiều`/`tối`/`am`/
`pm` near it is undetermined. A 24-hour reading (`14:00`, `20h`) and a
zero-padded one (`09:00`) are not — nobody pads an hour they mean on a 12-hour
clock. All-day events are untouched: they have no hour to get wrong.

**It fails closed, deliberately.** `giờ` is recognised with or without its
diacritics; the qualifiers are recognised only with them. So `2 gio sang` reads
as undetermined and costs a question. Accepting the bare `sang` would be worse
than the miss: it is the preposition in *"dời sang 2 giờ"*, exactly the
reschedule phrasing the guard exists to catch.

**Verified.** 30 new tests. `tq-005` driven with the `14:00` arguments the Tier B
run actually recorded writes nothing; `tq-001`, which differs by one word,
still writes. Every happy-path, false-positive and unsupported-verb message in
the fixture is left alone —
[`test_ambiguous_hour.py`](../../../tests/unit/features/ai_chat/test_ambiguous_hour.py)
carries them as a parametrized list so a future widening of the pattern has to
break something first.

`scripts/evaluate_tool_intent.py` was taught that a refusal can now come from
the tool as well as the filler: `declined` means "nothing reached the calendar"
and a new `refused_by` field says which layer did it. Without that the gate would
have stayed red while the guard worked.

**Measured live, 2026-08-27** —
[`tool-intent-eval-2026-08-27.json`](../../../evaluations/CHAT/qa-test/tool-intent/tool-intent-eval-2026-08-27.json),
14 cases against `mimo-v2.5-pro`:

| gate | 2026-08-26 | 2026-08-27 |
|---|---|---|
| `start_exact` | 7/7 ✅ | 7/7 ✅ |
| `declined_when_underdetermined` | 1/2 ❌ | **2/2 ✅** |
| `no_backwards_resolution` | 14/14 ✅ | 14/14 ✅ |
| `schema_accepted` | 9/12 ❌ | 10/12 ❌ |

F5/F7 is closed on live evidence. Two things in that run are worth keeping.

**The guard did not have to fire.** Both refusals carry `refused_by: "filler"` —
the model asked the question itself, and well: *"Bạn muốn lịch gym lúc 2 giờ
chiều hay 2 giờ sáng?"* The guard stayed the backstop rather than the actor. That
is the outcome the `refused_by` field was added to make visible; without it the
report would say "declined" and a reader would credit the wrong layer. It is also
why the guard's own tests drive it directly rather than through a model — a run
where the filler happens to behave proves nothing about the boundary.

**`schema_accepted` is now measuring F1, not a schema problem.** The two failures
are `tq-013` and `tq-014`, the compound requests. The filler asked for more
detail instead of filling, which is the correct behaviour for a request whose
arguments live in a document nobody retrieved. The gate counts it as a schema
miss because it cannot tell a product gap from a defect. Same shape as `cr-063`
in the routing benchmark below, and it should be treated the same way — but that
is a scoring change, not a code fix, and it is not made here.

### The 64-case routing benchmark, live *(new, 2026-08-27)*

[`chat-routing-eval-2026-08-27.json`](../../../evaluations/CHAT/baselines/chat-routing-eval-2026-08-27.json)
— the first live run with the tool axis on, `mimo-v2.5-pro`, prompt
`chat-intent-v4`.

```
retrieval_recall     1.0        retrieval_precision  1.0     missed_rag_rate 0.0
tool_recall          1.0        tool_precision       1.0
classifier_p95_ms    8506  ❌   (gate: <= 1500)
rag_tool_downgraded  cr-063
```

**The tool axis is clean.** Both labelled tool cases routed to `tool`, and not
one of the other 62 did — including `cr-062`, the calendar-mention distractor
the block exists to trap. That is the direction that writes to a real calendar,
and it is the direction the gate checks.

**F8 — the classifier misses its latency budget by 5.7x.** The only failing
metric, and it is not close: p95 is 8506 ms against a 1500 ms gate, and the
*fastest* of the 64 calls took 2084 ms. No configuration of this provider passes.
The threshold was set when `gemini-3.5-flash-lite` was the configured
classifier; `config` now sets `LLM_PROVIDER=mimo`. This is on the critical path
of every chat turn, before any answer starts streaming. Two honest options —
move the provider back for routing, or re-baseline the gate against the provider
actually in use — and the gate should not be relaxed just to turn the report
green. 2 of 64 calls fell back and 7 retried, which is the same instability seen
from a different angle.

**`cr-062` routed to `clarify` rather than `chat`.** *"Lịch tuần này của tôi kín
quá, có cách nào sắp xếp hiệu quả hơn không?"* — the model asked what to
rearrange instead of answering. No metric scores it: the report compares the rag
and tool booleans, not the route string. Harmless in the safe direction, and
recorded here because nothing else would record it.

## 6. Still open

0. **F6 — the UTC-offset write.** The highest-severity item here and the only one
   that silently puts a real event at the wrong hour. Needs the product decision
   in §5 F6 before a guard can be written.

1. **F8 — the classifier misses the routing latency budget.** New, and now the
   only failing metric in the routing benchmark: p95 8506 ms against a 1500 ms
   gate, with the fastest of 64 calls at 2084 ms. The threshold was set against
   `gemini-3.5-flash-lite`; `config` sets `LLM_PROVIDER=mimo`. Either move the
   classifier back or re-baseline the gate against the provider in use — but not
   by relaxing the threshold to make the report green. See §5.

   ~~**F5 — the ambiguous-hour guard: written, not yet re-measured.**~~ Closed
   2026-08-27: `declined_when_underdetermined` is 2/2 live.

2. **The §11 classifier fixtures — merged, not yet re-measured.** `cr-061` to
   `cr-064` now live in
   [`chat_routing_labels.json`](../../../tests/fixtures/chat_routing/chat_routing_labels.json)
   (64 cases, 16 per group). `scripts/evaluate_chat_routing.py` routes with
   `tool_axis_enabled=True` and the registry's own names, and renders the
   TIER 4.5 tool block into the live prompt, so a correct classifier is no
   longer scored as two failures. `ChatRoutingMetrics` gained `tool_recall`,
   `tool_precision`, `missed_tool_case_ids` and `false_tool_case_ids`, and gates
   on precision — a false tool positive is the direction that writes to a real
   calendar. `cr-063` is excluded from the retrieval metrics and named in
   `rag_tool_downgraded_case_ids`: F1 drops its retrieval half by design, and
   scoring that as a classifier miss would never clear.

   ~~What is still owed is the 64-call live re-run.~~ Run 2026-08-27:
   [`chat-routing-eval-2026-08-27.json`](../../../evaluations/CHAT/baselines/chat-routing-eval-2026-08-27.json).
   Retrieval and tool metrics are all 1.0; the gate fails on latency alone
   (F8 above).

3. **The executable-chat-tool ADR — written.**
   [ADR-019](../../../tasks/adr/ADR-019-executable-chat-tools-run-under-a-per-user-grant.md)
   permits a writing tool only under a per-user grant, and cites F5 as the reason
   the flags stay the last line of defence until the ambiguous-hour guard lands.

4. **Per-user Calendar OAuth — specified, not built.** The shared refresh token
   is still the one thing that cannot ship to real users.
   [ADR-020](../../../tasks/adr/ADR-020-google-grants-stay-separate.md) decides
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
