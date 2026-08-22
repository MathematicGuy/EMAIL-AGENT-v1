# Kế hoạch nâng chất lượng RAG cho tài liệu quy trình

> **Dành cho agentic worker:** BẮT BUỘC dùng sub-skill superpowers:subagent-driven-development (khuyến nghị) hoặc superpowers:executing-plans để thực thi kế hoạch này theo từng task. Các bước dùng cú pháp checkbox (`- [ ]`) để theo dõi tiến độ.

**Mục tiêu:** Nâng chất lượng truy xuất và chất lượng câu trả lời trên *company corpus* tài liệu quy trình (thủ tục hành chính, quy trình nội bộ, SOP) lên mức grounding tương đương NotebookLM: section-level Hit@1 ≥ 0.85, bảng và các bước quy trình đều trả lời được, mọi khẳng định đều kèm trích dẫn giải được về đúng trang và đúng section. Project documents của tenant là workstream tách riêng ở cuối tài liệu.

**Kiến trúc tiếp cận:** Sửa pipeline theo đúng thứ tự chỗ mất mát thực sự xảy ra — extraction trước, gán nhãn chunk sau, rồi retrieval, cuối cùng là sinh câu trả lời có dẫn chứng. Đo lường không phải là phase cuối: Phase 0 dựng các baseline còn thiếu **trước khi** thay đổi bất kỳ hành vi nào, và mọi phase sau đều bị chặn bởi các baseline đó.

**Công nghệ:** Python 3.12, `pdf_inspector`, Mistral OCR (`mistral-ocr-latest`), python-docx, Jina embedding cho company RAG, Gemini embedding cho project documents/evaluator khi được cấu hình, BM25 + RRF + Turbovec, reranker cấu hình qua `RerankerAdapter`, pytest.

## Vì sao có kế hoạch này (số liệu đo được, không phải phỏng đoán)

Chạy ngày 2026-08-20 trên `data/extracted` (17 tài liệu, 949 chunk) bằng `scripts/diagnose_chunking.py`. Báo cáo gốc: `evaluations/RETRIEVAL/chunking-diagnostics/baseline-2026-08-20.json`.

| Phát hiện | Số đo |
| --- | --- |
| Chunk không có nhãn section | 1,1% toàn corpus, nhưng **20–25%** ở năm tài liệu thủ tục ngắn (`dang-ky-tam-tru`, `dang-ky-xe`, `thue-dien-tu`, `cap-lai-cccd`, `dang-ky-ket-hon`) — phần mở đầu trước heading đầu tiên |
| Chunk có nhãn section thực sự gây nhầm (cùng nhãn, khác section) | 1,1% toàn corpus, nhưng **40%** ở `chi-tiet-thu-tuc-1-004194`, **18%** ở `-2-001194`, **16%** ở `-1-116194` |
| Nhãn rác tệ nhất | `Trực tiếp` ×12, `Tên giấy tờ Mẫu đơn, tờ khai Số lượng` ×4, `Hình thức nộp Thời hạn giải Phí, lệ phí Mô tả quyết` ×4 — tất cả đều là **dòng header bảng và ô bảng đơn lẻ bị đẩy lên thành heading** |
| Bước quy trình bị vùi giữa đoạn văn | 13 trong 38 marker bước (34%); 9 chunk gộp ≥2 bước |
| Chunk vượt giới hạn 2 000 ký tự của embedding | 0 — chính sách kích thước đang đúng |
| Dòng bảng được **chunker** giữ lại | 104 / 104 — chunker **không** phải chỗ mất mát |

So sánh mang tính quyết định là theo extractor, lấy từ `data/extracted/ingestion-manifest.json`:

