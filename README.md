# Module Mail

Module Python biến email Gmail chưa đọc thành danh sách công việc có cấu trúc.

## Công nghệ nền

- Python 3.11+
- PostgreSQL
- Durable job queue

## Cấu trúc dự án

```text
.
├── src/
│   └── mail_todo/
│       ├── domain/          # Model và quy tắc nghiệp vụ thuần
│       ├── application/     # Use case, pipeline và port
│       ├── infrastructure/  # Gmail, DB, queue, LLM, attachment adapter
│       ├── api/             # HTTP handler và event handler
│       ├── gui/             # Streamlit testing interface
│       └── __init__.py      # Public API của package
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/            # Dữ liệu mẫu cho test
├── scripts/                 # Script tiện ích (launcher, tools)
├── migrations/              # SQL migration files
└── docs/
    ├── adr/
    ├── product_requirements.md
    └── technical_spec.md
```

## Nguyên tắc tổ chức

- Tổ chức theo module nghiệp vụ trước, không gom toàn bộ dự án theo loại file.
- `domain` không phụ thuộc framework, database, Gmail hoặc LLM.
- `application` điều phối nghiệp vụ và khai báo các port cần thiết.
- `infrastructure` hiện thực các port và chứa chi tiết kỹ thuật.
- `api` chỉ chuyển đổi request/event sang lời gọi application.
- Chỉ tạo thư mục con khi một nhóm đã có ít nhất vài file liên quan.
- Chưa tạo `shared/`; chỉ tách mã dùng chung sau khi có ít nhất hai nơi thật sự sử dụng.

## Bắt đầu

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
python -m pytest
```

## MVP hiện có

Repository hiện cung cấp Milestone 1 có thể chạy hoàn toàn bằng fixture, không cần
tài khoản Gmail hoặc lời gọi AI thật:

- Domain model và deterministic policy cho query, priority, fingerprint/dedupe.
- Use case tạo run có idempotency, worker claim `queued -> running` và result service.
- Port cho mailbox, attachment extractor, action extractor, queue, repository và outbox.
- Adapter in-memory, fake mailbox/action extractor và text attachment extractor giới hạn.
- HTTP handler không phụ thuộc framework, bám theo response contract trong `technical_spec.md`.
- PostgreSQL migration và rollback cho các bảng chính, constraint và index.

Các adapter fake nằm trong `mail_todo.infrastructure` để test pipeline mà không đưa
OAuth token, email body hay attachment vào queue/database. Gmail OAuth, LLM thật và
sandbox parser cho PDF/Office/OCR là các integration tiếp theo theo Milestone 2–3
trong Technical Spec; không nên dùng text adapter hiện tại thay cho sandbox production.

### Gemini với API-key rotation

Điền `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2` và `GEMINI_API_KEY_3` trong `.env`, sau đó
khởi tạo adapter từ environment:

```python
from mail_todo.infrastructure import GeminiActionExtractor

action_extractor = GeminiActionExtractor.from_env()
```

Mỗi request bắt đầu bằng key kế tiếp theo round-robin. Khi API trả `429`, adapter thử
key tiếp theo, tối đa `GEMINI_MAX_ATTEMPTS_PER_REQUEST`. Nội dung email và attachment
được đặt trong data delimiter, Gemini chỉ nhận structured-output schema và không có tool.

### Kết nối Gmail thật

1. Trong Google Cloud Console, bật Gmail API, cấu hình OAuth consent screen và tạo
   OAuth Client ID loại **Web application**.
2. Đăng ký Authorized redirect URI chính xác:

   ```text
   http://localhost:8000/v1/mail-todo/oauth/gmail/callback
   ```

3. Copy `.env.example` thành `.env`, điền `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`,
   ba Gemini key và tạo hai secret local:

   ```powershell
   .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Dùng kết quả đầu cho `TOKEN_ENCRYPTION_KEY`, kết quả sau cho `OAUTH_STATE_SECRET`.
   `OAUTHLIB_INSECURE_TRANSPORT=1` chỉ dùng cho callback HTTP trên localhost; xóa biến
   này khi deploy HTTPS production.

4. Chạy API:

   ```powershell
   .\.venv\Scripts\mail-todo-api.exe
   ```

5. Mở URL sau trong trình duyệt và chấp thuận quyền Gmail read-only:

   ```text
   http://localhost:8000/v1/mail-todo/oauth/gmail/connect?user_id=local-user
   ```

Callback trả về `connection.id`. Có thể kiểm tra email chưa đọc thật bằng:

```text
http://localhost:8000/v1/mail-todo/connections/{connection.id}/unread-preview?user_id=local-user
```

Tạo một digest thật bằng Gmail + Gemini:

```powershell
$payload = @{
  mailboxConnectionId = "mbx_..."
  query = "is:unread in:inbox"
  maxEmails = 50
} | ConvertTo-Json

$run = Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/v1/mail-todo/runs?user_id=local-user" `
  -Headers @{ "Idempotency-Key" = [guid]::NewGuid().ToString() } `
  -ContentType "application/json" `
  -Body $payload

$run
```

Dùng `statusUrl` trong response để theo dõi, sau đó gọi
`/v1/mail-todo/runs/{runId}/result?user_id=local-user` khi trạng thái đã hoàn tất.

Refresh token được mã hóa trước khi lưu ở `.data/mail_todo.db`. API không yêu cầu
scope gửi, sửa, đánh dấu đã đọc hoặc xóa email. Tham số `user_id` hiện dành cho local
development; production phải lấy user ID từ session/JWT đã xác thực, không tin query string.
Run/queue/result của server local hiện nằm trong memory và mất khi restart; Gmail connection
được lưu bền vững. Production vẫn cần thay bằng PostgreSQL và durable queue theo ADR-001.

## Streamlit Testing Interface

Dự án cung cấp giao diện trực quan với Streamlit để kiểm thử toàn bộ các tính năng (OAuth, Unread Preview, Digest Runs, Gemini API Key Rotation, và Offline Sandbox Fixture):

```bash
# Cài đặt dependency GUI (nếu chưa cài)
python -m pip install -e ".[gui]"

# Chạy giao diện Streamlit
python scripts/run_gui.py
```

Giao diện sẽ mở tại `http://localhost:8501`.

## Kiểm tra chất lượng

```bash
python -m ruff check .
python -m mypy src
python -m pytest -q
python -m pip wheel . --no-deps --wheel-dir .build
```

Chi tiết sản phẩm và kiến trúc nằm trong [Product Requirements](docs/product_requirements.md), [Technical Spec](docs/technical_spec.md) và các ADR trong [docs/adr](docs/adr).
