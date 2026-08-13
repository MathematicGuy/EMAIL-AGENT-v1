# Product Requirements Document

## Cowork Agent — Chat với tài liệu người dùng ("Chat with the PDF")

| Trường | Giá trị |
|---|---|
| Sản phẩm | Cowork Agent — AI Chat Assistant |
| Trạng thái tài liệu | Draft — chờ phê duyệt |
| Phiên bản | 3.0 |
| Ngày | 2026-08-12 |
| Phụ thuộc | PRD-v1 (Email + RAG) và PRD-v2 (Memory Extension) đã hoàn thành |
| Nguồn yêu cầu | `docs/references/user_preference.md` |
| Thiết kế kỹ thuật | [SPEC-chat-with-user-documents](../specs/SPEC-chat-with-user-documents.md) |
| Thẩm quyền kiến trúc | [ADR-007](../adr/ADR-007-project-scoped-classifier-gated-user-documents.md), [TARGET-ARCHITECTURE §21](../../docs/architectures/TARGET-ARCHITECTURE.md) |
| Chủ sở hữu tính năng | AI Chat Controller (`feature: ai_chat`) |
| Vector store | Qdrant (bắt buộc cho plane này) |
| OCR | Bật, dùng Mistral OCR |
| Retention mặc định | 30 ngày |
| Reflexion / Tool thực thi / Scheduling | Ngoài phạm vi |

---

## 1. Tóm tắt điều hành

PRD-v3 cho phép người dùng tải tài liệu vào một Project do backend quản lý, hỏi trong
AI Chat gắn với đúng Project đó, và
nhận câu trả lời có trích dẫn tới từng trang.

Hai thay đổi sản phẩm:

1. **Tài liệu của người dùng trở thành một nguồn tri thức** — tải lên một lần,
   mọi phiên chat của người đó đều dùng được. Không có bước đính kèm theo phiên.
2. **Trợ lý tự quyết định khi nào cần đọc tài liệu** — thay cho cơ chế nhận diện
   cụm từ khoá hiện tại, một intent classifier trở thành cổng định tuyến duy
   nhất của mỗi lượt chat.

Vòng lặp giá trị:

```text
Tải tài liệu lên
→ hệ thống trích xuất (native text + OCR), chunk theo trang, index vào Qdrant
→ người dùng hỏi trong chat
→ classifier quyết định lượt này có cần tài liệu không
→ nếu cần: truy hồi tài liệu của đúng người dùng đó, trả lời kèm trích dẫn trang
→ nếu không cần: trả lời chat bình thường, không tốn chi phí truy hồi
```

Ranh giới dữ liệu: tài liệu người dùng **không bao giờ** nhập vào company RAG
corpus, không bao giờ vào TaskEpisode dưới dạng văn bản, và không bao giờ chạm
vào luồng Email Agent PRD-v1.

## 2. Giả thuyết sản phẩm

> Khi trợ lý tự phán đoán được *"câu hỏi này có phụ thuộc vào tài liệu của người
> dùng hay không"*, người dùng nhận được câu trả lời grounded có trích dẫn trang
> mà không phải ra lệnh tường minh ("theo tài liệu, …"), đồng thời hệ thống không
> trả phí truy hồi cho những lượt trò chuyện thông thường.

Giả thuyết này sai nếu: classifier bỏ sót truy hồi ở mức đáng kể (trợ lý trả lời
không bằng chứng), hoặc truy hồi thừa quá nhiều tới mức chi phí và độ trễ vượt
ngưỡng ở §9.

## 3. Người dùng và tình huống sử dụng

Người dùng mục tiêu ở giai đoạn này là **một người dùng nội bộ đang làm việc với
tài liệu của chính mình** — tài liệu học tập, tài liệu kỹ thuật, quy trình, biểu
mẫu, hợp đồng, ghi chú.

Công việc cần làm (jobs to be done):

| # | Tình huống | Ví dụ câu hỏi |
|---|---|---|
| J1 | Tóm tắt tài liệu dài | "Tóm tắt tài liệu này" |
| J2 | Tra cứu một chi tiết | "Trang 7 nói gì về checkpointing?" |
| J3 | Hồi tưởng mơ hồ | "Yêu cầu lúc nãy là gì ấy nhỉ?" |
| J4 | So sánh nhiều tài liệu | "Hai cách tiếp cận trong các tài liệu khác nhau chỗ nào?" |
| J5 | Hỏi kiến thức chung xen kẽ | "Giải thích decorator trong Python" |
| J6 | Kiểm chứng | "Câu này lấy từ đâu?" |