- 11 PDF thủ tục đi qua **`pdf_native`**. Ở `chi-tiet-thu-tuc-1-115970`, bảng "Cách thức thực hiện" (thời hạn, phí, mô tả) sống sót với **0 dòng markdown**: header của nó biến thành heading `## Hình thức nộp Thời hạn giải Phí, lệ phí Mô tả quyết` — một bản ghép hỏng của hai ô bị rớt dòng — còn các dòng dữ liệu thành văn xuôi chạy dài, trong đó `Miễn phí` nằm lọt giữa câu.
- 1 PDF thủ tục đi qua **`mistral_ocr`** (`chi-tiet-thu-tuc-3-000228`). Đây là tài liệu sạch nhất corpus: 28 dòng bảng nguyên vẹn, 0 nhãn gây nhầm, 11 section riêng biệt cho 11 chunk, cả 4 bước đều đứng trên dòng riêng.

Nghĩa là: khoản mất mát lớn nhất của corpus này nằm ở **định tuyến extraction**, không phải ở chunking hay retrieval. Câu hỏi về phí, thời hạn và thành phần hồ sơ — những câu hỏi phổ biến nhất đối với một tài liệu quy trình — nhắm đúng vào phần nội dung mà `pdf_native` phá hủy.

Hai lỗi nữa đến từ đọc code, không phải từ corpus:

- `src/cowork_agent/integrations/knowledge_ingestion/project_documents.py:60` đóng gói output OCR thành `((1, markdown),)`, nên các marker `<!-- Page N -->` do Mistral sinh ra bị phân loại là boilerplate và bị loại bỏ. Mọi chunk của tài liệu scan do người dùng tải lên đều được trích dẫn là trang 1. Nhánh admin làm đúng nhờ `split_markdown_pages`; chỉ nhánh project-document bị sai.
- `DocxExtractor.extract` luôn trả `page_count=1` (`docx_extractor.py:32`), nên cả bốn file luật DOCX đều trích dẫn trang 1.

## Khoảng trống baseline phải lấp trước khi triển khai bất cứ thay đổi nào

Không có baseline retrieval nào còn so sánh được với corpus hiện tại. Hai report `hashing` ngày 2026-08-17 mang **1.069 chunk**, trong khi `load_corpus(data/extracted)` hiện trả 949 chunk; chúng đã được đánh dấu *superseded* trong `evaluations/baselines/README.md` và chỉ giữ lại để truy vết lịch sử. Các baseline `gemini` ngày 2026-08-08 dùng corpus 36 chunk nên cũng không so sánh được. Do đó Phase 0 phải tạo baseline mới trước khi trích dẫn bất kỳ điểm retrieval nào.

## Chỉ tiêu (định nghĩa "ngang tầm NotebookLM" cho hệ thống này)

Đo trên bộ golden 100 case cộng các probe mới từ task 0.3. Chỉ công bố kết quả là bằng chứng production khi harness dùng cùng embedder và cùng `RerankerAdapter`/model với runtime; `scripts/evaluate_retrieval.py` hiện mới hỗ trợ `hashing` và `gemini`, còn `--rerank` dùng riêng `JinaRerankerAdapter`.

| Chỉ số | Chỉ tiêu |
| --- | --- |
| Document-level Hit@1 / Recall@5 | ≥ 0,95 / ≥ 0,98 |
| Section-level Hit@1 / Hit@3 / MRR | ≥ 0,85 / ≥ 0,95 / ≥ 0,90 |
| Slice probe `semantic`, section Hit@1 | ≥ 0,75 (giả thuyết slice yếu cần đo ở Phase 0) |
| Slice probe `tabular` (mới), section Hit@1 | ≥ 0,85 |
| Slice probe `procedural` (mới, dạng "bước N"), section Hit@1 | ≥ 0,85 |
| Tỉ lệ từ chối trả lời đúng trên probe `unanswerable` | ≥ 0,90, không có trích dẫn bịa |
| Độ bám dẫn chứng của câu trả lời (LLM judge) | ≥ 0,95 số khẳng định truy được về chunk đã truy xuất |
| Tỉ lệ trích dẫn giải được | 100% — mọi trích dẫn trỏ tới chunk có thật, có trang và section thật |
| Độ trễ retrieval p95 (chưa tính sinh câu trả lời) | ≤ 1 200 ms |

