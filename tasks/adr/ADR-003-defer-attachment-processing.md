# ADR-003 — Hoãn xử lý attachment trong baseline target

- Trạng thái: Accepted
- Ngày: 2026-08-07
- Người quyết định: Product/Engineering team
- Supersedes: ADR-002 và các điều khoản xử lý attachment trong ADR-001

## Bối cảnh

Code hiện tại có `AttachmentExtractorPort`, adapter local và pipeline warning/partial-result cho
attachment. ADR-002 từng chọn sandboxed extraction để tìm action item chỉ xuất hiện trong file.

Baseline target mới ưu tiên luồng Gmail body → route classifier → optional RAG → Action Plan,
đồng thời giới hạn phạm vi bảo mật và vận hành của mốc đầu tiên. Sandbox, antivirus, parser/OCR,
resource isolation và vòng đời file tạm chưa có production implementation tương ứng trong
codebase hiện tại.

## Quyết định

Trong baseline target:

- Chỉ ghi nhận `attachments_present`.
- Luôn đặt `attachments_processed = false`.
- Không tải attachment bytes và không đưa attachment text vào classifier, generator, trace,
  task hoặc episode persistence.
- Production API compatibility có thể giữ attachment counters/warnings trong giai đoạn chuyển
  tiếp, nhưng phải đánh dấu rõ là nội dung không được xử lý.
- `AttachmentExtractorPort`, adapter và test cũ không được dùng trong production wiring. Chúng
  có thể tồn tại tạm thời như compatibility code cho đến khi migration hoàn tất.
- Việc bật lại attachment processing cần một ADR mới hoặc thay thế ADR này sau security review.

ADR này không thay đổi các quyết định còn lại của ADR-001 về async pipeline, durable queue,
PostgreSQL, idempotency, worker claim hoặc ports/adapters.

## Lý do

- Giữ baseline đầu tiên nhỏ và kiểm chứng được.
- Tránh đưa parser file không sandbox vào process đang giữ OAuth token và email data.
- Đồng bộ với PRD và Target Architecture hiện tại.
- Cho phép triển khai routing, RAG, validation và memory policy mà không phụ thuộc hạ tầng xử lý
  tài liệu.

## Hệ quả tích cực

- Giảm bề mặt tấn công, dependency và chi phí vận hành của baseline.
- Privacy boundary đơn giản hơn: raw attachment không rời Gmail adapter.
- Done criteria có thể kiểm chứng bằng việc quan sát không có attachment download/extraction.

## Hệ quả tiêu cực

- Có thể bỏ sót action item chỉ xuất hiện trong attachment.
- Kết quả hiện tại liên quan attachment warning/evidence sẽ thay đổi.
- API/GUI cũ cần compatibility mapping hoặc deprecation rõ ràng cho attachment fields.

## Guardrails triển khai

- Gmail normalization chỉ tạo metadata presence/count; không gọi attachment download endpoint.
- Queue payload chỉ chứa run/task IDs và không chứa email hoặc attachment content.
- Test phải chứng minh attachment có mặt nhưng download/extraction port không được gọi.
- Không mô tả việc bỏ xử lý attachment là behavior-preserving; đây là thay đổi phạm vi sản phẩm.
- ADR-002 không được dùng làm authority cho baseline target sau ngày của ADR này.

## Khi nào xem xét lại

- Product yêu cầu action-item coverage từ PDF/Office/image.
- Có sandbox runtime, antivirus, allowlist parser, resource limits và purge policy được kiểm chứng.
- Security/Compliance chấp thuận data flow, retention và isolation.
- Có evaluation chứng minh lợi ích coverage lớn hơn chi phí và rủi ro.

## Liên kết

- `ADR-001-async-pipeline-and-adapters.md`
- `ADR-002-sandboxed-attachment-extraction.md`
- `../prds/PRD-v1-Core-Email-and-RAG.md`
- `../../docs/architectures/TARGET-ARCHITECTURE.md`
