# Không Gian Đánh Giá Hệ Thống (Evaluation Workspace)

Đây là không gian lưu trữ duy nhất trong repository dành cho các báo cáo đánh giá, chỉ số metadata và sổ tay chất lượng của hệ thống Cowork Agent.

> **Sổ tay hướng dẫn vận hành chi tiết:** [Evaluation Harness Guide](./HARNESS-GUIDE.md)

---

## 1. Bản Đồ 5 Bộ Đánh Giá (Evaluation Suites Matrix)

| Bộ Đánh Giá | Phạm Vi Đo Lường | Script Thực Thi | Không Gian Lưu Trữ & Báo Cáo |
|---|---|---|---|
| **[RETRIEVAL](./RETRIEVAL/)** | Chất lượng tìm kiếm lai (Dense + BM25 + Turbovec + Reranker) trên tri thức công ty | `scripts/evaluate_retrieval.py` | [`RETRIEVAL/baselines/`](./RETRIEVAL/baselines/) |
| **[CHAT-RAGAS](./CHAT-RAGAS/)** | Độ trung thực (Faithfulness) và bám sát câu hỏi (Relevancy) khi Chat với tài liệu PDF/DOCX | `scripts/evaluate_chat_rag.py` | [`CHAT-RAGAS/baselines/`](./CHAT-RAGAS/baselines/) |
| **[MEMORIES](./MEMORIES/)** | 4 phạm vi bộ nhớ (Short-term, Long-term, Episodic, Semantic): recall, update, restraint, isolation | `scripts/evaluate_memory.py` | [`MEMORIES/reports/`](./MEMORIES/reports/)<br>[`MEMORIES/baselines/`](./MEMORIES/baselines/) |
| **[EMAIL](./EMAIL/)** | Phân loại luồng email (`NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG`) & trích xuất kế hoạch | `scripts/evaluate_routing.py`<br>`scripts/evaluate_action_plans.py` | [`EMAIL/runs/`](./EMAIL/runs/)<br>[`EMAIL/golden_dataset.json`](./EMAIL/golden_dataset.json) |
| **[CHAT](./CHAT/)** | Phân loại ý định chat & đo độ trễ chuyển đổi phiên giao diện người dùng | `scripts/evaluate_chat_routing.py`<br>`e2e/chat-history-latency.spec.ts` | [`CHAT/latency/TRACK.md`](./CHAT/latency/TRACK.md) |

---

## 2. Bốn Nguyên Tắc Bất Di Bất Dịch (Non-Negotiable Rules)

1. **Báo cáo chỉ lưu trữ Metadata:** Tuyệt đối không commit nội dung email thô, tin nhắn chat, trích đoạn văn bản, prompt, hay câu trả lời của mô hình vào Git. Mọi file commit phải được lọc sạch 100% dữ liệu nhạy cảm.
2. **Hashing chỉ là kiểm thử cơ chế:** Chạy `--embedder hashing` hoặc `--dry-run` chỉ để xác thực tính toàn vẹn của kịch bản chạy offline. Không dùng điểm số hashing để đưa ra quyết định kiến trúc hay lựa chọn retriever.
3. **Chỉ so sánh khi đồng nhất kích thước dữ liệu:** Hai báo cáo chỉ có giá trị so sánh A/B khi có cùng số lượng tài liệu/chunk và cùng phiên bản bộ câu hỏi kiểm định (probe set).
4. **Tách biệt mô hình giám khảo:** Trong mọi đánh giá LLM-as-judge, mô hình judge phải độc lập với mô hình sinh ($\text{model\_judge} \neq \text{model\_generator}$) để loại bỏ thiên lệch tự ưu ái (Self-Preference Bias).
