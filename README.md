# Cowork Agent (Email-to-Action-Plan)

## Knowledge ingestion (PDF/DOCX to the RAG corpus)

The RAG corpus is Markdown under `data/extracted/`. Administrators can convert
local `.docx` and native-text `.pdf` files from a separate source directory
with the `mail-todo-ingest-knowledge` CLI. This tool is intentionally separate
from Gmail: raw email bodies and email attachments are never ingested.

### Prerequisites

Install the Python project, then install the local Rust PDF utility. The
ingestion adapter calls its `detect-pdf` and `pdf2md` commands.

```powershell
python -m pip install -e ".[dev]"
cargo install pdf-inspector

# Optional verification: both commands must be on PATH.
detect-pdf --help
pdf2md --help
```

`cargo` is supplied by the [Rust toolchain](https://rustup.rs/). Restart the
shell after installation if Cargo's bin directory is not yet on `PATH`.

### Ingest documents

Place administrator-approved source files in `data/raw/` (or another source
directory outside the output directory). The CLI recursively discovers only
`.pdf` and `.docx` regular files, rejects symlinks, creates stable Markdown
names, and stores hashes/status in `data/extracted/ingestion-manifest.json`.

```powershell
# Native-text PDFs and DOCX only. OCR is not active in the current runtime.
$env:KNOWLEDGE_INGEST_OCR_ENABLED = "false"

# Inspect what would be processed without writing files.
mail-todo-ingest-knowledge --source data/raw --output data/extracted --dry-run

# Convert changed files to Markdown. Unchanged files are skipped by manifest hash.
mail-todo-ingest-knowledge --source data/raw --output data/extracted

# Re-extract every source file, replacing its generated Markdown output.
mail-todo-ingest-knowledge --source data/raw --output data/extracted --force
```

PDFs are first classified by `pdf-inspector`. Native-text pages are converted
locally and carry `<!-- Page N -->` markers in their Markdown output. A scanned
or mixed PDF that needs OCR currently reports `mistral_not_configured` and
writes no partial document; do not treat `KNOWLEDGE_INGEST_OCR_ENABLED=true` as
an enabled OCR backend yet.

After successful ingestion, restart the API/worker. If Qdrant is enabled and
already contains a collection, request a deliberate full re-index once:

```powershell
$env:QDRANT_REINDEX = "true"
mail-todo-api

# After the collection has been rebuilt, reset this to avoid rebuilding it on
# every subsequent startup.
$env:QDRANT_REINDEX = "false"
```

`QDRANT_REINDEX=true` recreates the entire collection from the committed
Markdown corpus; it is not an incremental update. See
[`docs/evaluations/RETRIEVAL/EMAIL-RAG-STATUS.md`](docs/evaluations/RETRIEVAL/EMAIL-RAG-STATUS.md)

---

## 1. Quy trình xử lý cốt lõi (Core Workflows) — KIẾN TRÚC MỤC TIÊU & RUNTIME

Kiến trúc hệ thống phân tách thành **2 quy trình độc lập (2 separate workflows)**:

### 1.1 Workflow 1: AI Chat Assistant & Hệ thống Bộ nhớ 4 Loại (Multi-Turn Conversational)

Quy trình trò chuyện đa lượt (multi-turn AI Chat) do **Chat Controller** làm chủ, kết nối trực tiếp với hệ thống bộ nhớ 4 loại để duy trì ngữ cảnh, sở thích người dùng và lịch sử tác vụ sinh từ chat (chat-native tasks) qua các phiên hội thoại:

```text
User Message (React web UI)
└── Chat Controller (Session Management & SSE Streaming)
    └── Memory Gateway (Namespace: tenant_id / user_id / session_id / feature: ai_chat)
        ├── 1. Short-Term Working Memory (Chat Session Buffer - Ephemeral TTL)
        ├── 2. Long-Term Declarative Memory (User Persona, Tone, Output Preferences)
        ├── 3. Long-Term Episodic Memory (Validated Task Episodes & Chat Summaries)
        └── 4. Semantic Memory (Enterprise RAG Corpus Access)
            └── Context Assembly & LLM Assistant Invocation
                ├── Direct Response (Streaming Tokens to Chat Thread)
                └── OR Explicit Task Proposal Request (Render Chat Task Proposal)
                    └── Record Turn & TaskEpisode (system_generated, retrieval_eligible=false)
```

**Đặc điểm chính của Chat Memory Workflow:**
- **Short-Term Working Memory**: Lưu trữ lịch sử câu hỏi/trả lời trong phiên hội thoại (`session_id`).
- **Declarative Profile**: Áp dụng quy tắc persona, phong cách trả lời và cấu hình cá nhân của người dùng.
- **Episodic Memory**: Lưu vết tóm tắt hội thoại và các tác vụ sinh trực tiếp từ chat (chat-native tasks) dưới dạng `TaskEpisode` (`system_generated`, `retrieval_eligible = false`). Khi người dùng bấm `Approve` hoặc `Complete` trên UI Chat, trạng thái chuyển thành `retrieval_eligible = true` để phục vụ truy hồi cho các câu hỏi hội thoại sau này.
- **Chat-Native Task Proposals**: Các đề xuất tác vụ được sinh ra khi người dùng yêu cầu trực tiếp trong phiên chat, hỗ trợ nút bấm thao tác (`Approve`, `Complete`, `Reject`) ngay trên luồng hội thoại.

---

### 1.2 Workflow 2: Standalone Email RAG Pipeline (Single-Turn Stateless) — ĐÃ TRIỂN KHAI LOCAL V1-M3

Quy trình xử lý email vận hành theo dạng **đơn lượt (single-turn), không trạng thái (stateless)** và **hoàn toàn độc lập với hệ thống bộ nhớ 4 loại** nhằm đảm bảo tính riêng tư tuyệt đối cho dữ liệu email:

```text
Trigger (Manual / API Request)
└── Gmail Fetch & Provider Normalization (Ephemeral Envelope)
    └── Batch Correlation & Intent Classification
        └── Route Decision: [NO_ACTION | DIRECT_PLAN | RETRIEVE_RAG]
            ├── (If RETRIEVE_RAG) Hybrid Semantic Memory Retrieval (Dense + BM25 + RRF + Jina)
            └── Action Item & Action Plan Generation (Agent Core)
                └── Output Schema & Grounding Validation
                    └── Persist Task DTO
                        └── Cleanup Temporary Email Envelope & Ephemeral Memory (Raw email deleted)
```

**Đã triển khai trong local V1-M3:**
- **Fetch & Normalization**: Lấy email chưa đọc từ Gmail API (`gmail.readonly`) và chuyển thành `EphemeralEmailEnvelope`.
- **Classification & Routing**: Phân loại tính hành động (`actionability`) cùng độ đủ thông tin (`knowledge_sufficiency`), từ đó đưa ra quyết định chuyển hướng không đổi (`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`).
- **Hybrid Retrieval**: Truy hồi tri thức doanh nghiệp từ `data/extracted/*.md` qua truy vấn song song Dense Vector + BM25 lexical, hợp nhất bằng thuật toán RRF (Reciprocal Rank Fusion) và rerank tùy chọn qua Jina AI.
- **Validation & Cleanup**: Ràng buộc citation grounding, lưu thông tin Task DTO, và **xóa sạch (purge)** toàn bộ nội dung thư gốc khỏi bộ nhớ tạm ngay sau khi kết thúc lượt chạy.

---

## 2. Cấu trúc dự án (Project Structure) — HIỆN TRẠNG

```text
email-agent-v1/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── .env.example
│
├── src/
│   └── cowork_agent/
│       ├── __init__.py
│       ├── app.py                      # FastAPI composition root; entry point `mail-todo-api`
│       ├── config.py                   # Environment settings loaders
│       ├── identity.py                 # Tenant & User identity context management
│       ├── ingestion_cli.py            # Knowledge ingestion CLI entry point `mail-todo-ingest-knowledge`
│       ├── api/                        # HTTP handlers & API endpoints
│       │   ├── handlers.py             # Mail-todo endpoints
│       │   ├── chat.py                 # Multi-turn chat & SSE streaming endpoints
│       │   └── projects.py             # Project & document management endpoints
│       ├── domain/                     # Pure business domain models & contracts
│       │   ├── models.py               # Domain entities (Task, ActionPlan, EmailEnvelope)
│       │   ├── target_contracts.py     # Target V2 contracts (Memory, TaskEpisode)
│       │   ├── chat_contracts.py       # Chat memory scope & SSE stream contracts
│       │   └── project_documents.py    # User/project document domain models
│       ├── features/                   # Core business features
│       │   └── email_action_plan/      # Classifier, routing, RAG retrieval & plan workflow
│       │       ├── workflow.py         # Main pipeline orchestrator
│       │       ├── routing.py          # Deterministic router logic
│       │       ├── policies.py         # Planning & decision policies
│       │       ├── ports.py            # Feature interfaces (LLM, RAG, Gmail, Repositories)
│       │       ├── schemas.py          # Feature DTOs & Pydantic models
│       │       ├── shaping.py          # Prompt & context shaping utilities
│       │       ├── validation.py       # Grounding & citation validation engine
│       │       ├── citation_accuracy.py# Citation scoring & verification
│       │       ├── correlation.py      # Thread correlation & batch grouping
│       │       ├── observability.py    # Execution trace logging & latency metrics
│       │       ├── short_term.py       # Transient run-state memory buffer
│       │       └── compat_mapper.py    # V1 ↔ V2 DTO compatibility adapters
│       ├── integrations/               # External service boundaries & adapters
│       │   ├── gmail/                  # OAuth flow, Gmail API adapter, deterministic fakes
│       │   ├── llm/                    # Gemini, Groq, Faucet LLM providers & fakes
│       │   ├── rag/                    # Dense + BM25 + Turbovec / Qdrant semantic memory
│       │   ├── knowledge_ingestion/    # Knowledge extraction pipeline (PDF/DOCX)
│       │   ├── project_documents/      # Encrypted document store & media sniffing
│       │   ├── storage/                # Supabase private storage adapter
│       │   └── key_rotation.py         # API key rotation manager
│       ├── orchestration/              # Dispatchers & background workers
│       │   ├── local.py                # In-process local execution dispatcher
│       │   └── worker.py               # Background worker process entry point `mail-todo-worker`
│       ├── persistence/                # Database repositories & storage adapters
│       │   ├── repositories/           # SQLite & Supabase Postgres repositories
│       │   └── migrations/             # SQL schema migration scripts
│       └── security/                   # Auth & security utilities
│
├── frontend/                           # React 19 + Vite + Tailwind 4 frontend (pnpm)
│
├── frontend/                            # React/Vite web application
│
├── tests/
│   ├── unit/                           # Unit tests (policies, providers, RAG)
│   ├── integration/                    # Integration tests (server, full workflow)
│   └── compatibility/                  # Contract & DTO compatibility tests
│
└── docs/                               # Documentation & Specifications
    ├── architectures/                  # Target architecture specs
    ├── PRD-v1-Core-Email-and-RAG.md    # Product requirements for V1 Email RAG
    ├── PRD-v2-Memory-Extension.md      # Product requirements for V2 Chat Memory System
    └── references/                     # Detailed technical specs & experience registry
```

**Tóm tắt trạng thái triển khai:**
- **Đã hoàn thành trong Runtime Local V1-M3:** Email RAG pipeline (`features/email_action_plan/`), Hybrid RAG (`integrations/rag/`), Gmail OAuth, Gemini/Groq providers, SQLite persistence, và React frontend (`frontend/`).
- **Kiến trúc mục tiêu đang di cư (Target Roadmap):** Khung Chat API Controller, SSE token streaming handler, Logical Memory Gateway cho AI Chat (`feature: ai_chat`, `session_id`), và giao diện AI Chat UI tập trung hỗ trợ các tác vụ sinh trực tiếp từ chat (chat-native tasks).

---

## 3. Nguyên tắc kiến trúc & Phụ thuộc (Architecture Boundaries)

- **Phân tách 2 Luồng nghiệp vụ (Decoupled Dual-Workflow Architecture):**
  - **AI Chat Assistant (Multi-Turn):** Quản lý phiên hội thoại (`session_id`), kết nối trực tiếp với hệ thống Bộ nhớ 4 loại (Working Memory, User Profile, Episodic Task Memory, Semantic Document RAG).
  - **Standalone Email RAG Pipeline (Single-Turn):** Hoạt động độc lập, không trạng thái (stateless), không tích hợp bộ nhớ 4 loại. Không còn công cụ thực thi `@Email` trong chat (đã bãi bỏ theo ADR-004).
- **Định hướng phụ thuộc (Dependency Direction):**
  $$\text{domain} \leftarrow \text{features} \leftarrow \text{integrations / orchestration / persistence} \leftarrow \text{app entry points}$$
  `domain` giữ sự thuần khiết tuyệt đối, không chứa import từ bất kỳ thư viện framework, database, Gmail SDK hay LLM provider nào.
- **Phân định trách nhiệm rõ ràng trong Email Pipeline:**
  - **Classifier**: Phân loại tính hành động (`actionability`) và tính đầy đủ thông tin (`knowledge_sufficiency`).
  - **Router**: Đưa ra quyết định định tuyến không đổi (`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`).
  - **Agent Core**: Làm chủ việc tổng hợp và sinh Action Plan cuối cùng; RAG chỉ đóng vai trò cung cấp ngữ cảnh bổ trợ và trích dẫn (citations).
- **Quyền riêng tư & Bảo vệ dữ liệu (Data Privacy First):**
  - Thư gốc (raw email) và nội dung chưa chuẩn hóa **chỉ tồn tại dưới dạng transient state** trong quá trình xử lý email và phải được **xóa sạch khỏi bộ nhớ** ngay sau khi hoàn tất (`cleanup`).
  - Tuyệt đối **không lưu trữ raw email** vào Long-term Profile, Episodic Memory hay Semantic Vector index.
  - Các `TaskEpisode` sinh ra từ chat (chat-native tasks) chỉ lưu trạng thái ban đầu `validation_status = system_generated` và `retrieval_eligible = false` cho tới khi có thao tác xác nhận người dùng (`Approve` / `Complete`) trên giao diện Chat.

---

## 4. Hướng dẫn khởi chạy (Getting Started)

### 4.1 Cài đặt môi trường

```bash
# Tạo môi trường ảo Python
python3 -m venv .venv
# Hoặc với uv: uv venv --python 3.12 .venv

# Kích hoạt môi trường ảo (Linux / Ubuntu / macOS)
source .venv/bin/activate

# Kích hoạt môi trường ảo (Windows PowerShell)
# .\.venv\Scripts\activate

# Cài đặt package ở chế độ editable cùng dev dependencies
pip install -e ".[dev]"
```

### 4.2 Cấu hình môi trường (`.env`)

Tạo file `.env` từ `.env.example` và thiết lập các biến môi trường quan trọng. `JINA_API_KEY` là tùy chọn: để trống thì retrieval giữ nguyên thứ tự RRF; lỗi/response không hợp lệ từ Jina cũng fallback an toàn theo cùng thứ tự.

```env
# Supabase Postgres control plane URL (nếu không thiết lập sẽ dùng local SQLite fallback)
DATABASE_URL="postgresql://user:pass@host:5432/dbname"

# Semantic Memory Store Provider (turbovec | qdrant)
RAG_STORE_PROVIDER="turbovec"
QDRANT_ENABLED="false"

# Feature Flags
USER_DOCUMENTS_ENABLED="false"

# Key xoay vòng Gemini (hoặc Groq)
GEMINI_API_KEY_1="your_key_1"
GEMINI_API_KEY_2="your_key_2"
GEMINI_API_KEY_3="your_key_3"

# Jina embeddings power RAG indexing and retrieval; the same key enables reranking.
JINA_API_KEY="your_jina_api_key"
JINA_EMBEDDING_MODEL="jina-embeddings-v5-omni-small"
JINA_EMBEDDING_DIMENSIONS=1024
JINA_EMBEDDING_TIMEOUT_SECONDS=30

# Gmail OAuth Credentials
GMAIL_CLIENT_ID="your_gmail_client_id"
GMAIL_CLIENT_SECRET="your_gmail_client_secret"

# Secret Keys mã hóa
TOKEN_ENCRYPTION_KEY="your_fernet_key"
OAUTH_STATE_SECRET="your_oauth_state_secret"
```

When migrating an existing Qdrant company-knowledge collection from Gemini,
set `QDRANT_REINDEX=true` for one startup so every vector is recreated with
Jina. Set it back to `false` after that startup. Set
`QDRANT_VECTOR_SIZE=1024` to match the default Jina v5 Omni Small output.

### 4.3 Khởi chạy dịch vụ

- **Chạy API Server (FastAPI):**
  - *Linux / Ubuntu:*
    ```bash
    .venv/bin/mail-todo-api
    ```
  - *Windows PowerShell:*
    ```powershell
    .\.venv\Scripts\mail-todo-api.exe
    ```

- **Chạy React frontend:**
  ```powershell
  cd frontend
  corepack enable
  pnpm install
  Copy-Item .env.example .env.local
  pnpm dev
  ```
  Giao diện sẽ khởi chạy tại `http://localhost:5173`.

---

## 5. Kiểm tra chất lượng & Testing (Quality Assurance)

Chạy bộ công cụ kiểm tra chất lượng mã nguồn:

```bash
# Kiểm tra linter & code style (Backend)
python -m ruff check .

# Kiểm tra kiểu tĩnh (Static Type Check - Backend)
python -m mypy src

# Chạy toàn bộ Unit, Integration và Contract tests
python -m pytest -q

# Chạy E2E Frontend-API Integration tests (Linux / Ubuntu & Windows)
python -m pytest tests/integration/api/test_e2e_frontend_api.py -v

# Kiểm tra linter, typecheck & unit test cho Frontend (folder frontend/)
cd frontend
pnpm test
pnpm check-types
pnpm lint
```

---

## 6. Tài liệu tham khảo (References)
- **Kiến trúc mục tiêu:** [`docs/architectures/TARGET-ARCHITECTURE.md`](docs/architectures/TARGET-ARCHITECTURE.md)
- **Product Requirements:** [`tasks/prds/PRD-v1-Core-Email-and-RAG.md`](tasks/prds/PRD-v1-Core-Email-and-RAG.md) và [`tasks/prds/PRD-v2-Memory-Extension.md`](tasks/prds/PRD-v2-Memory-Extension.md)
- **Experience Registry cho coding agents:** [`docs/references/agent-experience-registry.md`](docs/references/agent-experience-registry.md)

