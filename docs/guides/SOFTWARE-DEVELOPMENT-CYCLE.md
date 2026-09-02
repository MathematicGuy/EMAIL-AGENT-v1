# Software Development Life Cycle (SDLC) Specification & Reference Guide

**Repository:** `EMAIL-AGENT-v1`
**Document Level:** Authoritative Engineering & Operational Reference
**Last Aligned:** 2026-08-27
**Primary Authorities:** [`AGENTS.md`](../../AGENTS.md), [`README.md`](../../README.md), [`tests/README.md`](../../tests/README.md), [`pyproject.toml`](../../pyproject.toml), [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml), [`docs/architectures/README.md`](../architectures/README.md), [`docs/architectures/workspace.dsl`](../architectures/workspace.dsl), [`docs/evaluations/`](../evaluations/), [`evaluations/HARNESS-GUIDE.md`](../../evaluations/HARNESS-GUIDE.md), [`tasks/adr/`](../../tasks/adr/)

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

The `EMAIL-AGENT-v1` codebase represents an enterprise-grade AI coworker system (*Cowork Agent*) designed around **two strictly decoupled product flows** operating over a unified control plane, asynchronous background workers, and multi-tier persistence backends ([`AGENTS.md`](../../AGENTS.md), [`README.md`](../../README.md), [`docs/architectures/c1-system-context.md`](../architectures/c1-system-context.md), [`docs/architectures/c2-containers.md`](../architectures/c2-containers.md)).

```mermaid
flowchart TB
    subgraph PRESENTATION["Presentation Layer (React 19 + Vite + Tailwind 4)"]
        UI_MAIL["Mail Action Plan & Scan Protocol<br/>(@email / @outlook / @mail)"]
        UI_CHAT["AI Chat Workspace<br/>(SSE Stream + Live Reasoning + Execution Trace Drawer)"]
        UI_DOCS["Project & Raw Document Studio<br/>(DOCX/PDF Viewer & Editor)"]
        UI_REPORTS["Report Artifact Viewer<br/>(Markdown / PDF Export)"]
    end

    subgraph API_GW["FastAPI Control Plane API (mail-todo-api)"]
        COMP["Typed Composition Root (CoworkRuntime)<br/>Pure Settings Loader (config.py)"]
        AUTH["Identity & Session Security<br/>(Google OAuth / Linked Outlook PKCE / Fernet Cipher)"]

        subgraph ROUTERS["Transport Routers (api/*.py)"]
            ROUTER_MAIL["/v1/mail-todo/*"]
            ROUTER_CHAT["/v1/cowork/chat/*"]
            ROUTER_PROJ["/v1/cowork/chat/projects/*"]
            ROUTER_RAW["/api/v1/raw-documents/*"]
            ROUTER_REP["/api/v1/reports/*"]
            ROUTER_CAL["/v1/calendar/*"]
        end

        subgraph WORKFLOWS["Core Subsystems"]
            SUB_EMAIL["1. Standalone Email Action Plan Subsystem<br/>(Single-Turn, Memory-Free, Body-Purged)"]
            SUB_CHAT["2. AI Chat Subsystem<br/>(Controller + 4-Tier Memory + Tool Registry)"]
            SUB_RET["3. Hybrid Retrieval Engine<br/>(Dense + BM25 + RRF + Reranker)"]
            SUB_REP["4. Report PDF Renderer<br/>(fpdf2 + Bundled Noto Sans)"]
        end
    end

    subgraph WORKER["Background Worker (mail-todo-worker)"]
        POLLER_MAIL["Mail Digest Poller"]
        WORKER_DOC["Project Document Worker<br/>(Extract / Mistral OCR / Chunk / Index)"]
        RECOVERY["Run & Document Recovery Sweeper"]
        RETENTION["Retention Sweeper"]
    end

    subgraph CLI["Knowledge Ingestion CLI"]
        INGEST["mail-todo-ingest-knowledge<br/>(Adaptive Extract + Sanitizer + SHA-256 Manifest)"]
    end

    subgraph STORAGE["Persistence & Vector Stores"]
        DB_SQLITE[("SQLite Engine<br/>(.data/*.db)")]
        DB_POSTGRES[("PostgreSQL 16 Control Plane<br/>(Local Docker / Supabase Cloud)")]
        VEC_COMPANY[("Company Turbovec Index<br/>(4-bit TurboQuant .tvim)")]
        VEC_PROJECT[("Per-Project Turbovec Indexes<br/>(Postgres Chunks + .tvim)")]
        CORPUS_COMPANY[("Committed Company Corpus<br/>(data/extracted/*.md)")]
        STORE_DOCS[("Private Document Store<br/>(Supabase Storage / Local .data)")]
        STORE_REPORTS[("Report Artifact Store<br/>(data/reports/)")]
    end

    PRESENTATION --> ROUTERS
    ROUTERS --> WORKFLOWS
    WORKFLOWS --> STORAGE
    WORKER --> STORAGE
    CLI --> CORPUS_COMPANY

    SUB_EMAIL -.->|Retrieve Hybrid| VEC_COMPANY
    SUB_CHAT -.->|Gated Company RAG| VEC_COMPANY
    SUB_CHAT -.->|Project RAG| VEC_PROJECT
    WORKER_DOC --> VEC_PROJECT
    INGEST --> CORPUS_COMPANY
```

### 1.1 The Dual-Workflow Decoupling Principle

