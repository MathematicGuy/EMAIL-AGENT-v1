# Nghiên Cứu & Đánh Giá Đối Sánh: Đặc Tả và Hiện Thực RAGAS (Grounding Review)

> **Tài liệu tham chiếu:** [`docs/evaluations/RAGAS.md`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/docs/evaluations/RAGAS.md) (Commit `2f3ba24ea`), [`docs/superpowers/plans/2026-08-20-rag-procedure-document-quality.md`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/docs/superpowers/plans/2026-08-20-rag-procedure-document-quality.md) (§ 4.3), [`tasks/specs/SPEC-chat-ragas-evaluation.md`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/tasks/specs/SPEC-chat-ragas-evaluation.md) (Commit `3f4f68a`), [`scripts/evaluate_chat_rag.py`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/scripts/evaluate_chat_rag.py).  
> **Mục đích:** Tổng hợp kết quả nghiên cứu độc lập từ mã nguồn gốc, đối chiếu tài liệu RAGAS của đồng nghiệp (TungChill) với bản đặc tả mới và hiện trạng mã nguồn thực tế để đưa ra kết luận kiến trúc và kế hoạch chuẩn hóa.

---

## 1. Nguồn Gốc & Danh Mục Tài Liệu Gốc (Primary Sources)

1. **[`docs/evaluations/RAGAS.md`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/docs/evaluations/RAGAS.md)** (Dòng 1–242, Commit `2f3ba24ea7f3f57499f35d13b441a39585fbfb29`):  
   - Hướng dẫn vận hành RAGAS thực tế do đồng nghiệp xây dựng.
   - Nguyên tắc cốt lõi: RAGAS chỉ lấp khoảng trống đo lường **phía sinh câu trả lời** (`faithfulness`, `answer_relevancy`), không dùng LLM để đo lại retrieval đã có nhãn người.
2. **[`docs/superpowers/plans/2026-08-20-rag-procedure-document-quality.md`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/docs/superpowers/plans/2026-08-20-rag-procedure-document-quality.md)** (§ 0.4 Dòng 80, § 4.3 Dòng 111–138):  
   - Kế hoạch tổng thể về chất lượng RAG; yêu cầu đo baseline RAGAS **trước khi** re-ingest corpus (Task 0.4 / 4.3.3), cô lập mô hình giám khảo, và đẩy điểm sang Langfuse.
3. **[`tasks/specs/SPEC-chat-ragas-evaluation.md`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/tasks/specs/SPEC-chat-ragas-evaluation.md)** (Dòng 1–425, Commit `3f4f68a`):  
   - Bản đặc tả kỹ thuật cho Chat với PDF/DOCX (`CHAT-RAGAS`), kiến trúc 2 tầng (Tier 1 tất định + Tier 2 LLM judge), tuy nhiên đưa vào cả 5 metric RAGAS.
4. **[`scripts/evaluate_chat_rag.py`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/scripts/evaluate_chat_rag.py)** (Dòng 1–352):  
   - Mã nguồn thực thi CLI hiện tại: tính toán Tier 1 (`_case_metrics`) và hàm `run_ragas()`.
5. **[`tests/unit/scripts/test_evaluate_chat_rag.py`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/tests/unit/scripts/test_evaluate_chat_rag.py)** (Dòng 1–160):  
   - Unit test kiểm tra tính toán và ranh giới bảo mật (`_assert_no_local_only_fields`).
6. **[`scripts/evaluate_retrieval.py`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/scripts/evaluate_retrieval.py)** (Dòng 1–250):  
   - Bộ đánh giá retrieval chính xác dựa trên 100 câu hỏi gán nhãn tay (`retrieval_golden.json`).
7. **[`src/cowork_agent/config.py`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/src/cowork_agent/config.py)**:  
   - Định nghĩa cấu hình: `GeminiSettings` (L471), `GeminiEmbeddingSettings` (L536), `MistralSettings` (L739).
