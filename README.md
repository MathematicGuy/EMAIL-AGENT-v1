# Cowork Agent (Email-to-Action-Plan)

Hệ thống tự động chuyển đổi Email Gmail chưa đọc thành Kế hoạch Hành động (Action Plan) có cấu trúc, tuân thủ kiến trúc **Cowork Agent Specification** với Agent Core, Hệ thống Bộ nhớ 4 thành phần (Four-Type Memory System) và Module RAG.

---

## 1. Quy trình xử lý cốt lõi (Core Workflow)

Quy trình tự động hóa email diễn ra theo các bước định hình sẵn (deterministic pipeline):

```text
Trigger (Scheduled / Manual)
└── Gmail Fetch & Provider Normalization
    └── Load Context (Long-term profile, Episodic memory)
        └── Intent & Knowledge-Sufficiency Classification
            └── Route Decision: [NO_ACTION | DIRECT_PLAN | RETRIEVE_RAG]
                ├── (Optionally) Semantic Memory / RAG Retrieval
                └── Action Item & Action Plan Generation (Agent Core)
                    └── Output Schema & Grounding Validation
                        └── Persist Task & Persist Episode (system_generated)
                            └── Cleanup Temporary Email State & Short-Term Memory
```

---

## 2. Cấu trúc dự án (Project Structure)

Dự án được tổ chức theo cấu trúc module nghiệp vụ của Cowork Agent Specification (`src/cowork_agent/`):

```text
cowork-agent/
├── pyproject.toml
├── README.md
├── Makefile
├── .env.example
├── AGENTS.md
├── CLAUDE.md
│
├── src/
│   └── cowork_agent/
│       ├── __init__.py
│       ├── app.py                      # FastAPI Application entry point
│       ├── config.py                   # System configuration & environment loaders
│       │
│       ├── domain/                     # Pure business models, enums, errors, identifiers
│       │   ├── models.py
│       │   ├── enums.py
│       │   ├── errors.py
│       │   └── identifiers.py
│       │
│       ├── features/                   # Core business features
│       │   └── email_action_plan/      # Email-to-Action-Plan Agent Core
│       │       ├── workflow.py         # Complete workflow orchestrator
│       │       ├── state.py            # Workflow execution state
│       │       ├── classifier.py       # Intent & knowledge sufficiency classifier
│       │       ├── router.py           # Deterministic route resolver
│       │       ├── generator.py        # Final Action Plan generator
│       │       ├── validators.py       # Output & grounding validators
│       │       ├── policies.py         # Route & planning policies
│       │       ├── schemas.py          # Feature Pydantic schemas
│       │       └── prompts/            # Prompt templates (classify.md, generate.md)
│       │
│       ├── runtime/                    # Session lifecycle & in-memory context execution
│       │   ├── session.py
│       │   ├── context.py
│       │   ├── state.py
│       │   └── cleanup.py
│       │
│       ├── integrations/               # External service boundaries & clients
│       │   ├── gmail/                  # Gmail provider, API client, OAuth & normalizer
│       │   └── llm/                    # LLM multi-provider client (Gemini, OpenAI, Anthropic)
│       │
│       ├── memory/                     # Four-Type Memory System
│       │   ├── service.py              # Unified Memory Service boundary
│       │   ├── scope.py                # Tenant, User, Feature, Run scoping
│       │   ├── short_term/             # Temporary run context (Local in-memory / Redis)
│       │   ├── long_term/              # User preferences & system settings (PostgreSQL)
│       │   ├── episodic/               # Task execution history & outcomes
│       │   └── semantic/               # Semantic memory boundary connecting to RAG
│       │
│       ├── rag/                        # Modular RAG System (Company KB Retrieval)
│       │   ├── ingestion/              # Loaders, parsers, chunkers, enrichers, embedders
│       │   ├── indexing/               # Dense (pgvector/Chroma) & Lexical (BM25) vector stores
│       │   ├── retrieval/              # Dense, Sparse, Hybrid retrievers & Rerankers
│       │   └── context/                # Context builder, token budget & citations
│       │
│       ├── persistence/                # Durable database repositories & migrations
│       │   ├── database.py
│       │   └── repositories/           # Task & Run repositories
│       │
│       ├── orchestration/              # Worker & job scheduling
│       │   ├── scheduler.py
│       │   ├── queue.py                # Job queue & Dead-letter queue (DLQ)
│       │   ├── worker.py               # Idempotent execution worker
│       │   └── retry.py                # Retry policies & backoff timers
│       │
│       └── ops/                        # Observability & Tracing
│           ├── logging.py
│           ├── tracing.py
│           └── events.py
│
├── tests/                              # Software Test Suites
│   ├── unit/                           # Unit tests for features, memory, rag
│   ├── integration/                    # Integration tests for Gmail, DB, RAG
│   └── contracts/                      # Contract tests for memory, chunking, embedding
│
├── configs/                            # Environment & RAG configurations
│   ├── rag/
│   ├── memory/
│   └── environments/
│
├── scripts/                            # Utility scripts
│   ├── run_email.py                    # Manual execution script
│   ├── run_gui.py                      # Streamlit testing GUI
│   ├── ingest.py                       # RAG document ingestion script
│   └── rebuild_index.py                # Rebuild vector index script
│
└── docs/                               # Documentation & Specifications
    ├── architectures/                  # Architecture specs & Target designs
    └── references/                     # Cowork project structure spec
```