1. **Email Action Plan Pipeline (Single-Turn, Stateless)** ([`AGENTS.md`](../../AGENTS.md), [`README.md`](../../README.md), [`c3-api-email-action-plan.md`](../architectures/c3-api-email-action-plan.md)):
   - Operates on unread mail fetched via Gmail `gmail.readonly` or linked Microsoft Graph `Mail.Read`.
   - Normalizes messages into a transient `EphemeralEmailEnvelope` with 5-message reply-chain aggregation ([ADR-011](../../tasks/adr/ADR-011-reply-chain-context-aggregation.md)).
   - Route resolution classifies messages into `NO_ACTION`, `DIRECT_PLAN`, or `RETRIEVE_RAG`.
   - Company knowledge is retrieved from the committed Markdown corpus (`data/extracted/*.md`) via hybrid search (`RAG_STORE_PROVIDER=turbovec` combining Dense Embeddings + BM25 + RRF + Reranker).
   - **Critical Privacy Invariant:** Raw email bodies and attachments are transient ephemeral data. They are purged immediately upon plan generation and are **never persisted** to long-term databases or indexed into vector stores ([`AGENTS.md`](../../AGENTS.md), [ADR-003](../../tasks/adr/ADR-003-defer-attachment-processing.md)).
2. **AI Chat Assistant (Multi-Turn, Typed Memory)** ([`AGENTS.md`](../../AGENTS.md), [`README.md`](../../README.md), [`c3-api-ai-chat.md`](../architectures/c3-api-ai-chat.md)):
   - Governed by `ChatController.stream_message` ([ADR-014](../../tasks/adr/ADR-014-turn-pipeline-stays-one-function.md)) and the `MemoryGateway` across 4 explicit memory tiers:
     - **Short-Term Working Memory:** Active in-process session turn buffer (`InMemoryChatSessionBuffer`). Ephemeral TTL, never persisted to durable DB.
     - **Long-Term Declarative Memory:** Explicit user persona, formatting, timezone, and tone preferences (`chat_profiles`), written only with `explicit_user_config` provenance.
     - **Episodic Memory:** Validated chat summaries and chat-native `TaskEpisode` records. Tasks created in chat start with `retrieval_eligible=false` until explicitly approved in the UI ([ADR-004](../../tasks/adr/ADR-004-chat-native-task-episodes.md)) and support `supersedes` linking.
     - **Semantic Memory (Two Strictly Unmerged Planes):**
       - *Company Knowledge Plane:* Grounded enterprise search over committed Markdown (`data/extracted/*.md`), gated by `CHAT_COMPANY_RAG_ENABLED`.
       - *User Project Documents Plane:* Isolated project-scoped retrieval over user uploads (`project_document_chunks` + per-project `.tvim` indexes, [ADR-007](../../tasks/adr/ADR-007-project-scoped-classifier-gated-user-documents.md), [ADR-008](../../tasks/adr/ADR-008-turbovec-project-document-plane.md)). Never falls back to company index.
   - **Decoupling Enforcement (ADR-004):** There is **no executable `@Email` tool inside AI Chat**. Chat and Email pipelines are strictly isolated; frontend `@email`/`@outlook`/`@mail` commands trigger standalone mail digestion via `runMailScanProtocol`, and only an aggregate body-free `MailScanSummary` card enters chat history via transport-free reconciliation ([ADR-004](../../tasks/adr/ADR-004-chat-native-task-episodes.md), [`c1-system-context.md`](../architectures/c1-system-context.md)).

### 1.2 C4 Architecture Model & Container Topology

The system architecture is formally defined in the C4 model under [`docs/architectures/workspace.dsl`](../architectures/workspace.dsl) and documented across granular levels:

| Level | Document | Scope & Contents |
|---|---|---|
| **Level 1** | [`c1-system-context.md`](../architectures/c1-system-context.md) | System boundaries, knowledge workers, corpus admins, external providers (Google Identity, Microsoft Graph, Gemini/Mistral/OpenRouter LLMs, Jina Embeddings, Mistral OCR, Langfuse). |
| **Level 2** | [`c2-containers.md`](../architectures/c2-containers.md) | Containers (SPA, Control Plane API, Background Worker, Ingestion CLI, Control-Plane DB, Vector Indexes, File Stores) and dynamic flows (`flow-mail-scan`, `flow-chat-turn`). |
| **Level 2** | [`deployment.md`](../architectures/deployment.md) | Deployment topologies for `POSTGRES_MODE=off` (SQLite), `local` (Docker PostgreSQL 16), and `cloud` (Supabase PostgreSQL + Storage). |
| **Level 3** | [`c3-api-email-action-plan.md`](../architectures/c3-api-email-action-plan.md) | Mail API, Mailbox router, Gmail/Outlook adapters, workflow, routing guards, plan validator, security scanner. |
| **Level 3** | [`c3-api-ai-chat.md`](../architectures/c3-api-ai-chat.md) | Chat API & SSE stream, Chat controller, Intent classifier, 4-tier Memory gateway, Mail-scan reconciliation, Task episode settlement, Tool registry, Google Calendar adapter. |
| **Level 3** | [`c3-api-retrieval.md`](../architectures/c3-api-retrieval.md) | Hybrid retriever (Dense + BM25 + RRF), Query transformation & HyDE, Reranker, Structure-aware chunker, Turbovec memory adapter, Project document retriever. |
| **Level 3** | [`c3-api-platform.md`](../architectures/c3-api-platform.md) | Composition root (`CoworkRuntime`), Runtime settings (`config.py`), Identity & Session security (`VerifiedPrincipal`, Fernet `TokenCipher`), Repositories, Observability. |
| **Level 3** | [`c3-worker.md`](../architectures/c3-worker.md) | Background worker (`mail-todo-worker`), Digest poller, Project document worker with liveness heartbeat, Recovery sweeper, Retention sweeper. |
| **Level 3** | [`c3-ingestion-cli.md`](../architectures/c3-ingestion-cli.md) | Knowledge ingestion CLI (`mail-todo-ingest-knowledge`), Extractors, Sanitizer & Frontmatter writer, SHA-256 Manifest. |

### 1.3 Subsystems & Key Architectural Components