8. **[`pyproject.toml`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/pyproject.toml)** (Dòng 11–48):  
   - Danh sách dependencies: xác nhận `ragas` và `datasets` hiện chưa được ghim/cài đặt mặc định.

---

## 2. Phân Tích & Xác Thực Chi Tiết Theo Các Trục Kỹ Thuật

### Trục 1: Phạm Vi & Lựa Chọn Bộ Chỉ Số (Metric Scope)
*Tại sao tài liệu của đồng nghiệp (`RAGAS.md`) chỉ giữ 2 metric (`faithfulness`, `answer_relevancy`) và loại bỏ `context_precision`, `context_recall`?*

- **Cơ sở thực tế trong codebase:**  
  Trong [`scripts/evaluate_chat_rag.py:200-227`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/scripts/evaluate_chat_rag.py#L200-L227), **Tầng 1 (Deterministic)** đã tính sẵn:
  - `Hit@1`, `Hit@5`, `MRR`, `Recall@5` dựa trên `expected_document_ids` vs `retrieved_document_ids`.
  - `Citation Linkage Valid Rate` (`cited_ids ⊆ retrieved_ids`).
  - `Abstention Accuracy` (đo khả năng từ chối trả lời ngoài phạm vi).
- Ngoài ra, [`scripts/evaluate_retrieval.py`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/scripts/evaluate_retrieval.py) đã đo độ chính xác truy hồi cấp document và section trên 100 case chuẩn, phân rã theo slice `lexical`, `mixed`, `semantic` hoàn toàn miễn phí, offline và tất định.
- **Lập luận của `RAGAS.md` (§ 1.2, L29–31):**  
  > *"Thay nhãn người bằng phán đoán máy ở chỗ đã có nhãn người là đi lùi: trả tiền cho LLM để nó đoán lại thứ mình đã biết chắc, rồi nhận về con số dao động giữa các lần chạy. Harness gán nhãn là nguồn chân lý cho retrieval."*
- **Kết luận:** Tài liệu của đồng nghiệp **chặt chẽ, thực tế và tối ưu chi phí hơn rất nhiều**. RAGAS chỉ nên phụ trách **bảo đảm tính trung thực không ảo giác (`faithfulness`)** và **độ tập trung câu hỏi (`answer_relevancy`)** của câu trả lời đã sinh.

---

### Trục 2: Mô Hình Giám Khảo & Tránh Bẫy Thiên Lệch (Evaluator LLM & Biases)
*Quy tắc chọn mô hình judge, chống thiên lệch tự ưu ái và bẫy fallback OpenAI ngầm.*

1. **Chống thiên lệch tự ưu ái (Self-Preference Bias, `RAGAS.md` § 2.1, L45–52):**
   - Mô hình sinh câu trả lời **tuyệt đối không được làm giám khảo chấm điểm cho chính mình**. Mô hình luôn có xu hướng tự đánh giá cao output của mình, dẫn đến việc **che giấu ảo giác** — triệt tiêu mục đích của RAGAS.
   - Cấm dùng `gemini-3.5-flash-lite` (`GEMINI_MODEL`) làm judge vì mô hình tier throughput không đủ khả năng phân tách mệnh đề nguyên tử và suy luận NLI logic.
   - Phải dùng model mạnh hơn (ví dụ: `mistral-large-latest` hoặc model chỉ định từ `OPENROUTER_ALLOWED_MODELS`).
2. **Cấu hình bắt buộc (`RAGAS.md` § 2.2, L53–57):**
   - `temperature = 0` khi gọi judge.
   - Phải truyền `llm=` và `embeddings=` **tường minh** vào `evaluate()`.
3. **Phát hiện lỗi trong mã nguồn hiện tại ([`scripts/evaluate_chat_rag.py:305`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/scripts/evaluate_chat_rag.py#L305)):**
   ```python
   result = evaluate(
       dataset=Dataset.from_list(records),
       metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
   )
   ```
   Chỗ gọi hiện tại không truyền `llm` hay `embeddings`, sẽ khiến RAGAS ngầm khởi tạo OpenAI SDK (`ChatOpenAI`), gây phát sinh chi phí ngoài ý muốn và gửi dữ liệu sang OpenAI trái phép. Cần refactor để inject tường minh từ `cowork_agent.config`.

---

### Trục 3: Hiệu Chuẩn Tiếng Việt (Vietnamese Language Calibration)
*Quy trình hiệu chuẩn prompt và đối chiếu người chấm.*

- **Quy định trong `RAGAS.md` (§ 5, L142–163):**
  1. **Chuyển ngữ Prompt:** RAGAS mặc định dùng prompt tiếng Anh. Cần dịch các prompt trích xuất mệnh đề và prompt NLI sang tiếng Việt theo API của phiên bản RAGAS đã ghim.
  2. **Commit Prompt Vào Repo:** Prompt đã dịch phải được commit để đảm bảo tính tái lập (reproducibility), không dịch động lúc runtime.
  3. **Hiệu Chuẩn Với Người Chấm:** Chấm tay **30 case mẫu** (bám dẫn chứng: Có / Không) và so sánh với điểm judge.
  4. **Cơ Chế Bảo Vệ:** Nếu mức độ đồng thuận thấp, metric phải được ghi nhận là **chưa hiệu chuẩn** và **tuyệt đối không được dùng làm điều kiện gate**.
  5. **Chỉ Tiêu:** `faithfulness ≥ 0.95` trên tập golden.

---

### Trục 4: Ranh Giới Bảo Mật & Hợp Đồng Dữ Liệu (Privacy & Data Contracts)

- **Hợp đồng dữ liệu 2 tầng (`RAGAS.md` § 4.1 & `test_evaluate_chat_rag.py`):**
  - **Dữ liệu đầu vào (`*.local.json`):** Chứa văn bản thật (`question`, `answer`, `contexts`, `reference_answer`), nằm ngoài Git.
  - **Báo cáo baseline (`evaluations/CHAT-RAGAS/baselines/chat-rag-eval-*.json`):** Chỉ chứa metadata (Case ID, Document ID, điểm số, phân vị độ trễ).
  - Được kiểm chứng tự động bằng đệ quy trong test suite ([`test_evaluate_chat_rag.py:13-21`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/tests/unit/scripts/test_evaluate_chat_rag.py#L13-L21)): `_assert_no_local_only_fields()`.
- **Ranh giới tài liệu Tenant (`RAGAS.md` § 8, L203–211):**
  - Dữ liệu quy trình công khai (`data/extracted`): Được phép gửi tới API evaluator.
  - Dữ liệu người dùng tải lên (`project_documents`): Việc gửi nội dung tới LLM judge bên thứ ba phải có quyết định chính sách rõ ràng ghi nhận tại `evaluations/CHAT-RAGAS/README.md`.

---

### Trục 5: Vận Hành, Langfuse & Thời Điểm Đo Baseline

- **Tích Hợp Langfuse (`RAGAS.md` § 7.1 & Plan § 4.3.4):**
  - `evaluate()` hỗ trợ tham số `callbacks=`. Repo đã có sẵn `langfuse>=3,<4` trong [`pyproject.toml:20`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/pyproject.toml#L20). Đẩy điểm số RAGAS dưới dạng Langfuse score theo thời gian thay vì lưu tĩnh.
- **Không Đặt Vào CI Gate (`RAGAS.md` § 7.2):**
  - Đánh giá LLM judge tốn chi phí và không tất định. Chỉ chạy theo mốc (release / phase). CI thông thường luôn xanh mà không cần API key của evaluator.
- **Thời Điểm Lấy Baseline ("Trước" Đo Lường, Plan § 0.4 & § 4.3.3):**
  - Phải chạy đo baseline faithfulness trên 30 case quy trình **trước khi** chạy Task 1.3 re-ingest corpus. Sau khi cấu trúc chunk thay đổi, baseline cũ không thể tái lập.

---

## 3. Bảng So Sánh Tổng Hợp (Divergence Matrix)

| Tiêu Chí | `docs/evaluations/RAGAS.md` (TungChill / `2f3ba24`) | `SPEC-chat-ragas-evaluation.md` (`3f4f68a`) | Hiện Trạng Code (`evaluate_chat_rag.py`) | Trạng Thái Mục Tiêu Đề Xuất |
|---|---|---|---|---|
| **Phạm vi chỉ số** | Chỉ `faithfulness` + `answer_relevancy` | 5 chỉ số (cả `context_precision/recall`) | 4 chỉ số (cả `context_precision/recall`) | **Chỉ dùng `faithfulness` + `answer_relevancy`**. Tầng 1 đã bao phủ retrieval. |
| **Mô hình Giám khảo** | Model mạnh, cấm `gemini-flash-lite`, $\text{judge} \neq \text{generator}$ | `gemini-2.0-flash` hoặc `mistral-large` | Chưa inject (bẫy OpenAI ngầm) | **Inject tường minh qua `config.py`**, kiểm tra $\text{judge} \neq \text{generator}$, `temperature=0`. |
| **Mô hình Embedding** | `gemini-embedding-2` (`GEMINI_EMBEDDING_MODEL`) | `gemini-embedding-2` hoặc `mistral-embed` | Chưa inject (bẫy OpenAI ngầm) | **Inject `GeminiEmbeddingSettings`** cho evaluator. |
| **Hiệu chuẩn tiếng Việt** | Bắt buộc: dịch prompt, commit vào repo, 30 case kiểm tra người chấm | Chưa đề cập chi tiết | Chưa thực hiện | **Tạo `evaluations/CHAT-RAGAS/prompts/`** lưu prompt tiếng Việt chuẩn hóa. |
| **Ranh giới dữ liệu** | Input local-only, Report metadata-only | Input local-only, Report metadata-only | Đã hiện thực & có unit test | **Giữ nguyên ranh giới bảo mật nghiêm ngặt**. |
| **Langfuse Tracing** | Bắt buộc qua `callbacks=` | Chưa tích hợp | Chưa gắn callback | **Gắn `LangfuseCallbackHandler`** vào `evaluate()`. |
| **CI Gating** | Không chặn CI, chạy theo mốc phát hành | Tầng 1 chạy CI, RAGAS chạy thủ công | Đang theo mô hình này | **Duy trì: Tầng 1 trong CI, Tầng 2 theo mốc**. |

---

## 4. Kế Hoạch Chuẩn Hóa Mã Nguồn & Tài Liệu

1. **Ghim Dependency (`pyproject.toml`):**
   Thêm group tùy chọn `eval`:
   ```toml
   [project.optional-dependencies]
   eval = [
     "ragas>=0.2.8,<0.5.0",
     "datasets>=2.19.0",
     "langchain-google-genai>=2.0.0",
     "langchain-mistralai>=0.2.0",
   ]
   ```
2. **Refactor `run_ragas()` trong [`scripts/evaluate_chat_rag.py`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/scripts/evaluate_chat_rag.py):**
   - Loại bỏ `context_precision` và `context_recall`.
   - Nạp `evaluator_llm` và `evaluator_embeddings` từ `cowork_agent.config` (`GeminiSettings`, `MistralSettings`, `GeminiEmbeddingSettings`).
   - Khẳng định $\text{evaluator\_model} \neq \text{generator\_model}$.
   - Thiết lập `temperature=0` và tích hợp callback Langfuse.
3. **Đồng bộ hóa [`tasks/specs/SPEC-chat-ragas-evaluation.md`](file:///C:/Users/PC/.codex/worktrees/968e/EMAIL-AGENT-v1/tasks/specs/SPEC-chat-ragas-evaluation.md):**
   - Cập nhật tài liệu spec để phản ánh chính xác phạm vi tinh gọn (2 metric thế mạnh của RAGAS) và quy tắc hiệu chuẩn tiếng Việt từ `docs/evaluations/RAGAS.md`.
