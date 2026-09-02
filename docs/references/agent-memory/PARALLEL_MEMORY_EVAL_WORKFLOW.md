# Parallel Memory Evaluation Workflow (5-Worker Concurrency Architecture)

This document visualizes the complete end-to-end architecture, parallel dispatch pipeline, 3-arm isolation guarantees, and reporting lifecycle for high-speed memory evaluations in Cowork Agent.

MIMO `mimo-v2.5-pro` scoreboard and open defects:
[`MIMO-V2.5-PRO-CHAT-REPLY-TRAIL.md`](./MIMO-V2.5-PRO-CHAT-REPLY-TRAIL.md).

---

## 1. High-Level System Architecture

```mermaid
flowchart TD
    subgraph Input ["1. Evaluation Configuration & Input"]
        PS["Probe Set Definition<br/><code>v2-four-scopes-wide.json</code><br/><code>v3-four-scopes-hard.json</code>"]
        MIMO_CFG["Provider & Model<br/><code>MimoSettings (mimo-v2.5 / mimo-v2.5-pro)</code><br/><code>https://token-plan-ams.xiaomimimo.com/v1</code>"]
    end

    subgraph Dispatcher ["2. Parallel Concurrency Engine (evaluate_memory_parallel.py)"]
        QUEUE["Probe Queue (20 Probes × 3 Arms = 60 Calls)"]
        SEM["AsyncIO Semaphore Pool (5 Workers)"]
        W1["Worker 1"]
        W2["Worker 2"]
        W3["Worker 3"]
        W4["Worker 4"]
        W5["Worker 5"]
    end

    subgraph Isolation ["3. Tri-Arm Memory Isolation Matrix"]
        direction TB
        ARM_FULL["Arm 1: FULL<br/>(Loaded Memory Context)"]
        ARM_ABL["Arm 2: ABLATED<br/>(Masked Target Scope)"]
        ARM_CTL["Arm 3: CONTROL<br/>(Unseeded Baseline)"]
        
        ST["Short-Term Memory<br/>(Session Turns)"]
        LT["Long-Term Memory<br/>(User Profile)"]
        EP["Episodic Memory<br/>(Tasks & Revisions)"]
        SEM_MEM["Semantic Memory<br/>(Corpus Retrieval)"]
    end

    subgraph LLM ["4. Xiaomi MiMo Inference Cluster"]
        MIMO_API["Xiaomi MiMo Token Plan API<br/><code>100 RPM / 10M TPM Capacity</code>"]
    end

    subgraph Evaluation ["5. Scoring & Reporting Pipeline"]
        SCORER["Deterministic Scorer<br/>(PASS / MISS / INVENTED / STALE)"]
        ATTRIBUTION["Tri-Arm Attribution<br/>Earned-It (P,F,F) / Restraint"]
        AGG["Thread-Safe Aggregator"]
        BASELINE["Baseline JSON<br/><code>evaluations/MEMORIES/baselines/</code>"]
        DETAIL["Detail Transcript<br/><code>evaluations/MEMORIES/runs/</code>"]
        REPORT["Markdown Synthesizer<br/><code>evaluations/MEMORIES/reports/</code>"]
        LEADERBOARD["Leaderboard<br/><code>MODEL-MEMORY-EVAL-LEADERBOARD.md</code>"]
    end

    PS --> QUEUE
    MIMO_CFG --> Dispatcher
    QUEUE --> SEM
    SEM --> W1 & W2 & W3 & W4 & W5

    W1 & W2 & W3 & W4 & W5 --> ARM_FULL & ARM_ABL & ARM_CTL
    ARM_FULL --> ST & LT & EP & SEM_MEM
    ARM_ABL --> ST & LT & EP & SEM_MEM
    ARM_CTL -.-> ST & LT & EP & SEM_MEM

    ARM_FULL & ARM_ABL & ARM_CTL --> MIMO_API
    MIMO_API --> SCORER
    SCORER --> ATTRIBUTION --> AGG
    AGG --> BASELINE & DETAIL
    BASELINE & DETAIL --> REPORT --> LEADERBOARD
```

---

## 2. Parallel Worker Dispatch Sequence

The following sequence details how 5 concurrent workers pull probe-arm execution pairs from the queue without data contamination or race conditions:

```mermaid
sequenceDiagram
    autonumber
    participant CLI as evaluate_memory_parallel.py
    participant Pool as AsyncIO Worker Pool (5x)
    participant Live as LiveSession & Gateway
    participant MiMo as Xiaomi MiMo API (AMS)
    participant Scorer as Scorer Engine
    participant Store as Report & Artifact Store

    Note over CLI,Live: Phase 1: Environment & Store Initialization
    CLI->>Live: Initialize scratch SQLite & Semantic Embedder
    Live-->>CLI: Adapters & Identity Ready (Nonce generated)

    Note over CLI,MiMo: Phase 2: Parallel Probe Execution (60 calls across 5 workers)
    par Worker 1 to Worker 5 Concurrent Invocations
        Pool->>Live: Build Arm-Scoped Gateway (Session ID: {namespace}-{probe}-{arm})
        Live->>Live: Execute Seed Ritual (Short-Term / Long-Term / Episodic)
        Live->>MiMo: POST /v1/chat/completions (Structured Schema)
        MiMo-->>Live: HTTP 200 JSON Response (~7–8s)
        Live->>Scorer: Evaluate Response (Outcome: PASS/MISS/INVENTED/STALE)
        Scorer->>CLI: Append row to Thread-Safe Transcript
    end

    Note over CLI,Store: Phase 3: Aggregation & Teardown
    CLI->>Live: Teardown scratch stores & close connection pool
    CLI->>Store: Write Baseline JSON (evaluations/MEMORIES/baselines/)
    CLI->>Store: Write Detail Transcript (evaluations/MEMORIES/runs/)
    CLI->>Store: Build Synthesized Markdown Report (evaluations/MEMORIES/reports/)
    CLI->>Store: Synchronize Leaderboard (MODEL-MEMORY-EVAL-LEADERBOARD.md)
```

---

## 3. Tri-Arm Attribution Logic & Classification Matrix

Each probe is tested across three distinct states to isolate whether success is **genuinely earned by memory** or hallucinated / leaked from base model pretraining:

```mermaid
flowchart TD
    START["Probe Response (Full, Ablated, Control)"] --> Q1{"Did Full Arm PASS?"}
    
    Q1 -- No --> MISS["MISS / INVENTED / STALE<br/>(Memory Failed or Hallucinated)"]
    Q1 -- Yes --> Q2{"Did Control Arm PASS?"}
    
    Q2 -- Yes --> LEAK["LEAKED (P, ?, P)<br/>(Answered from Base Model Pretraining, not Memory)"]
    Q2 -- No --> Q3{"Did Ablated Arm PASS?"}
    
    Q3 -- Yes --> RESTRAINT["SAFE RESTRAINT / ADAPTED (P, P, F)<br/>(Handled via alternate context)"]
    Q3 -- No --> EARNED["EARNED-IT (P, F, F)<br/>🎯 Gold Standard: Strict Memory Attribution"]

    classDef earned fill:#22c55e,stroke:#15803d,color:#ffffff,font-weight:bold;
    classDef leak fill:#eab308,stroke:#a16207,color:#ffffff,font-weight:bold;
    classDef miss fill:#ef4444,stroke:#b91c1c,color:#ffffff,font-weight:bold;
    classDef restraint fill:#3b82f6,stroke:#1d4ed8,color:#ffffff,font-weight:bold;

    class EARNED earned;
    class LEAK leak;
    class MISS miss;
    class RESTRAINT restraint;
```

---

## 4. Execution Commands & Quick Reference

```powershell
# 1. High-Speed 5-Worker Evaluation on v2 Wide:
uv run python scripts/evaluate_memory_parallel.py `
  --provider mimo `
  --model mimo-v2.5-pro `
  --probe-set evaluations/MEMORIES/probes/v2-four-scopes-wide.json `
  --workers 5 `
  --output evaluations/MEMORIES/baselines/mimo-v2.5-pro-v2-parallel.json

# 2. High-Speed 5-Worker Evaluation on v3 Hard:
uv run python scripts/evaluate_memory_parallel.py `
  --provider mimo `
  --model mimo-v2.5-pro `
  --probe-set evaluations/MEMORIES/probes/v3-four-scopes-hard.json `
  --workers 5 `
  --output evaluations/MEMORIES/baselines/mimo-v2.5-pro-v3-parallel.json

# 3. Generate Synthesized Report:
uv run python scripts/build_memory_evaluation_report.py `
  --baseline evaluations/MEMORIES/baselines/mimo-v2.5-pro-v2-parallel.json
```
