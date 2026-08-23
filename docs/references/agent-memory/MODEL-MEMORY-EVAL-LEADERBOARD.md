# LLM Memory Evaluation Leaderboard & Benchmark Registry

This document records the cross-model benchmark results for the Cowork Agent Memory System evaluated against the wide 20-probe benchmark dataset [`evaluations/MEMORIES/probes/v2-four-scopes-wide.json`](../../evaluations/MEMORIES/probes/v2-four-scopes-wide.json) (60 live calls across 3 arms: `full`, `ablated`, `control` plus multi-turn memory seeding).

---

## 1. Main Model Leaderboard (Active & Competitive Models)

Sorted by overall capability, reliability, and strict attribution $(P, F, F)$:

| Rank | Model Name | Provider | Full Pass Rate | Scope Earned It $(P, F, F)$ | Restraint (Anti-Hallucination) | Avg Latency | Seed Failures | Status / Recommendation |
|:---:|---|---|:---:|:---:|:---:|:---:|:---:|---|
| **1** | **GLM 5.2** (`glm-5-2`) | Mistral AI | **90.0%** (18/20) | **50.0%** (10/20) | 80.0% (8/10) | **4.3s** | 1 | 🟢 **Top Leader / Highest Attribution** (highest 50% Earned-It, 90% Full Pass, fastest 4.3s latency) |
| **2** | **Mistral Medium 3.5** (`mistral-medium-3-5`) | Mistral AI | **90.0%** (18/20) | **45.0%** (9/20) | **90.0%** (9/10) | **4.7s** | **0** (100% clean) | 🟢 **Top Performer / Primary Recommendation** (clean 0 seed failures, 90% restraint, fast 4.7s latency) |
| **3** | **Gemini 3.5 Flash Lite** (`gemini-3.5-flash-lite`) | Google Gemini | **80.0%** (16/20) | **45.0%** (9/20) | 70.0% (7/10) | **4.6s** | **0** (100% clean) | 🟢 **Primary Production Alternative** (ultra-fast, zero dropouts, high reasoning) |
| **4** | **DeepSeek V4 Flash** (`deepseek/deepseek-v4-flash-0731`) | OpenRouter | **80.0%** (16/20) | **40.0%** (8/20) | **100.0%** (10/10) | 17.5s | 8 (gateway rate-limits) | 🟢 **Best Fallback Candidate** (superior restraint & clean refusals, slower than Gemini) |
| **5** | **Gemini 3.5 Flash** (`gemini-3.5-flash`) | Google Gemini | **55.0%** (11/20) | **20.0%** (4/20) | **100.0%** (10/10) | 25.6s | 27 (API rate-limiting during seeding) | 🟡 **Competent but Throttled** (100% short/long-term pass, perfect restraint, throttled on episodic) |
| **6** | **Mistral Small 2603** (`mistral-small-2603`) | Mistral AI | **35.0%** (7/20) | **15.0%** (3/20) | 30.0% (3/10) | **6.5s** | 2 | 🟡 **Low-Tier Fallback** (fast ~6.5s latency, good short-term/persona recall, high semantic amnesia) |

---

## 2. Incompatible & Dropped Models List

Models that failed basic viability, latency gates, or schema adherence:

| Model ID | Provider | Full Pass Rate | Avg Latency | Seed Failures | Failure Reason & Notes |
|---|---|:---:|:---:|:---:|---|
| `gemma-4-31b-it` | Google Gemini | Terminated (4/60 calls) | 2.94s (gate) | 4 | **Schema Incompatibility / Circuit Breaker**: Passed latency gate and answered short-term probes quickly (~2.7s–3.4s), but could not output the structured `task_proposal` JSON schema during episodic seeding (`task_proposal: null`), causing 4 consecutive seed failures and triggering auto-shutdown. |
| `gemma-4-26b-a4b-it` | Google Gemini | Terminated (5/60 calls) | 6.73s (gate) | 4 | **Low Accuracy, Latency Spikes & Schema Failure**: Missed short-term recall (`miss` on `st_recall_01` & `st_recall_02`), suffered extreme 64s latency spikes on control arm, and failed episodic task generation. Shut down by circuit breaker. |
| `gpt-5.6-luna` | Vyne | Terminated | 10.85s (gate) | 5 | **Schema Incompatibility on Negative/Refusal Turns & Slow Latency (10.85s avg, 17.55s task proposal)**: Failed the hardened 3-arm admission gate. While explicit task proposals successfully parse into JSON, when responding to questions with missing context/restraint (Ablated & Control arms), the model outputs raw plain-text conversational refusals instead of JSON schema objects (`Vyne response was not valid JSON`), causing repeated parse failures and tripping the circuit breaker. |
| `gemini-3.6-flash` | Google Gemini | N/A | **20.95s** | N/A | **Rejected by Pre-Evaluation Latency Gate**: Failed the hard performance gate ($\text{Avg Latency } 20.95\text{s} > 9.0\text{s}$ across 5 sample turns). Excluded from full 60-call queue. |
| `inclusionai/ling-3.0-flash` | OpenRouter | **45.0%** (9/20) | 27.4s | 18 | **Timeout / High Amnesia**: Severe generation timeouts, frequent gateway dropouts, and low memory recall. |

