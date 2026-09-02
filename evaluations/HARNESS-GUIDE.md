# Sổ Tay Vận Hành Khung Đánh Giá (Evaluation Harness Guide)

Tài liệu hướng dẫn thực thi và đọc hiểu kết quả cho toàn bộ 5 bộ harness đánh giá trong thư mục `scripts/`.

---

## 1. Mô Hình Tư Duy Đo Lường Đa Tầng (Mental Model)

Hệ thống Cowork Agent giải quyết bài toán qua các tầng độc lập. Lỗi ở một tầng sẽ hoàn toàn vô hình đối với các tầng khác:

```text
[Input Người Dùng / Email Đến]
      │
      ▼
1. ĐỊNH TUYẾN (Routing)      ──► Classifier chọn đúng luồng?           (evaluate_routing.py / evaluate_chat_routing.py)
      │
      ▼
2. TRUY HỒI (Retrieval)      ──► Tìm kiếm trả về đúng chunks/trang?    (evaluate_retrieval.py)
      │
      ▼
3. TẠO SINH (Generation)     ──► Câu trả lời trung thực, không ảo giác? (evaluate_chat_rag.py --ragas)
      │
      ▼
4. BỘ NHỚ (Memory)           ──► Lưu giữ, cập nhật & cô lập 4 phạm vi?  (evaluate_memory.py)
```

> **Nguyên tắc vàng:** Điểm truy hồi hoàn hảo không chứng minh câu trả lời không bị ảo giác. Điểm định tuyến hoàn hảo không nói lên chất lượng tìm kiếm. Tuyệt đối không trích dẫn số liệu của tầng này để làm bằng chứng cho tầng khác.

---

## 2. Bảng Tra Cứu 5 Bộ Harness & Lệnh Thực Thi

| Bộ Đánh Giá | Script Thực Thi | Đầu Vào (Input) | Đầu Ra Mặc Định (Output) | Lệnh Chạy Mẫu (CLI) |
|---|---|---|---|---|
| **1. RETRIEVAL** | `scripts/evaluate_retrieval.py` | `retrieval_golden.json`<br>`data/extracted/` | `evaluations/RETRIEVAL/baselines/` | `uv run python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid --rerank` |
| **2. CHAT-RAGAS** | `scripts/evaluate_chat_rag.py` | File cục bộ `*.local.json` | `evaluations/CHAT-RAGAS/baselines/` | `uv run python scripts/evaluate_chat_rag.py --input var/eval/chat.local.json --ragas --evaluator-provider google` |
| **3. MEMORIES** | `scripts/evaluate_memory.py` | `evaluations/MEMORIES/probes/` | `evaluations/MEMORIES/baselines/` | `uv run python scripts/evaluate_memory.py`<br>`uv run python scripts/build_memory_evaluation_report.py` |
| **4. EMAIL** | `scripts/evaluate_routing.py`<br>`scripts/evaluate_action_plans.py` | `tests/fixtures/routing/`<br>`evaluations/EMAIL/golden_dataset.json` | `evaluations/EMAIL/runs/` | `uv run python scripts/evaluate_routing.py --dry-run` |
| **5. CHAT** | `scripts/evaluate_chat_routing.py`<br>`e2e/chat-history-latency.spec.ts` | `tests/fixtures/chat_routing/` | `evaluations/CHAT/latency/TRACK.md` | `uv run python scripts/evaluate_chat_routing.py --dry-run` |

---

## 3. Chi Tiết Từng Bộ Harness

### 3.1 `evaluate_retrieval.py` (Truy Hồi Tri Thức Công Ty)
Chạy toàn bộ 100 câu truy vấn mẫu qua pipeline tìm kiếm lai và chấm điểm dựa trên nhãn document ID và section ID:
* **Chỉ số đo lường:** `Hit@1`, `Hit@3`, `MRR`, `Recall@5` cấp văn bản và phân đoạn; phân tích lát cắt câu hỏi `lexical`, `semantic`, `mixed`; tỷ lệ từ chối `abstention_rate`.
* **Lệnh chạy offline (Smoke Test):** `uv run python scripts/evaluate_retrieval.py --dry-run`
* **Lệnh chạy live:** `uv run python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid --rerank`

### 3.2 `evaluate_chat_rag.py` (Chat với PDF / DOCX - Dual-Tier)
Đo lường chất lượng câu trả lời từ tài liệu người dùng tải lên theo kiến trúc 2 tầng:
* **Tầng 1 (Tất định / Offline):** Đo `Hit@1`, `Hit@5`, `MRR` theo trang; kiểm tra tính hợp lệ của trích dẫn (`cited_pages ⊆ retrieved_pages`); đo độ trễ 3 chặng độc lập (`retrieval`, `generation`, `evaluator`).
* **Tầng 2 (RAGAS LLM Judge):** `--ragas` đo `faithfulness` ($\ge 0.95$) và `answer_relevancy` ($\ge 0.85$). Bắt buộc $\text{judge} \neq \text{generator}$ và `temperature = 0`.
* **Lệnh chạy:** `uv run python scripts/evaluate_chat_rag.py --input var/eval/chat.local.json --ragas --evaluator-provider google`

### 3.3 `evaluate_memory.py` (Bộ Nhớ AI Chat Agent - 4 Scopes)
Kiểm thử năng lực ghi nhớ, cập nhật, từ chối và cô lập thông tin qua 4 phạm vi bộ nhớ (Short-term, Long-term, Episodic, Semantic):
* **Probes:** `evaluations/MEMORIES/probes/v3_four_scopes_hard.json`
* **Lệnh chạy:** `uv run python scripts/evaluate_memory.py` và xuất báo cáo qua `uv run python scripts/build_memory_evaluation_report.py`.

### 3.4 `evaluate_routing.py` & `evaluate_chat_routing.py` (Phân Loại Ý Định & Định Tuyến)
Đánh giá độ chính xác của bộ phân loại ý định email (`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`) và phân loại ý định chat.
* **Lệnh chạy offline:** `uv run python scripts/evaluate_routing.py --dry-run`

---

## 4. Các Quy Định Bắt Buộc

1. **Báo cáo chỉ lưu Metadata:** Mọi file commit trong `evaluations/` tuyệt đối không chứa văn bản câu hỏi, câu trả lời, email body hay đoạn trích tài liệu.
2. **Hashing không đại diện cho Semantic:** `--embedder hashing` chỉ dùng kiểm thử kỹ thuật offline, không dùng để ra quyết định kiến trúc.
3. **Đồng nhất quy mô Corpus:** Không so sánh 2 bản báo cáo chạy trên số lượng tài liệu/chunk khác nhau.
4. **Tách biệt mô hình Judge:** Luôn bảo đảm $\text{model\_judge} \neq \text{model\_generator}$ trong các bài đo dùng LLM làm giám khảo.
