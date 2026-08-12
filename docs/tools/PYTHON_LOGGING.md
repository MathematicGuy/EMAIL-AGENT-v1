# Python Standard Logging Specification

| Field | Value |
|---|---|
| Tool Name | Python Standard Logging (`logging`) |
| Category | Infrastructure Logging, Server Error Diagnostics & Local File Audit |
| Target Layer | `src/cowork_agent/app.py`, `src/cowork_agent/orchestration/`, `src/cowork_agent/integrations/` |
| Specification Status | Approved Project Standard |

---

## 1. Purpose & Scope

### Primary Utility
Python Standard `logging` chịu trách nhiệm ghi nhận các sự kiện hạ tầng server mà **Langfuse KHÔNG THỂ bắt được**, bao gồm:
- **Server Boot & Lifespan:** Lỗi khởi động FastAPI (`app.py`), lỗi bind port `8000`, lỗi nạp biến môi trường `.env`.
- **Infrastructure Crashes:** Lỗi sập kết nối SQLite DB (`runs.db`), lỗi ngắt kết nối Redis Queue (`redis_queue.py`), lỗi crash tiến trình Worker ngầm (`worker.py`).
- **Transport & Network Retries:** Lỗi HTTP transport retries từ Google Gmail API (`provider.py`) hoặc Qdrant connection failure (`bootstrap.py`).
- **Uncaught Exception Stacktraces:** In đầy đủ `traceback` dòng mã nguồn bị crash khi có lỗi nghiêm trọng (`logger.exception`).
- **Local File Audit Logs:** Lưu toàn bộ vết log server local ra đĩa đĩa cứng tại `.data/app.log` và `.data/worker.log`.
- **Terminal Stdout/Stderr:** In dòng log trực tiếp lên màn hình Console/Terminal nơi backend đang thực thi.

### In-Scope (Bắt buộc dùng `logging`)
- **System Entry Points:** `app.py` và `worker.py` (khởi tạo `logging.basicConfig` với `StreamHandler` + `FileHandler`).
- **Background Worker & Redis Queue:** `src/cowork_agent/orchestration/` (log cảnh báo worker crash, queue recovery).
- **Transport Adapters:** `src/cowork_agent/integrations/gmail/provider.py` (log warning khi OAuth token refresh thất bại hoặc HTTP 5xx retry).

### Out-of-Scope (Cấm dùng `logging` thủ công)
- **AI Telemetry & Spans:** Không dùng `logger.info` rải rác để ghi vết từng bước AI agent (dùng Langfuse `@observe` cho việc này).
- **Domain Models:** Không dùng `logging` trong `src/cowork_agent/domain/`.

---

## 2. Architecture Boundary & Dependency Rules

- **Permitted Imports:**
  - `src/cowork_agent/app.py`
  - `src/cowork_agent/orchestration/`
  - `src/cowork_agent/integrations/`
- **Forbidden Imports:**
  - `src/cowork_agent/domain/`

---

## 3. Environment Variables (.env)

| Variable Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `APP_LOG_LEVEL` | String | No | `"INFO"` | Mức độ hiển thị log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## 4. Standard Implementation Patterns (Golden Snippets)

### Pattern 1: Basic Config with FileHandler & StreamHandler (`app.py` / `worker.py`)
```python
import logging
from pathlib import Path

def setup_logging(log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
```

### Pattern 2: Transport Retry & Network Warning Logging (`provider.py`)
```python
import logging

logger = logging.getLogger(__name__)

try:
    response = await self._call_gmail_api()
except HttpError as exc:
    logger.warning("Gmail API HttpError (status=%s, attempt=%d/3): %s", exc.resp.status, attempt, exc)
```

### Pattern 3: Uncaught Crash Exception Logging (`workflow.py` / `worker.py`)
```python
import logging

logger = logging.getLogger(__name__)

try:
    await run_worker_loop()
except Exception as exc:
    logger.exception("❌ [RUN %s] Worker process crashed unexpectedly: %s", run_id, exc)
```

---

## 5. Failure Handling & Fallback Policy

- **Directory Creation:** `FileHandler` tự động tạo thư mục `.data/` nếu chưa tồn tại.
- **Fail-Safe Logging:** Lỗi khi ghi log ra file không được làm crash luồng chính của ứng dụng (`StreamHandler` vẫn hoạt động).

---

## 6. Verification Commands

```powershell
# Chạy bộ test kiểm tra logging setup
.\.venv\Scripts\python.exe -m pytest tests/unit/test_app.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit/orchestration -q
```

---

## 7. Anti-Patterns & Privacy Rules

- ❌ **CẤM ghi log rải rác thay Langfuse:** Không lạm dụng `logger.info("Step 1 done")`, `logger.info("Step 2 done")` rải rác trong luồng AI execution (đó là công việc của Langfuse `@observe`).
- ❌ **CẤM in PII & Secret Keys:** Không log mã `TOKEN_ENCRYPTION_KEY`, `OAUTH_STATE_SECRET`, `GEMINI_API_KEY` hoặc mật khẩu người dùng ra Terminal hay File log.
- ❌ **CẤM nuốt ngoại lệ im lặng:** Không dùng `try...except: pass` mà không gọi `logger.warning` hoặc `logger.exception`.