Chỉ tiêu cấu trúc, đo bằng `scripts/diagnose_chunking.py`:

| Chỉ số | Hiện tại | Chỉ tiêu |
| --- | --- | --- |
| Chunk không có nhãn (tài liệu quy trình) | tới 25% | 0% |
| Chunk có nhãn gây nhầm (tài liệu quy trình) | tới 40% | ≤ 2% |
| Chunk gộp ≥2 bước | 9 | 0 |
| Dòng bảng khôi phục được từ PDF thủ tục | xem báo cáo theo từng tài liệu | ≥ 3× tổng hiện tại của corpus |

## Ràng buộc chung

- Không đổi `MAX_EMBEDDABLE_CHUNK_CHARS` hay chính sách 2 000 ký tự: hiện 0 chunk vi phạm, và giới hạn hướng-tới-persistence này là cố ý.
- Việc re-ingest sẽ ghi đè `data/extracted/*.md`; bộ golden gán nhãn section theo **tiêu đề**, nên phải gán nhãn lại các case bị ảnh hưởng trong cùng commit với lần re-ingest, và tuyệt đối không đánh số lại `q-001`…`q-100`.
- Phần company corpus không được thay đổi hành vi ACL của project-document plane. Mọi hạng mục project documents ở workstream riêng phải giữ ACL-first trong SQL và phạm vi một project index (ADR-007 §4, ADR-008 §3).
- Không bao giờ ghi log hay lưu trữ nội dung chunk hoặc nội dung truy vấn trong báo cáo đánh giá.
- Các phase 0–3 kết thúc khi các harness áp dụng cho thay đổi đó xanh: `scripts/diagnose_chunking.py`, `scripts/evaluate_retrieval.py`, và route test hẹp theo `tests/README.md`. Phase Chat-RAG dùng thêm `scripts/evaluate_chat_rag.py`; không yêu cầu judge có key trong CI.

---

## Phase 0 — Dựng nền đo lường

- [x] **0.1 Đưa công cụ chẩn đoán cấu trúc vào repo.** `scripts/diagnose_chunking.py` cùng `evaluations/RETRIEVAL/chunking-diagnostics/baseline-2026-08-20.json`. Chạy offline, không cần API key.
- [ ] **0.2 Làm cho harness đo được runtime, rồi tạo baseline mới.** Trước hết mở rộng `scripts/evaluate_retrieval.py` để đo được embedder và `RerankerAdapter`/model đang được runtime company RAG cấu hình; hiện script chỉ có `hashing`/`gemini` và `--rerank` chỉ dùng `JinaRerankerAdapter`. Sau đó chạy các cấu hình so sánh bằng `uv run python scripts/evaluate_retrieval.py ...` trên corpus 17 tài liệu hiện tại. **Hoàn thành khi:** report production-equivalent ghi `chunk_count: 949`, model/provenance reranker đã áp dụng hay fallback, và các baseline 1.069/36-chunk được chú thích là lịch sử, không phải acceptance evidence.
- [ ] **0.3 Bổ sung probe cho những lỗi mà bộ golden hiện chưa nhìn thấy.** Thêm khoảng 20 case vào `tests/fixtures/rag/retrieval_golden.json` dưới dạng `q-101`…`q-120`, gắn hai giá trị probe mới là `tabular` (phí, thời hạn, số lượng giấy tờ cần nộp, hình thức nộp) và `procedural` ("bước 3 làm gì", "sau khi nộp hồ sơ thì bước tiếp theo"). Lấy section kỳ vọng từ tài liệu ở **cả hai phía** của ranh giới extractor để thay đổi ở Phase 1 nhìn thấy được. **Hoàn thành khi:** `evaluate_retrieval.py` báo cáo hai slice mới; baseline Phase 0 cho thấy rõ mức lỗi của từng slice trước khi đổi extraction.
- [ ] **0.4 Kéo phép đo "trước" của RAGAS lên sớm.** Các task 4.3.1–4.3.3 phải hoàn tất **trước khi** task 1.3 re-ingest ghi đè corpus. Một baseline faithfulness đo sau khi corpus đã đổi thì không tách được phần cải thiện do pipeline với phần cải thiện do prompt của judge, và corpus hiện tại không dựng lại được sau khi re-ingest. **Hoàn thành khi:** báo cáo baseline từ 4.3.3 tồn tại và được dẫn chiếu tại đây.

