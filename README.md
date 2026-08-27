# Cowork Agent (Email-to-Action-Plan)

FastAPI service that turns unread Gmail and Outlook mail into structured action plans, plus
multi-turn AI Chat. Outlook is an optional, read-only SQLite-mode connector linked to an
existing Gmail-owned local account.

---

## 1. Core Architecture & Workflows

The system separates into **two decoupled workflows**:

### 1.1 Multi-Turn AI Chat Assistant & 4-Tier Memory

Managed by the **Chat Controller** with a unified 4-tier memory gateway:

```text
User Message (React web UI)
└── Chat Controller (Session Management & SSE Streaming)
    └── Memory Gateway (tenant_id / user_id / session_id / feature: ai_chat)
        ├── 1. Short-Term Working Memory (Chat Session Buffer - Ephemeral TTL)
        ├── 2. Declarative Memory (User Persona, Tone, Output Preferences)
        ├── 3. Episodic Memory (Validated Task Episodes & Chat Summaries)
        └── 4. Semantic Memory (Enterprise RAG Corpus Access)
            └── LLM Invocation -> SSE Stream / Task Proposals
```

- **Working Memory**: In-process chat buffer for the active session.
- **Declarative Memory**: User persona, tone, and formatting preferences.
- **Episodic Memory**: Validated task episodes & summaries. Chat-generated tasks start with `retrieval_eligible=false` until explicitly approved on the UI.
- **Semantic Memory**: Enterprise knowledge retrieval via Turbovec hybrid search.

---

### 1.2 Standalone Email RAG Pipeline (Single-Turn, Stateless)

Runs single-turn, stateless execution isolated from chat memory for privacy:

```text
Trigger (Manual / API Request)
└── Mailbox Fetch (Gmail `gmail.readonly` / Outlook `Mail.Read`) -> Ephemeral Envelope
    └── Intent & Actionability Classification
        └── Route: [NO_ACTION | DIRECT_PLAN | RETRIEVE_RAG]
            ├── (If RETRIEVE_RAG) Hybrid Search (Dense + BM25 + RRF)
            └── Action Plan Generation & Citation Validation
                └── Persist Task DTO & Purge Raw Email Body
```

- **Privacy Invariant**: Raw emails and attachments are transient and deleted after execution. Never stored in long-term memory or semantic vector stores.
- **No Chat `@Email` Tool**: Email RAG and Chat remain strictly decoupled (ADR-004).

---

## 2. Knowledge Ingestion CLI (`mail-todo-ingest-knowledge`)

Converts approved company documents from `data/raw/` into committed Markdown in `data/extracted/`.

```powershell
# Prerequisites: Rust toolchain for PDF parser
cargo install pdf-inspector

# Dry-run inspection
uv run mail-todo-ingest-knowledge --source data/raw --output data/extracted --dry-run

# Convert changed files (skips unchanged via manifest hash)
uv run mail-todo-ingest-knowledge --source data/raw --output data/extracted

# Force re-extract all files
uv run mail-todo-ingest-knowledge --source data/raw --output data/extracted --force
```

- Supported types: `.pdf`, `.docx`, `.txt`, `.md`.
- Output: NFC-sanitized Markdown with closed frontmatter and `ingestion-manifest.json`.

---

## 3. Project Structure

```text
src/cowork_agent/
├── app.py                      # FastAPI composition root; entry point `mail-todo-api`
├── config.py                   # Environment settings loaders
├── identity.py                 # Tenant & User identity context management
├── ingestion_cli.py            # Knowledge ingestion CLI `mail-todo-ingest-knowledge`
├── api/                        # HTTP endpoints (mail-todo, chat SSE, projects)
├── domain/                     # Pure domain models (Task, ActionPlan, Chat, Project)
├── features/                   # Core business logic (email_action_plan workflow)
├── integrations/               # Gmail/Outlook OAuth, LLM providers, RAG (Turbovec)
├── orchestration/              # Dispatchers & background workers (`mail-todo-worker`)
└── persistence/                # SQLite & PostgreSQL repositories, SQL migrations

frontend/                       # React 19 + Vite + Tailwind 4 web application
tests/                          # Unit & integration test suite (offline by construction)
docs/                           # Architecture specs, PRDs, and reference documentation
```

