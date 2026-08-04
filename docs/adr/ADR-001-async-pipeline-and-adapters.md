# ADR-001 — Dùng async pipeline và ports/adapters cho Email To-Do Summarizer

- Trạng thái: Accepted
- Ngày: 2026-08-03
- Người quyết định: Product/Engineering team

## Bối cảnh

Module phải lấy nhiều email cùng attachment từ Gmail, xử lý thread, trích xuất tài liệu, gọi LLM theo batch, chuẩn hóa kết quả, chống trùng lặp và hỗ trợ cả yêu cầu trực tiếp lẫn lịch tự động. Thời gian chạy phụ thuộc số email, loại/kích thước attachment, OCR, Gmail quota và độ trễ của AI provider; không phù hợp để buộc toàn bộ xử lý hoàn tất trong một HTTP request ngắn.

Module cũng cần tránh phụ thuộc chặt vào Gmail hoặc một nhà cung cấp LLM vì sản phẩm có thể bổ sung Outlook, đổi model hoặc dùng nhiều provider sau này. Dữ liệu email nhạy cảm nên boundary với connector và AI phải rõ, dễ kiểm tra và có thể thay bằng fake trong test.

## Quyết định

Triển khai module như một bounded module trong backend hiện tại, dùng async job pipeline bền vững và kiến trúc ports/adapters.

- HTTP API hoặc scheduler chỉ tạo `DigestRun` và enqueue job.
- Worker sở hữu toàn bộ pipeline: fetch, attachment extraction, preprocess, action extraction, normalize, dedupe, persist và publish completion event.
- Gmail chỉ được gọi qua `MailboxPort` read-only.
- File chỉ được đọc qua `AttachmentExtractorPort`; chi tiết isolation được quyết định trong ADR-002.
- LLM chỉ được gọi qua `ActionExtractorPort` với structured output; extractor không được cấp tool.
- PostgreSQL là source of truth cho run, action item, schedule và outbox.
- Durable queue chịu trách nhiệm delivery/retry; mọi handler phải idempotent.
- Scheduled và on-demand trigger đi qua cùng application service, không có hai code path nghiệp vụ riêng.
- Bắt đầu trong modular monolith; chỉ tách thành service độc lập khi có dữ liệu vận hành chứng minh cần scale hoặc isolation riêng.

## Lý do

- Độ trễ Gmail/LLM không làm giữ request hoặc gây timeout ở lớp giao diện.
- Có thể retry có kiểm soát và trả kết quả `partial` khi chỉ một batch lỗi.
- Idempotency và schedule dedupe được đặt tại một boundary thống nhất.
- Fake adapters giúp test không cần Gmail thật hoặc gọi LLM tốn chi phí.
- Gmail read-only port giảm bề mặt rủi ro: module không có capability thay đổi mailbox.
- Modular monolith giảm chi phí vận hành ban đầu nhưng vẫn giữ đường tách service về sau.

## Các lựa chọn đã cân nhắc

### 1. Xử lý đồng bộ trong HTTP request

Không chọn vì thời gian chạy khó dự đoán, dễ timeout, khó hiển thị tiến độ và retry có thể tạo kết quả trùng. Cách này chỉ phù hợp cho prototype với vài email.

### 2. Tạo microservice độc lập ngay từ đầu

Không chọn cho v1 vì thêm deployment, observability, network failure, secret management và data ownership complexity trước khi có bằng chứng về tải. Boundary module hiện tại đủ để tách sau này.

### 3. Serverless function cho mỗi email

Không chọn làm mô hình chính vì việc merge kết quả theo thread, giới hạn concurrency, kiểm soát token/cost và hoàn tất run sẽ phức tạp. Có thể dùng serverless worker ở tầng triển khai nếu vẫn giữ semantics của durable pipeline.

### 4. Để LLM tự gọi Gmail và tự điều phối toàn bộ

Không chọn vì khó đảm bảo query, giới hạn quyền, idempotency, audit, cost và chống prompt injection. LLM chỉ nhận dữ liệu đã fetch/sanitize và trả extraction theo schema.

## Hệ quả tích cực

- API phản hồi nhanh với `202 Accepted`.
- On-demand và scheduled run có cùng chất lượng và hành vi.
- Có thể thay Gmail bằng Outlook adapter hoặc thay model mà không sửa domain core.
- Retry, partial completion và quan sát theo từng stage rõ ràng.
- Bảo mật dễ audit hơn vì extractor không có tool và mailbox port không có write method.

## Hệ quả tiêu cực

- Cần queue, worker, scheduler và cơ chế theo dõi trạng thái run.
- UI phải poll hoặc nhận event thay vì luôn có kết quả ngay.
- Cần xử lý eventual consistency và outbox.
- Debug một run trải qua nhiều thành phần phức tạp hơn, đòi hỏi tracing tốt.

## Guardrails triển khai

- Mọi run có idempotency key và state transition bằng compare-and-set.
- Queue payload chỉ chứa `runId`; không chứa email body hoặc OAuth token.
- Chỉ một worker được claim một run.
- Gmail adapter không triển khai send/modify/delete.
- Không đưa raw attachment vào queue hoặc application database.
- Không log email body, prompt hoặc raw model output trong production.
- Kết quả LLM phải qua schema validation và deterministic policy trước khi lưu.
- Completion event dùng transactional outbox để tránh thông báo thiếu hoặc trùng.

## Khi nào xem xét lại

Xem xét tách service hoặc đổi mô hình khi có ít nhất một điều kiện:

- Worker workload ảnh hưởng rõ rệt đến SLA của backend chính.
- Cần scale Gmail/LLM pipeline độc lập theo tải lớn.
- Yêu cầu compliance cần network/data isolation riêng.
- Nhiều module khác bắt đầu dùng chung mailbox ingestion pipeline.
- Queue/provider hiện tại không đáp ứng latency hoặc data residency.

## Liên kết

- `../product_requirements.md`
- `../technical_spec.md`