## Phase 1 — Độ trung thực của extraction (khoản mất mát lớn nhất đo được)

- [ ] **1.1 Phát hiện mất layout và định tuyến vòng qua.** Trong `pdf_inspector.py`, thêm tín hiệu chất lượng theo từng trang: trang nào cho ra Markdown không có dòng bảng nào trong khi bản thân trang có hình học bảng kẻ khung, hoặc cho ra các dòng dạng heading lặp lại nguyên văn ở nơi khác trong tài liệu, thì đánh dấu `needs_layout_extraction`. Đưa các trang đó vào đúng nhánh OCR mà `pages_needing_ocr` đang dùng. **Hoàn thành khi:** sáu PDF `chi-tiet-thu-tuc` hiện chạy `pdf_native` đều bị đánh dấu; các PDF văn xuôi một trang `dang-ky-*` thì không.
- [ ] **1.2 Thiết kế lại chính sách extraction một cách tương thích.** `KnowledgeIngestionSettings` hiện có `EXTRACTION_MODE=adaptive|advance`, trong đó `advance` OCR cả tài liệu. Chốt và migration một chính sách có thể chọn `native` | `layout_first` | `auto` (hoặc tương đương), nêu rõ ánh xạ từ setting cũ và bảo đảm `auto` chỉ OCR trang được đánh dấu. Giữ `mistral_not_configured` là lỗi cứng thay vì âm thầm nạp vào tài liệu hỏng. **Hoàn thành khi:** unit test phủ các mode, migration setting cũ và OCR extractor giả; test không chạm mạng.
- [ ] **1.3 Re-ingest và đo lại.** Chạy `uv run mail-todo-ingest-knowledge --source data/raw --output data/extracted --force`, rồi `uv run python scripts/diagnose_chunking.py`. **Hoàn thành khi:** số dòng bảng và các chỉ số chẩn đoán được báo cáo trước/sau theo từng tài liệu; chunk có nhãn gây nhầm trong tài liệu quy trình ≤ 2%; diff của `data/extracted` được review từng tài liệu một.
- [ ] **1.6 Mở rộng nhận diện cấu trúc DOCX.** Trong `docx_extractor.py`, khớp `Heading N` theo tiền tố (thay vì bảng cứng 1–3), thêm `Title`/`Subtitle`, tên style bản địa hoá, và cấp outline lấy từ `numPr`. Đọc cả run nằm trong hyperlink khi kiểm tra "in đậm toàn phần". **Hoàn thành khi:** một fixture DOCX dùng Heading 4, một SOP đánh số outline và một heading in đậm có hyperlink đều cho ra đúng cấp ATX.

## Phase 2 — Gán nhãn và cấu trúc chunk

