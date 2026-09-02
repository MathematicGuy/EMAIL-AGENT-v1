# ADR-011 — Tổng hợp ngữ cảnh chuỗi email phản hồi (Reply Chain Context Aggregation)

- Trạng thái: Accepted
- Ngày: 2026-08-18 (Cập nhật: 2026-08-19)
- Người quyết định: Product / Engineering Team
- Liên quan: [`ADR-001`](ADR-001-two-workflow-split.md), [`ADR-003`](ADR-003-defer-attachment-processing.md), [`ADR-010`](ADR-010-local-postgres-control-plane-latency.md)

---

## 1. Bối cảnh (Context)

Khi người dùng nhận email phản hồi (reply), nội dung thường là thông tin bổ sung, tài liệu yêu cầu thêm, hoặc deadline mới tiếp nối các email trước đó trong thread.

Cần một cơ chế tổng hợp chuỗi thống nhất:
1. Chỉ quét thread khi **email mới nhất của thread là email chưa đọc (`UNREAD`)**.
2. Lấy trọn vẹn chuỗi tối đa **5 email gần nhất** (cả `READ` tiền nhiệm và `UNREAD` mới).
3. **Quy tắc kích hoạt Action Plan (Single-action activation)**: Chỉ cần có **ít nhất 1 email trong chuỗi 5 email đó được phân loại là cần hành động (`action_required` hoặc `action_suggested`)**, toàn bộ chuỗi sẽ được coi là cần hành động, và toàn bộ nội dung của các email trong chuỗi sẽ được nạp làm ngữ cảnh tổng hợp cho LLM Generator.
4. **Không nối chuỗi text thô (No naive string concatenation)**: Tránh việc ghép text phẳng làm mất metadata người gửi, mốc thời gian và luồng thảo luận. Phải bảo toàn cấu trúc dữ liệu theo từng email.
5. **Múi giờ mặc định**: Mọi mốc thời gian nhắc tới trong email phải mặc định theo giờ Việt Nam (ICT / UTC+7, format ISO-8601 offset `+07:00`).

---

## 2. Quyết định (Decision)

### 2.1. Điều kiện kích hoạt quét (Latest Email must be UNREAD)
- Sắp xếp toàn bộ email trong thread theo thời gian tăng dần (`received_at`: cũ -> mới).
- Kiểm tra email mới nhất (`latest_message = sorted_thread[-1]`).
- Chỉ quét thread khi `latest_message` là `UNREAD`. Nếu email mới nhất đã đọc, bỏ qua thread đó.

### 2.2. Lấy tối đa 5 email liên kết gần nhất (Bounded Reply Chain)
- Khi thỏa mãn điều kiện, lấy trọn bộ chuỗi tối đa **5 email gần nhất** trong thread (`sorted_thread[-5:]`), bảo toàn nguyên vẹn chuỗi không bị cắt vụn qua giới hạn batch.

### 2.3. Cấu trúc đóng gói dữ liệu chuỗi Email (Structured Envelope Payload)
Dữ liệu của toàn bộ các email trong chuỗi được đóng gói độc lập dạng mảng JSON trong thẻ `<untrusted_data>` (`_build_prompt` trong `src/cowork_agent/integrations/llm/providers/gemini.py`):
```json
{
  "userTimezone": "Asia/Ho_Chi_Minh",
  "currentTime": "2026-08-19T00:00:00+07:00",
  "threads": [
    {
      "messages": [
        {
          "providerMessageId": "msg_001",
          "threadId": "thread_001",
          "subject": "Triển khai hệ thống",
          "senderName": "Nguyen Van A",
          "sender": "a@company.com",
          "sentAt": "2026-08-18T09:00:00+07:00",
          "body": "Nội dung email gốc...",
          "attachmentsPresent": false
        },
        {
          "providerMessageId": "msg_002",
          "threadId": "thread_001",
          "subject": "Re: Triển khai hệ thống",
          "senderName": "Tran Thi B",
          "sender": "b@company.com",
          "sentAt": "2026-08-18T14:00:00+07:00",
          "body": "Nội dung phản hồi cập nhật...",
          "attachmentsPresent": true
        }
      ]
    }
  ]
}
```
- **Chuẩn hóa nội dung (`normalized_body`)**: Sử dụng `_extract_text` trong `src/cowork_agent/integrations/gmail/provider.py` lọc sạch HTML nhưng bảo toàn link URL.
- **Tối ưu 2 pha**:
  - *Pha phân loại (Classification)*: Cắt trích đoạn `body[:1200]` để tiết kiệm token và xác định nhanh route.
  - *Pha sinh Action Plan (Generation)*: Nạp 100% `normalized_body` và đầy đủ metadata của chuỗi 5 email.

