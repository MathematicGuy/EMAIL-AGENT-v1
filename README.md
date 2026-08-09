# Cowork Agent (Email-to-Action-Plan)

Hệ thống tự động chuyển đổi Email Gmail chưa đọc thành Kế hoạch Hành động (Action Plan) có cấu trúc. Runtime hiện tại tích hợp classifier/router riêng và Company Knowledge RAG truy hồi-only cục bộ. Hệ thống bộ nhớ bốn loại (Working, Profile, Episodic, Semantic RAG) được phân tách thành nền tảng cho **AI Chat Assistant**, trong đó quy trình xử lý email đóng vai trò công cụ thực thi **`@Email` Skill Tool**.

> **Trạng thái tài liệu — đọc trước khi dùng:** README này phân biệt **runtime hiện tại** và **kiến trúc mục tiêu**. Local V1-M3 đã hoàn thành khung Email RAG với classifier/router, `SemanticMemoryPort` và `HybridSemanticMemory` (in-memory dense search, BM25, RRF trên `data/extracted/*.md`, cùng Jina reranking tùy chọn). Qdrant và hệ thống bộ nhớ bốn loại được căn chỉnh làm nền tảng cho **AI Chat Assistant**, hỗ trợ công cụ thực thi `@Email` trong chat. Phân tích chi tiết: [`docs/master-comparison.md`](docs/master-comparison.md) và phạm vi căn chỉnh bộ nhớ: [`docs/references/doc-update-scope-memory-chat.md`](docs/references/doc-update-scope-memory-chat.md).

---

## 1. Quy trình xử lý cốt lõi (Core Workflows) — KIẾN TRÚC MỤC TIÊU & RUNTIME

Kiến trúc hệ thống phân tách thành **2 quy trình độc lập (2 separate workflows)**:

### 1.1 Workflow 1: AI Chat Assistant & Hệ thống Bộ nhớ 4 Loại (Multi-Turn Conversational) — KIẾN TRÚC MỤC TIÊU

Quy trình trò chuyện đa lượt (multi-turn AI Chat) do **Chat Controller** làm chủ, kết nối trực tiếp với hệ thống bộ nhớ 4 loại để duy trì ngữ cảnh, sở thích người dùng và lịch sử tác vụ qua các phiên hội thoại:

```text
User Message (Web UI / Streamlit)
└── Chat Controller (Session Management & SSE Streaming)
    └── Memory Gateway (Namespace: tenant_id / user_id / session_id / feature: ai_chat)
        ├── 1. Short-Term Working Memory (Chat Session Buffer - Ephemeral TTL)
        ├── 2. Long-Term Declarative Memory (User Persona, Tone, Output Preferences)
        ├── 3. Long-Term Episodic Memory (Validated Task Episodes & Chat Summaries)
        └── 4. Semantic Memory (Enterprise RAG Corpus Access)
            └── Context Assembly & LLM Assistant Invocation
                ├── Direct Response (Streaming Tokens to Chat Thread)
                └── OR Execute Tool Skill (e.g. `@Email` Skill Tool)
                    └── Render Embedded Action Plan Card in Chat Thread
                        └── Auto-Record Turn & Episode (system_generated, retrieval_eligible=false)
```

**Đặc điểm chính của Chat Memory Workflow:**
- **Short-Term Working Memory**: Lưu trữ lịch sử câu hỏi/trả lời trong phiên hội thoại (`session_id`).
- **Declarative Profile**: Áp dụng quy tắc persona, phong cách trả lời và cấu hình cá nhân của người dùng.
- **Episodic Memory**: Tự động lưu vết kết quả từ công cụ `@Email` dưới dạng Task Episode (`system_generated`). Khi người dùng bấm `Approve` hoặc `Complete` trên UI Chat, trạng thái chuyển thành `retrieval_eligible = true` để phục vụ truy hồi cho các câu hỏi hội thoại sau này.
- **Tích hợp `@Email` Skill Tool**: Công cụ `@Email` được kích hoạt như một skill thực thi stateless bên trong khung chat, hiển thị Action Plan Card đi kèm trích dẫn (citation chips) và bộ nút thao tác ngay trong luồng trò chuyện.

