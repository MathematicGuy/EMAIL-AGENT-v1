# ADR-002 — Trích xuất attachment trong sandbox với format allowlist

- Trạng thái: Superseded by ADR-003 (2026-08-07)
- Ngày: 2026-08-03
- Người quyết định: Product/Engineering/Security team

ADR này được giữ lại như hồ sơ lịch sử. Baseline target hiện tại chỉ ghi nhận attachment
có tồn tại và không tải hoặc xử lý nội dung; xem ADR-003.

## Bối cảnh

Action item có thể chỉ xuất hiện trong PDF, tài liệu Office, spreadsheet hoặc ảnh scan đính kèm. Vì vậy chỉ đọc email body sẽ bỏ sót công việc quan trọng. Tuy nhiên attachment là input không đáng tin cậy: file có thể khai báo sai MIME, chứa macro/embedded object, parser exploit, decompression bomb, prompt injection hoặc dữ liệu nhạy cảm. Parser tài liệu và OCR cũng có đặc tính tài nguyên rất khác LLM worker thông thường.

## Quyết định

Đọc attachment qua một `AttachmentExtractorPort` riêng và thực thi adapter trong sandbox bị giới hạn.

- Chỉ xử lý format nằm trong allowlist: PDF, DOCX, XLSX, PPTX, TXT, CSV, JSON, PNG, JPG/JPEG và TIFF.
- Xác minh loại file bằng magic bytes; không tin extension hoặc MIME do email khai báo.
- Quét mã độc trước khi parser đọc nội dung.
- Sandbox không có network, credential, shared filesystem hoặc quyền thực thi tiến trình tùy ý.
- Áp dụng limit cho bytes, trang/slides/sheets, ký tự, CPU, memory và wall-clock time.
- Không chạy macro, script, formula recalculation, embedded object hoặc external link.
- Dùng OCR chỉ khi ảnh/PDF không có text layer đủ dùng.
- Trả text cùng source coordinates chuẩn hóa: page, slide, sheet/cell range hoặc section.
- File tạm bị xóa trong mọi đường thoát. Raw attachment không được lưu mặc định.
- Một file lỗi chỉ tạo warning và không làm thất bại toàn bộ email/run, trừ khi hạ tầng sandbox không khả dụng cho tất cả file.

## Lý do

- Cho phép phát hiện action item nằm hoàn toàn trong attachment.
- Cô lập parser phức tạp khỏi process đang giữ OAuth token và domain data.
- Allowlist và resource limits làm giảm bề mặt tấn công và chi phí không kiểm soát.
- Source coordinates giúp kết quả có thể kiểm chứng và giảm hallucination.
- Port riêng cho phép thay parser/OCR hoặc tách attachment service mà không đổi pipeline nghiệp vụ.

## Các lựa chọn đã cân nhắc

### 1. Gửi nguyên file trực tiếp cho multimodal LLM

Không chọn làm đường chính vì khó kiểm soát định dạng, chi phí, page limits, citation, retention và hành vi với file độc hại. LLM chỉ nhận text/structure đã trích xuất và giới hạn.

### 2. Parse attachment trong cùng worker process

Không chọn vì parser lỗi hoặc file độc hại có thể ảnh hưởng worker, truy cập credential hoặc làm cạn memory/CPU của run khác.

### 3. Chỉ hỗ trợ PDF

Không chọn vì yêu cầu công việc thường nằm trong DOCX/XLSX/PPTX và ảnh scan. Format allowlist rộng hơn vẫn có thể kiểm soát bằng adapter và test theo loại.

### 4. Đọc mọi format dựa trên extension

Không chọn vì bề mặt parser quá lớn và extension dễ giả mạo. Format ngoài allowlist phải được bỏ qua với cảnh báo rõ ràng.

## Hệ quả tích cực

- Coverage action item cao hơn, đặc biệt với quy trình phê duyệt và báo cáo.
- File độc hại hoặc parser crash được giới hạn trong sandbox.
- Kết quả có nguồn cụ thể đến trang/slide/sheet.
- Có thể quan sát chi phí và tỷ lệ lỗi theo MIME type.

## Hệ quả tiêu cực

- Thêm antivirus, sandbox runtime, parser/OCR dependencies và lifecycle file tạm.
- Run có attachment sẽ chậm và tốn tài nguyên hơn.
- OCR và cấu trúc bảng có thể không chính xác tuyệt đối; cần confidence và warning.
- Cần cập nhật parser thường xuyên để vá lỗ hổng.

## Guardrails triển khai

- Queue payload chỉ chứa `runId` và IDs; không chứa bytes của file.
- Tên file từ email chỉ dùng làm nhãn hiển thị, không dùng làm filesystem path.
- Từ chối file khi declared MIME, extension và magic bytes không thể đối chiếu an toàn.
- Mọi parser image chạy với version pin, vulnerability scanning và SBOM.
- Sandbox egress bị chặn ở network layer, không chỉ dựa vào convention trong code.
- File có macro, password, encryption, archive hoặc executable signature bị từ chối trước extraction.
- Khi OCR confidence thấp hoặc tài liệu bị cắt, cảnh báo phải đi cùng kết quả.
- Text trích xuất vẫn là untrusted data và phải đi trong data delimiters khi gửi LLM.

## Khi nào xem xét lại

- Cần hỗ trợ file từ Google Drive/OneDrive link thay vì attachment trực tiếp.
- Tải attachment đủ lớn để cần một service/queue riêng.
- Multimodal model có kiểm soát dữ liệu, citation và cost tốt hơn pipeline parser hiện tại.
- Compliance yêu cầu raw file retention hoặc data residency khác.
- Có nhu cầu hỗ trợ archive hoặc macro-enabled documents sau security review riêng.

## Liên kết

- `../product_requirements.md`
- `../technical_spec.md`
- `ADR-001-async-pipeline-and-adapters.md`
