# QA Project Context: AI Chatbot User Intent Evaluation (Tiếng Việt)

Tài liệu ngữ cảnh kiểm thử chất lượng phân loại và phản hồi theo ý định người dùng (User Intent Alignment & Response Quality) gồm **240 kịch bản** (6 Loại Intent $\times$ 4 Paths $\times$ 10 câu/path). Không kiểm thử tầng hệ thống vật lý, bộ nhớ lưu trữ hay database.

---

## 1. Mục tiêu Kiểm thử Ý định Người dùng (User Intent Goals)
- **Mục tiêu cốt lõi:** Đảm bảo hệ thống AI Chatbot hiểu chính xác 100% ý định người dùng (User Intent) qua các dạng câu hỏi tiếng Việt thực tế và đưa ra câu trả lời phù hợp nhất.
- **Tiêu chuẩn đánh giá:**
  - Đúng ý định (Intent Accuracy): Phân loại đúng loại yêu cầu của người dùng.
  - Đúng phản hồi (Response Correctness): Trả lời đúng trọng tâm, trích dẫn đúng tài liệu khi cần, đề xuất kế hoạch đúng khi được giao việc.
  - Hỏi lại khi mơ hồ (Clarification Proactivity): Không đoán mò khi câu hỏi thiếu thông tin, chủ động hỏi làm rõ.
  - Xử lý rủi ro và an toàn (Safety & Boundary Compliance): Từ chối lịch sự với các yêu cầu độc hại hoặc ngoài phạm vi.

---

## 2. Danh mục 6 Loại Intent trong Hệ thống (240 Kịch bản)
1. **`ChatIntent.CHAT` (Hội thoại thông thường - 40 câu):** Chào hỏi, cảm ơn, hỏi đáp kiến thức tổng quát, giao tiếp tự nhiên.
2. **`ChatIntent.KNOWLEDGE_QUERY` (Tra cứu tri thức & Tài liệu RAG - 40 câu):** Hỏi về quy định, chính sách, hợp đồng, tài liệu nội bộ công ty.
3. **`ChatIntent.ACTION_REQUEST` (Giao việc & Lập kế hoạch hành động - 40 câu):** Chỉ thị tạo tác vụ, lập checklist công việc, xử lý sự cố.
4. **`report_generation` (Tổng hợp & Tạo báo cáo - 40 câu):** Yêu cầu tổng kết phiên làm việc, báo cáo tiến độ tuần/tháng, biên bản cuộc họp.
5. **`clarification` (Câu hỏi mơ hồ & Cần làm rõ - 40 câu):** Câu hỏi cụt lủn, từ ngữ tối nghĩa cần hỏi lại người dùng.
6. **`safety_guardrail` (An toàn & Phòng vệ - 40 câu):** Nhận diện và từ chối các câu lệnh jailbreak, prompt injection, đòi hỏi dữ liệu bí mật.

---

## 3. Khung 4 Paths Kiểm thử cho mỗi Intent (10 câu/path)
Mỗi loại Intent được kiểm thử qua 4 đường dẫn:
- **Path 1: Happy Path (10 câu):** Yêu cầu chuẩn, rõ ràng $\rightarrow$ Phản hồi đầy đủ, chính xác.
- **Path 2: Failure / Boundary Path (10 câu):** Yêu cầu vượt phạm vi, tài liệu không tồn tại $\rightarrow$ Phản hồi từ chối hoặc giải thích rõ ràng.
- **Path 3: Ambiguity Path (10 câu):** Câu hỏi mơ hồ, thiếu thông tin $\rightarrow$ Chủ động hỏi làm rõ.
- **Path 4: Confidence Loss / Edge Path (10 câu):** Câu hỏi đa nghĩa, lai giữa các intent, độ tin cậy thấp $\rightarrow$ Hạ cấp an toàn.

---

## 4. Tài nguyên & Dữ liệu Kiểm thử
- **File JSON Dataset chuẩn 240 kịch bản:** [`docs/qa-test/qa-chatbot-intent-dataset.json`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/qa-test/qa-chatbot-intent-dataset.json)
- **File Test Runner Python:** [`tests/unit/features/ai_chat/test_user_intent_vietnamese.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/tests/unit/features/ai_chat/test_user_intent_vietnamese.py)
- **Chiến lược QA:** [`docs/qa-test/qa-strategy.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/qa-test/qa-strategy.md)
- **Kế hoạch Sprint 1:** [`docs/qa-test/qa-plan-chatbot-sprint1.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/qa-test/qa-plan-chatbot-sprint1.md)