---

## 3. Scope Breakdown Matrix

### GLM 5.2 (`glm-5-2`)
- **`short_term` (5/5 - 100%)**: 3 Earned-It, 2 Did Nothing (safe refusal), 0 errors. Flawless in-session recall & update tracking.
- **`long_term` (4/4 - 100%)**: 1 Earned-It, 3 Did Nothing (safe refusal), 0 errors. Perfect persona attribution and safe refusal on missing fields.
- **`episodic` (5/5 - 100%)**: 3 Earned-It (including perfect handling of supersession `ep_update_01`), 2 Did Nothing, 0 errors. Highest episodic accuracy recorded.
- **`semantic` (4/6 - 67%)**: 3 Earned-It on Recall (100% recall success), 1 Did Nothing, 2 Verbose Safe Refusals (`sem_restraint_01`, `sem_restraint_03`).

### Mistral Medium 3.5 (`mistral-medium-3-5`)
- **`short_term` (5/5 - 100%)**: 3 Earned-It, 2 Did Nothing (safe refusal), 0 errors. Flawless in-session memory & update handling.
- **`long_term` (4/4 - 100%)**: 1 Earned-It, 2 Did Nothing, 1 Grader Misclassification (`lt_restraint_01` control refusal marked invented).
- **`episodic` (4/5 - 80%)**: 2 Earned-It, 2 Did Nothing, 1 Stale (`ep_update_01`). Reliable cross-session episodic retrieval.
- **`semantic` (5/6 - 83%)**: 3 Earned-It on Recall (100% recall success), 2 Did Nothing, 1 Verbose Refusal (`sem_restraint_03`).

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

### Mistral Small 2603 (`mistral-small-2603`)
- **`short_term` (3/5 - 60%)**: 2 Earned-It (`st_recall_01`, `st_update_01`), 1 Dangerous (`st_restraint_01`), 2 Unreadable (`st_recall_02`, `st_restraint_02`). Solid in-session update and simple recall.
- **`long_term` (2/4 - 50%)**: 1 Earned-It (`lt_recall_01`), 1 Did Nothing (`lt_restraint_01`), 2 Dangerous (`lt_restraint_02`, `lt_restraint_03`). Good persona attribution, but hallucinates missing profile fields on restraint probes.
- **`episodic` (2/5 - 40%)**: 2 Did Nothing (`ep_restraint_01`, `ep_restraint_02`), 3 Unreadable (`ep_recall_01`, `ep_recall_02`, `ep_update_01`). Safe refusal on non-existent fields, but dropped specific cross-session task lookups.
- **`semantic` (0/6 - 0%)**: 5 Unreadable / No-answer, 1 Dangerous (`sem_restraint_01`). High amnesia when retrieving factual corporate policies under the wide probe set.

---

## 4. Pre-Evaluation Light Harness & Admission Checklist

Before adding any new model into the full evaluation queue or running the 60-call probe suite, agents **MUST** run the Light Admission Harness ([`scripts/memeval_latency_gate.py`](../../scripts/memeval_latency_gate.py)) to ensure the model satisfies the two hard constraints:

### ⚠️ Admission Checklist Criteria

