# Comprehensive Analysis: Memory System Features, Frontend Demo Specs, & AI Chat Compatibility

> Updated 2026-08-13: the legacy Python demo was removed; the React/Vite app in `frontend/` is the only frontend.

> [!CAUTION]
> **DOCUMENT REALIGNMENT — COMPLETED**:
> **Primary Purpose of Memory System**: The 4-Type Memory System is implemented to support **AI Chat features**, **NOT Email features**. The documentation realignment originally demanded by this page has been executed (PRD-v2 v2.2, ADR-004, TARGET-ARCHITECTURE.md).
> **Status of Email RAG**: The standalone Email RAG feature (PRD-v1) remains completed, functional, and **memory-free by design**. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>
> **Status of AI Chat Memory**: The full 4-Type Memory System (Working, Declarative Profile, Episodic, Semantic RAG) plus governance (observability, retention, purge, deletion, backup/restore, evaluation) is implemented and accepted as of V2-M6 (2026-08-11). <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>

> [!IMPORTANT]
> **Executive Summary & Implementation Status Overview**:
> **Does the Memory System serve regular AI chat (like ChatGPT)?**
> **YES.** The 4-Type Memory System architecture (Working, Declarative Profile, Episodic, Semantic RAG) has been decoupled from Email and is implemented as the foundation for the AI Chat Assistant. ADR-004 supersedes earlier PRD-v2 decisions that bound memory to `@Email` Action Plans.
>
> **Codebase Implementation Status Summary (2026-08-11)**:
> - <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>: Stateless Email RAG Pipeline (`/v1/mail-todo/runs`, `/v1/tasks`), Local Hybrid RAG Corpus Inspection (`/v1/mail-todo/knowledge/*`), React/Vite frontend (`frontend/`), **and** the full AI Chat memory stack: `MemoryGateway` facade, `InMemoryChatSessionBuffer`, PostgreSQL `chat_profiles` / `task_episodes` / `chat_summary_episodes`, SSE `ChatController` + typed stream events, deterministic retrwieval policy, `MemoryPurgeCoordinator`, paired evaluation runner with launch gate, `LoggingMemoryOperationSink` + `MemoryOperationMetrics`.
> - <span style="color: #cb2431; font-weight: bold;">[NOT IMPLEMENTED — BY DESIGN]</span>: In-chat `@Email` executable tool (retired by ADR-004; chat-native `TaskEpisode` replaces it), automatic chat-facts extraction (explicit-only writes are a deliberate policy, not a gap), Redis-backed working memory (in-memory buffer is the accepted MVP tier).
> - <span style="color: #d97706; font-weight: bold;">[IN PROGRESS — SEPARATE WORKSTREAM]</span>: Further React UI coverage for backend memory capabilities.

---

## 1. How the Memory System Architecture Works (PRD-v2 v2.2, ADR-004)

The system categorizes memory into **four distinct, typed memory domains** ([PRD-v2-Memory-Extension.md](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/PRD-v2-Memory-Extension.md); [TARGET-ARCHITECTURE.md](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md)). Each operates under strict data privacy boundaries and read/write access policies:

```mermaid
flowchart TD
    classDef green fill:#2ea44f,stroke:#1e6b33,stroke-width:2px,color:#ffffff;
    classDef red fill:#cb2431,stroke:#8e1621,stroke-width:2px,color:#ffffff;
    classDef yellow fill:#d97706,stroke:#92400e,stroke-width:2px,color:#ffffff;

    GW["MemoryGateway facade<br/>(Namespace Resolution & Policy Engine)<br/>fail-closed; tenant/user/session/feature:ai_chat"]:::green

    GW --> WM["1. Short-Term Working Memory<br/>(InMemoryChatSessionBuffer)"]:::green
    GW --> DM["2. Long-Term Declarative Memory<br/>(PostgreSQL chat_profiles)"]:::green
    GW --> EM["3. Long-Term Episodic Memory<br/>(PostgreSQL task_episodes / chat_summary_episodes)"]:::green
    GW --> SM["4. Semantic Memory<br/>(Company RAG — local hybrid / optional Qdrant)"]:::green

    subgraph Boundaries["Strict Privacy Boundaries"]
        WM ---|"Bounded turns + TTL"| WM_POLICY["CHAT_MEMORY_MAX_TURNS / CHAT_MEMORY_TTL_SECONDS"]:::green
        DM ---|"Explicit-only writes"| DM_POLICY["No AI-inferred preference loops"]:::green
        EM ---|"Originating-session gated"| EM_GATE["retrieval_eligible derived atomically from validation_status"]:::green
        SM ---|"Company knowledge only"| SM_POLICY["Never stores user memory"]:::green
    end
```