- [ ] **2.1 Từ chối heading giả sinh ra từ bảng.** Trong `structure_normalizer.py` / `markdown_chunking.py`, một ứng viên heading lặp lại nguyên văn ở nơi khác trong tài liệu và không mang đánh số cấu trúc thì bị hạ xuống thành nội dung (hoặc gắn làm caption bảng) thay vì mở ra một section. **Hoàn thành khi:** `Trực tiếp` và `Tên giấy tờ Mẫu đơn, tờ khai Số lượng` không còn xuất hiện làm `section`; chunk có nhãn gây nhầm ≤ 2% toàn corpus.
- [ ] **2.2 Gán nhãn cho phần mở đầu.** Các chunk nằm trước heading đầu tiên thừa hưởng tiêu đề tài liệu làm `section` và làm breadcrumb. **Hoàn thành khi:** chunk không nhãn về 0% ở năm tài liệu thủ tục đã nêu, và < 0,5% toàn corpus.
- [ ] **2.3 Cắt chunk theo bước.** Thêm `Bước N` / `Step N` vào `_CLAUSE_START` như một ranh giới khoản, và mang "cuống bước" qua chỗ cắt đúng theo cách `_resume_prefix` đang mang cuống điểm chữ cái. **Hoàn thành khi:** `step_fused_chunks` = 0; mọi chunk chứa nội dung bước đều nêu tên bước của nó; slice probe `procedural` đạt chỉ tiêu.
- [ ] **2.4 Sửa bước nhảy cấp heading.** Chuẩn hoá cấp heading thành đơn điệu theo từng tài liệu trước khi dựng cây, để chuỗi `##` → `#` do OCR suy ra không thể đẩy một section thành con trực tiếp của tiêu đề tài liệu. **Hoàn thành khi:** `heading_level_jumps` = 0 sau lần re-ingest ở Phase 1.
- [ ] **2.5 Làm cho bảng trả lời được.** Với bảng metadata hai cột, tuyến tính hoá từng dòng thành `trường: giá trị` ngay trong nội dung chunk; với bảng rộng, lặp lại **cả caption lẫn tên cột** trên mọi phần bị cắt (hiện đã lặp header, chưa lặp caption). **Hoàn thành khi:** slice probe `tabular` đạt chỉ tiêu; các slice `lexical`/`mixed` không tụt.

## Phase 3 — Chất lượng truy xuất

- [ ] **3.1 Khớp lexical không phụ thuộc dấu.** Đánh chỉ mục thêm một biến thể đã khử dấu song song với token có dấu trong `bm25.py`, và thêm bigram âm tiết tiếng Việt. **Hoàn thành khi:** bản không dấu của 10 truy vấn golden truy ra cùng section; slice `lexical` không tụt.
- [ ] **3.2 Thay cổng kích hoạt semantic đang hard-code.** `features/ai_chat/retrieval_policy.py` chỉ kích hoạt company RAG khi gặp bảy cụm từ nguyên văn, nên câu hỏi về một quy trình không được diễn đạt kiểu "quy định công ty" sẽ không bao giờ truy xuất. Thay bằng phân loại intent, hoặc luôn truy xuất rồi để ngưỡng cắt của reranker quyết định. **Hoàn thành khi:** một bài đánh giá routing cho thấy recall của câu hỏi đáng truy xuất ≥ 0,9 với mức đánh đổi precision nhỏ; làm mới `evaluations/baselines/routing-eval-*.json`.
- [ ] **3.3 Gỡ thiên lệch trong biến đổi truy vấn.** Bỏ các mở rộng hard-code `Quy trình thủ tục …` / `Hướng dẫn quy định …` trong `query_transform.py`; điều kiện hoá HyDE theo chính corpus thay vì theo giọng văn bản quy phạm. **Hoàn thành khi:** slice `semantic` đạt section Hit@1 ≥ 0,75; vẫn nằm trong ngân sách độ trễ.
- [ ] **3.4 Đưa cơ chế mở rộng theo section sang nhánh knowledge.** `HybridProjectDocumentStore` đã ghép lại các chunk anh em cùng section trong phạm vi `_SECTION_HEADROOM`; `HybridSemanticMemory` thì chưa, nên một điều luật dài chỉ đến tay dưới dạng một mảnh. **Hoàn thành khi:** section-level Hit@3 tăng; ngân sách chunk cho mỗi câu trả lời vẫn có chặn trên.
- [ ] **3.5 Ngừng che lỗi thành "không có kết quả".** `hybrid.py:210` ánh xạ mọi exception thành `RetrievalStatus.TIMEOUT`. Phân biệt lỗi nhà cung cấp với việc thực sự không khớp, và ghi log loại lỗi. **Hoàn thành khi:** lỗi embedder được tiêm vào sẽ hiện ra là trạng thái lỗi trong test, không phải "không có kết quả".
- [ ] **3.6 Tinh chỉnh ngưỡng reranker.** Sau khi có chunk từ Phase 1–2, tinh chỉnh lại `min_rerank_score` và `relative_cutoff_ratio` dựa trên bộ golden thay vì để mặc định như hiện nay. **Hoàn thành khi:** tỉ lệ từ chối trên `unanswerable` ≥ 0,90 mà không mất Recall@5.

