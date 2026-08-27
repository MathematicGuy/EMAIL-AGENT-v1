# Software Development Life Cycle (SDLC) Specification & Reference Guide

**Repository:** `EMAIL-AGENT-v1`  
**Document Level:** Authoritative Engineering & Operational Reference  
**Last Aligned:** 2026-08-25  
**Primary Authorities:** [`AGENTS.md`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md), [`README.md`](file:///C:/WORK/EMAIL-AGENT-v1/README.md), [`tests/README.md`](file:///C:/WORK/EMAIL-AGENT-v1/tests/README.md), [`pyproject.toml`](file:///C:/WORK/EMAIL-AGENT-v1/pyproject.toml), [`.github/workflows/ci.yml`](file:///C:/WORK/EMAIL-AGENT-v1/.github/workflows/ci.yml), [`docs/architectures/TARGET-ARCHITECTURE.md`](file:///C:/WORK/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md), [`docs/evaluations/`](file:///C:/WORK/EMAIL-AGENT-v1/docs/evaluations/), [`evaluations/HARNESS-GUIDE.md`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/HARNESS-GUIDE.md)

---

## Table of Contents

1. [Executive Overview & System Architecture Context](#1-executive-overview--system-architecture-context)
2. [Phase 1: Inception, Specifications & Architectural Governance](#2-phase-1-inception-specifications--architectural-governance)
3. [Phase 2: Local Development & Toolchain Standards](#3-phase-2-local-development--toolchain-standards)
4. [Phase 3: Verification & Test Harness Architecture](#4-phase-3-verification--test-harness-architecture)
5. [Phase 4: Offline Evaluation & Benchmarking Lifecycle](#5-phase-4-offline-evaluation--benchmarking-lifecycle)
6. [Phase 5: Quality Gates, Pre-PR Verification & CI/CD Pipeline](#6-phase-5-quality-gates-pre-pr-verification--cicd-pipeline)
7. [Phase 6: Deployment, Persistence & Runtime Modes](#7-phase-6-deployment-persistence--runtime-modes)
8. [SDLC Lifecycle Summary Matrix & Comprehensive Workflow Diagram](#8-sdlc-lifecycle-summary-matrix--comprehensive-workflow-diagram)

---

## 1. Executive Overview & System Architecture Context

The `EMAIL-AGENT-v1` codebase represents an enterprise-grade AI coworker system (*Cowork Agent*) designed around **two strictly decoupled product flows** operating over a unified control plane and persistence engine ([`AGENTS.md:L15-25`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L15-L25), [`README.md:L9-52`](file:///C:/WORK/EMAIL-AGENT-v1/README.md#L9-L52), [`docs/architectures/current-architectures/04-overall-architecture.md`](file:///C:/WORK/EMAIL-AGENT-v1/docs/architectures/current-architectures/04-overall-architecture.md)).

```mermaid
flowchart TB
    subgraph PRESENTATION["Presentation Layer (React 19 + Vite + Tailwind 4)"]
        UI_MAIL["Mail Action Plan View<br/>(@email / @outlook / @mail)"]
        UI_CHAT["AI Chat Workspace<br/>(SSE Stream + Live Reasoning + Artifacts)"]
    end

    subgraph API_GW["FastAPI Composition Root (app.py)"]
        MAIL_API["Email Endpoints<br/>/v1/mail-todo/*"]
        CHAT_API["AI Chat Endpoints<br/>/v1/cowork/chat/*"]
        DOC_API["Document & Project Endpoints<br/>/v1/cowork/chat/projects/*"]
    end

    subgraph WORKFLOWS["Decoupled Workflows"]
        subgraph WORKFLOW_EMAIL["1. Standalone Email RAG (Single-Turn, Stateless)"]
            MAIL_FETCH["Mailbox Fetch<br/>(Gmail readonly / Outlook Mail.Read)"]
            ROUTER["Route Classifier<br/>(NO_ACTION | DIRECT_PLAN | RETRIEVE_RAG)"]
            PLANNER["Action Plan Generator<br/>+ Citation Validator"]
            MAIL_PERSIST["Persist Task DTO & Purge Raw Email Body"]
        end

        subgraph WORKFLOW_CHAT["2. Multi-Turn AI Chat & 4-Tier Memory"]
            CONTROLLER["Chat Controller & Intent Classifier"]
            MEM_GW["Unified Memory Gateway"]
            MEM_ST["1. Short-Term Working Memory (In-Process)"]
            MEM_DECL["2. Declarative Profile (Postgres / SQLite)"]
            MEM_EP["3. Episodic Validated Tasks (Postgres / SQLite)"]
            MEM_SEM["4. Semantic Memory (Turbovec Vector Plane)"]
        end
    end

    subgraph STORAGE["Persistence & Vector Layer"]
        DB_SQLITE[("SQLite Engine<br/>(.data/*.db)")]
        DB_POSTGRES[("PostgreSQL Control Plane<br/>(Local / Supabase Cloud)")]
        VEC_COMPANY[("Turbovec Company Corpus<br/>(data/extracted/*.md)")]
        VEC_USER[("Turbovec Project Plane<br/>(Postgres Chunks + .tvim)")]
    end

    UI_MAIL --> MAIL_API --> MAIL_FETCH --> ROUTER --> PLANNER --> MAIL_PERSIST
    UI_CHAT --> CHAT_API --> CONTROLLER --> MEM_GW
    MEM_GW --> MEM_ST
    MEM_GW --> MEM_DECL
    MEM_GW --> MEM_EP
    MEM_GW --> MEM_SEM

    PLANNER -.->|Retrieve Hybrid| VEC_COMPANY
    MEM_SEM -.->|Retrieve Hybrid| VEC_COMPANY
    MEM_SEM -.->|Retrieve Chunks| VEC_USER

    MAIL_PERSIST --> DB_SQLITE & DB_POSTGRES
    MEM_DECL & MEM_EP --> DB_SQLITE & DB_POSTGRES
```

### 1.1 The Dual-Workflow Decoupling Principle

1. **Email RAG Pipeline (Single-Turn, Stateless)** ([`AGENTS.md:L16-20`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L16-L20), [`README.md:L35-51`](file:///C:/WORK/EMAIL-AGENT-v1/README.md#L35-L51)):
   - Operates strictly on unread mail fetched via Gmail `gmail.readonly` or Microsoft Graph `Mail.Read`.
   - Classification routes execution into `NO_ACTION`, `DIRECT_PLAN`, or `RETRIEVE_RAG`.
   - Company knowledge is retrieved from the committed Markdown corpus (`data/extracted/*.md`) via hybrid search (`RAG_STORE_PROVIDER=turbovec` combining Dense Embeddings + BM25 + Reciprocal Rank Fusion [RRF] + Reranker).
   - **Critical Privacy Invariant:** Raw email bodies and attachments are transient ephemeral data. They are purged immediately upon plan generation and are **never persisted** to long-term databases or indexed into vector stores ([`AGENTS.md:L66-67`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L66-L67), [`README.md:L49`](file:///C:/WORK/EMAIL-AGENT-v1/README.md#L49)).
2. **AI Chat Assistant (Multi-Turn, Typed Memory)** ([`AGENTS.md:L21-24`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L21-L24), [`README.md:L13-33`](file:///C:/WORK/EMAIL-AGENT-v1/README.md#L13-L33), [`TARGET-ARCHITECTURE.md:L1-155`](file:///C:/WORK/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md#L1-L155)):
   - Governed by the `ChatController` and `MemoryGateway` with 4 explicit memory tiers:
     - **Short-Term Working Memory:** Active in-process session turn buffer (`InMemoryChatSessionBuffer`).
     - **Declarative Memory:** Explicit user persona, formatting, and tone preferences (`chat_profiles`).
     - **Episodic Memory:** Validated chat summaries and chat-native `TaskEpisode` records. Tasks created in chat start with `retrieval_eligible=false` until explicitly approved by the user in the UI ([`TARGET-ARCHITECTURE.md:L78-83`](file:///C:/WORK/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md#L78-L83), [`ADR-004`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/adr/ADR-004-chat-native-task-episodes.md)).
     - **Semantic Memory:** Grounded knowledge retrieval over user-uploaded project documents ([`ADR-007`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/adr/ADR-007-project-scoped-classifier-gated-user-documents.md), [`ADR-008`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/adr/ADR-008-turbovec-project-document-plane.md)) and optional company knowledge (`CHAT_COMPANY_RAG_ENABLED`).
   - **Decoupling Enforcement (ADR-004):** There is **no executable `@Email` tool inside AI Chat**. Chat and Email pipelines are strictly isolated; frontend `@email`/`@outlook`/`@mail` commands trigger standalone mail digestion without leaking email bodies into chat context ([`AGENTS.md:L23`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L23), [`README.md:L50`](file:///C:/WORK/EMAIL-AGENT-v1/README.md#L50), [`TARGET-ARCHITECTURE.md:L41`](file:///C:/WORK/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md#L41)).

### 1.2 Strict Layered Architecture & Dependency Direction

The codebase enforces a strict directional architecture where dependencies point strictly inward toward domain contracts ([`AGENTS.md:L43-45`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L43-L45)):

$$\text{domain} \longleftarrow \text{features} \longleftarrow (\text{integrations} \mid \text{orchestration} \mid \text{persistence}) \longleftarrow \text{app (FastAPI Root)}$$

```text
src/cowork_agent/
├── domain/                      # Pure domain models (frozen dataclasses, no framework imports)
├── features/                    # Core business logic
│   ├── ai_chat/                 # Chat controller, memory gateway, intent routing
│   ├── batch_evaluation/        # Scalable batch eval plugins & credential leasing
│   ├── email_action_plan/       # Email digest workflows, policies, ports, schemas
│   └── user_documents/          # Ingestion state machine and extraction pipelines
├── integrations/                # External systems & hardware adapters
│   ├── gmail/                   # Google OAuth & Gmail API adapters
│   ├── outlook/                 # Microsoft Graph OAuth & Mail API adapters
│   ├── llm/                     # Provider implementations (Gemini, Mistral, OpenRouter, Mimo)
│   ├── knowledge_ingestion/     # PdfInspector & DocxExtractor parsers
│   └── rag/                     # Hybrid Turbovec, BM25, RRF fusion, embeddings & rerankers
├── orchestration/               # Background workers (dev server, mail-todo-worker)
├── persistence/                 # PostgreSQL & SQLite repositories, migrations (001-016)
├── app.py                       # FastAPI composition root & entry point (mail-todo-api)
├── config.py                    # Environment settings loaders & fallback resolution
└── prompting.py                 # Shared block delimiters preventing prompt injections
```

---

## 2. Phase 1: Inception, Specifications & Architectural Governance

The inception and architectural evolution of any feature or modification in `EMAIL-AGENT-v1` follow an immutable governance protocol rooted in PRDs, ADRs, and capability specifications.

```mermaid
flowchart LR
    PRD["Product Requirements<br/>(tasks/prds/PRD-v*.md)"] --> SPEC["Capability Specifications<br/>(tasks/specs/SPEC-*.md)"]
    SPEC --> ADR["Architecture Decision Records<br/>(tasks/adr/ADR-*.md)"]
    ADR --> DOMAIN["Domain Modeling & Glossary<br/>(docs/agents/domain.md)"]
    DOMAIN --> ARCH_TARGET["Target Architecture Update<br/>(docs/architectures/TARGET-ARCHITECTURE.md)"]
    ARCH_TARGET --> CODE["Implementation & Verification"]
```

### 2.1 PRD Hierarchy (Product Requirements)

Located in [`tasks/prds/`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/prds/), PRDs define user intent, functional requirements (FR), and acceptance criteria across product versions:
- [`PRD-v0-product_requirements.md`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/prds/PRD-v0-product_requirements.md): Baseline email ingestion and action plan synthesis MVP.
- [`PRD-v1-Core-Email-and-RAG.md`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/prds/PRD-v1-Core-Email-and-RAG.md): Core single-turn Email RAG, intent routing (`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`), and company knowledge grounding.
- [`PRD-v2-Memory-Extension.md`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/prds/PRD-v2-Memory-Extension.md): 4-tier typed memory extension, chat session persistence, and episodic memory governance.
- [`PRD-v3-chat-with-user-documents.md`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/prds/PRD-v3-chat-with-user-documents.md): Grounded user document upload (PDF/DOCX), page-level citations, and intent classifier routing.
- [`PRD-v4-pluggable-hybrid-rag-providers.md`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/prds/PRD-v4-pluggable-hybrid-rag-providers.md): Pluggable vector store architecture and Turbovec transition.

### 2.2 ADR Lifecycle & Strict Decision Governance

Every non-trivial architectural change is recorded in an Architecture Decision Record under [`tasks/adr/`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/adr/) (Note: this repository does **not** use `docs/adr/`, as mandated by [`docs/agents/domain.md:L9`](file:///C:/WORK/EMAIL-AGENT-v1/docs/agents/domain.md#L9)).

Key active ADRs governing system invariants:
- **ADR-001**: Async pipeline and adapter pattern for decoupled mail ingestion.
- **ADR-003**: Deferral of raw attachment processing (presence recorded only; content ignored).
- **ADR-004**: Chat-native `TaskEpisode` lifecycle and strict decoupling from `@Email` tool in chat.
- **ADR-006**: Supabase-managed data isolation with Gmail cookie sessions (no Supabase keys in browser).
- **ADR-007**: Project-scoped classifier-gated user document plane (`tenant → user → project → documents`).
- **ADR-008**: Turbovec vector store + PostgreSQL FTS for project-scoped document retrieval.
- **ADR-009**: Retirement of legacy Qdrant backend in favor of Turbovec with graceful degrade-to-null memory.
- **ADR-010**: Local PostgreSQL control plane for low-latency dev loops without porting memory to SQLite.
- **ADR-011**: Reply-chain context aggregation for conversational email threads.
- **ADR-012**: OpenRouter as last-resort fallback for Google Gemini LLM key exhaustion.

> [!IMPORTANT]
> **ADR Conflict Rule ([`docs/agents/domain.md:L33-38`](file:///C:/WORK/EMAIL-AGENT-v1/docs/agents/domain.md#L33-L38)):** If an implementation proposal or output contradicts an existing ADR, it must be explicitly surfaced (e.g., *"Contradicts ADR-004 — but worth reopening because..."*) rather than silently overriding repository architecture.

### 2.3 Capability Specifications & Domain Modeling

- **Capability Specs ([`tasks/specs/`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/specs/)):** Bridge PRDs and implementation. Notable specs include [`SPEC-chat-ragas-evaluation.md`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/specs/SPEC-chat-ragas-evaluation.md), [`SPEC-memory-evaluation.md`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/specs/SPEC-memory-evaluation.md), and [`SPEC-parallel-memory-evaluation.md`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/specs/SPEC-parallel-memory-evaluation.md).
- **Domain Modeling & Ubiquitous Language ([`docs/agents/domain.md:L27-32`](file:///C:/WORK/EMAIL-AGENT-v1/docs/agents/domain.md#L27-L32)):** Developers and agents must strictly adhere to the domain glossary. Inventing synonyms for established domain entities is treated as a drift defect.

---

## 3. Phase 2: Local Development & Toolchain Standards

The repository enforces strict environment isolation, static type safety, and code formatting standards across both Python backend and TypeScript frontend.

```mermaid
flowchart TD
    subgraph BACKEND_TOOLCHAIN["Python Backend Toolchain (Python 3.13)"]
        UV["Astral uv (uv sync)"] --> VENV[".venv Virtual Environment"]
        VENV --> RUFF["Ruff Linter & Formatter (py311, len=100)"]
        VENV --> MYPY["Mypy Strict Static Typing"]
        VENV --> PYTEST["Pytest Suite (uv run pytest)"]
    end

    subgraph FRONTEND_TOOLCHAIN["Frontend Toolchain (Node >=20)"]
        PNPM["pnpm@9.0.0 (pnpm install)"] --> NODE_MODS["node_modules"]
        NODE_MODS --> ESLINT["ESLint (eslint .)"]
        NODE_MODS --> TSC["TypeScript Compiler (tsc -b --noEmit)"]
        NODE_MODS --> VITEST["Vitest (vitest run)"]
        NODE_MODS --> VITE_BUILD["Vite Bundler (vite build)"]
    end

    subgraph ISOLATION_PRIVACY["Privacy & Isolation Layer"]
        GIT_EXCLUDE[".git/info/exclude (Personal scripts & overrides)"]
        ENV_GUARD[".env / config.example (Zero committed secrets)"]
        SOCKET_GUARD["test_network_guard.py (Offline-by-construction)"]
    end
```

### 3.1 Python Toolchain & Interpreter Isolation (Astral `uv`)

- **Virtual Environment Management:** All Python dependencies must be installed via `uv` ([`AGENTS.md:L4-5`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L4-L5)):
  ```powershell
  uv sync --extra dev --extra postgres
  ```
- **Strict Execution Directive ([`AGENTS.md:L49-50`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L49-L50)):** Always prefix Python commands with `uv run`. Bare `python` or `python -m` picks up the Windows Anaconda interpreter / App Execution Alias and crashes with unrelated SSL certificate or encoding errors.
- **Static Analysis & Strict Typing:**
  - **Mypy (`strict = true`, `python_version = "3.13"`)** ([`pyproject.toml:L96-104`](file:///C:/WORK/EMAIL-AGENT-v1/pyproject.toml#L96-L104)):
    ```powershell
    uv run mypy src
    ```
  - **Ruff Linter & Formatter (`line-length = 100`, rules: `["E", "F", "I", "UP", "B"]`)** ([`pyproject.toml:L88-95`](file:///C:/WORK/EMAIL-AGENT-v1/pyproject.toml#L88-L95)):
    ```powershell
    uv run ruff check .
    ```

### 3.2 Frontend Toolchain (React 19 + TypeScript + `pnpm`)

- **Package Manager:** `pnpm@9.0.0` with Node `>=20` ([`frontend/package.json:L5-8`](file:///C:/WORK/EMAIL-AGENT-v1/frontend/package.json#L5-L8)).
- **Core Stack:** React `19.2.7`, Vite `8.1.1`, Tailwind CSS `4.3.3`, `@base-ui/react`, `@testing-library/react`, Vitest `4.1.10`, TypeScript `~6.0.2` ([`frontend/package.json:L18-55`](file:///C:/WORK/EMAIL-AGENT-v1/frontend/package.json#L18-L55)).
- **Frontend Verification Scripts:**
  ```powershell
  cd frontend
  pnpm install
  pnpm lint          # eslint .
  pnpm check-types    # tsc -b --noEmit
  pnpm test           # vitest run
  pnpm build          # tsc -b && vite build
  ```

### 3.3 Workspace Privacy, Security & Isolation Rules

1. **Git Isolation via `.git/info/exclude`:** Personal scripts, scratch artifacts, and local config overrides must be kept out of git tracking via `.git/info/exclude` to prevent leaking unapproved files to teammates upon `git push` (`<RULE[user_global]>`, [`agent-experience-registry.md:L98-103`](file:///C:/WORK/EMAIL-AGENT-v1/docs/references/agent-experience-registry.md#L98-L103)).
2. **Zero-Secret Invariant:** Never commit `.env` files or secret keys (`TOKEN_ENCRYPTION_KEY`, `OAUTH_STATE_SECRET`, `GEMINI_API_KEY`, `JINA_API_KEY`). Never expose server secrets in frontend `VITE_*` environment variables ([`AGENTS.md:L65`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L65), [`TARGET-ARCHITECTURE.md:L228-229`](file:///C:/WORK/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md#L228-L229)).
3. **Transient Email Rule:** Raw email bodies and attachments are transient ephemeral memory only; they must never be stored in persistent SQL tables or vector embeddings ([`AGENTS.md:L66-67`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L66-L67)).

---

## 4. Phase 3: Verification & Test Harness Architecture

The testing philosophy of `EMAIL-AGENT-v1` is **Offline by Construction**, structured to run the entire suite of 1,600+ tests in under ~15–18 seconds on local hardware ([`tests/README.md:L8`](file:///C:/WORK/EMAIL-AGENT-v1/tests/README.md#L8)).

```mermaid
flowchart TB
    subgraph HARNESS["Test Routing Index (tests/README.md §1)"]
        R1["R1: unit/domain (0.7s)"]
        R2["R2: unit/features (2.1s)"]
        R3["R3: unit/integrations/rag (4.5s)"]
        R4["R4: unit/integrations/llm (1.4s)"]
        R5["R5: unit/integrations/gmail+outlook (0.7s)"]
        R11["R11: integration/api (6.4s)"]
        R12["R12: integration/persistence (1.0s)"]
        R13["R13: integration/email_action_plan (2.8s)"]
    end

    subgraph EXECUTION["Parallel Execution Presets (tests/xdist_plugin.py)"]
        XDIST["pytest-xdist: 4 Workers"]
        LOADGROUP["--dist loadgroup (Pin grouped tests)"]
        SOCKET_GUARD["Network Socket Guard (Non-loopback raise RuntimeError)"]
        MARKER_FILTER["Default: -m 'not live' --strict-markers"]
    end

    subgraph OWNERSHIP["Invariant Ownership (§3)"]
        OWN_EMAIL["Boundary Guard: No raw email in API/Chat"]
        OWN_IDEM["Idempotency: Workflow run deduplication"]
        OWN_PG["Persistence: Idempotent migrations 001-016"]
        OWN_DEG["Resilience: Degrade to NullSemanticMemory"]
    end

    HARNESS --> EXECUTION
    EXECUTION --> OWNERSHIP
```

### 4.1 Granular Test Route Index (R1–R16)

Instead of executing the entire test suite on every minor change, developers and subagents pick the **narrowest covering route** from [`tests/README.md:L14-58`](file:///C:/WORK/EMAIL-AGENT-v1/tests/README.md#L14-L58):

| Route | Target Path | Tests | Cost (Serial) | Architectural Scope Covered |
|---|---|:---:|:---:|---|
| **R1** | `tests/unit/domain` | 179 | 0.7 s | Pure domain models, frozen contracts, enums, validation rules. Zero I/O. |
| **R2** | `tests/unit/features` | 588 | 2.1 s | Chat controller, memory gateway, intent routing, action-plan mapping. Fakes only. |
| **R3** | `tests/unit/integrations/rag` | 102 | 4.5 s | BM25, RRF fusion, reranker, query guard, key rotation, Turbovec memory. |
| **R4** | `tests/unit/integrations/llm` | 77 | 1.4 s | Prompt assembly, parsing, key rotation, classifiers, OpenRouter last-resort. |
| **R5** | `tests/unit/integrations/gmail` + `mailbox` + `outlook` | 54 | 0.7 s | Gmail/Microsoft OAuth, PKCE, token cipher, provider router, mailbox adapters. |
| **R6** | `tests/unit/integrations` | 375 | 6.2 s | R3 + R4 + R5 plus bootstrap, Supabase adapters. |
| **R7** | `tests/unit/persistence` | 37 | 1.8 s | Repository logic against deterministic in-memory fakes. |
| **R8** | `tests/unit/orchestration` | 19 | 1.7 s | Background worker dispatchers, pollers, queue recovery. |
| **R9** | `tests/unit/scripts` | 188 | 8.3 s | `scripts/*.py` evaluation CLIs (in-process `main(argv)`). |
| **R10** | `tests/unit/fixtures` | 33 | 2.2 s | Golden fixture schemas, corpus frontmatter, and label validation. |
| **R11** | `tests/integration/api` | 61 | 6.4 s | FastAPI routes via in-process ASGI transport (`httpx.ASGITransport`). |
| **R12** | `tests/integration/persistence` | 9 | 1.0 s | Real PostgreSQL repositories (`pg-control-plane` xdist group; skips without DB). |
| **R13** | `tests/integration/email_action_plan` | 38 | 2.8 s | Provider-neutral mailbox → classify → plan → persist on fakes. |
| **R14** | `tests/integration` | 100 | 7.8 s | R11 + R12 + R13 plus corpus-backed end-to-end workflows. |
| **R15** | `tests/unit` | 1538 | 14.1 s | Entire unit test surface. |
| **R16** | `tests/unit --ignore=tests/unit/scripts` | 1350 | 9.1 s | Fast unit test tier (excluding evaluation CLI harnesses). |
| **Full** | `uv run pytest -q` | **1638** | **~15 s (Parallel)** | Full repository test suite. |

### 4.2 Invariant Ownership Principle (§3 `tests/README.md`)

To prevent duplicate test maintenance and testing bloat, each critical system invariant is assigned a **single owner file** ([`tests/README.md:L71-110`](file:///C:/WORK/EMAIL-AGENT-v1/tests/README.md#L71-L110)):
- **Raw Email Privacy Boundary:** Owned by `tests/integration/api/test_principal_boundary.py` (API level) and `tests/unit/domain/test_chat_contracts.py` (Chat memory level). Prohibited from being re-asserted in repository/gateway tests.
- **Run Idempotency:** Owned by `tests/integration/email_action_plan/test_workflow.py`.
- **Postgres Migrations Idempotency:** Owned by `tests/integration/persistence/test_postgres_repositories.py`.
- **Network Socket Guard:** Owned by `tests/unit/test_network_guard.py` (raises `RuntimeError` if any non-loopback outbound socket is opened during unit tests).
- **Graceful RAG Degradation:** Owned by `tests/unit/integrations/test_bootstrap.py`.

### 4.3 Parallel Execution & Hardware Optimization

- **Hardware Profile:** Optimized for multi-core architectures (e.g. Intel Core Ultra 7 255H with 16 cores: 6P + 8E + 2LP-E) (`<RULE[user_global]>`).
- **Worker Configuration:** Pytest defaults to 4 workers via custom `tests/xdist_plugin.py` using `--dist loadgroup` (or `--dist loadfile`) to prevent LP-E core stragglers and database schema contention ([`tests/README.md:L6-10`](file:///C:/WORK/EMAIL-AGENT-v1/tests/README.md#L6-L10), [`pyproject.toml:L58-86`](file:///C:/WORK/EMAIL-AGENT-v1/pyproject.toml#L58-L86)).
- **Single Consolidated Suite Audit:** As documented in [`docs/evaluations/TEST-SUITE-AUDIT-REPORT.md:L81-118`](file:///C:/WORK/EMAIL-AGENT-v1/docs/evaluations/TEST-SUITE-AUDIT-REPORT.md#L81-L118), the suite abolished the separate `@pytest.mark.extended` tier. Splitting the suite deselected 382 production unit tests saving only 1.5s while risking silent regressions. The active standard is **one single comprehensive suite** running in ~15s.

---

## 5. Phase 4: Offline Evaluation & Benchmarking Lifecycle

The repository maintains an extensive evaluation framework across 5 distinct domains ([`evaluations/README.md:L9-18`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/README.md#L9-L18), [`evaluations/HARNESS-GUIDE.md:L7-40`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/HARNESS-GUIDE.md#L7-L40)).

```mermaid
flowchart TD
    subgraph EVAL_SUITES["5 Evaluation Harness Suites"]
        EVAL_RET["1. RETRIEVAL (evaluate_retrieval.py)<br/>100 Hand-Labeled Golden Queries<br/>Hit@1/3, MRR, Recall@5, Lexical/Semantic Slices"]
        EVAL_RAGAS["2. CHAT-RAGAS (evaluate_chat_rag.py)<br/>Dual-Tier: Deterministic + LLM-Judge<br/>Faithfulness >=0.95, Relevancy >=0.85"]
        EVAL_MEM["3. MEMORIES (evaluate_memory.py)<br/>4 Scopes, 3-Arm Attribution (P, F, F)<br/>Probes: v1, v2, v3-four-scopes-hard"]
        EVAL_EMAIL["4. EMAIL (evaluate_email_golden.py)<br/>Pipeline v4, 100-Case Sharded Eval<br/>Rerank Evidence Gating (min=0.30, ratio=0.85)"]
        EVAL_CHAT["5. CHAT (evaluate_chat_routing.py)<br/>240 Intent Scenarios (6 Intents x 4 Paths)<br/>UI Switch Latency (Playwright)"]
    end

    subgraph OBSERVABILITY["Observability & Telemetry"]
        LANGFUSE["Langfuse Tracing (@observe)<br/>Spans, Token Usage & LLM Cost Tracking<br/>Domain Layer Pure (Excluded)"]
        PYTHON_LOG["Python Standard Logging<br/>Boot/Crash Logs, Retries, Local File Audit<br/>(.data/app.log, .data/worker.log)"]
    end

    subgraph RULES["Non-Negotiable Eval Rules (evaluations/README.md §2)"]
        RULE_META["1. Metadata-Only: Zero Raw Text/Prompt Commit"]
        RULE_HASH["2. HashingEmbedder is Mechanics-Only"]
        RULE_SCALE["3. Exact Corpus Scale Match for Comparisons"]
        RULE_JUDGE["4. Model_Judge != Model_Generator (Zero Self-Bias)"]
    end

    EVAL_SUITES --> RULES
    EVAL_SUITES --> OBSERVABILITY
```

### 5.1 The 5 Evaluation Suites

1. **RETRIEVAL Evaluation ([`evaluations/RETRIEVAL/`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/RETRIEVAL/)):**
   - **Harness:** `scripts/evaluate_retrieval.py` against `tests/fixtures/rag/retrieval_golden.json` and `data/extracted/`.
   - **Metrics:** `Hit@1`, `Hit@3`, `MRR`, `Recall@5` (document and section level), evaluated across 3 query slices (`lexical`, `semantic`, `mixed`), plus `abstention_rate`.
   - **Execution:**
     ```powershell
     uv run python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid --rerank
     ```
2. **CHAT-RAGAS Dual-Tier Evaluation ([`docs/evaluations/RAGAS.md`](file:///C:/WORK/EMAIL-AGENT-v1/docs/evaluations/RAGAS.md), [`evaluations/CHAT-RAGAS/`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/CHAT-RAGAS/)):**
   - **Harness:** `scripts/evaluate_chat_rag.py --ragas`.
   - **Tier 1 (Deterministic/Offline):** Page `Hit@1`, `Hit@5`, `MRR`, citation subset validation (`cited_pages ⊆ retrieved_pages`), and 3-stage latency breakdown (`retrieval`, `generation`, `evaluator`).
   - **Tier 2 (LLM Judge):** Measures `faithfulness` ($\ge 0.95$) and `answer_relevancy` ($\ge 0.85$).
   - **Strict Invariant:** $\text{model\_judge} \neq \text{model\_generator}$ with `temperature = 0` to prevent self-preference bias ([`docs/evaluations/RAGAS.md:L45-58`](file:///C:/WORK/EMAIL-AGENT-v1/docs/evaluations/RAGAS.md#L45-L58)).
3. **MEMORIES 4-Tier Memory Evaluation ([`evaluations/MEMORIES/`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/MEMORIES/)):**
   - **Harness:** `scripts/evaluate_memory.py` using probe sets (`v1-four-scopes`, `v2-four-scopes-wide`, `v3-four-scopes-hard`).
   - **3-Arm Attribution Protocol ($P, F, F$):** Full memory must Pass ($P$), Ablated memory must Fail ($F$), and Empty baseline must Fail ($F$) to prove earned memory attribution ([`evaluations/MEMORIES/README.md:L28`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/MEMORIES/README.md#L28)).
   - **Backend Support:** Full parity between SQLite (`POSTGRES_MODE=off`) and PostgreSQL (`POSTGRES_MODE=local`/`cloud`).
4. **EMAIL Pipeline Evaluation ([`evaluations/EMAIL/`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/EMAIL/)):**
   - **Harness:** `scripts/evaluate_email_golden.py` (sharded across 50 cases/run) against `evaluations/EMAIL/golden_dataset.json`.
   - **Metrics:** Actionability accuracy, retrieve-first compliance, Cohere evidence gate status (`EMAIL_RAG_MIN_RERANK_SCORE=0.30`, `EMAIL_RAG_RELATIVE_CUTOFF_RATIO=0.85`), and final route resolution.
5. **CHAT Intent & Latency Evaluation ([`evaluations/CHAT/`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/CHAT/)):**
   - **Harness:** `scripts/evaluate_chat_routing.py` (240-scenario dataset across 6 intents $\times$ 4 paths) and `e2e/chat-history-latency.spec.ts` for UI switch latency benchmarking.

### 5.2 Four Non-Negotiable Evaluation Rules ([`evaluations/README.md:L21-27`](file:///C:/WORK/EMAIL-AGENT-v1/evaluations/README.md#L21-L27))

1. **Metadata-Only Commit:** Never commit raw email text, chat messages, prompts, or chunk text to git. Artifacts store only IDs, counts, verdicts, and timings.
2. **Mechanics-Only Hashing:** `--embedder hashing` / `--dry-run` is strictly for offline pipeline verification and carries zero semantic rank value.
3. **Corpus Scale Parity:** Evaluation reports are comparable only when executed against identical corpus chunk counts and probe set versions.
4. **Independent Judge:** Model judge must never equal model generator in LLM-as-a-judge evaluations.

### 5.3 Observability Architecture (Langfuse & Python Logging)

- **AI Telemetry (Langfuse Python SDK)** ([`docs/observability/LANGFUSE.md`](file:///C:/WORK/EMAIL-AGENT-v1/docs/observability/LANGFUSE.md)): Uses `@observe` across controllers, LLM providers, and RAG retrievers to track execution spans, tokens, and cost. Domain models are strictly excluded from importing Langfuse (`domain` remains pure python).
- **System Infrastructure Logging** ([`docs/observability/PYTHON_LOGGING.md`](file:///C:/WORK/EMAIL-AGENT-v1/docs/observability/PYTHON_LOGGING.md)): Uses Python `logging` with `StreamHandler` and `FileHandler` (`.data/app.log`, `.data/worker.log`) for server boot, unhandled exceptions, and transport retries.

---

## 6. Phase 5: Quality Gates, Pre-PR Verification & CI/CD Pipeline

The repository implements a strict, multi-tiered verification gate before any code can enter the `main` branch.

```mermaid
flowchart TD
    DEV["Local Code Changes"] --> PRE_PR{"Mandatory Local Pre-PR Gate<br/>(AGENTS.md L85-91)"}
    
    subgraph GATE_LOCAL["Local Pre-PR Gate (Mandatory)"]
        G1["1. Backend: uv run ruff check . && uv run mypy src && uv run pytest -q"]
        G2["2. Frontend: cd frontend && pnpm lint && pnpm check-types && pnpm test && pnpm build"]
        G3["3. E2E: pnpm run test:e2e (Playwright)"]
    end

    PRE_PR --> G1 --> G2 --> G3
    G3 -->|All Pass| PR["Open Pull Request to main"]
    G3 -->|Any Fail| BLOCK["BLOCKED: Fix Failures Locally"]

    PR --> GITHUB_CI["GitHub Actions CI Workflow (.github/workflows/)"]

    subgraph CI_JOBS["GitHub Actions Workflow Execution"]
        CI_PY["Python CI Job (ubuntu-latest)<br/>• PostgreSQL 16 Service Container<br/>• Astral setup-uv (Python 3.13)<br/>• postgresql-client-16 install<br/>• ruff check & mypy src<br/>• pytest parallel (1600+ tests)<br/>• Serial Postgres verification (Fatal on skip)"]
        CI_FE["Frontend CI Job (ubuntu-latest)<br/>• pnpm 9 setup-node (Node 20)<br/>• eslint . & tsc -b --noEmit<br/>• vitest run & vite build"]
        CI_PW["Playwright E2E Job (ubuntu-latest)<br/>• Chromium installation<br/>• npx playwright test<br/>• Upload 30-day artifact report"]
    end

    GITHUB_CI --> CI_PY & CI_FE & CI_PW
    CI_PY & CI_FE & CI_PW -->|All Green| MERGE["Authorized to Merge into main"]
```

### 6.1 Mandatory 3-Step Local Pre-PR Gate

Before opening any Pull Request to `main`, developers and AI agents must run and pass the 3-step verification gate ([`AGENTS.md:L85-91`](file:///C:/WORK/EMAIL-AGENT-v1/AGENTS.md#L85-L91), [`tests/README.md:L124-138`](file:///C:/WORK/EMAIL-AGENT-v1/tests/README.md#L124-L138)):

```powershell
# Step 1: Backend CI quality gate
uv run ruff check . && uv run mypy src && uv run pytest -q

# Step 2: Frontend CI quality gate
cd frontend && pnpm lint && pnpm check-types && pnpm test && pnpm build

# Step 3: End-to-End Playwright test suite
pnpm run test:e2e
```

### 6.2 GitHub Actions Workflows

1. **`ci.yml` (Python & Frontend Pipelines)** ([`.github/workflows/ci.yml`](file:///C:/WORK/EMAIL-AGENT-v1/.github/workflows/ci.yml)):
   - **Postgres Service Container:** Spins up `postgres:16-alpine` on port 5432 with healthchecks.
   - **Client Parity:** Installs `postgresql-client-16` on Ubuntu runner to ensure `pg_dump` version matching for backup/restore tests ([`.github/workflows/ci.yml:L51-59`](file:///C:/WORK/EMAIL-AGENT-v1/.github/workflows/ci.yml#L51-L59)).
   - **Fast Parallel Test Suite:** Runs `uv run pytest -q --ignore=tests/integration/persistence`.
   - **Fatal Skip Guard ([`.github/workflows/ci.yml:L71-79`](file:///C:/WORK/EMAIL-AGENT-v1/.github/workflows/ci.yml#L71-L79)):** Runs the PostgreSQL persistence tier serially (`-n 0`) and fails the build with fatal error if any persistence test is skipped.
   - **Frontend Verification:** Runs frozen lockfile installation, `pnpm lint`, `pnpm check-types`, `pnpm test`, and `pnpm build` under Node 20.
2. **`playwright.yml` (E2E Integration)** ([`.github/workflows/playwright.yml`](file:///C:/WORK/EMAIL-AGENT-v1/.github/workflows/playwright.yml)):
   - Installs Chromium browser dependencies and executes `npx playwright test`.
   - Uploads test execution reports with a 30-day artifact retention policy.

### 6.3 Agent Experience & Collaboration Patterns

Recorded in [`docs/references/agent-experience-registry.md`](file:///C:/WORK/EMAIL-AGENT-v1/docs/references/agent-experience-registry.md), the **MindSeeds Epistemic Compression Protocol** distills operational wisdom into load-bearing seeds (<12 words) that govern agent pair programming:
- *"Test narrow, prove broad"* ([`agent-experience-registry.md:L55-61`](file:///C:/WORK/EMAIL-AGENT-v1/docs/references/agent-experience-registry.md#L55-L61)): Use targeted unit tests first; expand only when contracts change.
- *"Fresh passing evidence needs no echo"* ([`agent-experience-registry.md:L62-68`](file:///C:/WORK/EMAIL-AGENT-v1/docs/references/agent-experience-registry.md#L62-L68)): Do not rerun passing test suites without intervening edits.
- *"Default pytest already fans out to 4 workers"* ([`agent-experience-registry.md:L78-84`](file:///C:/WORK/EMAIL-AGENT-v1/docs/references/agent-experience-registry.md#L78-L84)): Do not inject `-n 0` into standard test commands.
- *"An ignore rule is only real when git confirms it"* ([`agent-experience-registry.md:L97-103`](file:///C:/WORK/EMAIL-AGENT-v1/docs/references/agent-experience-registry.md#L97-L103)): Verify gitignore rules via `git check-ignore`.

---

## 7. Phase 6: Deployment, Persistence & Runtime Modes

The control plane supports three runtime persistence modes selected via `POSTGRES_MODE` in `.env` ([`config.py:L41-80`](file:///C:/WORK/EMAIL-AGENT-v1/src/cowork_agent/config.py#L41-L80), [`ADR-010`](file:///C:/WORK/EMAIL-AGENT-v1/tasks/adr/ADR-010-local-postgres-control-plane-latency.md), [`current-architectures/03-control-plane-persistence-and-uis.md`](file:///C:/WORK/EMAIL-AGENT-v1/docs/architectures/current-architectures/03-control-plane-persistence-and-uis.md)).

```mermaid
flowchart TD
    ENV_CONF["Environment Configuration (POSTGRES_MODE in .env)"] --> MODE_SWITCH{POSTGRES_MODE}

    MODE_SWITCH -->|off| MODE_OFF["SQLite Local Mode (.data/*.db)<br/>• In-process session buffer<br/>• 8 Local SQLite DB files<br/>• Outlook connector enabled<br/>• Ephemeral / Zero-Docker"]
    MODE_SWITCH -->|local| MODE_LOCAL["Local Docker PostgreSQL (ADR-010)<br/>• Docker Compose postgres:16-alpine<br/>• 127.0.0.1:5432 LAN ms latency<br/>• Migrations 001-016 applied<br/>• Production SQL Parity"]
    MODE_SWITCH -->|cloud| MODE_CLOUD["Supabase Cloud PostgreSQL<br/>• Session / Direct :5432 connection<br/>• Multi-user Production Mode<br/>• Migrations 001-016 applied<br/>• Private Supabase Storage"]

    subgraph ENTRYPOINTS["System Entry Points (pyproject.toml)"]
        EP_API["mail-todo-api (cowork_agent.app:main)"]
        EP_WORKER["mail-todo-worker (orchestration.worker:main)"]
        EP_DEV["mail-todo-dev (Concurrent API + Worker)"]
        EP_INGEST["mail-todo-ingest-knowledge (ingestion_cli:main)"]
        EP_FE["Frontend Vite Server (pnpm dev)"]
    end
```

### 7.1 Persistence Mode Decision Matrix

| Dimension | `POSTGRES_MODE=off` | `POSTGRES_MODE=local` | `POSTGRES_MODE=cloud` |
|---|---|---|---|
| **Target Engine** | Local SQLite (`.data/*.db`) + In-process buffer | Local Docker `postgres:16-alpine` | Hosted Supabase PostgreSQL |
| **Connection URL** | `""` (Empty string in `config.py`) | `DATABASE_URL_LOCAL` (Default: `postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork`) | `DATABASE_URL_CLOUD` (Direct/Session `:5432`) |
| **Migrations** | None (SQLite table creation on boot) | Migrations `001`–`016` applied on boot | Migrations `001`–`016` applied on boot |
| **Outlook Support** | **Enabled** (SQLite-linked connector) | **Disabled** (SQLite-only feature) | **Disabled** (SQLite-only feature) |
| **Worker Support** | In-process dispatching | `mail-todo-worker` enabled | `mail-todo-worker` enabled |
| **Primary Use Case** | Fast local dev loops, zero-dependency offline runs | Local MVP with low latency and full SQL parity | Deployed staging and production environments |

### 7.2 Database Migrations (001–016)

PostgreSQL schema evolution is managed via SQL migrations in [`src/cowork_agent/persistence/migrations/`](file:///C:/WORK/EMAIL-AGENT-v1/src/cowork_agent/persistence/migrations/). Each migration ships both `.sql` (up) and `.down.sql` (rollback):
- `001_mail_todo.sql`: Mailbox connections, digest runs, task storage, outbox events.
- `002_chat_profiles.sql` – `004_task_episodes.sql`: Typed memory (declarative profiles, summaries, chat-native task episodes).
- `005_identity_workspace_sessions.sql` & `006_durable_chat_sessions.sql`: Tenant/workspace sessions and chat session registry.
- `007_projects_documents.sql` – `012_project_document_chunks.sql`: Project-scoped document storage, chunks, and FTS indexes.
- `014_chat_turn_lifecycle.sql` – `016_chat_turn_activity.sql`: Chat turn lifecycle, `supersedes` episode linking, and reasoning activity.

Migrations are applied automatically on FastAPI startup via `apply_migrations(pool)` guarded by PostgreSQL advisory locks ([`config.py:L31-80`](file:///C:/WORK/EMAIL-AGENT-v1/src/cowork_agent/config.py#L31-L80), [`.github/workflows/ci.yml:L34-36`](file:///C:/WORK/EMAIL-AGENT-v1/.github/workflows/ci.yml#L34-L36)).

### 7.3 System Entry Points & CLI Commands

Defined in [`pyproject.toml:L49-54`](file:///C:/WORK/EMAIL-AGENT-v1/pyproject.toml#L49-L54) and [`README.md:L158-171`](file:///C:/WORK/EMAIL-AGENT-v1/README.md#L158-L171):
- `mail-todo-api`: Starts the core FastAPI server (`uv run mail-todo-api` on port `8000`).
- `mail-todo-worker`: Runs the background document ingestion queue worker.
- `mail-todo-dev`: Concurrently boots both FastAPI API and background worker for development.
- `mail-todo-ingest-knowledge`: Ingestion CLI converting raw company documents into committed Markdown in `data/extracted/`.
- Frontend Dev Server: `cd frontend && pnpm dev` (Vite dev server on port `5173`).

---

## 8. SDLC Lifecycle Summary Matrix & Comprehensive Workflow Diagram

### 8.1 Summary Lifecycle Matrix

| SDLC Phase | Core Artifacts & Tools | Primary Authority Files | Validation & Quality Gates |
|---|---|---|---|
| **Phase 1: Inception & Governance** | PRDs, Capability Specs, ADRs | `tasks/prds/`<br>`tasks/specs/`<br>`tasks/adr/`<br>`docs/agents/domain.md` | • Domain glossary alignment<br>• ADR conflict check<br>• Target architecture alignment |
| **Phase 2: Local Dev & Tooling** | `uv`, Python 3.13, `pnpm 9`, React 19, Vite, Tailwind 4 | `AGENTS.md`<br>`pyproject.toml`<br>`frontend/package.json` | • Strict typing (`mypy src`)<br>• Linting (`ruff check .`, `eslint .`)<br>• Type checking (`tsc -b --noEmit`) |
| **Phase 3: Verification & Harness** | Pytest, `tests/xdist_plugin.py`, Routes R1–R16 | `tests/README.md`<br>`tests/conftest.py`<br>`docs/evaluations/` | • Route Index execution<br>• Single Invariant Owner rule (§3)<br>• Network socket isolation guard |
| **Phase 4: Offline Evaluation** | 5 Evaluation Harnesses, Langfuse, Python Logging | `evaluations/HARNESS-GUIDE.md`<br>`evaluations/README.md`<br>`docs/observability/` | • Metadata-only commit rule<br>• RAGAS judge $\neq$ generator<br>• 3-arm memory attribution ($P,F,F$) |
| **Phase 5: Quality Gates & CI/CD** | Pre-PR script, GitHub Actions, Playwright E2E | `AGENTS.md`<br>`.github/workflows/ci.yml`<br>`playwright.yml` | • Mandatory 3-step local Pre-PR gate<br>• CI Python (Postgres container + check)<br>• CI Frontend & Playwright E2E |
| **Phase 6: Persistence & Deployment** | Docker Compose, Postgres migrations (001–016), FastAPI | `config.py`<br>`docker-compose.yml`<br>`src/cowork_agent/persistence/` | • Advisory lock migrations on boot<br>• Fallback persistence verification<br>• Service healthchecks |

### 8.2 End-to-End SDLC Lifecycle Flow

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer / Agent
    participant Gov as Governance (PRD / ADR / Spec)
    participant Local as Local Dev (uv / pnpm / mypy)
    participant Test as Test Harness (R1-R16 / Pytest)
    participant Eval as Evaluation Harness (5 Suites)
    participant Gate as Local Pre-PR Gate (3 Steps)
    participant CI as GitHub Actions CI & E2E
    participant Prod as Runtime & Deployment

    Note over Dev,Gov: Phase 1: Inception & Architectural Governance
    Dev->>Gov: Review PRD, verify ADRs & Domain Glossary
    Gov-->>Dev: Architectural boundary & invariant confirmed

    Note over Dev,Local: Phase 2: Local Development & Toolchain Standards
    Dev->>Local: uv sync & pnpm install
    Dev->>Local: Implement feature (Domain Pure -> Features -> Integrations)
    Dev->>Local: uv run ruff check . & uv run mypy src & pnpm check-types

    Note over Dev,Test: Phase 3: Verification & Test Harness Architecture
    Dev->>Test: Run narrow route (e.g. R1 -> R2 -> R13)
    Dev->>Test: Run parallel suite (uv run pytest -q, ~15s, 1600+ tests)
    Test-->>Dev: Invariants verified & 0 socket leaks

    Note over Dev,Eval: Phase 4: Offline Evaluation & Benchmarking Lifecycle
    opt When modifying Prompts, Retrieval, Memory, or Intent
        Dev->>Eval: Run evaluation harness (Retrieval / RAGAS / Memory / Email)
        Eval-->>Dev: Generate metadata-only baseline JSON
    end

    Note over Dev,Gate: Phase 5: Quality Gates & Pre-PR Verification
    Dev->>Gate: Step 1: Backend CI (ruff + mypy + pytest)
    Dev->>Gate: Step 2: Frontend CI (eslint + tsc + vitest + build)
    Dev->>Gate: Step 3: Playwright E2E (pnpm run test:e2e)
    Gate-->>Dev: ALL GATES GREEN

    Note over Dev,CI: Phase 6: CI/CD & Deployment
    Dev->>CI: Push branch & open Pull Request to main
    CI->>CI: Python job with PostgreSQL 16 container
    CI->>CI: Frontend job with Node 20 & pnpm cache
    CI->>CI: Playwright E2E Chromium tests
    CI-->>Dev: CI Green -> Authorized to Merge
    Dev->>Prod: Merge to main -> Boot mail-todo-api & migrations 001-016
```