1. **Latency Budget**:
   $$\text{Average Latency} < 9.0\text{ seconds across } 5 \text{ sample turns}$$
   *Protects against slow runs and API credit waste.*

2. **Hard Structured Response Adherence**:
   $$\text{Schema Adherence} = 100\%\text{ on nested task proposals and structured JSON}$$
   *Ensures the model can output complex nested structures (e.g. `task_proposal` containing `task_title`, `action_plan`, `minimal_request_paraphrase`) on explicit task creation requests without schema dropouts, invalid fields, or `null` omissions.*

Any model exceeding 9.0s average latency or failing to parse hard structured responses will **NOT** be admitted to the full evaluation queue.

### Execution Command
```bash
# Run Pre-Evaluation Admission Gate on candidate model:
uv run python scripts/memeval_latency_gate.py --provider gemini --model <candidate-model-slug>
```

---

## 5. Evaluation Queue & Historical Model Status

| Priority | Target Model | Provider | Latency Gate Result | Status / Summary |
|:---:|---|---|:---:|---|
| **—** | **`glm-5-2`** | Mistral AI | **1.74s** (Passed) | 🟢 **Top Leader**: 90% Full Pass, 50% Earned-It, 4.3s avg latency. |
| **—** | **`mistral-medium-3-5`** | Mistral AI | **~4.7s** (Passed) | 🟢 **Top Production Performer**: 90% Full Pass, 0 seed failures, 4.7s avg latency. |
| **—** | **`gemini-3.5-flash-lite`** | Google Gemini | **4.6s** | 🟢 **Production Baseline**: 80% Full Pass, 0 seed failures, fast & robust. |
| **—** | **`deepseek/deepseek-v4-flash-0731`** | OpenRouter | **17.5s** | 🟢 **Production Fallback**: 80% Full Pass, 100% restraint, higher latency. |
| **—** | **`mistral-small-2603`** | Mistral AI | **6.28s** (Passed) | 🟡 **Low-Tier Fallback**: 35% Full Pass, 15% Earned-It, 6.5s avg latency. |
| **—** | **`gemma-4-31b-it`** | Google Gemini | **~2.94s** (Passed) | 🔴 **Incompatible**: Fast short-term recall, but failed JSON schema for task proposals. |
| **1** | **`gemini-3.5-flash-lite`** | Google Gemini | **1.06s** (Passed) | 🟢 **Baseline / Champion**: 100% 3-Arm Gate Pass (Full: 0.94s, Ablated: 0.97s, Control: 0.95s, Task Schema: 1.38s). |
| **—** | **`gpt-5.6-luna`** | Vyce | **6.45s** (Passed < 9s) | 🟢 **Admitted via Instructor Pattern**: 100% 3-Arm Gate Pass (Full: 4.49s, Ablated: 4.72s, Control: 7.91s, Task Schema: 8.68s). Admitted to evaluation queue. |
| **—** | **`gemini-3.6-flash`** | Google Gemini | **20.95s** (Failed < 9s) | 🔴 **Rejected at Gate**: Exceeded 9.0s latency budget. |

> *Note: Antigravity is dedicated as an Agentic Framework / Task Engine for broader operational tasks and is excluded from memory evaluation.*

---

## 6. Execution Safety & Circuit Breaker Rule

### 🛑 Max 3 Seed Failures Auto-Shutdown
To prevent runs from spinning and wasting dozens of calls when a provider has severe throttling or network outages:
* If seed failures (e.g. `chat_provider_unavailable`, missing task proposals) occur **more than 3 times** in a run, the harness automatically raises `ExcessiveSeedFailuresError` and shuts down the evaluation immediately.
* When this triggers, the run tears down scratch stores safely and marks the provider/model as throttled/incompatible.

---

## 7. Standard Evaluation Execution Commands

```bash
# 1. First verify candidate model with Light Latency Gate:
uv run python scripts/memeval_latency_gate.py --provider vyce --model gpt-5.6-luna

# 2. Run full 20-probe evaluation on SQLite scratch backend:
uv run python scripts/evaluate_memory.py \
  --probe-set evaluations/MEMORIES/probes/v2-four-scopes-wide.json \
  --provider vyce \
  --model gpt-5.6-luna

# 3. Build synthesized markdown evaluation report:
uv run python scripts/build_memory_evaluation_report.py
```