1. **Typed Composition Root (`composition.py`, [ADR-013](../../tasks/adr/ADR-013-composition-as-typed-value.md)):**
   Assembles the single, immutable `CoworkRuntime` instance (holding `control_plane`, `mailbox`, `chat`, `email_rag`, `evaluation`, `reports`, `report_pdf_renderer`). Handlers access dependencies purely through `runtime(request)` with zero ad-hoc construction.
2. **Pure Runtime Configuration (`config.py`, [ADR-017](../../tasks/adr/ADR-017-settings-parsing-is-pure.md)):**
   Settings parsers are pure functions over mappings or `os.environ`. `.env` files are loaded exactly once at executable boundaries via `load_runtime_environment()`.
3. **Transport-Isolated Router Modules (`api/*.py`, [ADR-015](../../tasks/adr/ADR-015-routers-own-their-transport.md)):**
   Each API router is created via a factory `create_*_router()`. The application root `app.py` serves exactly one route of its own: `/health`.
4. **Chat Tool Registry & Calendar Tool ([ADR-019](../../tasks/adr/ADR-019-executable-chat-tools-run-under-a-per-user-grant.md), [ADR-020](../../tasks/adr/ADR-020-google-grants-stay-separate.md)):**
   Defines a single `specs()` / `run()` boundary. Tool execution is a server-side routing decision (`TOOL` / `RAG_TOOL`), never a client request field. Google Calendar tool operates under a separate per-user OAuth grant with turn idempotency key binding.
5. **Report Artifacts & PDF Renderer ([ADR-016](../../tasks/adr/ADR-016-report-artifacts-are-validated-domain-values.md), [ADR-018](../../tasks/adr/ADR-018-report-pdfs-use-fpdf2-and-bundled-noto-sans.md)):**
   Reports are stored in `data/reports/` using the `ReportFilename` slug sanitation rule. PDF exports use `fpdf2` with 4 bundled Noto Sans font styles, ensuring full Unicode Vietnamese support without OS or network dependencies.
6. **Out-of-Process Background Worker (`orchestration/worker.py`):**
   Claims queued digest runs and project document jobs via database leases. The project document worker runs text extraction, adaptive Mistral OCR escalation, hierarchical chunking, and per-project Turbovec index compilation, publishing a heartbeat for `/v1/cowork/chat/document-health`.
7. **Offline Knowledge Ingestion CLI (`ingestion_cli.py`):**
   Converts administrator sources in `data/raw/` into committed Markdown in `data/extracted/` with closed 6-field YAML frontmatter and SHA-256 skip checks in `ingestion-manifest.json`.

### 1.4 Strict Layered Architecture & Dependency Direction

The codebase enforces a strict directional architecture where dependencies point strictly inward toward domain contracts ([`AGENTS.md`](../../AGENTS.md)):

$$\text{domain} \longleftarrow \text{features} \longleftarrow (\text{integrations} \mid \text{orchestration} \mid \text{persistence}) \longleftarrow \text{app (FastAPI Root)}$$

```text
src/cowork_agent/
├── domain/                      # Pure domain models (frozen dataclasses, zero framework imports)
├── features/                    # Core business logic & workflows
│   ├── ai_chat/                 # Chat controller, memory gateway, intent routing, tool runner
│   ├── batch_evaluation/        # Scalable batch eval plugins & credential leasing
│   ├── email_action_plan/       # Email digest workflows, policies, ports, schemas, validation
│   └── user_documents/          # Ingestion state machine and extraction pipelines
├── integrations/                # External systems & hardware adapters
│   ├── gmail/                   # Google OAuth & Gmail API adapters (gmail.readonly)
│   ├── google_calendar/         # Google Calendar OAuth & event adapter (ADR-019)
│   ├── knowledge_ingestion/     # DOCX extractor, PDF inspector, Mistral OCR, text sanitizer
│   ├── llm/                     # Provider implementations (Gemini, Mistral, OpenRouter, Mimo)
│   ├── mailbox/                 # Provider-routing mailbox adapter
│   ├── outlook/                 # Microsoft Graph OAuth & Mail API adapters (SQLite mode)
│   ├── rag/                     # Hybrid Turbovec, BM25, RRF fusion, embeddings & rerankers
│   ├── report_pdf/              # fpdf2 PDF export renderer with bundled Noto Sans (ADR-018)
│   ├── security/                # Magic-byte scanner, ClamAV, VirusTotal, Google Web Risk
│   └── storage/                 # Supabase Storage & local private file storage
├── orchestration/               # Background workers (dev server, mail-todo-worker, recovery)
├── persistence/                 # PostgreSQL & SQLite repositories, migrations (001–017)
├── api/                         # Transport-isolated routers (chat, mail, projects, reports, calendar)
├── app.py                       # FastAPI composition root & /health entry point (mail-todo-api)
├── composition.py               # Typed composition root assembling CoworkRuntime (ADR-013)
├── config.py                    # Environment settings loaders & fallback resolution (ADR-017)
├── identity.py                  # Tenant & User identity context management
├── ingestion_cli.py             # Knowledge ingestion CLI entry point (mail-todo-ingest-knowledge)
├── observability.py             # Metadata-only Langfuse tracing (@observe)
└── prompting.py                 # Shared block delimiters preventing prompt injections
```

---

## 2. Phase 1: Inception, Specifications & Architectural Governance

The inception and architectural evolution of any feature or modification in `EMAIL-AGENT-v1` follow an immutable governance protocol rooted in PRDs, ADRs, capability specifications, and the C4 model.

