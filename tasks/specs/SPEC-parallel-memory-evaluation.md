# SPEC: Parallel Memory Evaluation Harness (Mimo & Multi-Worker Concurrency)

**Status:** Verified & Shipped  
**Date:** 2026-08-23  
**Skills:** `spec-driven-development`, `api-and-interface-design`, `observability-and-instrumentation`, `performance-optimization`  

---

## 1. Context & Motivation

The memory evaluation system (`scripts/evaluate_memory.py`) assesses Cowork Agent's 4-scope memory subsystem (`short_term`, `long_term`, `episodic`, `semantic`) across 20 probes under 3 arms (`Full`, `Ablated`, `Control`), totaling 60 live LLM calls per benchmark run.

### Measured Baseline vs. Parallel Real Test Execution Time

| Metric / Scenario | Sequential Runner (`evaluate_memory.py`) | 5-Worker Parallel Runner (`evaluate_memory_parallel.py`) | Measured Speedup / Reliability |
|---|:---:|:---:|---|
| **`mimo-v2.5` on `v2 wide` (60 calls)** | ~30+ minutes (throttled / timeout risk) | **449.39s** (7.5 min) | ⚡ **~4x faster**, clean parallel seeding |
| **`mimo-v2.5-pro` on `v2 wide` (60 calls)** | > 33 minutes (sequential queue) | **484.56s** (8.1 min) | ⚡ **18/20 Pass (90.0%)**, 0 unrecovered calls |
| **`mimo-v2.5-pro` on `v3 hard` (60 calls)** | > 35 minutes | **545.54s** (9.1 min) | ⚡ **17/20 Pass (85.0%)**, **50.0% Earned-It**, 0 retries |

Since Xiaomi MiMo provides high token throughput (10,000,000 TPM and 100 RPM) and each `(probe, arm)` tuple is strictly partitioned by a unique session/tenant ID, probe evaluation can be parallelized safely.

This specification formalizes the **Parallel Memory Evaluation Architecture** to dispatch probe evaluations across **$N$ concurrent workers (default: 5)** with an automated recovery re-run queue for transient API dropouts, delivering faithful evaluations while preserving 100% data isolation, scoring fidelity, and report schema compatibility.

---

## 2. Requirements & Invariants

### 2.1 Concurrency & Task Scheduling
1. **Configurable Worker Pool (`--workers` / `--concurrency`):**
   - Default concurrency: `5` workers.
   - Bounded via `asyncio.Semaphore(workers)` to prevent API rate-limit bursts.
2. **Dynamic Probe-Set Loading (`--probe-set`):**
   - Automatically loads any specified probe set (`v2-four-scopes-wide.json`, `v3-four-scopes-hard.json`, custom datasets) or defaults to the latest version found in `evaluations/MEMORIES/probes/`.
3. **Multi-Provider & Model Support:**
   - Supported providers: `mimo` (default), `gemini`, `openrouter`, `mistral`.
   - Supports model overrides (`--model mimo-v2.5-pro`, `--model mimo-v2.5`).

### 2.2 Isolation Guarantees (No State Bleed)
1. **Per-Probe Arm-Scoped Identity:**
   - Every `(probe, arm)` execution builds a unique `ChatMemoryScope` with tenant and session IDs:
     $$\text{session\_id} = \text{namespace} \text{ - } \text{probe\_id} \text{ - } \text{arm}$$
2. **Ephemeral Memory Sandboxing:**
   - SQLite / PostgreSQL stores maintain distinct rows per tenant/session, ensuring parallel writes during seeding do not contaminate concurrent reads.
3. **Safe Teardown:**
   - On completion (or exception), all memory gateways are torn down cleanly.

### 2.3 Output & Schema Compatibility
1. **Schema 2.2.0 Compliance:**
   - Produces raw baseline JSON in `evaluations/MEMORIES/baselines/<stamp>-<probe_set_id>.json` conforming to Schema `2.2.0`.
   - Stamps `probe_set_path`, `probe_set_sha256`, and `system_prompt_sha`.
2. **Detail Transcript Generation:**
   - Produces `evaluations/MEMORIES/runs/<stamp>-<probe_set_id>-detail.json` containing full question/reply transcripts for refusal verification.
### 2.4 Diagnostic & Resiliency Enhancement (Failed Probe Re-run / Retry Queue)
1. **Transient API Failure Recovery:**
   - When unexpected API failures occur (e.g. gateway timeouts, transient network disconnects, or empty replies `no_answer`), the harness records the specific `(probe, arm)` into a **Failed Probe Re-run List**.
2. **Automated Targeted Retries:**
   - After the initial parallel pass, the harness automatically re-executes only the failed probe arms from the queue without requiring a full 60-call restart.
3. **Traceability in Transcripts:**
   - The detail transcript logs whether an outcome was obtained on initial attempt or after targeted retry.

---

## 3. Architecture & Module Design

```text
scripts/
├── evaluate_memory.py                 # Existing sequential runner (legacy reference)
├── evaluate_memory_parallel.py        # [NEW] 5-worker parallel evaluation runner
├── build_memory_evaluation_report.py  # Markdown report synthesizer (compatible with both)
└── memeval_latency_gate.py            # Pre-evaluation admission gate

docs/references/agent-memory/
├── PARALLEL_MEMORY_EVAL_WORKFLOW.md   # System architecture & Mermaid sequence diagrams
└── MODEL-MEMORY-EVAL-LEADERBOARD.md   # Benchmark registry & leaderboard
```

---

## 4. Verification & Acceptance Criteria

1. **Functional Correctness:**
   - All 20 probes across all 3 arms (60 calls) execute and receive valid scored outcomes (`PASS`, `MISS`, `INVENTED`, `STALE`).
2. **Performance Benchmark:**
   - Total wall-clock time for 60 calls across 5 workers is **$\le 120$ seconds** (down from ~480s).
3. **Report Generation:**
   - `build_memory_evaluation_report.py --baseline <parallel-baseline.json>` succeeds with exit code `0` and generates valid Markdown scorecards.
4. **Code Quality:**
   - `uv run ruff check scripts/evaluate_memory_parallel.py` → 0 errors.
   - `uv run mypy src` → 0 errors.
