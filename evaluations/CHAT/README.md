# Chat Routing & Intent Evaluation

Thư mục lưu trữ toàn bộ các bộ đánh giá, tập dữ liệu kiểm thử QA, và baseline kết quả thực tế cho hệ sinh thái AI Chatbot.

---

## 1. Cấu trúc Thư mục

```text
evaluations/CHAT/
├── qa-test/                        # Bộ dữ liệu & Báo cáo QA Intent Tiếng Việt
│   ├── qa-chatbot-intent-dataset.json  # 240 kịch bản Dataset (6 Intents × 4 Paths)
│   ├── HOW-TO-CREATE-QA-TEST.md        # Hướng dẫn mở rộng kịch bản kiểm thử
│   ├── QA-TEST-RESULTS-REPORT.md       # Báo cáo phân tích kết quả 5 trục
│   ├── qa-plan-chatbot-sprint1.md      # Kế hoạch Sprint kiểm thử
│   └── qa-strategy.md                  # Chiến lược ma trận QA
├── baselines/                      # Kết quả JSON đo lường thực tế từ Gemini Live
│   ├── live-real-gemini-eval-full.json # Kết quả thô 240 câu hỏi
│   ├── live-real-gemini-extended.json  # Kết quả thô 12 kịch bản mở rộng
│   └── chat-routing-eval-2026-08-14.json
├── latency/                        # UI switch latency harness
└── ingestion-latency/              # Benchmark độ trễ nạp dữ liệu
```

---

## 2. Các Lệnh Chạy Đánh Giá

* **Chạy 240 kịch bản QA Intent (Offline Mocked):**
  ```bash
  uv run pytest tests/unit/features/ai_chat/test_user_intent_vietnamese.py -m extended
  ```

* **Chạy toàn bộ 267 test QA Intent (Đơn lượt + Mở rộng):**
  ```bash
  uv run pytest tests/unit/features/ai_chat/test_user_intent_*.py -m "extended or not extended"
  ```

* **Chạy đánh giá trực tiếp với Google Gemini Live API (100% Real API CLI):**
  ```bash
  uv run python scripts/evaluate_user_intent_real_llm.py
  uv run python scripts/evaluate_extended_scenarios_real_llm.py
  ```

* **Đánh giá Chat-RAG Grounding:** Xem [`evaluations/CHAT-RAG/`](../CHAT-RAG/).