J3 và J5 là hai đầu đối lập mà classifier phải phân biệt được: J3 phải truy hồi
dù không nhắc chữ "tài liệu", J5 không được truy hồi dù người dùng vừa nhắc tới
tài liệu ở câu trước.

## 4. Nguyên tắc sản phẩm

1. **Định tuyến tập trung ở classifier.** Một quyết định LLM có cấu trúc cho mỗi
   lượt. Không có lớp từ khoá nào được kết luận thay. Chất lượng định tuyến được
   cải thiện bằng prompt và bằng bộ fixture gán nhãn, không bằng danh sách cụm
   từ ngày càng dài.
2. **Ưu tiên recall cho truy hồi, ưu tiên precision cho hành động.** Bỏ sót tài
   liệu khiến trợ lý trả lời không có bằng chứng — hỏng niềm tin. Truy hồi thừa
   chỉ tốn tiền và vài trăm mili giây.
3. **Không có bằng chứng thì nói không có.** Khi tài liệu không chứa câu trả
   lời, trợ lý phải nói rõ và nêu phần còn thiếu. Bịa từ kiến thức nền là lỗi.
4. **Trích dẫn phải kiểm chứng được.** Mọi khẳng định dựa trên tài liệu đều kèm
   tên tài liệu và số trang, để người dùng mở đúng chỗ.
5. **Tài liệu là dữ liệu của người dùng.** Xoá được, hết hạn được, không rò sang
   người khác, không lẫn vào tri thức công ty, không xuất hiện trong log.
6. **Suy giảm phải nói ra.** Khi kho vector không khả dụng, trợ lý nói rằng bằng
   chứng tài liệu đang không dùng được — không lặng lẽ trả lời như thường.

## 5. Phạm vi

Trong phạm vi:

- Tải lên PDF và DOCX, theo dõi trạng thái xử lý, xoá tài liệu.
- Trích xuất văn bản gốc và OCR cho trang scan.
- Truy hồi theo ngữ nghĩa trên tài liệu của đúng người dùng đó.
- Intent classifier và bộ định tuyến 5 nhánh (MVP dùng 3).
- Trả lời có trích dẫn theo trang, xử lý trường hợp không có bằng chứng.
- Retention 30 ngày và xoá theo yêu cầu.

Ngoài phạm vi: §13.

## 6. Trải nghiệm người dùng

### 6.1 Tải tài liệu

```text
Chọn file (PDF/DOCX)
→ hệ thống nhận (202), hiển thị "đang xử lý"
→ trạng thái: received → extracting → indexing → ready
→ khi ready: tài liệu xuất hiện trong danh sách, dùng được ở mọi phiên chat
```

Người dùng không phải chờ để tiếp tục chat. Trong lúc tài liệu chưa `ready`, các
lượt chat vẫn chạy bình thường và tài liệu đó chưa được truy hồi.

Khi thất bại, người dùng thấy lý do bằng ngôn ngữ người đọc được, ánh xạ 1–1 từ
`reason_code`:

| `reason_code` | Thông điệp |
|---|---|
| `file_too_large` | File vượt quá dung lượng cho phép |
| `unsupported_media_type` | Chỉ hỗ trợ PDF và DOCX |
| `encrypted_document` | File có mật khẩu, cần bỏ bảo vệ trước khi tải lên |
| `pdf_page_limit_exceeded` | File vượt quá số trang cho phép |
| `empty_extraction` | Không trích xuất được văn bản nào |
| `ocr_page_limit_exceeded` | Quá nhiều trang cần OCR |
| `ocr_failed` | Không đọc được trang scan, thử lại sau |
| `quota_exceeded` | Đã đạt giới hạn số tài liệu |
| `embedding_unavailable` / `index_unavailable` | Hệ thống xử lý tạm thời gián đoạn |

### 6.2 Hỏi trong chat

Người dùng hỏi bình thường. Không cần cú pháp đặc biệt, không cần chọn tài liệu.

