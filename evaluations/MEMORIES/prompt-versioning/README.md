# Prompt versioning — the diagnosis loop

The report says **what** happened. This directory is about **where to look** and
**what was tried**. Two pieces:

- `scripts/triage_memory_evaluation.py` turns a finished run into one issue file
  per probe whose cause is still open, each carrying the evidence needed to
  answer "why".
- `<slot>/v<N>-<date>.md` here records a prompt version: the hypothesis written
  before the run, and the verdict after it.

## The loop

```
run eval  ->  report  ->  triage harness  ->  agent triage  ->  hypothesis
   ^                                                               |
   +------------------- approve spend, rerun  <--------------------+
```

1. **Run** the eval (RUNBOOK). It writes `baselines/*.json` and `runs/*-detail.json`.
2. **Triage**: `python scripts/triage_memory_evaluation.py`. Writes issues to
   `runs/triage/<run_key>/`, gitignored because they contain reply and seed text
   (RUNBOOK rule 5).
3. **Diagnose**: a coding agent works `ISSUES.md` top to bottom, filling the
   triage record at the bottom of each issue file.
4. **Hypothesise**: if the cause is the prompt, write a version file here —
   hypothesis and target metric **before** the run.
5. **Rerun** only with explicit approval. A `v3` run is 60 live provider calls.
6. **Conclude**: verdict `confirmed` / `refuted` / `inconclusive`, plus what
   worked, what didn't, and the next idea.

## Which failures are prompt failures

`fault.classify` derives this from the three arms; it is not a judgement call.

| Verdict / shape | Fault class | Why |
|---|---|---|
| `dangerous`, full arm `invented`/`stale`, both blind arms clean | `prompt_fault` | Memory delivered; generation misused it. |
| `dangerous`, a blind arm also `invented`/`stale` | `not_attributable` | It reproduces without the scope. |
| `broken` (full arm missed) | `memory_fault` | The fact never reached the model. |
| `leaked` | `memory_fault` | A clean-store arm answered a recall probe. |
| `scope_did_nothing` | `not_attributable` | Answerable without the scope — probe design. |
| `unreadable` | `run_failed` | The provider dropped it. Repeat the run; do not read it. |
| `scope_earned_it`, `restraint_held` | `healthy` | Nothing to act on. |

Issues are written for `prompt_fault` and `not_attributable` only. A
`memory_fault` is already attributed — it needs retrieval work, not a reading;
pass `--all` to include them anyway. A `run_failed` probe is named in
`ISSUES.md` but never gets an issue file: on the 2026-08-23 mistral run four of
five open probes were dropouts, and an agent spent on those is an agent not
spent on the one real defect.

## Version file fields

```
version, slot, date, parent_version
hypothesis          -- written BEFORE the run
target_metric       -- the number expected to move, and by how much
motivating_probes   -- probe ids and their fault class, from the prior run
triage              -- the agent triage records those probes produced
prompt_diff         -- what changed vs parent_version
run                 -- run_key, probe set, provider/model
scoreboard          -- pass rate, scope earned-it, restraint, dangerous, latency
verdict             -- confirmed | refuted | inconclusive
what_worked / what_did_not
next_hypotheses
```

## Rules

- The deterministic verdict is authoritative. An agent that disagrees records the
  disagreement; it does not restate the score.
- Every triage claim cites a probe expectation, a seed line, or an arm reply.
- Propose prompt text; never edit production code, probe JSON, or the grader to
  make a report green (RUNBOOK rule 4).
- Numbers are linked or auto-filled, never retyped from a report.

Full design, including the Langfuse prompt-registry step not yet built:
[SPEC-prompt-versioning-and-performance-tracking.md](../../../tasks/specs/SPEC-prompt-versioning-and-performance-tracking.md).