```mermaid
flowchart LR
    PRD["Product Requirements<br/>(tasks/prds/PRD-v*.md)"] --> SPEC["Capability Specifications<br/>(tasks/specs/SPEC-*.md)"]
    SPEC --> ADR["Architecture Decision Records<br/>(tasks/adr/ADR-*.md)"]
    ADR --> DOMAIN["Domain Modeling & Glossary<br/>(docs/agents/domain.md)"]
    DOMAIN --> ARCH_TARGET["C4 Architecture Model<br/>(docs/architectures/workspace.dsl)"]
    ARCH_TARGET --> CODE["Implementation & Verification"]
```

### 2.1 PRD Hierarchy (Product Requirements)

Located in [`tasks/prds/`](../../tasks/prds/), PRDs define user intent, functional requirements (FR), and acceptance criteria across product versions:
- [`PRD-v0-product_requirements.md`](../../tasks/prds/PRD-v0-product_requirements.md): Baseline email ingestion and action plan synthesis MVP.
- [`PRD-v1-Core-Email-and-RAG.md`](../../tasks/prds/PRD-v1-Core-Email-and-RAG.md): Core single-turn Email RAG, intent routing (`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`), and company knowledge grounding.
- [`PRD-v2-Memory-Extension.md`](../../tasks/prds/PRD-v2-Memory-Extension.md): 4-tier typed memory extension, chat session persistence, and episodic memory governance.
- [`PRD-v3-chat-with-user-documents.md`](../../tasks/prds/PRD-v3-chat-with-user-documents.md): Grounded user document upload (PDF/DOCX), page-level citations, and intent classifier routing.
- [`PRD-v4-pluggable-hybrid-rag-providers.md`](../../tasks/prds/PRD-v4-pluggable-hybrid-rag-providers.md): Pluggable vector store architecture and Turbovec transition.

### 2.2 ADR Lifecycle & Comprehensive Decision Matrix

Every architectural decision is recorded in an Architecture Decision Record under [`tasks/adr/`](../../tasks/adr/):

| ADR | Title | Core Invariant & Architectural Impact |
|---|---|---|
| **ADR-001** | Async Pipeline & Adapters | Asynchronous decoupled mail ingestion with provider-neutral abstractions. |
| **ADR-002** | Sandboxed Attachment Extraction | Security screening for attachments before processing. |
| **ADR-003** | Defer Attachment Processing | Attachments recorded as presence only; raw content is never downloaded for planning. |
| **ADR-004** | Chat-Native Task Episodes | Chat tasks start `retrieval_eligible=false`; strict isolation of chat from `@Email` tool. |
| **ADR-006** | Supabase Managed Data & User Plane | Data isolation with Gmail cookie sessions; project-scoped document classifier routing. |
| **ADR-007** | Project-Scoped User Document Plane | Tenant → user → project document hierarchy; project retrieval never falls back to company RAG. |
| **ADR-008** | Turbovec Project Document Plane | Turbovec vector store + PostgreSQL FTS for project-scoped document retrieval. |
| **ADR-009** | Qdrant Backend Retired | Qdrant backend retired; Turbovec used exclusively with graceful degrade-to-null memory. |
| **ADR-010** | Local Postgres Control Plane | Docker PostgreSQL at `127.0.0.1:5432` for low-latency dev loops with full SQL parity. |
| **ADR-011** | Reply-Chain Context Aggregation | Aggregates up to 5 messages in email threads for conversational context. |
| **ADR-012** | OpenRouter Gemini Last Resort | OpenRouter failure retries on Gemini as last-resort fallback with key rotation. |
| **ADR-013** | Composition as Typed Value | Single frozen `CoworkRuntime` dataclass assembled once at app lifespan boot. |
| **ADR-014** | Turn Pipeline Stays One Function | `ChatController.stream_message` stays one linear function to avoid 28 local state handoffs. |
| **ADR-015** | Routers Own Their Transport | Factory `create_*_router()` per API module; `app.py` only serves `/health`. |
| **ADR-016** | Report Artifacts Validated Values | `ReportFilename` value object sanitizes slugs and prevents path traversal attacks. |
| **ADR-017** | Settings Parsing is Pure | Pure environment parsers; `.env` loaded once at executable boundaries. |
| **ADR-018** | Report PDFs Use fpdf2 & Noto Sans | Standalone `fpdf2` renderer with 4 bundled Noto Sans styles for extractable Vietnamese text. |
| **ADR-019** | Executable Chat Tools Per-User Grant | Chat tools run under per-user grants; calendar events derive from turn idempotency keys. |
| **ADR-020** | Google Grants Stay Separate | Gmail `gmail.readonly` and Google Calendar grants remain completely separate. |

> [!IMPORTANT]
> **ADR Conflict Rule ([`docs/agents/domain.md`](../agents/domain.md)):** If an implementation proposal contradicts an existing ADR, it must be explicitly surfaced (e.g., *"Contradicts ADR-004 — but worth reopening because..."*) rather than silently overriding repository architecture.

### 2.3 C4 Architecture Governance Protocol

1. **Single Source of Truth ([`docs/architectures/README.md`](../architectures/README.md)):**
   `docs/architectures/workspace.dsl` is the C4 model and the **only place** an element or relationship is defined.
2. **Generated Output:** Diagrams under `docs/architectures/diagrams/` are generated via Docker Structurizr & PlantUML commands. Never edit `.puml` or `.png` files by hand.
3. **Automated Verification:** Run `uv run python docs/architectures/check_docs.py` before committing.
4. **Mandatory Architecture Edit Trigger:** Any change adding/removing a container, adding/removing a Level 3 component, changing communication protocols, adding external dependencies, or altering a trust boundary requires an architecture update in the same PR.

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

- **Virtual Environment Management:** All Python dependencies must be installed via `uv` ([`AGENTS.md`](../../AGENTS.md)):
  ```powershell
  uv sync --extra dev --extra postgres
  ```
