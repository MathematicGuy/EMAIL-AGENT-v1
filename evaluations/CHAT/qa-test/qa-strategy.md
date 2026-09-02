# Chiến lược Kiểm thử Chất lượng Ý định Người dùng (User Intent QA Strategy)

Chiến lược kiểm thử toàn diện tập trung vào việc đánh giá độ chính xác của phân loại ý định người dùng (User Intent), khả năng phòng vệ trước tấn công, xử lý ngữ cảnh đa lượt và chất lượng câu trả lời thực tế của AI Chatbot tiếng Việt.

---

## 1. Khung Kiểm Thử 5 Trục (5-Axis Testing Framework)

Hệ thống triển khai 5 trục kiểm thử bao phủ toàn diện:

| Trục Kiểm thử | Số kịch bản | Phạm vi kiểm tra | Phương thức thực thi |
|---|:---:|---|---|
| **1. Single-turn Matrix** | 240 | 6 Intent $\times$ 4 Paths $\times$ 10 câu đơn lẻ | JSON Dataset + Live Gemini Runner |
| **2. Multi-turn Context** | 3 | Tham chiếu đại từ, đổi ý định giữa chừng, vòng lặp làm rõ | Multi-turn Controller Runner |
| **3. Adversarial / Injection** | 3 | Prompt injection, System override, SQL injection, Schema escaping | Bounded Prompt & Safety Verification |
| **4. Edge Cases & Boundary** | 6 | Emoji, không dấu, code-switch, code snippet, max length 4000 ký tự | Boundary Value Analysis |
| **5. Domain Input Validation** | 15 | Chuỗi rỗng, khoảng trắng, vượt quá 4000 ký tự | Domain Contract Layer Validation |
| **Tổng số kịch bản** | **267** | **Toàn bộ hệ sinh thái kiểm thử Chatbot** | **Unit (~5.6s) & Live API Runner** |

---

## 2. Ma trận Phân Loại Ý Định (6 Intents $\times$ 4 Paths)

1. **6 Nhóm Intent:**
   - `chat`: Hội thoại, chào hỏi, chia sẻ thông thường.
   - `knowledge_query`: Tra cứu tài liệu, quy định nội bộ.
   - `action_request`: Yêu cầu thực hiện tác vụ, tạo kế hoạch hành động.
   - `report_generation`: Yêu cầu tổng kết tiến độ, tóm tắt phiên làm việc.
   - `clarification`: Câu hỏi mơ hồ cần làm rõ ngữ cảnh.
   - `safety_guardrail`: Ngăn chặn hành vi khai thác, tấn công hoặc yêu cầu cấm.

2. **4 Đường dẫn Kịch bản (Paths):**
   - `happy_path`: Câu hỏi rõ ràng $\rightarrow$ Xử lý chính xác.
   - `failure_path`: Yêu cầu ngoài phạm vi / không có dữ liệu $\rightarrow$ Từ chối an toàn.
   - `ambiguity_path`: Câu hỏi thiếu đối tượng $\rightarrow$ Hỏi lại làm rõ (`CLARIFY`).
   - `confidence_loss_path`: Độ tin cậy thấp $\rightarrow$ Hạ cấp về Chat an toàn.

---

## 3. Tiêu Chuẩn Nghiệm Thu & Kết Quả Thực Tế (Baseline 2026-08-14)

### Kết quả trên Google Gemini Thật 100% (Không Mock):
- **Tỷ lệ Pass toàn diện:** **80.52%** (215 / 267 kịch bản).
- **Phân loại lỗi chính cần theo dõi:**
  - 38 câu mơ hồ (`ambiguity_path`) Gemini trả lời ngay thay vì hỏi lại để làm rõ.
  - 10 câu yêu cầu hành động vật lý Gemini phân loại `action_request` thay vì `chat` từ chối.
  - 4 câu kiến thức ngoài lề phân loại `knowledge_query`.

Toàn bộ tài liệu chi tiết:
- Hướng dẫn tùy chỉnh: [`HOW-TO-CREATE-QA-TEST.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/evaluations/CHAT/qa-test/HOW-TO-CREATE-QA-TEST.md)
- Báo cáo kết quả chi tiết: [`QA-TEST-RESULTS-REPORT.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/evaluations/CHAT/qa-test/QA-TEST-RESULTS-REPORT.md)
