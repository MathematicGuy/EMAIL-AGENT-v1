# Thiết kế ingest PDF/DOCX cho Knowledge RAG

> **Trạng thái:** Đã chốt thiết kế, chờ review spec trước khi lập implementation plan.

## Mục tiêu

Cho phép chuyển các tài liệu knowledge do quản trị viên đặt trong `data/raw/`
(PDF và DOCX) thành Markdown đã chuẩn hóa trong `data/extracted/`, để corpus RAG
hiện có nạp chúng khi khởi động hoặc tái lập chỉ mục Qdrant.

## Phạm vi và ràng buộc

- Đây là pipeline quản trị corpus chạy thủ công bằng CLI, không phải upload API
  và không phải xử lý Gmail attachment.
- Gmail vẫn chỉ có quyền `gmail.readonly`; không tải hay trích xuất attachment
  trong luồng email. ADR-003 vẫn giữ nguyên hiệu lực.
- Raw file chỉ nằm ở `data/raw/`; index RAG chỉ nhận Markdown được tạo ở
  `data/extracted/`.
- Không log PDF/DOCX bytes, base64 gửi lên Mistral, Markdown trích xuất, hoặc
  nội dung OCR. Log chỉ chứa metadata vận hành không nhạy cảm.
- `MISTRAL_API_KEY` chỉ lấy từ environment; tuyệt đối không commit vào repo hay
  manifest.

## Kiến trúc

```text
data/raw/{*.pdf,*.docx}
  -> discovery + allowlist + SHA-256 + giới hạn tài nguyên
  -> PDF classifier (pdf-inspector) -- text_based --> Markdown local
                               |-- scanned/mixed --> Mistral OCR (trang cần OCR)
  -> DOCX extractor (python-docx) --> Markdown local
  -> chuẩn hóa, kiểm tra không-rỗng, ghi output nguyên tử
  -> data/extracted/*.md + .data/knowledge-ingestion-manifest.json
  -> startup với QDRANT_REINDEX=true (hoặc restart Hybrid retriever)
```

CLI được thêm ở `scripts/ingest_knowledge.py`:

```powershell
python scripts/ingest_knowledge.py --source data/raw --output data/extracted
python scripts/ingest_knowledge.py --source data/raw --output data/extracted --force
```

Mặc định CLI chỉ xử lý file có hash khác manifest. `--force` xử lý lại toàn bộ
file allowlist. Script không tự chạy reindex Qdrant để tránh vô tình ghi vào
collection dùng chung; người vận hành chọn thời điểm reindex riêng.

## Chuyển đổi PDF

1. Xác thực extension, file thường, dung lượng và số trang trước khi parse.
2. `pdf-inspector` chạy local để phân loại `text_based`, `scanned`,
   `image_based` hoặc `mixed` và lấy Markdown native-text khi dùng được.
3. Với `text_based` có kết quả không-rỗng, dùng kết quả local; không gọi
   Mistral.
4. Với `scanned`/`image_based`, gửi PDF đến Mistral OCR. Với `mixed`, gửi các
   trang `pages_needing_ocr` do classifier trả về; Markdown native và OCR được
   ghép theo thứ tự trang.
5. Gọi OCR model cấu hình mặc định `mistral-ocr-latest`, yêu cầu Markdown và
   bảng Markdown. Không dùng document annotation ở phase này.
6. Lỗi Mistral sau retry hữu hạn không xóa output cũ hợp lệ; manifest đánh dấu
   file `failed` cùng reason code không chứa nội dung tài liệu.

Lý do chọn: PDF native-text được xử lý nhanh và không rời máy; chỉ các trang
không có text layer được gửi OCR để giảm chi phí, độ trễ và dữ liệu ra ngoài.

## Chuyển đổi DOCX

DOCX được trích xuất local bằng `python-docx`: tiêu đề thành `#`/`##`, đoạn văn
giữ theo thứ tự, danh sách thành Markdown list, bảng thành Markdown table.
File DOCX lỗi/có nội dung rỗng bị đánh dấu thất bại, không tạo Markdown rỗng.
DOCX không gọi Mistral ở phase này vì không phải OCR.

## Chuẩn hóa đầu ra và metadata

Mỗi Markdown có H1 tiêu đề, dòng metadata nguồn, các marker trang cho PDF và
nội dung đã loại khoảng trắng dư/header-footer lặp lại khi extractor cung cấp
trường đó. Tên output ổn định theo stem đã được slug hóa; va chạm tên phải báo
lỗi thay vì ghi đè nhầm.

Manifest cục bộ `.data/knowledge-ingestion-manifest.json` lưu cho từng source:

- đường dẫn tương đối nguồn và output;
- SHA-256 nguồn;
- extractor đã dùng và phiên bản;
- trạng thái `succeeded`, `skipped` hoặc `failed`;
- số trang, số trang OCR và thời điểm xử lý;
- mã lỗi an toàn nếu thất bại.

Không lưu raw content, payload request/response, API key hoặc markdown text
trong manifest.

## Cấu hình và khả năng chịu lỗi

- Thêm dependency PDF/DOCX và Mistral client tối thiểu cần thiết, được pin theo
major version tương thích Python 3.11+.
- `MISTRAL_API_KEY` vắng mặt: native PDF/DOCX vẫn chạy; file cần OCR thất bại
  rõ ràng với reason `mistral_not_configured`.
- Áp dụng timeout request, retry có giới hạn cho lỗi transient, file/page-size
  limit và giới hạn số trang OCR mỗi document.
- Ghi output qua file tạm và rename nguyên tử sau khi kiểm tra Markdown
  không-rỗng; nhờ đó lỗi giữa chừng không làm hỏng corpus đang dùng.

## Kích hoạt RAG sau ingest

Với Qdrant:

```powershell
$env:QDRANT_REINDEX = "true"
mail-todo-api
```

Sau khi index hoàn thành, khởi động lại bình thường với `QDRANT_REINDEX=false`
(hoặc bỏ biến môi trường). Với Qdrant tắt, restart API là đủ để hybrid memory
nạp toàn bộ `data/extracted/*.md` mới.

## Kiểm thử và tiêu chí nghiệm thu

1. PDF native-text tạo Markdown và không gọi Mistral.
2. PDF scan gọi Mistral fake và tạo Markdown theo trang.
3. PDF mixed chỉ truyền trang thiếu text sang Mistral, ghép đúng thứ tự.
4. DOCX có heading, list và table tạo Markdown hợp lệ.
5. Thiếu API key, timeout, phản hồi lỗi, output rỗng và file vượt giới hạn có
   reason code xác định; output tốt trước đó vẫn nguyên vẹn.
6. Hash không đổi dẫn tới `skipped`; `--force` xử lý lại.
7. Test log/manifest chứng minh không xuất hiện base64, API key hay content.
8. Smoke test corpus rồi xác nhận `load_corpus()` đọc toàn bộ Markdown sinh ra;
   sau reindex, `/v1/mail-todo/knowledge/documents` phản ánh document mới.

## Ngoài phạm vi

- Upload API, job queue, document registry/versioning nhiều tenant và ingest
  incremental trực tiếp vào Qdrant.
- OCR Gmail attachment, XLSX/PPTX/image, antivirus/sandbox runtime.
- Mistral document annotation/QnA và semantic evaluation benchmark Qdrant.
