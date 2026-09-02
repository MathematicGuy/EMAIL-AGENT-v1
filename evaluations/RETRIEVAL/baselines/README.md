# Báo Cáo Baseline Truy Hồi (`evaluations/RETRIEVAL/baselines/`)

Thư mục này lưu trữ các file báo cáo JSON đo lường chất lượng truy hồi được tạo bởi [`scripts/evaluate_retrieval.py`](../../../scripts/evaluate_retrieval.py).

---

## 1. Định Dạng Đặt Tên File

* **Quy ước:** `retrieval-eval-YYYY-MM-DD-<embedder>-<retriever>.json`
* **Ví dụ:** `retrieval-eval-2026-08-08-gemini-hybrid-rerank.json`

---

## 2. Quy Định So Sánh & Hợp Lệ Dữ Liệu

1. **Quy mô Corpus:** Chỉ so sánh các báo cáo có cùng số lượng document và chunk trong trường `corpus` (ví dụ: cùng trên tập 17 tài liệu / 949 chunks hiện tại).
2. **Loại bằng chứng:**
   * `hashing`: Đánh giá kỹ thuật offline / smoke test cơ chế.
   * `gemini` / `mistral`: Đánh giá semantic thực tế.
3. **Bảo mật:** Báo cáo chỉ chứa case ID, document ID, điểm số và độ trễ — tuyệt đối không chứa văn bản thô.