---

### 1.2 Workflow 2: Standalone Email RAG Pipeline / Executable `@Email` Tool (Single-Turn Stateless) — ĐÃ TRIỂN KHAI LOCAL V1-M3

Quy trình xử lý email vận hành theo dạng **đơn lượt (single-turn), không trạng thái (stateless)** và **hoàn toàn độc lập với hệ thống bộ nhớ 4 loại** nhằm đảm bảo tính riêng tư tuyệt đối cho dữ liệu email:

```text
Trigger (Manual / Scheduled / In-Chat `@Email` Skill Call)
└── Gmail Fetch & Provider Normalization (Ephemeral Envelope)
    └── Batch Correlation & Intent Classification
        └── Route Decision: [NO_ACTION | DIRECT_PLAN | RETRIEVE_RAG]
            ├── (If RETRIEVE_RAG) Hybrid Semantic Memory Retrieval (Dense + BM25 + RRF + Jina)
            └── Action Item & Action Plan Generation (Agent Core)
                └── Output Schema & Grounding Validation
                    └── Persist Task DTO & Task Episode Entry
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
│       ├── config.py                   # Environment settings loaders (Gmail, Gemini, Groq)
│       ├── identity.py                 # Tenant & User identity context management
│       ├── api/                        # HTTP handlers / response serialization
│       │   └── handlers.py
│       ├── domain/                     # Pure business domain models & target contracts
│       │   ├── models.py               # Domain entities (Task, ActionPlan, EmailEnvelope)
│       │   └── target_contracts.py     # Target V2 contracts (Memory, Chat, TaskEpisode)
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
│       ├── gui/                        # Streamlit testing GUI
│       │   └── app.py                  # Multi-tab Streamlit dashboard
│       ├── integrations/               # External service boundaries & adapters
│       │   ├── gmail/                  # OAuth flow, Gmail API adapter, deterministic fakes
│       │   ├── llm/                    # Gemini, Groq, Faucet LLM providers & fakes
│       │   └── rag/                    # Dense + BM25 + RRF + optional Jina reranker
│       │       ├── hybrid.py           # HybridSemanticMemory implementation
│       │       ├── bm25.py             # Lexical BM25 keyword indexer
│       │       ├── embeddings.py       # Dense vector embedding generator
│       │       ├── jina_reranker.py    # Jina AI reranking API client
│       │       ├── knowledge_base.py   # Corpus document loader & chunker
│       │       ├── rrf.py              # Reciprocal Rank Fusion algorithm
│       │       └── bootstrap.py        # Local RAG bootstrap loader
│       ├── orchestration/              # Local dispatchers & workers
│       │   └── local.py                # In-process local execution dispatcher
│       └── persistence/                # Database repositories & storage adapters
│           ├── repositories/           # SQLite mailbox-connection & task repos
│           └── migrations/             # SQL schema migration scripts
│
├── scripts/
│   └── run_gui.py                      # Streamlit testing GUI launcher script
│
├── tests/
│   ├── unit/                           # Unit tests (policies, providers, RAG)
│   └── integration/                    # Integration tests (server, full workflow)
│
└── docs/                               # Documentation & Specifications
    ├── adr/                            # Architecture Decision Records (ADR-001..003)
    ├── architectures/                  # Target architecture specs (TARGET-ARCHITECTURE.md)
    ├── master-comparison.md            # Gap analysis & migration roadmap
    ├── PRD-v1-Core-Email-and-RAG.md    # Product requirements for V1 Email RAG
    ├── PRD-v2-Memory-Extension.md      # Product requirements for V2 Chat Memory System
    ├── SPEC-Demo-Frontend.md           # Streamlit Frontend Spec & UI requirements
    └── references/                     # Detailed technical specs & experience registry
        ├── doc-update-scope-memory-chat.md # Scope realignment for AI Chat Memory
        ├── memory-system-and-chat-demo-analysis.md # Detailed analysis doc
        └── EMAIL-RAG-ARCHITECHTURE.md  # Detailed RAG architecture spec
```