- Câu hỏi phụ thuộc tài liệu → trả lời kèm trích dẫn `Tên tài liệu · trang N`.
- Câu hỏi kiến thức chung → trả lời thẳng, không trích dẫn, không tốn truy hồi.
- Câu hỏi quá mơ hồ để hành động ("làm đi") → trợ lý hỏi lại một câu làm rõ.
- Tài liệu không chứa câu trả lời → trợ lý nói rõ và nêu phần còn thiếu.

### 6.3 Trích dẫn

Trích dẫn hiển thị trong luồng chat qua cơ chế `memory_citation` sẵn có, phân
biệt bằng `citation_scope`. Người dùng thấy tên tài liệu và khoảng trang; không
có loại sự kiện giao diện mới.

### 6.4 Quản lý tài liệu

Danh sách tài liệu hiển thị tên, trạng thái, số trang, ngày hết hạn. Xoá là hành
động một bước, có xác nhận, và xoá thật: byte gốc, văn bản đã trích xuất, và các
điểm vector. Trích dẫn cũ trong TaskEpisode trỏ tới tài liệu đã xoá được hiển thị
là "không còn khả dụng", không làm hỏng episode.

## 7. Yêu cầu chức năng

### Nhóm A — Vòng đời tài liệu

| ID | Yêu cầu | Tiêu chí chấp nhận |
|---|---|---|
| FR-01 | Người dùng tải lên PDF hoặc DOCX | API trả `202` kèm `document_id` và `status`; xử lý chạy ngoài request path |
| FR-02 | Kiểm tra hợp lệ trước khi xử lý | Loại file xác định bằng nội dung, không bằng phần mở rộng; vi phạm dung lượng/số trang/hạn mức bị từ chối kèm `reason_code` |
| FR-03 | Trùng nội dung không tạo bản sao | Tải lại đúng byte đó trả về bản ghi cũ; số chunk không tăng |
| FR-04 | Trích xuất trang scan bằng OCR | Trang cần OCR được gửi tới Mistral OCR trong giới hạn cấu hình; trang có văn bản gốc không bao giờ OCR lại |
| FR-05 | Không index kết quả trích xuất rỗng hoặc một phần | Tài liệu chuyển `failed` với `reason_code` tương ứng; không có chunk nào được ghi |
| FR-06 | Theo dõi trạng thái | Endpoint trạng thái trả `status`, `reason_code`, số trang, số chunk |
| FR-07 | Xoá tài liệu | Xoá byte gốc, văn bản trích xuất và điểm vector; lặp lại được cho tới khi mọi kho xác nhận |
| FR-08 | Hết hạn tự động | Mặc định 30 ngày kể từ khi tải lên; tài liệu hết hạn bị loại **trước** khi xếp hạng và được dọn nền |

### Nhóm B — Định tuyến

| ID | Yêu cầu | Tiêu chí chấp nhận |
|---|---|---|
| FR-09 | Classifier là cổng định tuyến duy nhất | Mỗi lượt có đúng một quyết định có cấu trúc gồm `needs_rag`, `needs_tool`, `needs_clarification`; không thành phần nào khác được đặt các giá trị này |
| FR-10 | Bộ giải tất định | Ánh xạ ba boolean sang route theo bảng chân trị cố định, không phụ thuộc mô hình |
| FR-11 | Phân biệt được nhắc-tài-liệu với cần-tài-liệu | Nhóm distractor trong bộ fixture (nhắc tài liệu rồi đổi chủ đề) phải cho `needs_rag = false` |
| FR-12 | Bắt được câu hỏi hồi tưởng mơ hồ | Nhóm ambiguous ("yêu cầu lúc nãy là gì ấy nhỉ") phải cho `needs_rag = true` |
| FR-13 | Không có tài liệu thì không truy hồi | Người dùng chưa có tài liệu `ready` ⇒ lượt chạy như chat thường, không gọi embedding hay vector store |
| FR-14 | Lỗi classifier nghiêng về truy hồi | Sau một lần thử lại, lỗi schema hoặc timeout ⇒ coi như `needs_rag = true` (khi có tài liệu `ready`), ghi lý do |
| FR-15 | Trục hành động tắt ở bản này | `needs_tool` luôn bị hạ về `false`; không có tool nào chạy trong chat |
| FR-16 | Yêu cầu quá mơ hồ thì hỏi lại | `needs_clarification = true` ⇒ trợ lý phát một câu hỏi làm rõ, không đoán |

