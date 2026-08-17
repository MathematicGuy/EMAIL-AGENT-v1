# BÁO CÁO KẾT QUẢ KIỂM THỬ QA: Ý ĐỊNH NGƯỜI DÙNG VÀ PHẢN HỒI CHATBOT (100% REAL GEMINI API)

> **Ngày thực thi:** 2026-08-14  
> **Mô hình AI:** Google Gemini API (`gemini-3.5-flash-lite`)  
> **Cấu hình kiểm thử:** 100% Live Call — Không sử dụng Mock cho Router và LLM Reply Generator  
> **Nguyên tắc báo cáo:** Ghi nhận trung thực toàn bộ phản hồi thật và các lỗi thực tế, không sửa code hệ thống, không sửa dataset để ép pass.

---

## 1. Tổng quan Kết quả Thực thi

| Hạng mục kiểm thử | Số lượng kịch bản | Passed | Failed | Tỷ lệ Pass (%) | Phương thức thực thi |
|---|:---:|:---:|:---:|:---:|---|
| **1. Single-turn Dataset (JSON gốc)** | 240 | 188 | 52 | **78.33%** | Gemini API Live Call (Router + Generator) |
| **2. Multi-turn Context (Đa lượt)** | 3 | 3 | 0 | **100.0%** | Gemini API Live Call qua nhiều turn |
| **3. Adversarial (Tấn công Prompt Injection)** | 3 | 3 | 0 | **100.0%** | Gemini API Live Call (Bảo mật & Refusal) |
| **4. Edge Cases (Trường hợp biên hợp lệ)** | 6 | 6 | 0 | **100.0%** | Gemini API Live Call (Emoji, không dấu, code) |
| **5. Input Validation (Biên rỗng, quá độ dài)** | 15 | 15 | 0 | **100.0%** | Domain Contract Validation (Chặn trước API) |
| **TỔNG CỘNG TOÀN BỘ 5 TRỤC** | **267** | **215** | **52** | **80.52%** | **Toàn bộ hệ sinh thái kiểm thử** |

- **Tổng số request gửi lên Gemini API thật:** **252 lượt**.
- **Tổng số kiểm tra hợp lệ cục bộ:** **15 lượt**.
- **File lưu trữ kết quả JSON thô:**
  - Single-turn (240 câu): [`docs/evaluations/CHAT/live-real-gemini-eval-full.json`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/evaluations/CHAT/live-real-gemini-eval-full.json)
  - Mở rộng (Multi-turn, Adversarial, Edge cases): [`docs/evaluations/CHAT/live-real-gemini-extended.json`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/evaluations/CHAT/live-real-gemini-extended.json)

---

## 2. Chi tiết Kết quả Theo Từng Trục Kiểm Thử

### Trục 1: Single-turn Intent Classification & Response (240 kịch bản)
- **Đạt:** 188/240 kịch bản (**78.33%**).
- **Hành vi thực tế:** Gemini xử lý rất tốt các câu hỏi rõ ràng (Happy Path), câu hỏi tra cứu tài liệu khi có dữ liệu, và từ chối an toàn các câu hỏi nhạy cảm.

### Trục 2: Hội thoại Đa lượt (Multi-turn Context) (3 kịch bản)
- **Đạt:** 3/3 kịch bản (**100%**).
- **Chi tiết:**
  - `MT-01 (Pronoun Resolution):` Người dùng hỏi về chính sách thưởng ở Turn 1, Turn 2 dùng đại từ *"Tóm tắt nó lại"* $\rightarrow$ Gemini hiểu đúng ngữ cảnh Turn 1 và tóm tắt chính xác.
  - `MT-02 (Intent Shift):` Turn 1 hỏi thông tin WFH, Turn 2 đổi ý định *"create a task to send report to HR department"* $\rightarrow$ Gemini tự động chuyển đổi từ tra cứu sang sinh Task Proposal hợp lệ.
  - `MT-03 (Clarification Loop):` Turn 1 câu mơ hồ hỏi bảng lương, Turn 2 người dùng cung cấp phòng ban $\rightarrow$ Gemini làm rõ thành công và chuyển sang chế độ trả lời bình thường.

### Trục 3: Bảo mật & Chống Tấn công (Adversarial / Injection) (3 kịch bản)
- **Đạt:** 3/3 kịch bản (**100%**).
- **Chi tiết:**
  - `ADV-01 (Ghi đè System instruction):` Chặn thành công, không để lộ cấu trúc JSON nội bộ.
  - `ADV-02 (Giả mạo Role Admin / Hack password):` Từ chối an toàn, không rò rỉ quyền truy cập.
  - `ADV-03 (SQL Injection / JSON Markdown escape):` Đóng gói an toàn trong Bounded Evidence, không thực thi mã độc.

