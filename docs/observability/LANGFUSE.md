# Langfuse Telemetry & Tracing Specification

| Field | Value |
|---|---|
| Component Name | Langfuse Python Provider (`langfuse`) |
| Telemetry Domain | AI Execution Tracing, Span Hierarchy, Token & LLM Cost Tracking |
| Target Layer | `src/cowork_agent/features/`, `src/cowork_agent/integrations/`, `src/cowork_agent/api/` |
| Specification Status | Approved Project Standard |

---

## 1. Purpose & Scope

### Primary Utility
Langfuse chịu trách nhiệm thu thập dữ liệu giám sát tự động (automated telemetry) về toàn bộ vòng đời thực thi của AI Agent bao gồm:
- **Trace & Spans Hierarchy:** Cây liên vết và độ trễ thực thi (execution latency) của các bước xử lý.
- **Payload Monitoring:** Dữ liệu Input / Output của từng thành phần (Classifier, Generator, Retriever).
- **Token & Cost Telemetry:** Đếm chính xác số lượng Tokens (Prompt / Completion) và tính toán chi phí gọi LLM API.

### In-Scope (Bắt buộc dùng Langfuse Tracing)
- **LLM Providers:** Tất cả các provider Gemini (`gemini.py`), Vyce (`vyce.py`), Mistral (`mistral.py`), OpenRouter (`openrouter.py`).
- **Workflow Controllers:** Các luồng xử lý chính `execute()` trong `workflow.py` và `stream_message()` trong `controller.py`.
- **RAG Retrievers:** Các hàm truy vấn tri thức `retrieve()` trong `qdrant.py` và `chat_memory.py`.
- **API Routers:** Các endpoint API chính trong `api/chat.py`.

### Out-of-Scope (Cấm dùng Langfuse Tracing)
- **Domain Layer:** Không được import hay gọi `@observe` trong `src/cowork_agent/domain/` (giữ domain pure python, không phụ thuộc framework/telemetry).
- **Local Infrastructure Error Logging:** Không dùng `@observe` thay thế cho `python logging` khi bắt lỗi crash hạ tầng server (boot failure, socket timeout).

---

## 2. Architecture Boundary & Dependency Rules

- **Permitted Imports:**
  - `src/cowork_agent/features/`
  - `src/cowork_agent/integrations/`
  - `src/cowork_agent/api/`
- **Forbidden Imports:**
  - `src/cowork_agent/domain/`

---

## 3. Environment Variables (.env)

| Variable Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | Secret String | Yes (for cloud export) | `""` | Public key lấy từ Langfuse Dashboard |
| `LANGFUSE_SECRET_KEY` | Secret String | Yes (for cloud export) | `""` | Secret key lấy từ Langfuse Dashboard |
| `LANGFUSE_HOST` | URL String | No | `"https://cloud.langfuse.com"` | Địa chỉ server Langfuse Cloud hoặc Local |
| `LANGFUSE_DEBUG` | Boolean | No | `false` | In trực tiếp log Trace/Span ra Terminal Backend |

---

## 4. Standard Implementation Patterns (Golden Snippets)

### Pattern 1: Decorate Workflow / Controller Step (General Span)
```python
from langfuse import observe

@observe(name="execute_digest_run")
async def execute(self, run_id: str) -> RunExecutionResult:
    # Tự động ghi vết span, latency và status
    pass
```

### Pattern 2: Decorate LLM Generation Step
```python
from langfuse import observe

@observe(as_type="generation", name="gemini_route_classifier")
async def classify(self, messages: Sequence[EphemeralEmailEnvelope]) -> ClassificationResult:
    # Tự động ghi vết LLM Generation, prompt/completion payload
    pass
```

### Pattern 3: Decorate RAG Retriever Step
```python
from langfuse import observe

@observe(as_type="retriever", name="qdrant_semantic_retriever")
async def retrieve(self, request: SemanticRetrievalRequest) -> SemanticRetrievalResponse:
    # Tự động ghi vết Retrieval query & document chunks
    pass
```

---

## 5. Failure Handling & Fallback Policy

- **Non-blocking Telemetry:** Langfuse SDK xử lý gửi telemetry data hoàn toàn bất đồng bộ ở background thread. Lỗi kết nối mạng hoặc sai API Key (`401 Unauthorized`) KHÔNG ĐƯỢC LÀM CRASH luồng chính của ứng dụng.
- **Mock/Silent Mode:** Khi thiếu `LANGFUSE_PUBLIC_KEY` hoặc `LANGFUSE_SECRET_KEY`, SDK tự động fallback về chế độ im lặng (silent/mock mode).

---

## 6. Verification Commands

Chạy bộ test tự động để đảm bảo decorator không phá vỡ contract:

```powershell
# Run unit tests
.\.venv\Scripts\python.exe -m pytest tests/unit -q

# Run ruff lint check
.\.venv\Scripts\python.exe -m ruff check src
```

---

## 7. Anti-Patterns & Privacy Rules

- ❌ **CẤM ghi PII & Passwords:** Không ghi thông tin mật khẩu, Fernet Encryption Keys hoặc Google OAuth Client Secrets vào Langfuse metadata.
- ❌ **CẤM import ở Domain Layer:** Không import `from langfuse import observe` trong bất kỳ file nào thuộc `src/cowork_agent/domain/`.
- ❌ **CẤM xóa Python `logging` hạ tầng:** Giữ nguyên `logger.warning` / `logger.error` để debug server crash và ghi file `.data/app.log`.