---

## 3. Nguyên tắc kiến trúc & Phụ thuộc (Architecture Boundaries)

- **Định hướng phụ thuộc (Dependency Direction):**
  $$\text{domain} \leftarrow \text{features} \leftarrow \text{runtime / orchestration} \leftarrow \text{app entry points}$$
  `domain` hoàn toàn thuần khiết, không phụ thuộc vào framework, database, Gmail SDK hay LLM providers.
- **Phân định trách nhiệm rõ ràng:**
  - **Classifier** xác định tính hành động (`actionability`) và tính đầy đủ của thông tin (`knowledge_sufficiency`).
  - **Router** quyết định luồng chuyển hướng (`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`).
  - **Agent Core** làm chủ việc sinh Action Plan cuối cùng; RAG chỉ cung cấp thông tin ngữ cảnh và trích dẫn (citations).
- **Quyền riêng tư & Bảo vệ dữ liệu (Data Privacy First):**
  - Thư gốc (raw email) và nội dung chưa chuẩn hóa chỉ tồn tại ở `short_term` memory và bị xóa sạch sau khi hoàn tất lượt chạy (`cleanup`).
  - Tuyệt đối không lưu raw email vào Long-term memory, Episodic memory hay Semantic index.

---

## 4. Hướng dẫn khởi chạy (Getting Started)

### 4.1 Cài đặt môi trường

```bash
# Tạo môi trường ảo Python
python -m venv .venv

# Kích hoạt môi trường ảo (Windows PowerShell)
.\.venv\Scripts\activate

# Cài đặt package ở chế độ editable cùng dev & gui dependencies
python -m pip install -e ".[dev,gui]"
```

### 4.2 Cấu hình môi trường (`.env`)

Tạo file `.env` từ `.env.example` và thiết lập các biến môi trường quan trọng:

```env
# Key xoay vòng Gemini (hoặc OpenAI/Anthropic)
GEMINI_API_KEY_1="your_key_1"
GEMINI_API_KEY_2="your_key_2"
GEMINI_API_KEY_3="your_key_3"

# Gmail OAuth Credentials
GMAIL_CLIENT_ID="your_gmail_client_id"
GMAIL_CLIENT_SECRET="your_gmail_client_secret"

# Secret Keys mã hóa
TOKEN_ENCRYPTION_KEY="your_fernet_key"
OAUTH_STATE_SECRET="your_oauth_state_secret"
```

### 4.3 Khởi chạy dịch vụ

- **Chạy API Server (FastAPI):**
  ```powershell
  .\.venv\Scripts\mail-todo-api.exe
  ```

- **Chạy Giao diện Kiểm thử Streamlit GUI:**
  ```powershell
  python scripts/run_gui.py
  ```
  Giao diện sẽ khởi chạy tại `http://localhost:8501`.

- **Chạy Script xử lý thủ công (CLI):**
  ```powershell
  python scripts/run_email.py
  ```

---

## 5. Kiểm tra chất lượng & Testing (Quality Assurance)

Chạy bộ công cụ kiểm tra chất lượng mã nguồn:

```bash
# Kiểm tra linter & code style
python -m ruff check .

# Kiểm tra kiểu tĩnh (Static Type Check)
python -m mypy src

# Chạy toàn bộ Unit, Integration và Contract tests
python -m pytest -q
```

---

## 6. Tài liệu tham khảo (References)
- **Project Structure Spec:** [`docs/references/cowork-project-structure-spec.md`](docs/references/cowork-project-structure-spec.md)

