# PLAN — Google Calendar tool-use QA

Implementation breakdown for [`SPEC-calendar-tool-qa.md`](SPEC-calendar-tool-qa.md).
Each task is independently verifiable and names its own done condition.

Ordering is a dependency chain, not a preference: T1 gives every later task its
input, T5 cannot report until T2–T4 exist, and T6 is the only task that spends
money.

---

## T1 — Fixture loader

**Deliver:** `tests/fixtures/tool_intent/loader.py` — a typed loader over
`tool_intent_qa.json`, mirroring `tests/fixtures/chat_routing/loader.py`.

**Why a loader and not `json.load` in the test:** the JSON is consumed by three
callers (the pytest runner, the live script, the HTML register). A raw dict in
three places means three copies of the schema and three ways to drift. One
frozen dataclass is the seam.

**Interface:** `load_tool_intent_cases(path=None) -> tuple[ToolIntentCase, ...]`.

**Done when:** the loader raises on a missing field, an unknown tier, or a case
that claims `expected_tool_outcome` without routing to `tool` — and
`tests/unit/fixtures/test_tool_intent_loader.py` proves each rejection.

**Route:** R10.

---

## T2 — Layer A: routing gates

**Deliver:** `tests/unit/features/ai_chat/test_tool_intent_qa.py`, parametrized
by case id over all 25 cases.

**Asserts:** I1 (route) and I2 (server-owned reason codes) per case.

**Done when:** 25 parametrized cases pass, each id visible in `pytest -v` output
so a failure names the story rather than an index.

**Route:** R2.

---

## T3 — Layer A: tool execution

**Deliver:** in the same file — for the 12 `tool`-route cases, drive a real
`ChatToolRunner` over `InMemoryCalendar` with a scripted completion built from
the case.

**Asserts:** I3 (no write without a time), I6 (idempotency), I7 (unsupported
verbs create nothing), and `events_created` per case.

**Explicit limit, stated in the test docstring:** the completion is scripted, so
this proves the handler, not the model. Layer B covers the model.

**Done when:** every `expected_tool_outcome.events_created` matches
`len(calendar.events)`, and every case with a stated `expect_start` produces an
event whose start is that instant.

**Route:** R2.

---

## T4 — Layer A: guards without a model

**Deliver:** direct tests of `build_calendar_tool`'s validation, no runner
involved: end-before-start, all-day/timed mismatch, a date resolved backwards,
a date more than a year out.

**Asserts:** I5's backstop.

**Done when:** each guard returns `ToolResult(ok=False)` with its own message
and the calendar stays empty.

**Route:** R2.

---

## T5 — Mutation verification

**Deliver:** the §8 table in the SPEC, executed. For each of the four
mutations: apply it to `src/`, run the named route, record the failure, revert.

**Done when:** all four mutations produce red, and `git status` is clean
afterwards. Recorded in `PROGRESS.md` §4.

**This is the task that makes the suite worth having.** A green parametrized
suite that has never been shown to fail is decoration.

---

## T6 — Layer B: live scorer *(built, not run)*

**Deliver:** `scripts/evaluate_tool_intent.py` plus
`tests/unit/scripts/test_evaluate_tool_intent.py` (fake completion, offline).

**Asserts offline:** the scorer computes the §6 metrics correctly and writes
well-formed JSON, proven against a fake that returns known-good and known-bad
answers.

**Does not run live.** ~14 structured completions against the configured
provider. Small spend, real provider, so it waits for an explicit instruction.

**Route:** R9.

---

## T7 — Progress report

**Deliver:** `docs/evaluations/CHAT/PROGRESS.md` — what ran, what passed, what
the mutation pass proved, what is still open.

**Done when:** every acceptance criterion in SPEC §7 is either checked with
evidence or explicitly listed as not done, with the reason.

---

## Verification gate (applies to every task)

```bash
uv run pytest -q && uv run ruff check . && uv run mypy src
```

Plus the ADR-015 route-table invariant: 63 byte-identical routes, since this
change touches no route and must prove it.

---

## What this plan deliberately does not do

- **No new marker.** Layer A is offline and fast; hiding it behind `extended`
  would mean it runs never.
- **No change to `src/`.** This is QA over shipped behaviour. If a case reveals
  a defect, it is recorded in the report and fixed in its own change — not
  silently patched to make the suite green.
- **No second owner for the Google adapter.** `smoke_test_google_calendar.py`
  keeps that invariant.