### The 4 Memory Pillars

| Memory Type | Definition & Purpose | Primary Storage | Write & Privacy Policy | Implementation Status |
|---|---|---|---|---|
| **1. Short-Term Working Memory** | Active chat session turn history (`session_id`). Bounded by `CHAT_MEMORY_MAX_TURNS` (default 20) and `CHAT_MEMORY_TTL_SECONDS` (default 1800) via `ChatMemorySettings`. | In-memory `InMemoryChatSessionBuffer` ([session_buffer.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/session_buffer.py)) | **Strict Ephemerality**: Raw email bodies and full prompt contexts are never held here. Buffer is bound to verified session scope. | <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span> |
| **2. Long-Term Declarative Memory** | Explicit, durable user preferences (language, timezone, formatting style, priority rules). Expiry-aware reads with default-profile fallback on outage. | PostgreSQL `chat_profiles` via `PostgresChatProfileRepository` ([postgres.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/persistence/repositories/postgres.py)) | **Explicit-Only Writes**: Populated *only* via `MemoryGateway.write_profile`. Auto-inference from raw emails or chat is forbidden. | <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span> |
| **3. Long-Term Episodic Memory** | Chat-native `TaskEpisode` created **only** after an explicit user task request (finite deterministic grammar). Initial status `system_generated` + `retrieval_eligible=false`. Stable opaque `record_id` derived from `(tenant, user, session, turn)` for retry-safe idempotency. Originating-session approve/complete/reject transitions atomically derive eligibility. Also: `chat_summary_episodes` for bounded chat summaries. Raw email/transcript/tool fields structurally excluded. | PostgreSQL `task_episodes` and `chat_summary_episodes` via `PostgresTaskEpisodeRepository` ([postgres.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/persistence/repositories/postgres.py)); ADR-004 | **Human-Gated Eligibility**: Approval/completion sets `retrieval_eligible = true`; rejection keeps it false. Eligible-only retrieval (approved/completed, unexpired, same tenant/user/feature). | <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span> |
| **4. Semantic Memory (Company RAG)** | Enterprise-wide declarative domain knowledge (SOPs, governance, technical guides, templates). **Never stores user memory.** Citation allowlisting enforced. | Local hybrid BM25 + dense + RRF + optional Jina reranker ([hybrid.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/integrations/rag/hybrid.py)); optional Qdrant | **Read-Only Company Knowledge**: Accessed only when relevant per retrieval policy. Raw emails are never ingested into company RAG. | <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span> |

> [!NOTE]
> **Note on Procedural Memory**: Standard cognitive architecture includes "Procedural Memory" (how-to rules & skills). In PRD-v2, procedural rules are enforced deterministically via hard policy guards and backend schemas rather than a dynamic, user-writeable memory store. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED via Code Policies]</span>

### Context Precedence

The generation context assembler ([generation_context.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/generation_context.py)) enforces:

$$\text{Current User Instruction} > \text{Current Company Evidence} > \text{Stored Preferences} > \text{Advisory Episodes}$$

<span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>

### Hybrid Retrieval & Fusion Pipeline

Semantic Memory retrieval utilizes a hybrid dense-sparse vector pipeline:
1. **Tenant ACL Pre-Filter**: Filters chunks by `tenant_id` and document permissions before scoring. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — LOCAL_TENANT_ID]</span>
2. **Dense Vector Search + Lexical BM25 Keyword Search**: Evaluated concurrently over document chunks. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — bm25.py / embeddings.py]</span>
3. **Reciprocal Rank Fusion (RRF, $k=60$)**: Merges dense and sparse result lists into a single ranked list. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — rrf.py]</span>
4. **Jina Reranking**: Optional reranker layer refines top-k relevance scores. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — jina_reranker.py]</span>

### Selective Retrieval Policy

The retrieval policy ([retrieval_policy.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/retrieval_policy.py)) applies deterministic intent-based selection: episodic reads only when relevant, bounded result counts, eligible-only (approved/completed, unexpired, same tenant/user/feature). <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>

### V2-M6 Governance Layer

V2-M6 (completed 2026-08-11) adds a cross-cutting governance layer over the four memory pillars:

| Concern | Implementation | Key Files |
|---|---|---|
| **Observability** | `MemoryOperationEvent` (metadata-only, 8 fields, validated bounds); production `LoggingMemoryOperationSink` + thread-safe `MemoryOperationMetrics` injected into every `MemoryGateway` at app startup. DENIED outcomes log at ERROR as alertable safety incidents. Sink failure can never block chat. Zero hard-safety counters enforced: unvalidated retrieval, cross-tenant access, raw-email memory violation, rejected-episode retrieval, expired-record retrieval. | [memory_observability.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/memory_observability.py), [app.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/app.py) |
| **Retention** | `compute_expires_at` applies `CHAT_EPISODE_RETENTION_SECONDS` / `CHAT_PROFILE_RETENTION_SECONDS` (product-approved default 90 days = 7776000s, documented in `.env.example`). `ChatController` computes expiry once per request and reuses on retry for idempotency. Expired records are unreadable before physical purge. | [retention.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/retention.py), [.env.example](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/.env.example) |
| **Purge** | `MemoryPurgeCoordinator` + `scripts/purge_chat_memory.py` — explicit infrastructure entry point only (NO scheduler, not a product feature). Emits purge telemetry. | [retention.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/retention.py), [purge_chat_memory.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/scripts/purge_chat_memory.py) |
| **Deletion** | Exact-scope user-wide deletion (profile + episodes, same tenant/user/feature only). Live PostgreSQL audit proves deleted/expired/rejected memory unretrievable, other users untouched, semantic company RAG never deleted. | [deletion.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/deletion.py) |
| **Backup / Restore** | `scripts/backup_restore_chat_memory.py` (pg_dump/pg_restore of the three chat-memory tables, host or docker-exec mode). Live test proves namespace/lifecycle/eligibility/expiry survive backup/restore. | [backup_restore_chat_memory.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/scripts/backup_restore_chat_memory.py) |
| **Evaluation** | `evaluation_runner.py` + `evaluation_dataset.py` (8 synthetic labeled cases, opaque IDs, no sensitive content, deterministic MVP scorer) + `scripts/run_paired_chat_evaluation.py` CLI. Fail-closed launch gate (`evaluate_launch_gate`). Product-approved Moderate-MVP thresholds in `.env.example` (min deltas 0.05, min scores 0.6, max degradation 0.25). Gate passes with all five safety counters at zero. | [evaluation_runner.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/evaluation_runner.py), [evaluation_dataset.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/evaluation_dataset.py), [evaluation.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/evaluation.py), [run_paired_chat_evaluation.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/scripts/run_paired_chat_evaluation.py) |
| **Index propagation** | N/A — no derived user-memory search index exists; Qdrant/local hybrid indexes company knowledge only. | — |

---

## 3. React frontend integration

> [!NOTE]
> **Backend readiness**: The React/Vite frontend consumes the FastAPI contracts. The chat APIs (`/v1/cowork/chat/sessions`, `/v1/cowork/chat/sessions/{session_id}/messages`, and task-episode lifecycle endpoints) are implemented in [chat.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/api/chat.py).

The React frontend is the sole UI surface. It lives in `frontend/` and communicates with FastAPI through its browser API modules.

```text
React/Vite frontend (frontend/):
├── Dashboard and mail workflow
├── Work intake and streaming chat
├── Project-document management
└── Settings and connected integrations
```

### Key Memory Inspector & UI Components

1. **Knowledge API — Semantic RAG Inspection**: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>
   * **Corpus Readiness Indicator**: Real-time health badge. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — /v1/mail-todo/knowledge/ready]</span>
   * **Loaded Document List**: Shows ingested files, titles, chunk counts, and source links. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — /v1/mail-todo/knowledge/documents]</span>
   * **Ad-hoc Grounded RAG Query Input (`POST /v1/mail-todo/knowledge/chat`)**: A single-turn query panel to test document retrieval with relevance scores, reranker status, and citation chips. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>
   > [!CAUTION]
   > `POST /v1/mail-todo/knowledge/chat` is an **ad-hoc RAG corpus testing panel**, NOT a multi-turn conversational AI chatbot.

