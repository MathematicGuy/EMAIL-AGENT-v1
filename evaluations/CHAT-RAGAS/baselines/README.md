# Báo Cáo Baseline Chat-RAGAS (`chat-rag-eval.v1`)

Thư mục này lưu trữ các file báo cáo baseline chính thức đã được lọc sạch dữ liệu nhạy cảm.

---

## 1. Quy Định Lưu Trữ & Đặt Tên File

* **Quy ước đặt tên:** `chat-rag-eval-YYYY-MM-DD-<dataset>-<model>.json`
  * Ví dụ: `chat-rag-eval-2026-08-22-synthetic-gemini-2.0-flash.json`
* **Ranh giới bảo mật:** Mọi file commit tại đây **tuyệt đối không chứa văn bản thô** của câu hỏi, câu trả lời, hay các đoạn trích từ tài liệu PDF/DOCX (được bảo vệ bởi unit test đệ quy `_assert_no_local_only_fields`).

---

## 2. Cấu Trúc Báo Cáo Metadata Chuẩn

```json
{
  "schema_version": "chat-rag-eval.v1",
  "generated_at": "2026-08-22T05:00:00Z",
  "dataset_version": "local-chat-ragas-v1",
  "provider": "gemini",
  "model": "gemini-2.0-flash",
  "evaluator_provider": "mistral",
  "evaluator_model": "mistral-large-latest",
  "case_count": 40,
  "metrics": {
    "retrieval": {
      "labeled_case_count": 35,
      "hit_at_1": 0.9143,
      "hit_at_5": 1.0,
      "mrr": 0.9429,
      "recall_at_5": 0.9714
    },
    "citation_linkage": {
      "case_count": 40,
      "valid_rate": 1.0
    },
    "abstention": {
      "labeled_case_count": 5,
      "accuracy": 1.0,
      "false_abstention_count": 0
    },
    "ragas": {
      "evaluated_case_count": 40,
      "faithfulness": 0.9625,
      "answer_relevancy": 0.9110
    },
    "latency_ms": {
      "retrieval": {"p50": 22, "p95": 48},
      "generation": {"p50": 310, "p95": 620},
      "evaluator": {"p50": 480, "p95": 910}
    }
  },
  "per_case": [
    {
      "case_id": "chat-case-001",
      "expected_document_count": 1,
      "retrieved_document_count": 2,
      "citation_document_count": 1,
      "hit_at_1": true,
      "hit_at_5": true,
      "reciprocal_rank": 1.0,
      "recall_at_5": 1.0,
      "citation_id_valid": true,
      "should_abstain": false,
      "abstained": false,
      "ragas_scores": {
        "faithfulness": 1.0,
        "answer_relevancy": 0.92
      },
      "latency_ms": {
        "retrieval": 25,
        "generation": 340,
        "evaluator": 520
      }
    }
  ]
}
```