## Phase 4 — Câu trả lời có dẫn chứng

- [ ] **4.1 Thực thi hợp đồng trích dẫn.** Mọi khẳng định ánh xạ tới `chunk_id` cộng trang/section giải được; câu trả lời trích dẫn một chunk không nằm trong tập đã truy xuất sẽ bị chặn trước khi tới người dùng. **Hoàn thành khi:** tỉ lệ trích dẫn giải được đạt 100% trên bộ golden; một test tiêm lỗi chứng minh cơ chế chặn hoạt động.
- [ ] **4.2 Từ chối trả lời khi không có căn cứ.** Trả lời "không có trong tài liệu được cung cấp" trên các probe `unanswerable` thay vì tự suy diễn. **Hoàn thành khi:** tỉ lệ từ chối ≥ 0,90, không có trích dẫn bịa.

### 4.3 Chấm độ bám dẫn chứng bằng RAGAS

> Hướng dẫn vận hành chi tiết (cài đặt, chạy, hiệu chuẩn tiếng Việt, cách đọc điểm, lỗi thường gặp): [`docs/evaluations/RAGAS.md`](../../evaluations/RAGAS.md). Mục này chỉ ghi các task và tiêu chí hoàn thành.

RAGAS đã được đấu nối một nửa: `scripts/evaluate_chat_rag.py:329` đã có cờ `--ragas`, và `evaluations/CHAT-RAG/README.md` đã có sẵn mục adoption gate. Nhưng package chưa được cài, chưa có baseline nào, và chỗ gọi dùng API legacy không thể tương thích với một phiên bản RAGAS chưa ghim. Các task dưới đây khép lại phần đó, và **chỉ cho phía sinh câu trả lời**.

**Quyết định phạm vi — không dùng LLM để chấm retrieval.** `context_precision` và `context_recall` là bản xấp xỉ bằng LLM của đúng thứ mà `scripts/evaluate_retrieval.py` đã đo chính xác, offline và miễn phí, dựa trên 100 case gán nhãn tay. Giữ harness gán nhãn làm nguồn chân lý cho retrieval; RAGAS chỉ trả lời "câu trả lời sinh ra có được context bảo chứng không, và có đúng trọng tâm câu hỏi không".

**Quyết định model — dùng lại chính nhà cung cấp của dự án, nhưng không bao giờ để một model tự chấm chính mình.**

| Vai trò | Model | Lý do |
| --- | --- | --- |
| LLM chấm điểm | model mạnh nhất trong `OPENROUTER_ALLOWED_MODELS` mà **không phải** model đã sinh ra câu trả lời đang được chấm | Cùng key, cùng nhà cung cấp đã có trong hệ; một model chấm chính output của nó chịu thiên lệch tự ưu ái, và thiên lệch đó lệch về phía **che giấu bịa đặt** — đúng thứ mà metric này sinh ra để bắt. Model cụ thể là cấu hình runtime, không phải dữ liệu commit trong repo. |
| Embedding chấm điểm | Embedder được chọn và ghi trong report; `gemini-embedding-2` qua `GEMINI_EMBEDDING_MODEL` là lựa chọn dự kiến cho evaluator/project documents | `answer_relevancy` chỉ cần độ tương đồng câu-hỏi-với-câu-hỏi. Không được khẳng định đây là embedder company-RAG runtime, vì runtime đó hiện dùng Jina. |
| Không dùng làm judge | `gemini-3.5-flash-lite` (`GEMINI_MODEL`) | Đây là tier throughput để sinh câu trả lời. Faithfulness cần tách mệnh đề rồi suy luận NLI; judge yếu tạo nhiễu nhiều hơn tín hiệu |