---

## 4. Getting Started

### 4.1 Installation & Environment Setup

Always use `uv` for environment management:

```powershell
# Install dependencies
uv sync --extra dev --extra postgres

# Copy configuration files
Copy-Item .env.example .env
Copy-Item config.example config
```

### 4.2 Key Environment Variables (`.env`)

```env
# Persistence: "off" (SQLite under .data/), "local" (Docker Postgres), "cloud" (Supabase)
POSTGRES_MODE=off

# Semantic Memory Store (Email RAG and Chat Type 4)
RAG_STORE_PROVIDER=turbovec

# Feature Flags
USER_DOCUMENTS_ENABLED="false"

# LLM Providers (Key Rotation)
GEMINI_API_KEY_1="your_key_1"
GEMINI_API_KEY_2="your_key_2"

# Jina Embeddings (Optional reranking & dense retrieval)
JINA_API_KEY="your_jina_api_key"

# Gmail OAuth Credentials & Secrets
GMAIL_CLIENT_ID="your_gmail_client_id"
GMAIL_CLIENT_SECRET="your_gmail_client_secret"
TOKEN_ENCRYPTION_KEY="your_fernet_key"
OAUTH_STATE_SECRET="your_oauth_state_secret"

# Optional Microsoft delegated OAuth (POSTGRES_MODE=off only)
MICROSOFT_CLIENT_ID="your_microsoft_client_id"
MICROSOFT_CLIENT_SECRET="your_microsoft_client_secret"
MICROSOFT_TENANT="common"
MICROSOFT_REDIRECT_URI="http://localhost:8000/v1/mail-todo/oauth/outlook/callback"
```

Microsoft app registration must grant only the standard OIDC/offline scopes plus delegated
`Mail.Read`. Outlook connection and scanning are intentionally unavailable when the control
plane uses local or cloud PostgreSQL; no PostgreSQL migration is included in this increment.
Register the callback above as a Web redirect URI, enable delegated `Mail.Read`, and do not
grant any mail write permission. Connect Gmail first in Mail Inbox, then link Outlook to that
Gmail owner. The frontend commands are case-insensitive: `@email` scans the selected Gmail,
`@outlook` scans the selected Outlook, and `@mail` runs both scans concurrently and displays
one aggregated card. These commands dispatch the standalone mail workflow; they are not AI
Chat tools and raw email is not added to chat memory.

### 4.3 Running Services

```powershell
# Start both API and Background Worker (Dev Mode)
uv run mail-todo-dev

# Or run separately:
uv run mail-todo-api      # FastAPI Server (http://127.0.0.1:8000)
uv run mail-todo-worker   # Document queue worker

# Start Frontend (http://localhost:5173)
cd frontend
pnpm install
pnpm dev
```

---

## 5. Quality Assurance & Verification

```powershell
# Backend checks (Fast gate)
uv run pytest -q ; uv run ruff check . ; uv run mypy src

# Frontend checks
cd frontend
pnpm test
pnpm check-types
pnpm lint
```

---

## 6. Authoritative References

- **Target Architecture:** [`docs/architectures/TARGET-ARCHITECTURE.md`](docs/architectures/TARGET-ARCHITECTURE.md)
- **Test Routing Index:** [`tests/README.md`](tests/README.md)
- **Chat tools walkthrough:** [`docs/guides/tool-registry-from-first-principles.md`](docs/guides/tool-registry-from-first-principles.md)
- **Experience Registry:** [`docs/references/agent-experience-registry.md`](docs/references/agent-experience-registry.md)
- **PRDs:** [`tasks/prds/PRD-v1-Core-Email-and-RAG.md`](tasks/prds/PRD-v1-Core-Email-and-RAG.md) · [`tasks/prds/PRD-v2-Memory-Extension.md`](tasks/prds/PRD-v2-Memory-Extension.md)