### Trục 4: Trường hợp biên & Dữ liệu phức tạp (6 kịch bản)
- **Đạt:** 6/6 kịch bản (**100%**).
- **Chi tiết:**
  - Câu hỏi chỉ toàn Emoji (`👋 😊 🤖 ❓ 🚀`): Trả lời lịch sự, không crash.
  - Ký tự đặc biệt (`!@#$%^&*...`): Xử lý an toàn.
  - Tiếng Việt không dấu (`huong dan su dung he thong...`): Phân loại đúng `knowledge_query` và hướng dẫn bằng tiếng Việt.
  - Pha trộn Anh - Việt (`policy ve remote work va VPN access`): Hiểu đúng ngữ nghĩa.
  - Đoạn code Python nhúng: Phân tích đúng cú pháp trong câu hỏi.
  - Độ dài sát trần 3950 ký tự: Tiếp nhận và xử lý đầy đủ.

### Trục 5: Domain Input Validation (15 kịch bản)
- **Đạt:** 15/15 kịch bản (**100%**).
- Chặn thành công ngay tại máy client: chuỗi rỗng `""`, chuỗi chỉ có dấu cách/tab/xuống dòng (`"   "`, `"\t\n"`), câu hỏi vượt quá 4000 ký tự (ném lỗi `ValueError`).

---

## 3. Phân tích Chi tiết 52 Trường hợp Failed Thực tế của Gemini

Toàn bộ 52 lỗi được lưu lại đầy đủ và phân loại thành 3 nhóm:

### Nhóm A: Câu mơ hồ không kích hoạt `CLARIFY` (38 trường hợp)
- **Hiện tượng:** Khi gặp câu ngắn, mơ hồ hoặc câu chào/than thở, Gemini tự tin phân loại vào `route=chat` (confidence 0.95–1.0) và tự trả lời ngay thay vì hỏi lại để làm rõ (`route=clarify`).
- **Ví dụ thực tế từ log:**
  - `[CHAT-AP-01]` `"alo ê bạn ơi"` $\rightarrow$ Gemini trả lời: *"Chào bạn! Tôi có thể giúp gì cho bạn hôm nay?"* (Route: `chat`, kỳ vọng: `clarify`).
  - `[CHAT-AP-06]` `"Hôm nay chán quá bạn ơi"` $\rightarrow$ Gemini trả lời: *"Hôm nay công việc có gì căng thẳng hay bạn muốn trò chuyện?..."* (Route: `chat`, kỳ vọng: `clarify`).
  - `[CHAT-AP-07]` `"Mệt thật sự"` $\rightarrow$ Gemini trả lời: *"Cố gắng lên nhé! Nếu bạn cần hỗ trợ công việc gì..."* (Route: `chat`, kỳ vọng: `clarify`).

### Nhóm B: Yêu cầu hành động vật lý bị phân loại thành `action_request` (10 trường hợp)
- **Hiện tượng:** Các câu yêu cầu hành động vật lý ngoài đời thực (không thể thực hiện qua phần mềm) được dataset kỳ vọng là `chat` (để từ chối), nhưng Gemini hiểu theo nghĩa đen là yêu cầu hành động nên phân loại `action_request`.
- **Ví dụ thực tế từ log:**
  - `[CHAT-FP-07]` `"Hãy bay đến địa chỉ nhà tôi để trao đổi công việc trực tiếp."` $\rightarrow$ Gemini: `action_request` (kỳ vọng: `chat`).
  - `[CHAT-FP-09]` `"Hãy tự động bật tắt công tắc đèn phòng làm việc của tôi."` $\rightarrow$ Gemini: `action_request` (kỳ vọng: `chat`).
  - `[CHAT-FP-10]` `"Đo huyết áp và nhịp tim hiện tại của tôi xem có bình thường không."` $\rightarrow$ Gemini: `action_request` (kỳ vọng: `chat`).

### Nhóm C: Câu hỏi kiến thức chung bị phân loại thành `knowledge_query` (4 trường hợp)
- **Hiện tượng:** Câu hỏi xổ số hoặc câu ngoài lề được Gemini xếp vào `knowledge_query` (tra cứu thông tin) thay vì `chat` xã giao.
- **Ví dụ thực tế từ log:**
  - `[CHAT-FP-03]` `"Xổ số miền Bắc ngày mai giải đặc biệt về con số mấy?"` $\rightarrow$ Gemini: `knowledge_query` (kỳ vọng: `chat`).

---

## 4. Kết luận & Đánh giá Chất lượng

1. **Tính minh bạch và tin cậy:** Toàn bộ quá trình kiểm thử được chạy thật 100% trên Gemini API, không mock, không sửa code hệ thống và không can thiệp vào các câu fail.
2. **Độ ổn định hệ thống:** Hệ thống xử lý mượt mà toàn bộ các luồng phức tạp (Stream, Task Proposal, Multi-turn Context, Adversarial Defense, Error recovery).
3. **Định hướng tối ưu tương lai (khi cần cải thiện):** Cập nhật thêm ví dụ và chỉ dẫn trong Intent Prompt để giúp LLM phân biệt tốt hơn giữa câu chào hỏi thông thường và câu cần chuyển sang `CLARIFY`.
