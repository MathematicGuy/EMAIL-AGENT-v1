# Không Gian Đánh Giá Truy Hồi (RETRIEVAL)

> **Tài liệu tham chiếu:** [`docs/evaluations/RETRIEVAL/EMAIL-RAG-STATUS.md`](./EMAIL-RAG-STATUS.md) & [`docs/evaluations/RETRIEVAL/RETRIEVAL-EVALUATION-STATUS.md`](./RETRIEVAL-EVALUATION-STATUS.md)  
> **Kịch bản thực thi:** [`scripts/evaluate_retrieval.py`](../../scripts/evaluate_retrieval.py)  
> **Không gian lưu trữ baseline:** [`evaluations/RETRIEVAL/baselines/`](./baselines/)

Thư mục này quản lý toàn bộ quy trình đo lường chất lượng truy hồi tri thức công ty (Email-RAG và Semantic Chat) trên kho tài liệu nội bộ (`data/extracted/*.md`).

---

## 1. Cơ Chế Đo Lường (Harness Mechanics)

Kịch bản [`scripts/evaluate_retrieval.py`](../../scripts/evaluate_retrieval.py) chạy 100 câu truy vấn mẫu đã được gán nhãn thủ công (`tests/fixtures/rag/retrieval_golden.json`) qua tầng truy hồi lai (Dense Vector + BM25 Lexical + Turbovec RRF + Reranker) và tính toán:

* **Document-level & Section-level Metrics:** `Hit@1`, `Hit@3`, `MRR`, `Recall@5`.
* **Phân tích theo lát cắt (Probe Slices):** Đo riêng biệt trên 3 nhóm câu hỏi:
  * `lexical`: Câu hỏi khớp từ khóa chính xác.
  * `semantic`: Câu hỏi diễn đạt lại ngữ nghĩa, không trùng từ khóa.
  * `mixed`: Câu hỏi kết hợp cả ngữ nghĩa và từ khóa chuyên ngành.
* **Tỷ lệ từ chối (Abstention Rate):** Khả năng chủ động không trả về đoạn trích sai đối với các câu hỏi ngoài phạm vi tài liệu.
* **Độ trễ truy hồi:** Phân vị thời gian thực thi p50 và p95 (ms).

---

## 2. Các Lệnh Thực Thi

```powershell
# 1. Chạy smoke test offline (không tốn API key, dùng hashing embedder)
uv run python scripts/evaluate_retrieval.py --dry-run

# 2. Chạy đánh giá semantic đầy đủ với mô hình Gemini & Hybrid Retriever
uv run python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid --rerank

# 3. Lưu báo cáo baseline trực tiếp vào thư mục baselines/
uv run python scripts/evaluate_retrieval.py --embedder gemini --retriever hybrid --rerank --output evaluations/RETRIEVAL/baselines/retrieval-eval-2026-08-22-gemini-hybrid-rerank.json
```

---

## 3. Quy Tắc So Sánh Baseline

1. **Chỉ so sánh khi cùng quy mô Corpus:** Không so sánh 2 bản báo cáo có số lượng chunk khác nhau (ví dụ: bản 36 chunks cũ không thể so sánh với bản 949 chunks hiện tại).
2. **Hashing không phải là bằng chứng Semantic:** Kết quả `--embedder hashing` chỉ chứng minh kịch bản chạy đúng kỹ thuật; không dùng điểm số hashing để kết luận chất lượng tìm kiếm thực tế.
3. **Bảo mật tuyệt đối:** File báo cáo JSON chỉ lưu `case_id`, `document_id`, điểm số và thời gian thực thi — tuyệt đối không lưu văn bản câu hỏi hay đoạn trích.
