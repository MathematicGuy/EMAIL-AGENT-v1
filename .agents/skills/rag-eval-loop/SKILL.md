---
name: rag-eval-loop
description: >
  Autonomous RAG evaluation and optimization loop. Use when the user asks to
  "run evaluation", "benchmark", "optimize retrieval", "improve scores",
  "diagnose low recall", "tune the pipeline", or "compare runs". Use when
  another skill needs to verify RAG quality after a code change.
---

# RAG Evaluation Loop

An evaluation-driven optimization cycle for the Cowork-RAG pipeline.
The agent runs `rag-eval`, reads structured outputs, forms a **hypothesis**
about the **deficit**, applies a targeted fix, and proves improvement via
**delta** comparison — all without human intervention between iterations.

> **Leading words**: *baseline* (the reference run), *deficit* (a metric below
> target), *hypothesis* (a falsifiable explanation for the deficit), *delta*
> (the measured change between baseline and candidate).

---

## Step 1 — Establish the Baseline

Run a full, unfiltered e2e evaluation to create the reference point.

```powershell
rag-eval e2e --name "baseline"
```

Read `reports/rag_evaluation/<run_dir>/manifest.json`. Record:
- `run_id` (the human-readable directory name)
- `dataset_fingerprint`
- `environment_hash`

**Completion criterion**: A `report.json` exists with `status == "complete"`,
`baseline_eligible == true`, and `evaluated_cases == 100`.

---

## Step 2 — Surface the Deficit

Read `report.json` → `target_comparison`. Identify every metric where
`meets_target == false`.

Build a **deficit table** — one row per failing metric:

| Metric | Actual | Target | Layer |
|--------|--------|--------|-------|
| `coordinate_recall` | 0.78 | ≥ 0.85 | Retrieval |
| `evidence_recall` | 0.72 | ≥ 0.85 | Retrieval |

Classify each deficit into a **failure layer** using the lookup in
[`references/diagnostic-map.md`](references/diagnostic-map.md).

**Completion criterion**: Every metric with `meets_target == false` has a row
in the deficit table and a classified failure layer. If no deficit exists, stop
and report *all targets met*.

---

## Step 3 — Localize Failing Cases

For each deficit, inspect the per-case JSONL log:

- **Retrieval deficits** → read `retrieval.jsonl`, filter cases where
  `deterministic_scores.coordinate_recall < 1.0` or
  `deterministic_scores.evidence_recall < 1.0`.
- **Generation deficits** → read `generation.jsonl`, filter cases where
  `deterministic_scores.citation_validity < 1.0`,
  `deterministic_scores.citation_coverage < 1.0`, or
  `deterministic_scores.abstention_accuracy < 1.0`.

For each failing case, extract:
1. `case_id`, `type`, `difficulty`
2. The gap between expected evidence and actual retrieval hits (or expected
   abstention and actual LLM answer)

Record findings as a **failure log** — a list of `(case_id, gap_description)`.

**Completion criterion**: Every case that contributed to a deficit has a
failure-log entry explaining what was missing or wrong.

---

## Step 4 — Form the Hypothesis

State a single, **falsifiable** hypothesis that explains the failure log.
Good hypotheses name a specific tuning knob or code path:

- *"`min_dense_score=0.35` filters Article 22 because its embedding similarity
  to multi-hop queries averages 0.31."*
- *"The system prompt omits the citation-bracket instruction for sentences
  following a list."*

Consult [`references/tuning-knobs.md`](references/tuning-knobs.md) for the
full inventory of parameters and code paths that influence each metric.

**Completion criterion**: The hypothesis names a concrete parameter or code
location, predicts which cases will improve, and is falsifiable by a re-run.

---

## Step 5 — Implement the Fix

Apply the smallest code change that tests the hypothesis. Prefer parameter
changes over structural refactors.

**Completion criterion**: Exactly one logical change committed (or staged),
targeting the parameter or code path named in the hypothesis.

---

## Step 6 — Re-evaluate and Measure the Delta

Run a candidate evaluation with a descriptive experiment name:

```powershell
rag-eval e2e --name "<hypothesis-slug>"
```

Then compare:

```powershell
rag-eval compare --baseline reports/rag_evaluation/<baseline_dir>/report.json --candidate reports/rag_evaluation/<candidate_dir>/report.json
```

Read the delta report. For every metric:
- `🟢 IMPROVED` or `🟢 WITHIN SLA` → hypothesis confirmed for that metric.
- `🔴 REGRESSED` → hypothesis refuted or fix introduced a side-effect.

### Hash Guard

Before trusting the delta, verify in the candidate `manifest.json`:
- `dataset_fingerprint` matches baseline → same golden set.
- `environment_hash` difference is explainable by the intentional change.

If either hash diverges unexpectedly, the comparison is invalid. Diagnose the
hash drift before continuing.

**Completion criterion**: Delta report printed, every metric accounted for as
improved, unchanged, or regressed. If any regression exists, return to Step 4
with a revised hypothesis.

---

## Step 7 — Record the Experiment

Write a short experiment entry to the evaluation tracking log at
`reports/rag_evaluation/experiment_log.md`:

```markdown
### <run_id>
- **Hypothesis**: <one-line summary>
- **Change**: <file:line or setting changed>
- **Baseline**: <baseline_run_id>
- **Delta**: coordinate_recall +3%, evidence_recall +5%
- **Verdict**: ✅ Confirmed / ❌ Refuted / ⚠️ Partial
```

**Completion criterion**: Entry appended. If verdict is *Refuted* or *Partial*
and deficits remain, loop back to Step 4.
