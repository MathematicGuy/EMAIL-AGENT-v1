# Chat evaluation — narrative docs

Specs, plans, and written results for chat-side evaluation work. Run **artifacts**
(JSON reports, baselines) live under [`evaluations/CHAT/`](../../../evaluations/CHAT/README.md);
this directory holds the reasoning that produced them.

| Document | What it covers |
|---|---|
| [`SPEC-calendar-tool-qa.md`](SPEC-calendar-tool-qa.md) | Google Calendar tool-use QA: the eight invariants, the two test layers, acceptance criteria. |
| [`PLAN-calendar-tool-qa.md`](PLAN-calendar-tool-qa.md) | Task breakdown T1–T7 with per-task done conditions. |
| [`PROGRESS.md`](PROGRESS.md) | What ran, what the mutation pass proved, three findings, four open items. |

Related, elsewhere:

- [`docs/evaluations/RAGAS.md`](../RAGAS.md) — RAG grounding evaluation.
- [`docs/evaluations/SYSTEM-TEST-EVALUATION-PROCESS.md`](../SYSTEM-TEST-EVALUATION-PROCESS.md) — how system tests are tiered.
- [`evaluations/CHAT/qa-test/`](../../../evaluations/CHAT/qa-test/) — the 240-case Vietnamese intent QA set and its reports.
- [`tests/fixtures/tool_intent/`](../../../tests/fixtures/tool_intent/README.md) — the 25 calendar stories this QA runs on.