**Tóm tắt trạng thái triển khai:**
- **Đã hoàn thành trong Runtime Local V1-M3:** Email RAG pipeline (`features/email_action_plan/`), Hybrid RAG (`integrations/rag/`), Gmail OAuth, Gemini/Groq providers, SQLite persistence, và Streamlit GUI (`gui/app.py`).
- **Kiến trúc mục tiêu đang di cư (Target Roadmap):** Khung Chat API Controller, SSE token streaming handler, Logical Memory Gateway cho AI Chat (`feature: ai_chat`, `session_id`), và giao diện AI Chat UI tập trung hỗ trợ công cụ thực thi `@Email` Skill.

---

## 3. Nguyên tắc kiến trúc & Phụ thuộc (Architecture Boundaries)

- **Phân tách 2 Luồng nghiệp vụ (Decoupled Dual-Workflow Architecture):**
  - **AI Chat Assistant (Multi-Turn):** Quản lý phiên hội thoại (`session_id`), kết nối trực tiếp với hệ thống Bộ nhớ 4 loại (Working Memory, User Profile, Episodic Task Memory, Semantic Document RAG).
  - **Email RAG Pipeline / `@Email` Skill (Single-Turn):** Hoạt động không trạng thái (stateless), không dính líu trực tiếp với hệ thống bộ nhớ 4 loại. Đóng vai trò là một Skill/Tool thực thi độc lập khi được kích hoạt.
- **Định hướng phụ thuộc (Dependency Direction):**
  $$\text{domain} \leftarrow \text{features} \leftarrow \text{integrations / orchestration / persistence} \leftarrow \text{app entry points}$$
  `domain` giữ sự thuần khiết tuyệt đối, không chứa import từ bất kỳ thư viện framework, database, Gmail SDK hay LLM provider nào.
- **Phân định trách nhiệm rõ ràng trong Email Tool Pipeline:**
  - **Classifier**: Phân loại tính hành động (`actionability`) và tính đầy đủ thông tin (`knowledge_sufficiency`).
  - **Router**: Đưa ra quyết định định tuyến không đổi (`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`).
  - **Agent Core**: Làm chủ việc tổng hợp và sinh Action Plan cuối cùng; RAG chỉ đóng vai trò cung cấp ngữ cảnh bổ trợ và trích dẫn (citations).
- **Quyền riêng tư & Bảo vệ dữ liệu (Data Privacy First):**
  - Thư gốc (raw email) và nội dung chưa chuẩn hóa **chỉ tồn tại dưới dạng transient state** trong quá trình chạy lượt `@Email` tool và phải được **xóa sạch khỏi bộ nhớ** ngay sau khi hoàn tất (`cleanup`).
  - Tuyệt đối **không lưu trữ raw email** vào Long-term Profile, Episodic Memory hay Semantic Vector index.
  - Các Action Plan sinh ra từ `@Email` chỉ được lưu dưới dạng `task_episode` với trạng thái ban đầu `validation_status = system_generated` và `retrieval_eligible = false` cho tới khi có thao tác xác nhận người dùng (`Approve` / `Complete`) trên giao diện Chat.

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

Tạo file `.env` từ `.env.example` và thiết lập các biến môi trường quan trọng. `JINA_API_KEY` là tùy chọn: để trống thì retrieval giữ nguyên thứ tự RRF; lỗi/response không hợp lệ từ Jina cũng fallback an toàn theo cùng thứ tự.

```env
# Key xoay vòng Gemini (hoặc Groq)
GEMINI_API_KEY_1="your_key_1"
GEMINI_API_KEY_2="your_key_2"
GEMINI_API_KEY_3="your_key_3"

# Optional hybrid-RAG reranking; blank means pass-through after RRF
JINA_API_KEY=""

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