2. **Memory UI — Declarative & Episodic Inspection**: <span style="color: #d97706; font-weight: bold;">[IN PROGRESS — separate workstream]</span>
   * **Preferences Profile Editor**: Editable form for explicit user preferences. Backend: `MemoryGateway.write_profile` + PostgreSQL `chat_profiles`. <span style="color: #d97706; font-weight: bold;">[Backend IMPLEMENTED; UI IN PROGRESS]</span>
   * **Task Validation Badges & Lifecycle Controls**: Per-episode `Approve`, `Complete`, `Reject` transitions via `/v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/{approve|complete|reject}`. <span style="color: #d97706; font-weight: bold;">[Backend IMPLEMENTED; UI IN PROGRESS]</span>
   * **Episode Provenance Inspector**: Metadata panel (model ID, prompt version, pipeline version, chat session/turn provenance). <span style="color: #d97706; font-weight: bold;">[Backend IMPLEMENTED; UI IN PROGRESS]</span>
   * **Deletion Controls**: Exact-scope user-wide deletion. <span style="color: #d97706; font-weight: bold;">[Backend IMPLEMENTED; UI IN PROGRESS]</span>

3. **Strict Constraints**:
   * **No Client Mocks**: Pure API client talking to FastAPI backend. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>
   * **No Raw Prompt/Context Visualizer**: Raw email bodies and full prompts are strictly hidden from the UI to protect user privacy. <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span>

---

## 4. Email Agent vs. Regular AI Chat Comparison

| Architectural Dimension | Email Task Agent (PRD-v1 Scope) | Regular AI Chat (ChatGPT-style, PRD-v2 + ADR-004) | Implementation Status |
|---|---|---|---|
| **Trigger & Interaction** | Event/batch driven via `/v1/mail-todo/runs` | Interactive multi-turn dialogue with SSE token streaming via `/v1/cowork/chat/sessions/{session_id}/messages` | Email Agent: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span><br>AI Chat SSE Controller: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — controller.py, api/chat.py]</span> |
| **Short-Term Context** | Single run (`run_id`), deleted immediately post-execution | Multi-turn chat session buffer (`session_id`) retained across dialogue turns, bounded by `CHAT_MEMORY_MAX_TURNS` + `CHAT_MEMORY_TTL_SECONDS` | Email Run: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span><br>Chat Session Buffer: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — session_buffer.py]</span> |
| **Episodic Memory Entry** | Derived from task outputs; strictly human-gated | Chat-native `TaskEpisode` created only after explicit user task request (finite deterministic grammar); `system_generated` + `retrieval_eligible=false` until originating-session approval/completion. Chat summaries stored in `chat_summary_episodes`. Raw email/transcript/tool fields structurally excluded (ADR-004). | Email Task DB: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — SQLiteTaskRepository]</span><br>Chat Episode Engine: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — PostgreSQL task_episodes/chat_summary_episodes]</span> |
| **Preference Learning** | Explicit UI/command configuration only | Explicit-only writes via `MemoryGateway.write_profile`. **No automatic extraction of facts from chat messages** — this is a deliberate product policy (privacy-by-design), not a gap. | Email Preferences: N/A (memory-free pipeline)<br>Chat Facts: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — explicit-only by design]</span> |
| **API Endpoints** | `/v1/mail-todo/runs`, `/v1/tasks` | `/v1/cowork/chat/sessions`, `/v1/cowork/chat/sessions/{session_id}/messages` (SSE), `/v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/{approve|complete|reject}`, `GET/DELETE` on episodes | `/v1/mail-todo/*`: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span><br>`/v1/cowork/chat/*`: <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — api/chat.py]</span> |
| **In-Chat Tool Surface** | N/A | **None by design (AC-18)**: no in-chat `@Email` tool, no scheduler, no recurring email processing, no autonomous Gmail action. Gmail scopes remain `gmail.readonly`. Retired `tool_choices` rejected 422. ADR-004 supersedes the earlier `@Email`-in-chat direction. | <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED — AC-18 accepted]</span> |

---

## 5. Extending the 4-Type Memory System to Support Regular AI Chat

The 4-type memory system is **fully mapped to AI Chat** as follows:

```mermaid
flowchart LR
    classDef green fill:#2ea44f,stroke:#1e6b33,stroke-width:2px,color:#ffffff;
    classDef yellow fill:#d97706,stroke:#92400e,stroke-width:2px,color:#ffffff;

    subgraph Chat_Memory_Mapping["4-Type Memory System Mapped to AI Chat (Implemented)"]
        direction TB
        WM2["1. Short-Term Working Memory<br/>InMemoryChatSessionBuffer (bounded turns + TTL)"]:::green
        DM2["2. Long-Term Declarative Memory<br/>PostgreSQL chat_profiles<br/>Explicit-only writes, expiry-aware"]:::green
        EM2["3. Long-Term Episodic Memory<br/>PostgreSQL task_episodes + chat_summary_episodes<br/>Chat-native, human-gated (ADR-004)"]:::green
        SM2["4. Semantic Memory<br/>Company RAG only (local hybrid / optional Qdrant)<br/>Never stores user memory"]:::green
        PM2["+ Procedural Memory<br/>Enforced via code policies & schemas<br/>(no user-writeable store)"]:::green
    end
```

### Architecture Extensions — Delivered

| Extension | Status | Notes |
|---|---|---|
| **Chat Controller + SSE Streaming** | <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span> | `ChatController` in [controller.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/features/ai_chat/controller.py); typed SSE stream events (error/delta/completed + proposal/lifecycle events); idempotency keys; retry-safe episode persistence (same `record_id` AND same `expires_at` on retry); graceful degradation on memory outage (FR-18 behavior). |
| **Chat REST API** | <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span> | `POST /v1/cowork/chat/sessions`, `POST /v1/cowork/chat/sessions/{session_id}/messages` (SSE), task-episode lifecycle endpoints, episode GET/DELETE — see [chat.py](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/src/cowork_agent/api/chat.py). Verified principal; typed events; explicit bounded task proposals. |
| **MemoryGateway namespace** | <span style="color: #2ea44f; font-weight: bold;">[IMPLEMENTED]</span> | Fail-closed namespace (`tenant_id/user_id/session_id/feature:ai_chat`); cross-scope access raises `NamespaceAccessDenied`. |
| **In-chat `@Email` tool** | <span style="color: #cb2431; font-weight: bold;">[NOT IMPLEMENTED — BY DESIGN]</span> | Retired by ADR-004. AC-18: no in-chat tool surface, no scheduler, no recurring email processing, no autonomous Gmail action. Gmail scopes remain `gmail.readonly`. Retired `tool_choices` rejected 422 by strict deserialization. |
| **Frontend Chat Screen** | <span style="color: #d97706; font-weight: bold;">[IN PROGRESS — separate workstream]</span> | React `AI Chat Assistant` screen; backend APIs it consumes are implemented. |

---

*Analysis originally produced by Antigravity AI Team via parallel research dispatches. Realignment audit completed 2026-08-11.*

---

## 6. Documentation Update Roadmap — Completed Outcomes

> [!IMPORTANT]
> **ROADMAP STATUS — COMPLETED**:
> The documentation realignment demanded in earlier revisions of this page has been executed. The 4-Type Memory System is now documented (and implemented) as the foundation for the AI Chat Assistant, decoupled from the standalone Email pipeline.

### Completed Outcomes Summary

1. **PRD-v2 realigned**: PRD-v2-Memory-Extension.md v2.2 governs. Memory is scoped to AI Chat; Email remains memory-free.
2. **ADR-004 accepted**: [ADR-004-chat-native-task-episodes.md](file:///E:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-004-chat-native-task-episodes.md) supersedes PRD-v2 and target-architecture decisions that made `@Email` the producer of AI Chat TaskEpisodes. The in-chat `@Email` feature and its Action Plan card lifecycle are retired. The standalone PRD-v1 Email Agent remains unchanged and memory-free.
3. **TARGET-ARCHITECTURE.md realigned**: Chat Controller, SSE handler, memory plane all target AI Chat.
4. **master-comparison.md realigned**: Gap analysis and milestone breakdown reflect V2-M1..V2-M6 as completed.
5. **SPEC-Demo-Frontend.md realigned**: Backend API assumptions include `/v1/cowork/chat/*`; Memory and Chat screens remain the frontend workstream's deliverable.
6. **V2-M1..V2-M6 accepted**: All six backend memory milestones (gateway + working memory, declarative profile store, episodic store, SSE controller, selective retrieval, governance) are implemented and accepted as of 2026-08-11.
7. **AC-18 accepted**: No in-chat tool surface, no scheduler, no recurring email processing, no autonomous Gmail action. Gmail scopes remain `gmail.readonly`. Retired `tool_choices` rejected 422.

The per-file modification tables that previously occupied this section have been retired — they described planned edits, and those edits are now landed in the authoritative documents they targeted.