### Nhóm C — Truy hồi và trả lời

| ID | Yêu cầu | Tiêu chí chấp nhận |
|---|---|---|
| FR-17 | Cách ly theo chủ sở hữu | Điều kiện `tenant_id`, `user_id`, trạng thái `ready`, còn hạn được dựng **trước** khi truy vấn được embed; chunk của người khác không bao giờ được chấm điểm |
| FR-18 | Trích dẫn theo trang | Mỗi chunk mang `page_start`/`page_end`; câu trả lời hiển thị tên tài liệu và khoảng trang |
| FR-19 | Không đủ bằng chứng thì nói ra | Không chunk nào vượt ngưỡng ⇒ câu trả lời nêu rõ tài liệu không đề cập và liệt kê phần còn thiếu |
| FR-20 | Xung đột được nêu, không bị che | Khi tài liệu người dùng mâu thuẫn với tri thức công ty đang bật, cả hai được nêu kèm trích dẫn |
| FR-21 | Suy giảm được công bố | Vector store hỏng hoặc timeout ⇒ trả lời nói rõ bằng chứng tài liệu không khả dụng; không thay bằng suy đoán |
| FR-22 | Thu hẹp phạm vi tuỳ chọn | Request có thể giới hạn theo `document_ids`; mặc định là toàn bộ tài liệu `ready` của người dùng |

### Nhóm D — Bộ nhớ và riêng tư

| ID | Yêu cầu | Tiêu chí chấp nhận |
|---|---|---|
| FR-23 | Episode chỉ lưu toạ độ trích dẫn | `citation_scope`, `document_id`, tiêu đề, khoảng trang; không có văn bản chunk |
| FR-24 | Tài liệu không phải nguồn sở thích | Nội dung tài liệu không bao giờ ghi vào declarative profile |
| FR-25 | Tài liệu không vào company corpus | Không có đường nào đưa tài liệu người dùng sang kho tri thức công ty |
| FR-26 | Không rò vào log | Văn bản tài liệu, văn bản trang, truy vấn thô và prompt đã lắp không xuất hiện trong log, telemetry, fixture |
| FR-27 | Email Agent không bị ảnh hưởng | Luồng PRD-v1 không đọc, không ghi, không thấy plane này |

## 8. Yêu cầu phi chức năng

| Hạng mục | Mục tiêu |
|---|---|
| Độ trễ classifier | p95 ≤ 1.5 s |
| Độ trễ truy hồi tài liệu | p95 ≤ 3 s, timeout 3 s + một lần thử lại |
| Ảnh hưởng tới lượt chat không truy hồi | Không quá một lời gọi LLM tăng thêm |
| Thời gian xử lý tài liệu | Tài liệu 20 trang native ≤ 60 s tới `ready`; có OCR ≤ 5 phút |
| Giới hạn mặc định | ≤ 25 MB/file, ≤ 100 trang, ≤ 50 tài liệu/người dùng |
| Khả dụng | Ingestion hỏng không làm hỏng chat; chat hỏng không mất tài liệu đã index |

## 9. Chỉ số thành công và ngưỡng ra mắt

| Chỉ số | Ý nghĩa | Ngưỡng |
|---|---|---|
| Retrieval recall | tỉ lệ câu **thật sự** cần tài liệu được truy hồi | ≥ 0.95 |
| Missed-RAG rate | cần tài liệu nhưng đi nhánh chat | ≤ 0.05 |
| Retrieval precision | tỉ lệ lượt đã truy hồi mà **thật sự** cần | ≥ 0.75 |
| Citation accuracy | trích dẫn trỏ đúng trang chứa nội dung | ≥ 0.90 |
| Ingestion success rate | tài liệu hợp lệ đạt `ready` | ≥ 0.95 |
| Safety counters | truy hồi chéo người dùng/tenant, tài liệu hết hạn hoặc đã xoá, văn bản tài liệu trong log | **= 0** |

Missed-RAG rate là chỉ số quyết định. Nó đo đúng chế độ hỏng nguy hiểm nhất:
trợ lý trả lời tự tin mà không đọc tài liệu đáng lẽ phải đọc.

Ngưỡng đo trên bộ fixture gán nhãn ≥ 60 câu, chia đều bốn nhóm: obvious RAG,
obvious chat, ambiguous, distractor. Ví dụ trong prompt và câu trong fixture
không được trùng nhau.

