# ADR-008 — Artifact Creation and Preview Flow

- Status: Accepted
- Date: 2026-08-13
- Decision makers: Core Architecture Team
- Extends: ADR-004, ADR-006, ADR-007

## Context

AI Chat trong `EMAIL-AGENT-v1` cần khả năng sinh và lưu trữ file báo cáo/tài liệu từ một
chat turn. Người dùng cần xem nhanh nội dung ngay trong khung Chat (inline preview), và có
thể tải file về dạng `.docx`.

Tính năng được lưu trữ trực tiếp tại đĩa **local filesystem (`workspace/reports/`)** theo mô hình
workspace local của F-Cowork, tách biệt hoàn toàn khỏi Supabase Storage.

## Decision

### 1. LLM Intent Trigger & Intent Classification

- `ChatRoutingService` gửi prompt 5-tier (`intent/prompt.py`) lên LLM để phân loại ngữ nghĩa.
- Khi intent là `action_request`, `route` trả về `ChatRoute.TOOL` hoặc `ChatRoute.RAG_TOOL`,
  `reason_codes` chứa `["external_action_requested"]`.
- LLM tự nhận intent từ user prompt và phát sinh `artifact_refs` trong structured response.
  Không có explicit tool-calling hay nút "Export" phía UI. Không có background extraction hay
  implicit promotion.

### 2. Prompt Engineering & Native Structured Output (không dùng `instructor`)

- Tái sử dụng hạ tầng Native JSON Schema tại `src/cowork_agent/integrations/llm/chat_reply.py`;
  **không thêm dependency bên ngoài** như `instructor` hay `outlines`.
- System Instruction hướng dẫn LLM: nếu phát hiện intent tạo tài liệu/báo cáo, bắt buộc trả
  về khối `artifact_refs` chứa tên file gợi ý và nội dung Markdown.
- **Extended Response Schema (`_RESPONSE_SCHEMA`)**:

```json
{
  "type": "object",
  "required": ["assistant_text", "citation_ids", "artifact_refs", "task_proposal"],
  "properties": {
    "assistant_text": { "type": "string" },
    "citation_ids": { "type": "array", "items": { "type": "string" } },
    "artifact_refs": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["filename", "content"],
        "properties": {
          "filename": { "type": "string" },
          "content": { "type": "string" }
        }
      }
    },
    "task_proposal": { "type": ["object", "null"] }
  }
}
```

### 3. Internal Wire — `ChatController` → `LocalReportStorage` & Direct Error Handling

- **Không phải UI gọi `POST /api/v1/reports`**; endpoint `POST` chỉ dành cho external clients /
  test harnesses. Luồng tự động đi qua service nội bộ trong `ChatController`:
  1. `ChatController` detect `artifact_refs` list có ít nhất 1 item từ LLM reply chunk.
  2. Gọi `LocalReportStorage.upload_bytes()` cho từng item.
  3. `LocalReportStorage` ghi file vào `workspace/reports/{ref_id}` và trả về `ref_id` đã gắn suffix.
  4. `ChatController` emit SSE Stream Event `ChatEventType.ARTIFACT_REFS` chứa payload `artifact_refs`.
- **Strict Error Handling**:
  - Nếu `LocalReportStorage` gặp lỗi đĩa / ghi file:
    - **API Endpoint**: Ném HTTP `503 Service Unavailable` với `{ "detail": "storage unavailable" }`.
    - **SSE Stream**: Emit ngay lập tức event `ChatEventType.ERROR` với `code="storage_unavailable"`, `safe_message="Local report storage is unavailable. Artifact could not be saved."` và chấm dứt luồng stream (`return`).

### 4. Storage Backend — Local Filesystem (`workspace/reports/`)

- File artifact lưu trực tiếp vào thư mục local **`workspace/reports/`** qua `LocalReportStorage`.
- Không phụ thuộc vào Supabase Storage hay cloud storage.
- Backend đọc/ghi file Markdown `.md` tại thư mục này để phục vụ preview và export.

### 5. Metadata Strategy

- Metadata (`ref_id`, `filename`, `size_bytes`, `created_at`) được tổng hợp trực tiếp từ thuộc tính file hệ thống (`stat.st_size`, `stat.st_mtime`).
- `list_objects()` duyệt thư mục `workspace/reports/` trả về metadata đủ để render danh sách tại `ArtifactsView`.

### 6. Unique Filename & On-the-Fly DOCX Conversion

- Backend **bắt buộc** gắn suffix `{timestamp}_{hash_short}` tạo `ref_id` duy nhất trước khi
  lưu `.md`. Mẫu: `{base_stem}_{timestamp}_{hash_short}.md`.
- File path mẫu: `workspace/reports/{ref_id}`
- **Chuyển đổi DOCX**: Thực hiện **on-the-fly** tại thời điểm người dùng gọi
  `GET /api/v1/reports/{ref_id}/download`.
- Backend dùng **`python-docx`** để chuyển đổi cấu trúc Markdown (Title, Heading 1/2/3,
  Bullet points, Paragraphs) sang `.docx` nhị phân, trả về với
  `Content-Disposition: attachment; filename="{filename}.docx"`.

### 7. SSE Event Contract & Stream Specification

- Mở rộng `ChatEventType` enum thêm `ARTIFACT_REFS = "artifact_refs"`.
- Event payload cho `artifact_refs`:
  ```json
  {
    "event_id": "evt_123",
    "session_id": "sess_456",
    "turn_id": "turn_789",
    "event_type": "artifact_refs",
    "artifact_refs": [
      {
        "ref_id": "bao_cao_ke_hoach_1723545600_a1b2c3d4.md",
        "filename": "bao_cao_ke_hoach.md",
        "title": "Báo cáo Kế hoạch"
      }
    ]
  }
  ```
- `artifactRefs` chỉ được emit về client **sau khi** `LocalReportStorage` lưu file thành công.

### 8. REST API Contract (`/api/v1/reports`)

| Endpoint | Method | Input | Output | Ghi chú |
| --- | --- | --- | --- | --- |
| `/api/v1/reports` | `GET` | — | `ReportFile[]` | Lấy danh sách tất cả artifact trong `workspace/reports` |
| `/api/v1/reports` | `POST` | `{ filename, content }` | `ReportFile` | External clients / test harnesses |
| `/api/v1/reports/{ref_id}` | `GET` | `ref_id` (path) | `text/markdown` | Lấy nội dung preview Markdown |
| `/api/v1/reports/{ref_id}` | `DELETE` | `ref_id` (path) | `{ success: true }` | Xoá file khỏi `workspace/reports` |
| `/api/v1/reports/{ref_id}/download` | `GET` | `ref_id` (path) | Binary DOCX (`application/vnd.openxmlformats...`) | Convert `.md` → `.docx` on-the-fly qua `python-docx` |

### 9. UI & Navigation

- `ArtifactRefCard` render dưới bubble AI message khi stream nhận `artifact_refs`.
- `ArtifactsView` hiển thị danh sách file từ local `workspace/reports`, xem trước Markdown và tải file DOCX.

## Consequences

- Không sử dụng Supabase Storage cho tính năng báo cáo/artifact.
- File Markdown được lưu giữ tại `workspace/reports/` cục bộ.
