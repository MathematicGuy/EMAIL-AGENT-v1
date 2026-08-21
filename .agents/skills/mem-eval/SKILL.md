---
name: mem-eval
description: Run, diagnose, and report memory evaluations across short-term, long-term, episodic, and semantic memory scopes. Use when asked to "run memory eval", "evaluate memory", "benchmark memory", "generate memory report", "test AI chat memory", or update MODEL-MEMORY-EVAL-LEADERBOARD.md.
---

# Memory Evaluation (mem-eval) Skill

This skill guides the end-to-end operational lifecycle for evaluating the AI Chat memory subsystem across all 4 scopes: `short_term`, `long_term`, `episodic`, and `semantic`.

Authoritative reference documents:
- Runbook & Procedures: [`evaluations/MEMORIES/RUNBOOK.md`](../../../evaluations/MEMORIES/RUNBOOK.md)
- Commands & Store Config: [`evaluations/MEMORIES/README.md`](../../../evaluations/MEMORIES/README.md)
- Model Leaderboard: [`docs/references/agent-memory/MODEL-MEMORY-EVAL-LEADERBOARD.md`](../../../docs/references/agent-memory/MODEL-MEMORY-EVAL-LEADERBOARD.md)
- System Architecture & Invariants: [`tasks/specs/SPEC-memory-evaluation.md`](../../../tasks/specs/SPEC-memory-evaluation.md)

---

## 1. Core Invariants & Safety Guardrails

1. **Environment Setup**: Always run commands using `uv run` (or `.venv/Scripts/python.exe`) with `PYTHONPATH=src` and `PYTHONIOENCODING=utf-8`.
2. **Database Safety**: Never point the harness at a remote or production database (`DATABASE_URL_CLOUD`). By default, evaluations run against an isolated scratch SQLite database (`POSTGRES_MODE=off`). If targeting PostgreSQL, explicitly set `PG_TEST_URL` to a throwaway local database (`cowork_memeval`).
3. **Automatic Defaults**:
   - **Provider & Model**: Default to configured `.env` settings (`LLM_PROVIDER`, e.g. `gemini`).
   - **Eval Dataset**: The harness automatically discovers and runs the **latest probe set** found in `evaluations/MEMORIES/probes/` (e.g. `v2-four-scopes-wide.json`).
4. **Single-Run Policy**: Run one evaluation at a time to prevent advisory lock contention and avoid provider rate-limit dropouts.

---

## 2. Execution Lifecycle

### Step 1: Pre-Flight Verification
Always prove every dependency answers before spending model calls:

```powershell
# SQLite target (zero setup, isolated scratch DB):
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
uv run python scripts/memeval_preflight.py

# PostgreSQL target (throwaway local DB only):
$env:PG_TEST_URL="postgresql://postgres:postgres@localhost:5432/cowork_memeval"
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
uv run python scripts/memeval_preflight.py
```
*(Exit code `0` = Ready to run. Exit code `1` = Abort immediately and fix dependencies).*

---

### Step 2: Running the Evaluation

#### Standard Evaluation (Default Config Provider)
```powershell
uv run python scripts/evaluate_memory.py `
  --output evaluations/MEMORIES/baselines/<name>-sqlite.json
```

#### Cross-Model Benchmarking (Leaderboard Runs)
When comparing specific models for [`MODEL-MEMORY-EVAL-LEADERBOARD.md`](../../../docs/references/agent-memory/MODEL-MEMORY-EVAL-LEADERBOARD.md), pass `--provider` and `--model` explicitly:
```powershell
uv run python scripts/evaluate_memory.py `
  --provider openrouter --model <model-id> `
  --output evaluations/MEMORIES/baselines/<name>-<model>-sqlite.json
```

---

### Step 3: Generating Markdown Reports & Diagnoses
Automate metric aggregation, scorecards, 3-arm matrices, and deterministic diagnostics:

```powershell
uv run python scripts/build_memory_evaluation_report.py
```
*(Report is written to `evaluations/MEMORIES/reports/<YYYY-MM-DD>-<probe-set>.md`).*

---

## 3. Systematic 4-Tier Triage Drill (RUNBOOK §5)

When reviewing "Needs Reading" or anomalous probes in Section 4.2 of the report, use the automated deterministic diagnosis as the baseline and verify against the 4 concerns:

| Concern | Area | Question | Manifestation | Resolution |
|---|---|---|---|---|
| **Concern C** | **Plumbing / Network** | Did we fill, mask, and connect as claimed? | `no_answer`, empty reply `""`, or seed failures | Fix provider connectivity, retry probe run. |
| **Concern A** | **The Grader** | Was the reply graded correctly? | Honest refusal marked as `dangerous`/`invented` due to missing regex pattern | Add refusal pattern to Grader; do not touch production code. |
| **Concern B** | **The Question** | Does the question actually require memory? | `control` arm passes without memory context | Question was guessable; rewrite probe question in probe JSON. |
| **Concern D** | **The Product** | Does memory retrieval / restraint work? | Hallucinated answer on restraint, or retrieval omission on recall | Fix memory prompt / retrieval logic deliberately with failing tests first. |

---

## 4. Leaderboard Maintenance
After running benchmarks across candidate models, record the metrics in [`docs/references/agent-memory/MODEL-MEMORY-EVAL-LEADERBOARD.md`](../../../docs/references/agent-memory/MODEL-MEMORY-EVAL-LEADERBOARD.md):
- Record `Model`, `Pass Rate (Full Arm)`, `Earned-It Rate (P,F,F)`, `Restraint Rate`, `Avg Latency`, and `Date`.
- Note any seed failure anomalies or Grader misclassifications in the notes column.
