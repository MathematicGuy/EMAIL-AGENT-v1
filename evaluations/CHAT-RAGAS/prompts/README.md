# Thư Mục Lưu Trữ Prompt RAGAS Tiếng Việt Đã Hiệu Chuẩn

Thư mục này dùng để lưu trữ các định nghĩa prompt tiếng Việt đã qua hiệu chuẩn thực nghiệm cho các chỉ số RAGAS (`faithfulness`, `answer_relevancy`).

---

## 1. Mục Đích

Theo quy định tại [`docs/evaluations/RAGAS.md § 5`](../../docs/evaluations/RAGAS.md) và [`tasks/specs/SPEC-chat-ragas-evaluation.md`](../../tasks/specs/SPEC-chat-ragas-evaluation.md):
- Các prompt mặc định của thư viện RAGAS được xây dựng bằng tiếng Anh.
- Việc dịch prompt lúc runtime (dynamic translation) sẽ tạo ra các biến thể diễn đạt khác nhau giữa các lần chạy, làm mất tính ổn định và so sánh được của chuỗi đo lường baseline.
- Mọi prompt tiếng Việt (bao gồm prompt trích xuất mệnh đề nguyên tử và prompt suy luận logic NLI) **bắt buộc phải được commit vào thư mục này**.

---

## 2. Quy Trình Hiệu Chuẩn & Đưa Prompt Vào Sử Dụng

1. **Chuyển ngữ:** Dịch prompt và các ví dụ few-shot chuẩn theo ngữ cảnh tiếng Việt và tài liệu hành chính/doanh nghiệp.
2. **Hiệu chuẩn với người chấm:** Chạy thử nghiệm trên tập 30 case mẫu và so sánh mức độ đồng thuận giữa điểm judge và điểm con người chấm tay.
3. **Commit & Ghi nhận:** Chỉ khi đạt độ đồng thuận cao, prompt mới được lưu tại đây và áp dụng cho các lượt chạy baseline chính thức.
