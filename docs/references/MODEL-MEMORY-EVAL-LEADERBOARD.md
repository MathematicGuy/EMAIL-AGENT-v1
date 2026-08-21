# LLM Memory Evaluation Leaderboard & Benchmark Registry

This document records the cross-model benchmark results for the Cowork Agent Memory System evaluated against the wide 20-probe benchmark dataset [`evaluations/MEMORIES/probes/v2-four-scopes-wide.json`](../../evaluations/MEMORIES/probes/v2-four-scopes-wide.json) (60 live calls across 3 arms: `full`, `ablated`, `control` plus multi-turn memory seeding).

---

## 1. Main Model Leaderboard (Active & Competitive Models)

Sorted by overall capability, reliability, and strict attribution $(P, F, F)$:

| Rank | Model Name | Provider | Full Pass Rate | Scope Earned It $(P, F, F)$ | Restraint (Anti-Hallucination) | Avg Latency | Seed Failures | Status / Recommendation |
|:---:|---|---|:---:|:---:|:---:|:---:|:---:|---|
| **1** | **Gemini 3.5 Flash Lite** (`gemini-3.5-flash-lite`) | Google Gemini | **80.0%** (16/20) | **45.0%** (9/20) | 70.0% (7/10) | **4.6s** | **0** (100% clean) | 🟢 **Primary Production Recommendation** (ultra-fast, zero dropouts, high reasoning) |
| **2** | **DeepSeek V4 Flash** (`deepseek/deepseek-v4-flash-0731`) | OpenRouter | **80.0%** (16/20) | **40.0%** (8/20) | **100.0%** (10/10) | 17.5s | 8 (gateway rate-limits) | 🟢 **Best Fallback Candidate** (superior restraint & clean refusals, slower than Gemini) |
| **3** | **Gemini 3.5 Flash** (`gemini-3.5-flash`) | Google Gemini | **55.0%** (11/20) | **20.0%** (4/20) | **100.0%** (10/10) | 25.6s | 27 (API rate-limiting during seeding) | 🟡 **Competent but Throttled** (100% short/long-term pass, perfect restraint, throttled on episodic) |

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

### Gemini 3.5 Flash (`gemini-3.5-flash`)
- **`short_term` (5/5 - 100%)**: 3 Earned-It, 2 Did Nothing (safe refusal), 0 errors. Flawless in-session conversational state tracking.
- **`long_term` (4/4 - 100%)**: 1 Earned-It, 3 Did Nothing (safe refusal), 0 errors. Perfect persona attribution and safe refusal.
- **`episodic` (0/5 - 0%)**: 5 Unreadable due to provider throttling during turn generation in seeding.
- **`semantic` (2/6 - 33%)**: 2 Pass on Recall, 4 Unreadable due to provider throttling. Restraint maintained perfectly.

---

## 4. Candidate Models for Next Evaluation Sessions

1. **Gemini 3.6 Flash** (`gemini-3.6-flash`): Next-generation Google Gemini Flash architecture.
2. **Antigravity** (`antigravity`): Google Antigravity Agentic Runtime / Model.
3. **Gemma 4 31B** (`gemma-4-31b` / `gemma-4-31b-it`): Google open-weights instruction-tuned flagship model.

---

## 5. Standard Evaluation Execution Commands

```bash
# Evaluate on SQLite scratch backend with Gemini:
POSTGRES_MODE=off PYTHONPATH=src PYTHONIOENCODING=utf-8   uv run python scripts/evaluate_memory.py     --probe-set evaluations/MEMORIES/probes/v2-four-scopes-wide.json     --provider gemini

# Build synthesized markdown evaluation report:
uv run python scripts/build_memory_evaluation_report.py
```