### 2.4. Phối hợp 4 khối ngữ cảnh để sinh Action Plan
LLM Generator tổng hợp thông tin từ 4 khối ngữ cảnh (`_build_generation_prompt` trong `src/cowork_agent/integrations/llm/providers/gemini.py`):
1. `<untrusted_data>`: Chuỗi email và mốc thời gian kèm múi giờ.
2. `<route_context>`: Quyết định định tuyến (`DIRECT_PLAN` hoặc `RETRIEVE_RAG`) và các gap kiến thức.
3. `<retrieved_context>`: Các đoạn tài liệu trích xuất từ RAG công ty (kèm `citationId`) khi route là `RETRIEVE_RAG`.
4. Ràng buộc Schema (`GENERATION_SCHEMA`) & System Instruction: Chống hallucination, format deadline ISO-8601 theo giờ VN (`+07:00`).

### 2.5. Nguyên tắc kích hoạt Action Plan toàn chuỗi (Single-Action Precedence)
- Qua hàm `resolve_candidate_route` trong `src/cowork_agent/features/email_action_plan/routing.py`, Route có độ ưu tiên cao nhất trong chuỗi sẽ thắng (`RETRIEVE_RAG` > `DIRECT_PLAN` > `NO_ACTION`).
- Chỉ cần 1 email trong chuỗi có `action_required` hoặc `action_suggested`, toàn bộ chuỗi được kích hoạt để sinh Action Plan.
- LLM tổng hợp ngữ nghĩa (synthesize) toàn bộ diễn tiến từ đầu đến cuối chuỗi để sinh **1 Action Plan duy nhất** đại diện cho thread.

### 2.6. Hạch toán số lượng quét (Accounting)
- Tất cả các email trong chuỗi 5 email (cả `read` và `unread`) đều được lưu vào `ProcessedEmail` và tính cộng dồn vào `run.emails_processed`.
- Trường `task.source_message_ids` lưu đầy đủ ID của tất cả các email đã đóng góp vào Action Plan.

---

## 3. Lý do & Hiệu quả (Rationale)

- **Bảo toàn ngữ cảnh thời gian thực**: LLM nhìn thấy chính xác ai phản hồi gì, vào lúc nào (`sentAt`), deadline mới nhất điều chỉnh ra sao thay vì đọc một đoạn văn bản bị nối dính liền vô nghĩa.
- **Chuẩn hóa múi giờ Việt Nam**: Đảm bảo deadline sinh ra luôn có offset `+07:00`, không bị nhầm lẫn múi giờ UTC/Z.
- **Không bỏ sót yêu cầu**: Cho dù email yêu cầu gốc là `READ` hoặc một email trả lời ngắn ở giữa là `informational`, chỉ cần luồng có hành động, toàn bộ ngữ cảnh vẫn được tổng hợp đầy đủ.
- **Dữ liệu minh bạch**: Giao diện hiển thị chính xác "Mở email nguồn (N)" tương ứng với toàn bộ các email trong chuỗi đã phân tích.

---

## 4. Liên kết liên quan

- [`src/cowork_agent/features/email_action_plan/workflow.py`](../../src/cowork_agent/features/email_action_plan/workflow.py)
- [`src/cowork_agent/features/email_action_plan/shaping.py`](../../src/cowork_agent/features/email_action_plan/shaping.py)
- [`src/cowork_agent/features/email_action_plan/routing.py`](../../src/cowork_agent/features/email_action_plan/routing.py)
- [`src/cowork_agent/integrations/gmail/provider.py`](../../src/cowork_agent/integrations/gmail/provider.py)
- [`src/cowork_agent/integrations/llm/providers/gemini.py`](../../src/cowork_agent/integrations/llm/providers/gemini.py)
- [`tests/unit/features/email_action_plan/test_reply_chain_aggregation.py`](../../tests/unit/features/email_action_plan/test_reply_chain_aggregation.py)