## 10. Rủi ro

| Rủi ro | Ảnh hưởng | Giảm thiểu |
|---|---|---|
| Classifier bỏ sót truy hồi | Trả lời không bằng chứng, mất niềm tin | Fail-open sang truy hồi; đo missed-RAG rate riêng; quy tắc chốt trong prompt nghiêng về truy hồi |
| Classifier truy hồi thừa | Tốn chi phí và độ trễ | Ngưỡng precision ≥ 0.75; chặn cứng khi không có tài liệu `ready` |
| Prompt hồi quy khi chỉnh sửa | Chất lượng tụt âm thầm | `prompt_version` bắt buộc; đổi prompt phải chạy lại fixture |
| OCR gửi ảnh trang ra ngoài | Vấn đề riêng tư | Công bố rõ trong sản phẩm; chỉ chạy khi người dùng chủ động tải lên |
| Qdrant là điểm hỏng đơn | Mất khả năng trả lời theo tài liệu | Công bố suy giảm rõ ràng; không thay bằng suy đoán |
| Người dùng tưởng đã xoá nhưng chưa | Vấn đề pháp lý và niềm tin | Xoá lặp lại được tới khi mọi kho xác nhận; safety counter cho tài liệu đã xoá |

## 11. Phụ thuộc và giả định

- Qdrant sẵn sàng ở môi trường chạy; plane này không có kho dự phòng trong repo.
- `MISTRAL_API_KEY` được cấu hình; OCR hiện đang bị chặn trong runtime và sẽ được
  mở trong phạm vi PRD này.
- Công cụ `pdf-inspector` cục bộ có trên PATH của tiến trình chạy job.
- Company RAG trong chat **tắt** ở bản này (đứng sau cờ cấu hình); tri thức công
  ty vẫn phục vụ Email Agent PRD-v1 như cũ.
- Một tài liệu thuộc về một người dùng. Không có chia sẻ ở bản này.

## 12. Mốc triển khai và điều kiện hoàn thành

| Mốc | Nội dung | Điều kiện hoàn thành |
|---|---|---|
| M1 | Hợp đồng dữ liệu và trạng thái tài liệu | Test contract xanh; không đổi hành vi chat |
| M2 | Ingestion: validate → extract → OCR → chunk theo trang → index | FR-01..FR-06 đạt; mọi nhánh `failed` có test |
| M3 | Truy hồi ACL-first + xoá lan truyền | FR-07, FR-17, FR-22 đạt; safety counters = 0 |
| M4 | Classifier + resolver + fixture gán nhãn | FR-09..FR-16 đạt; ngưỡng §9 đạt |
| M5 | Trả lời có trích dẫn trang, xử lý thiếu bằng chứng và suy giảm | FR-18..FR-21 đạt |
| M6 | Retention, xoá, telemetry, gate đánh giá | FR-08, FR-23..FR-27 đạt |

M1–M3 không thay đổi hành vi chat hiện tại. Hành vi chat chỉ đổi từ M4.

## 13. Ngoài phạm vi

- Chia sẻ tài liệu giữa người dùng hoặc ở mức workspace.
- Đưa tài liệu người dùng vào kho tri thức công ty (cấm vĩnh viễn).
- Container project để nhóm tài liệu.
- Hiểu ảnh, biểu đồ, cấu trúc bảng vượt quá văn bản OCR.
- Sửa, chú thích, tái sinh tài liệu; tái ingest theo lịch.
- Ingest attachment Gmail (vẫn ngoài phạm vi theo ADR-003).
- Tool thực thi trong chat, bao gồm `@Email`.
- Reflexion, multi-agent, ReAct loop tự trị.
- Truy hồi episodic theo tài liệu.

## 14. Câu hỏi mở

| # | Câu hỏi | Trạng thái |
|---|---|---|
| Q1 | Dùng LangGraph làm lớp lắp graph hội thoại? | SPEC mặc định **có**, cô lập trong một module |
| Q2 | Có mở lại company RAG trong chat sau MVP không? | Hoãn; cờ cấu hình đã có sẵn |
| Q3 | Ngưỡng precision 0.75 có quá lỏng cho chi phí thực tế không? | Đo lại sau M4 bằng số liệu thật |