Lời gọi judge chạy ở `temperature = 0`, và mọi báo cáo đều ghi lại **cả** model sinh lẫn model chấm.

- [ ] **4.3.1 Cài, ghim phiên bản, và port chỗ gọi.** Chọn một phiên bản RAGAS theo tài liệu chính thức hiện hành, ghim tường minh trong `pyproject.toml`, rồi port `run_ragas()` theo đúng API của phiên bản đó. Code hiện dùng `datasets.Dataset`, import metric mức module và không truyền evaluator tường minh; không được giữ đường legacy với dependency thả nổi. Khởi tạo metric/evaluator với `llm` và `embeddings` tường minh để không rơi về mặc định OpenAI. Bỏ `context_precision` / `context_recall` khỏi danh sách metric theo quyết định phạm vi ở trên; xoá thư mục `.deepeval/` đang không dùng thay vì nuôi hai framework. **Hoàn thành khi:** `--ragas` chạy trọn vẹn trên một fixture với judge giả; có test khẳng định lần chạy sẽ báo lỗi rõ ràng nếu không truyền evaluator cần thiết.
- [ ] **4.3.2 Chuyển prompt của metric sang tiếng Việt và chứng minh nó có nghĩa.** `faithfulness` tách câu trả lời thành các mệnh đề rồi chạy NLI đối chiếu context; prompt và ví dụ few-shot đi kèm có thể là tiếng Anh. Dùng API chuyển ngữ được tài liệu hoá cho **phiên bản đã ghim** để chuyển từng prompt cần thiết, commit prompt đã chuyển ngữ để tái lập được, rồi hiệu chuẩn với khoảng 30 case do người chấm tay. **Hoàn thành khi:** prompt đã chuyển ngữ nằm trong repo; mức đồng thuận với điểm người chấm được báo cáo. Nếu đồng thuận yếu, metric được ghi nhận là **chưa hiệu chuẩn** và **không được** dùng để gate bất cứ thứ gì.
- [ ] **4.3.3 Lấy phép đo "trước" ngay bây giờ, không đợi sau Phase 1–2.** Chạy khoảng 30 case thủ tục qua judge trên corpus hiện tại và lưu báo cáo. Một khi extraction và cách gán nhãn thay đổi, baseline này không dựng lại được, và nó là cách duy nhất để quy phần tăng faithfulness sau này cho các sửa chữa pipeline thay vì cho công sức chỉnh prompt. **Hoàn thành khi:** có một báo cáo `chat-rag-eval.v1` trong `evaluations/CHAT-RAG/baselines/`, mang đủ phiên bản RAGAS, model chấm, model sinh, model embedding, phiên bản dataset, số liệu tổng hợp từng metric, số ca lỗi, và độ trễ retrieval/sinh/chấm tách riêng — tức mọi trường mà adoption gate yêu cầu.
- [ ] **4.3.4 Đẩy điểm sang Langfuse và không đặt vào CI gate.** `evaluate()` nhận `callbacks=`; repo đã chạy Langfuse 3.15. Phát điểm dưới dạng Langfuse score để theo dõi faithfulness theo thời gian thay vì nằm trong một file JSON đơn lẻ. Điểm của judge không tất định và tốn tiền mỗi lần chạy: xếp lịch theo mốc, tuyệt đối không làm bước chặn trong CI, và tuyệt đối không dùng làm bộ lọc runtime cho câu trả lời của người dùng. **Hoàn thành khi:** điểm hiển thị trong Langfuse; `evaluations/dashboard.md` dẫn link lần chạy; CI vẫn xanh khi không có key của evaluator.
- [ ] **4.3.5 Quyết định về quyền riêng tư, ghi lại trước lần chạy đầu tiên trên dữ liệu riêng.** Báo cáo được commit vốn đã loại bỏ nội dung văn bản, nhưng bản thân lời gọi judge **gửi context và câu trả lời tới nhà cung cấp chấm điểm**. Corpus thủ tục hành chính công khai: chấp nhận được. `project_documents` của tenant: cần một quyết định tường minh. **Hoàn thành khi:** quyết định được ghi vào `evaluations/CHAT-RAG/README.md`; chạy judge trên tài liệu tenant khi chưa có quyết định này bị coi là lỗi.