- **Strict Execution Directive ([`AGENTS.md`](../../AGENTS.md)):** Always prefix Python commands with `uv run`. Plain `python` or `python -m` picks up the Windows Anaconda interpreter and fails with SSL/encoding errors.
- **Static Analysis & Strict Typing:**
  - **Mypy (`strict = true`, `python_version = "3.13"`):**
    ```powershell
    uv run mypy src
    ```
  - **Ruff Linter & Formatter (`line-length = 100`, rules: `["E", "F", "I", "UP", "B"]`):**
    ```powershell
    uv run ruff check .
    ```

### 3.2 Frontend Toolchain (React 19 + TypeScript + `pnpm`)

- **Package Manager:** `pnpm@9.0.0` with Node `>=20` ([`frontend/package.json`](../../frontend/package.json)).
- **Core Stack:** React `19.2.7`, Vite `8.1.1`, Tailwind CSS `4.3.3`, `@base-ui/react`, Vitest `4.1.10`, TypeScript `~6.0.2`.
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

1. **Git Isolation via `.git/info/exclude`:** Personal scripts, scratch artifacts, and local config overrides must be kept out of git tracking via `.git/info/exclude` to prevent leaking unapproved files.
2. **Zero-Secret Invariant:** Never commit `.env` files or secret keys (`TOKEN_ENCRYPTION_KEY`, `OAUTH_STATE_SECRET`, `GEMINI_API_KEY`, `JINA_API_KEY`). Never expose server secrets in frontend `VITE_*` environment variables.
3. **Transient Email Rule:** Raw email bodies and attachments are transient ephemeral memory only; they must never be stored in persistent SQL tables or vector embeddings ([`AGENTS.md`](../../AGENTS.md), [ADR-003](../../tasks/adr/ADR-003-defer-attachment-processing.md)).

---

## 4. Phase 3: Verification & Test Harness Architecture

The testing philosophy of `EMAIL-AGENT-v1` is **Offline by Construction**, structured to run the entire suite of 1,600+ tests in under ~12–15 seconds on local hardware ([`tests/README.md`](../../tests/README.md)).

```mermaid
flowchart TB
    subgraph HARNESS["Test Routing Index (tests/README.md §1)"]
        R1["R1: unit/domain (0.7s)"]
        R2["R2: unit/features (2.1s)"]
        R3["R3: unit/integrations/rag (3.5s)"]
        R4["R4: unit/integrations/llm (1.4s)"]
        R5["R5: unit/integrations/gmail+outlook (0.7s)"]
        R11["R11: integration/api (6.4s)"]
        R12["R12: integration/persistence (1.0s)"]
        R13["R13: integration/email_action_plan (2.8s)"]
    end

    subgraph EXECUTION["Parallel Execution Presets (tests/xdist_plugin.py)"]
        XDIST["pytest-xdist: 4 Workers"]
        LOADGROUP["--dist loadgroup (Pin grouped tests)"]
        SOCKET_GUARD["Network Socket Guard (Non-loopback raises RuntimeError)"]
        MARKER_FILTER["Default: -m 'not live' --strict-markers"]
    end

    subgraph OWNERSHIP["Invariant Ownership (§3)"]
        OWN_EMAIL["Boundary Guard: No raw email in API/Chat"]
        OWN_IDEM["Idempotency: Workflow run deduplication"]
        OWN_PG["Persistence: Idempotent migrations 001-017"]
        OWN_DEG["Resilience: Degrade to NullSemanticMemory"]
    end

    HARNESS --> EXECUTION
    EXECUTION --> OWNERSHIP
```

### 4.1 Granular Test Route Index (R1–R16)

Pick the **narrowest covering route** from [`tests/README.md`](../../tests/README.md):

| Route | Target Path | Tests | Cost (Serial) | Architectural Scope Covered |
|---|---|:---:|:---:|---|
| **R1** | `tests/unit/domain` | 76 | 0.7 s | Pure domain models, frozen contracts, enums, validation rules. Zero I/O. |
| **R2** | `tests/unit/features` | 563 | 2.1 s | Chat controller, memory gateway, intent routing, action-plan mapping. Fakes only. |
| **R3** | `tests/unit/integrations/rag` | 83 | 3.5 s | BM25, RRF fusion, reranker, query guard, key rotation, Turbovec memory. |
| **R4** | `tests/unit/integrations/llm` | 103 | 1.4 s | Prompt assembly, parsing, key rotation, classifiers, OpenRouter last-resort. |
| **R5** | `tests/unit/integrations/gmail` + `mailbox` + `outlook` | 49 | 0.7 s | Gmail/Microsoft OAuth, PKCE, token cipher, provider router, mailbox adapters. |
| **R6** | `tests/unit/integrations` | 366 | 5.2 s | R3 + R4 + R5 plus bootstrap, Supabase adapters. |
| **R7** | `tests/unit/persistence` | 80 | 1.8 s | Repository logic against deterministic in-memory fakes. |
| **R8** | `tests/unit/orchestration` | 19 | 1.7 s | Background worker dispatchers, pollers, queue recovery. |
| **R9** | `tests/unit/scripts` | 100 | 4.2 s | `scripts/*.py` evaluation CLIs (in-process `main(argv)`). |
| **R10** | `tests/unit/fixtures` | 23 | 1.1 s | Golden fixture schemas, corpus frontmatter, and label validation. |
| **R11** | `tests/integration/api` | 78 | 6.4 s | FastAPI routes via in-process ASGI transport (`httpx.ASGITransport`). |
| **R12** | `tests/integration/persistence` | 9 | 1.0 s | Real PostgreSQL repositories (`pg-control-plane` xdist group; skips without DB). |
| **R13** | `tests/integration/email_action_plan` | 38 | 2.8 s | Provider-neutral mailbox → classify → plan → persist on fakes. |
| **R14** | `tests/integration` | 117 | 7.8 s | R11 + R12 + R13 plus corpus-backed end-to-end workflows. |
| **R15** | `tests/unit` | 1369 | 9.8 s | Entire unit test surface. |
| **R16** | `tests/unit --ignore=tests/unit/scripts` | 1269 | 6.8 s | Fast unit test tier (excluding evaluation CLI harnesses). |
| **Full** | `uv run pytest -q` | **1486** | **~12 s (Parallel)** | Full repository test suite. |

