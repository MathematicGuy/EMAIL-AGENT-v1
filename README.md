# Cowork Agent (Email-to-Action-Plan)

Hệ thống tự động chuyển đổi Email Gmail chưa đọc thành Kế hoạch Hành động (Action Plan) có cấu trúc, tuân thủ kiến trúc **Cowork Agent Specification** với Agent Core, Hệ thống Bộ nhớ 4 thành phần (Four-Type Memory System) và Module RAG.

> **Trạng thái tài liệu — đọc trước khi dùng:** README này mô tả cả **hiện trạng** (mục 4, 5) và **kiến trúc mục tiêu** (mục 1, 2). Bản MVP hiện tại chỉ triển khai pipeline trích xuất một lượt với SQLite + bộ nhớ in-process; các module `memory/`, `rag/`, `runtime/`, `ops/`, queue/DLQ và PostgreSQL **chưa tồn tại trong mã nguồn**. Phân tích đầy đủ hiện trạng ↔ mục tiêu và các milestone di cư: [`docs/master-comparison.md`](docs/master-comparison.md).

---

## 1. Quy trình xử lý cốt lõi (Core Workflow) — KIẾN TRÚC MỤC TIÊU, CHƯA TRIỂN KHAI ĐẦY ĐỦ

Quy trình tự động hóa email **theo kiến trúc mục tiêu** diễn ra theo các bước định hình sẵn (deterministic pipeline):

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

**Đã triển khai trong MVP:** Gmail Fetch & Normalization → một lượt gọi LLM (phân loại + trích xuất kế hoạch gộp trong `features/email_action_plan/workflow.py`) → lọc/ưu tiên/sắp xếp deterministic → lưu kết quả in-memory. Chưa có classifier/router riêng, RAG, memory system hay queue. Chi tiết: [`docs/master-comparison.md`](docs/master-comparison.md).

---

## 2. Cấu trúc dự án (Project Structure) — HIỆN TRẠNG

Dự án được tổ chức theo module nghiệp vụ của Cowork Agent Specification (`src/cowork_agent/`). Dưới đây là cấu trúc **thực tế đang tồn tại trong mã nguồn**:

```text
email-agent-v1/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── .env.example
│
├── src/
│   └── cowork_agent/
│       ├── app.py                      # FastAPI composition root; entry point `mail-todo-api`
│       ├── config.py                   # Environment settings loaders (Gmail, Gemini, Groq)
│       ├── api/                        # HTTP handlers / response serialization
│       │   └── handlers.py
│       ├── domain/                     # Pure business models
│       │   └── models.py
│       ├── features/                   # Core business features
│       │   └── email_action_plan/      # Email-to-Action-Plan workflow (combined extraction)
│       │       ├── workflow.py         # Workflow orchestrator
│       │       ├── policies.py         # Route & planning policies
│       │       ├── ports.py            # Provider/repository protocols
│       │       └── schemas.py          # Feature schemas
│       ├── gui/                        # Streamlit testing GUI
│       │   └── app.py
│       ├── integrations/               # External service boundaries
│       │   ├── gmail/                  # OAuth, Gmail adapter, deterministic fakes
│       │   └── llm/                    # Gemini & Groq providers, fakes
│       ├── orchestration/              # In-process local orchestration
│       │   └── local.py
│       └── persistence/                # SQLite mailbox-connection repo
│           ├── repositories/
│           └── migrations/             # SQL migrations (no runner wired yet)
│
├── scripts/
│   └── run_gui.py                      # Streamlit testing GUI launcher
│
├── tests/
│   ├── unit/                           # Unit tests (policies, providers)
│   └── integration/                    # Integration tests (server, workflow)
│
└── docs/                               # Documentation & Specifications
    ├── adr/                            # Architecture Decision Records (ADR-001..003)
    ├── architectures/                  # Target architecture & gap analysis
    └── references/                     # Specs & experience registry
```

**Chưa tồn tại (thuộc kiến trúc mục tiêu):** `memory/`, `rag/`, `runtime/`, `ops/`, `orchestration/queue.py|worker.py|scheduler.py`, `tests/contracts/`, `configs/`, `Makefile`, `CLAUDE.md`, `scripts/run_email.py`. Chỉ scaffold các module này khi yêu cầu trích dẫn rõ một milestone trong [`docs/master-comparison.md`](docs/master-comparison.md).

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

Tạo file `.env` từ `.env.example` và thiết lập các biến môi trường quan trọng (`.env.example` hiện chỉ chứa các biến mà bản MVP **thực sự đọc** — cấu hình PostgreSQL/queue thuộc kiến trúc mục tiêu chưa triển khai):

```env
# Key xoay vòng Gemini (hoặc Groq)
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
- **Hiện trạng ↔ Mục tiêu & Milestone di cư:** [`docs/master-comparison.md`](docs/master-comparison.md)
- **Kiến trúc mục tiêu:** [`docs/architectures/TARGET-ARCHITECTURE.md`](docs/architectures/TARGET-ARCHITECTURE.md)
- **Architecture Decision Records:** [`docs/adr/`](docs/adr/) (ADR-003 thay thế phạm vi attachment của ADR-001/002)
- **Product Requirements:** [`docs/PRD-v1-Core-Email-and-RAG.md`](docs/PRD-v1-Core-Email-and-RAG.md) và [`docs/PRD-v2-Memory-Extension.md`](docs/PRD-v2-Memory-Extension.md)
- **Project Structure Spec:** [`docs/references/cowork-project-structure-spec.md`](docs/references/cowork-project-structure-spec.md)
- **Experience Registry cho coding agents:** [`docs/references/agent-experience-registry.md`](docs/references/agent-experience-registry.md)