**Chỉ tiêu của 4.3:** faithfulness ≥ 0,95 trên bộ golden, đo bằng một judge tiếng Việt đã hiệu chuẩn và không phải model sinh.

- [ ] **4.4 Probe tổng hợp liên tài liệu.** Thêm các case mà câu trả lời đòi hỏi hai quy trình (ví dụ so sánh phí hoặc thời hạn giữa các tài liệu). **Hoàn thành khi:** slice mới được báo cáo; các ca thất bại được phân loại vào một kế hoạch tiếp theo chứ không im lặng chấp nhận.

## Workstream B — Project documents và hợp đồng citation

Workstream này không thay đổi corpus `data/extracted` và chỉ thực hiện sau khi kiểm tra các ràng buộc OCR deferred, ACL-first và privacy của user documents.

- [ ] **B.1 Sửa lỗi số trang OCR ở nhánh project-document.** Thay `pages = ((1, markdown),)` tại `knowledge_ingestion/project_documents.py:60` bằng `split_markdown_pages(markdown)`, ánh xạ từng `MarkdownPage.page_number` tương ứng. **Hoàn thành khi:** một regression test nạp fixture OCR hai trang và khẳng định các chunk mang cả trang 1 *và* trang 2; `page_count` phản ánh đúng số trang thật.
- [ ] **B.2 Ngừng bịa số trang cho DOCX.** Hoặc suy ra ngắt trang (`w:lastRenderedPageBreak`), hoặc để trích dẫn DOCX chỉ theo section. Không tiếp tục xuất `trang 1`. **Hoàn thành khi:** trích dẫn từ bốn file luật DOCX không còn khẳng định trang 1; hợp đồng citation ghi rõ nguồn nào có số trang.

---

## Rủi ro

- **Phụ thuộc và chi phí OCR.** Ưu tiên layout khiến việc nạp tài liệu phụ thuộc một dịch vụ mạng có phí và chậm hơn. Giảm thiểu: chế độ `auto` chỉ leo thang cho các trang bị đánh dấu; việc nạp vốn diễn ra offline so với thời điểm truy vấn, và manifest đã bỏ qua file không đổi theo digest.
- **Xáo trộn do re-ingest.** Task 1.3 ghi lại corpus, làm đổi số lượng chunk và một số tiêu đề section. Giảm thiểu: nhãn trong bộ golden là tiêu đề chứ không phải chunk id; gán nhãn lại trong cùng commit và chạy lại cả hai harness trước–sau.
- **Đánh lừa chỉ số.** Section-level Hit@1 có thể bị thổi lên bằng cách làm section thô hơn. Giảm thiểu: báo cáo cấu trúc (số chunk trên mỗi section, tỉ lệ nhãn gây nhầm) luôn được công bố kèm mọi điểm số retrieval.
- **Ảnh hưởng ngược tới nhánh chat.** Task 3.2 mở rộng thời điểm truy xuất được kích hoạt, kéo theo thay đổi độ trễ và chi phí token trong AI Chat. Giảm thiểu: chặn bằng các baseline độ trễ sẵn có trong `evaluations/CHAT/latency/baselines/`.
- **Khoá cứng vào một nhà cung cấp extraction theo layout.** Hiện Mistral OCR là đường duy nhất hiểu được layout. Giảm thiểu: giữ extraction sau cổng port sẵn có để thêm nhà cung cấp thứ hai mà không phải đụng vào chunking.