### 4.2 Invariant Ownership Principle (§3 `tests/README.md`)

Each invariant is assigned a **single owner file** ([`tests/README.md`](../../tests/README.md)):
- **Raw Email Privacy Boundary:** Owned by `tests/integration/api/test_principal_boundary.py` (API level) and `tests/unit/domain/test_chat_contracts.py` (Chat memory level). Prohibited from being re-asserted in repository/gateway tests.
- **Run Idempotency:** Owned by `tests/integration/email_action_plan/test_workflow.py`.
- **Postgres Migrations Idempotency:** Owned by `tests/integration/persistence/test_postgres_repositories.py`.
- **Network Socket Guard:** Owned by `tests/unit/test_network_guard.py` (raises `RuntimeError` if any non-loopback outbound socket is opened during unit tests).
- **Graceful RAG Degradation:** Owned by `tests/unit/integrations/test_bootstrap.py`.
- **Report Filename Validation:** Owned by `tests/unit/domain/test_report_artifacts.py`.
- **Typed Runtime Composition:** Owned by `tests/unit/test_composition.py`.
- **Pure Settings Parsing:** Owned by `tests/unit/test_config.py`.

---

## 5. Phase 4: Offline Evaluation & Benchmarking Lifecycle

The repository maintains an extensive evaluation framework across 5 distinct domains ([`evaluations/README.md`](../../evaluations/README.md), [`evaluations/HARNESS-GUIDE.md`](../../evaluations/HARNESS-GUIDE.md)).

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

1. **RETRIEVAL Evaluation ([`evaluations/RETRIEVAL/`](../../evaluations/RETRIEVAL/)):**
   - **Harness:** `scripts/evaluate_retrieval.py` against `tests/fixtures/rag/retrieval_golden.json` and `data/extracted/`.
   - **Metrics:** `Hit@1`, `Hit@3`, `MRR`, `Recall@5` across `lexical`, `semantic`, and `mixed` slices, plus `abstention_rate`.
2. **CHAT-RAGAS Dual-Tier Evaluation ([`docs/evaluations/RAGAS.md`](../evaluations/RAGAS.md), [`evaluations/CHAT-RAGAS/`](../../evaluations/CHAT-RAGAS/)):**
   - **Harness:** `scripts/evaluate_chat_rag.py --ragas`.
   - **Tier 1 (Deterministic/Offline):** Page `Hit@1`, `Hit@5`, `MRR`, citation subset validation (`cited_pages ⊆ retrieved_pages`), and 3-stage latency breakdown.
   - **Tier 2 (LLM Judge):** Measures `faithfulness` ($\ge 0.95$) and `answer_relevancy` ($\ge 0.85$) with $\text{model\_judge} \neq \text{model\_generator}$ at `temperature = 0`.
3. **MEMORIES 4-Tier Memory Evaluation ([`evaluations/MEMORIES/`](../../evaluations/MEMORIES/)):**
   - **Harness:** `scripts/evaluate_memory.py` across probe sets (`v1`, `v2`, `v3-four-scopes-hard`).
   - **3-Arm Attribution Protocol ($P, F, F$):** Full memory Pass ($P$), Ablated memory Fail ($F$), and Baseline Fail ($F$). Full parity across SQLite and PostgreSQL.
4. **EMAIL Pipeline Evaluation ([`evaluations/EMAIL/`](../../evaluations/EMAIL/)):**
   - **Harness:** `scripts/evaluate_email_golden.py` against `evaluations/EMAIL/golden_dataset.json`.
   - **Metrics:** Actionability accuracy, retrieve-first compliance, evidence gate cutoff (`EMAIL_RAG_MIN_RERANK_SCORE=0.30`, `EMAIL_RAG_RELATIVE_CUTOFF_RATIO=0.85`), and final route resolution.
5. **CHAT Intent & Latency Evaluation ([`evaluations/CHAT/`](../../evaluations/CHAT/)):**
   - **Harness:** `scripts/evaluate_chat_routing.py` (240 scenarios across 6 intents $\times$ 4 paths) and `e2e/chat-history-latency.spec.ts` for UI switch latency benchmarking.

### 5.2 Four Non-Negotiable Evaluation Rules ([`evaluations/README.md`](../../evaluations/README.md))

1. **Metadata-Only Commit:** Never commit raw email text, chat messages, prompts, or chunk text to git. Artifacts store only IDs, counts, verdicts, and timings.
2. **Mechanics-Only Hashing:** `--embedder hashing` / `--dry-run` is strictly for offline pipeline verification and carries zero semantic rank value.
3. **Corpus Scale Parity:** Evaluation reports are comparable only when executed against identical corpus chunk counts and probe set versions.
4. **Independent Judge:** Model judge must never equal model generator in LLM-as-a-judge evaluations.

---

## 6. Phase 5: Quality Gates, Pre-PR Verification & CI/CD Pipeline

The repository implements a strict, multi-tiered verification gate before any code can enter the `main` branch.

