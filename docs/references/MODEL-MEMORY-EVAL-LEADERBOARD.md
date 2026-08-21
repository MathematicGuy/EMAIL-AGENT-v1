# LLM Memory Evaluation Leaderboard & Benchmark Registry

This document records the cross-model benchmark results for the Cowork Agent Memory System evaluated against the wide 20-probe benchmark dataset [`evaluations/MEMORIES/probes/v2-four-scopes-wide.json`](../../evaluations/MEMORIES/probes/v2-four-scopes-wide.json) (60 live calls across 3 arms: `full`, `ablated`, `control` plus multi-turn memory seeding).

---

## 1. Main Model Leaderboard (Active & Competitive Models)

Sorted by overall capability, reliability, and strict attribution $(P, F, F)$:

| Rank | Model Name | Provider | Full Pass Rate | Scope Earned It $(P, F, F)$ | Restraint (Anti-Hallucination) | Avg Latency | Seed Failures | Status / Recommendation |
|:---:|---|---|:---:|:---:|:---:|:---:|:---:|---|
| **1** | **Gemini 3.5 Flash Lite** (`gemini-3.5-flash-lite`) | Google Gemini | **80.0%** (16/20) | **45.0%** (9/20) | 70.0% (7/10) | **4.6s** | **0** (100% clean) | 🟢 **Primary Production Recommendation** (ultra-fast, zero dropouts, high reasoning) |
| **2** | **DeepSeek V4 Flash** (`deepseek/deepseek-v4-flash-0731`) | OpenRouter | **80.0%** (16/20) | **40.0%** (8/20) | **100.0%** (10/10) | 17.5s | 8 (gateway rate-limits) | 🟢 **Best Fallback Candidate** (superior restraint & clean refusals, slower than Gemini) |

---

## 2. Weak Models List (Dropped from Primary Comparison)

Models that failed basic viability due to poor reasoning, excessive latency, or high dropouts:

| Model ID | Provider | Full Pass Rate | Avg Latency | Seed Failures | Note |
|---|---|:---:|:---:|:---:|---|
| `inclusionai/ling-3.0-flash` | OpenRouter | **45.0%** (9/20) | 27.4s | 18 | Dropped due to severe generation timeouts, frequent dropouts, and low memory recall. |

---

## 3. Scope Breakdown Matrix

### Gemini 3.5 Flash Lite (`gemini-3.5-flash-lite`)
- **`short_term` (5/5 - 100%)**: 3 Earned-It, 2 Did Nothing (safe refusal), 0 errors. Perfect in-session memory & correction handling.
- **`long_term` (4/4 - 100%)**: 1 Earned-It, 3 Did Nothing (safe refusal). Accurate user persona & profile preservation.
- **`episodic` (4/5 - 80%)**: 2 Earned-It, 2 Did Nothing, 1 Broken (`ep_update_01`). Reliable cross-session task retrieval.
- **`semantic` (3/6 - 50%)**: 3 Earned-It on Recall (100% recall success). Restraint probes flagged as `dangerous` with `certain:false` due to verbose courteous refusals.

### DeepSeek V4 Flash (`deepseek/deepseek-v4-flash-0731`)
- **`short_term` (5/5 - 100%)**: 3 Earned-It, 2 Did Nothing, 0 errors. Fast and accurate in-session recall.
- **`long_term` (4/4 - 100%)**: 1 Earned-It, 1 Did Nothing, 2 Unreadable (OpenRouter gateway dropout on control arm).
- **`episodic` (4/5 - 80%)**: 2 Earned-It, 1 Did Nothing, 1 Dangerous (`ep_update_01`), 1 Unreadable.
- **`semantic` (3/6 - 50%)**: 2 Earned-It, 1 Did Nothing, 3 Unreadable (OpenRouter 60s read timeout on full arm).

---

## 4. Candidate Models for Next Evaluation Sessions

Configured in [`config`](../../config) under `# Candidate Models for Memory Evaluation`:

1. **Gemini 3.5 Flash** (`gemini-3.5-flash`): Full-tier version of 3.5 Flash with larger parameter capacity.
2. **Gemini 3.6 Flash** (`gemini-3.6-flash`): Next-generation Google Gemini Flash architecture.
3. **Antigravity** (`antigravity`): Google Antigravity Agentic Runtime / Model.
   > **Note for Next Session**: Perform research about Antigravity capabilities (agentic loops, tools, execution sandbox, and context management) before starting its memory evaluation.
4. **Gemma 4 31B** (`gemma-4-31b` / `gemma-4-31b-it`): Google open-weights instruction-tuned flagship model.

---

## 5. Standard Evaluation Execution Commands

```bash
# Evaluate on SQLite scratch backend with Gemini:
POSTGRES_MODE=off PYTHONPATH=src PYTHONIOENCODING=utf-8   uv run python scripts/evaluate_memory.py     --probe-set evaluations/MEMORIES/probes/v2-four-scopes-wide.json     --provider gemini

# Build synthesized markdown evaluation report:
uv run python scripts/build_memory_evaluation_report.py
```
