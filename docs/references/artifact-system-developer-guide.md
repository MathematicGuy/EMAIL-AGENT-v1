# Hướng Dẫn Developer: Hệ Thống Tạo & Xem Báo Cáo (Artifact System)

Tài liệu này dành cho Developer để nhanh chóng nắm bắt cách hoạt động, luồng code, API contract và cách bảo trì tính năng tạo/xem trước báo cáo (Artifact System) trong dự án.

---

## 1. Tóm tắt nhanh (TL;DR)

Khi người dùng gửi tin nhắn yêu cầu *"Tạo báo cáo..."* hoặc *"Xuất tài liệu..."*:
1. LLM nhận diện và tự động sinh mảng `artifact_refs` chứa tên file `.md` và nội dung Markdown.
2. Backend (`ChatController`) tự động lưu file vào thư mục local (`workspace/reports/{ref_id}.md`).
3. Backend bắn sự kiện SSE `artifact_refs` về client.
4. Frontend hiển thị thẻ `ArtifactRefCard`. Người dùng bấm vào thẻ để fetch nội dung hiển thị preview trực tiếp hoặc tải xuống file `.docx`.

---

## 2. Sơ đồ Luồng Dữ liệu (Data Flow)

```text
[User Prompt] 
    │
    ▼
[ChatRoutingService] ──► Phân loại intent: action_request
    │
    ▼
[LLM Adapter (chat_reply.py)] ──► Sinh JSON tuân thủ _RESPONSE_SCHEMA
    │                             (assistant_text + artifact_refs)
    ▼
[ChatController (controller.py)]
    │
    ├── 1. Upload bytes lên LocalReportStorage (file_path: workspace/reports/{ref_id})
    ├── 2. Nếu lỗi Storage ──► Bắn SSE ERROR (code: "storage_unavailable") & STOP
    └── 3. Nếu thành công ──► Bắn SSE Event: "artifact_refs"
    │
    ▼
[Frontend ChatStreamView.tsx]
    │
    ├── 1. Nhận SSE event "artifact_refs", hiển thị thẻ ArtifactRefCard
    └── 2. Click "Xem trước" ──► Gọi API GET /api/v1/reports/{ref_id}
```

---

## 3. Chi tiết Mã nguồn & Contract

### 3.1. Stream Event Contract (`domain/_chat_contracts_chat.py`)
Sự kiện SSE được phát về client với cấu trúc:

- **`event_type`**: `"artifact_refs"` (`ChatEventType.ARTIFACT_REFS`)
- **`artifact_refs`**: Mảng các object chứa:
  - `ref_id`: Mã duy nhất dạng `{stem}_{timestamp}_{hash}.md`
  - `filename`: Tên file gốc (ví dụ: `bao_cao_tuan.md`)
  - `title`: Tiêu đề hiển thị (ví dụ: `bao_cao_tuan`)

### 3.2. Local Storage Backend (`api/reports.py` - `LocalReportStorage`)
Lưu file Markdown trực tiếp tại thư mục local `workspace/reports/`:
- **Path format**: `workspace/reports/{ref_id}`
- **On-the-fly DOCX Conversion**: Khi người dùng tải file Word, API convert file `.md` tại `workspace/reports/` thành binary `.docx` qua `python-docx`.

---

## 4. Kiểm thử & Debugging

### Lệnh chạy test liên quan:
```bash
.venv\Scripts\python -m pytest -q tests/unit/features/ai_chat/test_controller.py tests/unit/api/test_reports_api.py
```

### Lệnh kiểm tra Type & Lint:
```bash
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m ruff check src
```