```mermaid
flowchart TD
    DEV["Local Code Changes"] --> PRE_PR{"Mandatory Local Pre-PR Gate<br/>(AGENTS.md)"}

    subgraph GATE_LOCAL["Local Pre-PR Gate (Mandatory)"]
        G1["1. Backend: uv run ruff check . && uv run mypy src && uv run pytest -q"]
        G2["2. Frontend: cd frontend && pnpm lint && pnpm check-types && pnpm test && pnpm build"]
        G3["3. E2E: pnpm run test:e2e (Playwright)"]
        G4["4. Architecture: uv run python docs/architectures/check_docs.py"]
    end

    PRE_PR --> G1 --> G2 --> G3 --> G4
    G4 -->|All Pass| PR["Open Pull Request to main"]
    G4 -->|Any Fail| BLOCK["BLOCKED: Fix Failures Locally"]

    PR --> GITHUB_CI["GitHub Actions CI Workflow (.github/workflows/)"]

    subgraph CI_JOBS["GitHub Actions Workflow Execution"]
        CI_PY["Python CI Job (ubuntu-latest)<br/>• PostgreSQL 16 Service Container<br/>• Astral setup-uv (Python 3.13)<br/>• postgresql-client-16 install<br/>• ruff check & mypy src<br/>• pytest parallel (1400+ tests)<br/>• Serial Postgres verification (Fatal on skip)"]
        CI_FE["Frontend CI Job (ubuntu-latest)<br/>• pnpm 9 setup-node (Node 20)<br/>• eslint . & tsc -b --noEmit<br/>• vitest run & vite build"]
        CI_PW["Playwright E2E Job (ubuntu-latest)<br/>• Chromium installation<br/>• npx playwright test<br/>• Upload 30-day artifact report"]
    end

    GITHUB_CI --> CI_PY & CI_FE & CI_PW
    CI_PY & CI_FE & CI_PW -->|All Green| MERGE["Authorized to Merge into main"]
```

### 6.1 Mandatory 4-Step Local Pre-PR Gate

Before opening any Pull Request to `main`, developers and AI agents must run and pass the 4-step verification gate ([`AGENTS.md`](../../AGENTS.md), [`tests/README.md`](../../tests/README.md)):

```powershell
# Step 1: Backend CI quality gate
uv run ruff check . && uv run mypy src && uv run pytest -q

# Step 2: Frontend CI quality gate
cd frontend && pnpm lint && pnpm check-types && pnpm test && pnpm build

# Step 3: End-to-End Playwright test suite
pnpm run test:e2e

# Step 4: Architecture Model consistency check
uv run python docs/architectures/check_docs.py
```

### 6.2 GitHub Actions Workflows

