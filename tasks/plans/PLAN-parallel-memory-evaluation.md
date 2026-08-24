# Implementation & Verification Plan: Parallel Memory Evaluation Harness

**Status:** Ready for Execution  
**Date:** 2026-08-23  
**Spec Reference:** [`tasks/specs/SPEC-parallel-memory-evaluation.md`](../specs/SPEC-parallel-memory-evaluation.md)  
**Workflow Reference:** [`docs/references/agent-memory/PARALLEL_MEMORY_EVAL_WORKFLOW.md`](../../docs/references/agent-memory/PARALLEL_MEMORY_EVAL_WORKFLOW.md)  

---

## 1. Objectives & Key Deliverables

1. Implement `scripts/evaluate_memory_parallel.py` with `asyncio.Semaphore` task pooling (5 workers default).
2. Ensure full compatibility with probe sets (`v2-four-scopes-wide.json`, `v3-four-scopes-hard.json`).
3. Maintain zero state bleed across concurrent probe-arm invocations.
4. Verify execution speedup ($\ge 4\times$ throughput improvement) and report synthesis.
5. Benchmark `mimo-v2.5` and `mimo-v2.5-pro` on `v2` and `v3` and record in `MODEL-MEMORY-EVAL-LEADERBOARD.md`.

---

## 2. Implementation Phases

```
Phase 1: Parallel CLI Architecture & Worker Engine
    ├── [x] Implement evaluate_memory_parallel.py
    ├── [x] Integrate asyncio.Semaphore(workers=5)
    ├── [x] Thread-safe progress indicators and transcript recorder
    ├── [x] Diagnostic & Resiliency: Automatic Failed Probe Re-run Queue
    └── [x] Linting and formatting compliance (ruff / mypy)

Phase 2: Single-Run Verification & Schema Compatibility
    ├── [x] Run 5-worker parallel evaluation on v2-four-scopes-wide.json (mimo-v2.5)
    ├── [x] Verify baseline JSON schema 2.2.0 output in evaluations/MEMORIES/baselines/
    ├── [x] Verify detail transcript JSON output in evaluations/MEMORIES/runs/
    └── [x] Verify build_memory_evaluation_report.py generates Markdown report successfully

Phase 3: Multi-Model Benchmark Execution (Mimo v2.5 & v2.5 Pro on v2 & v3)
    ├── [ ] Run mimo-v2.5-pro on v2 wide (5 workers)
    ├── [ ] Run mimo-v2.5-pro on v3 hard (5 workers)
    ├── [ ] Run mimo-v2.5 on v2 wide (5 workers)
    ├── [ ] Run mimo-v2.5 on v3 hard (5 workers)
    └── [ ] Generate reports for all 4 benchmark runs

Phase 4: Leaderboard Synchronization & Final Sign-Off
    ├── [ ] Extract metrics (Full Pass Rate, Earned-It P,F,F, Restraint, Latency)
    ├── [ ] Update docs/references/agent-memory/MODEL-MEMORY-EVAL-LEADERBOARD.md
    └── [ ] Run quality gates (ruff check, mypy src)
```

---

## 3. Verification & Quality Gates

### Step 1: Linter & Type Checks
```powershell
uv run ruff check scripts/evaluate_memory_parallel.py
uv run mypy src
```

### Step 2: Live 5-Worker Evaluation Benchmark
```powershell
uv run python scripts/evaluate_memory_parallel.py `
  --provider mimo `
  --model mimo-v2.5-pro `
  --probe-set evaluations/MEMORIES/probes/v2-four-scopes-wide.json `
  --workers 5 `
  --output evaluations/MEMORIES/baselines/mimo-v2.5-pro-v2-parallel.json
```
*Expected Result:* Exit code 0, 60 calls completed in $\le 120\text{s}$, baseline file generated.

### Step 3: Markdown Report Generation
```powershell
uv run python scripts/build_memory_evaluation_report.py `
  --baseline evaluations/MEMORIES/baselines/mimo-v2.5-pro-v2-parallel.json
```
*Expected Result:* Exit code 0, report markdown written to `evaluations/MEMORIES/reports/`.