1. **`ci.yml` (Python & Frontend Pipelines)** ([`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):
   - Spins up `postgres:16-alpine` on port 5432 with healthchecks and `postgresql-client-16`.
   - Runs parallel pytest suite with fatal skip guard for PostgreSQL persistence tests.
   - Runs frozen lockfile installation, `pnpm lint`, `pnpm check-types`, `pnpm test`, and `pnpm build` under Node 20.
2. **`playwright.yml` (E2E Integration)** ([`.github/workflows/playwright.yml`](../../.github/workflows/playwright.yml)):
   - Installs Chromium browser dependencies and executes `npx playwright test`.

---

## 7. Phase 6: Deployment, Persistence & Runtime Modes

The control plane supports three runtime persistence modes selected via `POSTGRES_MODE` in `.env` ([`config.py`](../../src/cowork_agent/config.py), [ADR-010](../../tasks/adr/ADR-010-local-postgres-control-plane-latency.md), [`c3-api-platform.md`](../architectures/c3-api-platform.md), [`deployment.md`](../architectures/deployment.md)).

```mermaid
flowchart TD
    ENV_CONF["Environment Configuration (POSTGRES_MODE in .env)"] --> MODE_SWITCH{POSTGRES_MODE}

    MODE_SWITCH -->|off| MODE_OFF["SQLite Local Mode (.data/*.db)<br/>• In-process session buffer<br/>• 8 Local SQLite DB files<br/>• Outlook connector enabled<br/>• Ephemeral / Zero-Docker"]
    MODE_SWITCH -->|local| MODE_LOCAL["Local Docker PostgreSQL (ADR-010)<br/>• Docker Compose postgres:16-alpine<br/>• 127.0.0.1:5432 LAN ms latency<br/>• Migrations 001-017 applied<br/>• Production SQL Parity"]
    MODE_SWITCH -->|cloud| MODE_CLOUD["Supabase Cloud PostgreSQL<br/>• Session / Direct :5432 connection<br/>• Multi-user Production Mode<br/>• Migrations 001-017 applied<br/>• Private Supabase Storage"]

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
| **Connection URL** | `""` (Empty string in `config.py`) | `DATABASE_URL_LOCAL` (`postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork`) | `DATABASE_URL_CLOUD` (Direct/Session `:5432`) |
| **Migrations** | None (SQLite table creation on boot) | Migrations `001`–`017` applied on boot | Migrations `001`–`017` applied on boot |
| **Outlook Support** | **Enabled** (SQLite-linked connector) | **Disabled** (SQLite-only feature) | **Disabled** (SQLite-only feature) |
| **Worker Support** | In-process dispatching | `mail-todo-worker` enabled | `mail-todo-worker` enabled |
| **Primary Use Case** | Fast local dev loops, zero-dependency offline runs | Local MVP with low latency and full SQL parity | Deployed staging and production environments |

### 7.2 Database Migrations (001–017)

PostgreSQL schema evolution is managed via SQL migrations in [`src/cowork_agent/persistence/migrations/`](../../src/cowork_agent/persistence/migrations/). Each migration ships `.sql` (up) and `.down.sql` (rollback):
- `001_mail_todo.sql`: Mailbox connections, digest runs, task storage, outbox events.
- `002_chat_profiles.sql` – `004_task_episodes.sql`: Typed memory (declarative profiles, summaries, chat-native task episodes).
- `005_identity_workspace_sessions.sql` & `006_durable_chat_sessions.sql`: Tenant/workspace sessions and chat session registry.
- `007_projects_documents.sql` – `012_project_document_chunks.sql`: Project-scoped document storage, chunks, and FTS indexes.
- `014_chat_turn_lifecycle.sql` – `016_chat_turn_activity.sql`: Chat turn lifecycle, `supersedes` episode linking, and reasoning activity.
- `017_calendar_connections.sql`: Per-user Google Calendar OAuth connection and token storage ([ADR-019](../../tasks/adr/ADR-019-executable-chat-tools-run-under-a-per-user-grant.md), [ADR-020](../../tasks/adr/ADR-020-google-grants-stay-separate.md)).

Migrations are applied automatically on FastAPI startup via `apply_migrations(pool)` guarded by PostgreSQL advisory locks ([`config.py`](../../src/cowork_agent/config.py), [`persistence/migrate.py`](../../src/cowork_agent/persistence/migrate.py)).

### 7.3 System Entry Points & CLI Commands

Defined in [`pyproject.toml`](../../pyproject.toml) and [`README.md`](../../README.md):
- `mail-todo-api`: Starts the core FastAPI server (`uv run mail-todo-api` on port `8000`).
- `mail-todo-worker`: Runs the background document ingestion & digest queue worker (`uv run mail-todo-worker`).
- `mail-todo-dev`: Concurrently boots both FastAPI API and background worker for development.
- `mail-todo-ingest-knowledge`: Ingestion CLI converting raw company documents into committed Markdown in `data/extracted/`.
- Frontend Dev Server: `cd frontend && pnpm dev` (Vite dev server on port `5173`).

---

## 8. SDLC Lifecycle Summary Matrix & Comprehensive Workflow Diagram

### 8.1 Summary Lifecycle Matrix

| SDLC Phase | Core Artifacts & Tools | Primary Authority Files | Validation & Quality Gates |
|---|---|---|---|
| **Phase 1: Inception & Governance** | PRDs, Capability Specs, ADRs, C4 Model | `tasks/prds/`<br>`tasks/specs/`<br>`tasks/adr/`<br>`docs/architectures/` | • Domain glossary alignment<br>• ADR conflict check<br>• `workspace.dsl` & `check_docs.py` |
| **Phase 2: Local Dev & Tooling** | `uv`, Python 3.13, `pnpm 9`, React 19, Vite, Tailwind 4 | `AGENTS.md`<br>`pyproject.toml`<br>`frontend/package.json` | • Strict typing (`mypy src`)<br>• Linting (`ruff check .`, `eslint .`)<br>• Type checking (`tsc -b --noEmit`) |
| **Phase 3: Verification & Harness** | Pytest, `tests/xdist_plugin.py`, Routes R1–R16 | `tests/README.md`<br>`tests/conftest.py`<br>`docs/evaluations/` | • Route Index execution<br>• Single Invariant Owner rule (§3)<br>• Network socket isolation guard |
| **Phase 4: Offline Evaluation** | 5 Evaluation Harnesses, Langfuse, Python Logging | `evaluations/HARNESS-GUIDE.md`<br>`evaluations/README.md`<br>`docs/observability/` | • Metadata-only commit rule<br>• RAGAS judge $\neq$ generator<br>• 3-arm memory attribution ($P,F,F$) |
| **Phase 5: Quality Gates & CI/CD** | Pre-PR script, GitHub Actions, Playwright E2E | `AGENTS.md`<br>`.github/workflows/ci.yml`<br>`playwright.yml` | • Mandatory 4-step local Pre-PR gate<br>• CI Python (Postgres container + check)<br>• CI Frontend & Playwright E2E |
| **Phase 6: Persistence & Deployment** | Docker Compose, Postgres migrations (001–017), FastAPI | `config.py`<br>`docker-compose.yml`<br>`src/cowork_agent/persistence/` | • Advisory lock migrations on boot<br>• Fallback persistence verification<br>• Service healthchecks |

### 8.2 End-to-End SDLC Lifecycle Flow

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer / Agent
    participant Gov as Governance (PRD / ADR / C4 Model)
    participant Local as Local Dev (uv / pnpm / mypy)
    participant Test as Test Harness (R1-R16 / Pytest)
    participant Eval as Evaluation Harness (5 Suites)
    participant Gate as Local Pre-PR Gate (4 Steps)
    participant CI as GitHub Actions CI & E2E
    participant Prod as Runtime & Deployment

    Note over Dev,Gov: Phase 1: Inception & Architectural Governance
    Dev->>Gov: Review PRD, verify ADRs, C4 DSL & Domain Glossary
    Gov-->>Dev: Architectural boundary & invariant confirmed

    Note over Dev,Local: Phase 2: Local Development & Toolchain Standards
    Dev->>Local: uv sync & pnpm install
    Dev->>Local: Implement feature (Domain Pure -> Features -> Integrations)
    Dev->>Local: uv run ruff check . & uv run mypy src & pnpm check-types

    Note over Dev,Test: Phase 3: Verification & Test Harness Architecture
    Dev->>Test: Run narrow route (e.g. R1 -> R2 -> R13)
    Dev->>Test: Run parallel suite (uv run pytest -q, ~12s, 1400+ tests)
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
    Dev->>Gate: Step 4: Architecture Model (uv run python docs/architectures/check_docs.py)
    Gate-->>Dev: ALL GATES GREEN

    Note over Dev,CI: Phase 6: CI/CD & Deployment
    Dev->>CI: Push branch & open Pull Request to main
    CI->>CI: Python job with PostgreSQL 16 container
    CI->>CI: Frontend job with Node 20 & pnpm cache
    CI->>CI: Playwright E2E Chromium tests
    CI-->>Dev: CI Green -> Authorized to Merge
    Dev->>Prod: Merge to main -> Boot mail-todo-api & migrations 001-017
```
