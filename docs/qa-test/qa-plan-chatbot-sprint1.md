# Kế hoạch & Kết Quả Kiểm Thử Ý Định Người Dùng Sprint 1 (User Intent Test Plan & Actual Results)

Kế hoạch kiểm thử toàn diện **267 kịch bản** (240 kịch bản Dataset đơn lượt + 27 kịch bản mở rộng: Đa lượt, Tấn công, Trường hợp biên, Input Validation) cho AI Chatbot tiếng Việt.

---

## 1. Phân Bổ 267 Kịch Bản Kiểm Thử

| Nhóm Kiểm thử | Số lượng | Trạng thái Live Run (Gemini Thật) | Trạng thái Unit Test | File kịch bản |
|---|:---:|:---:|:---:|---|
| **1. Single-turn Intent Matrix (6 $\times$ 4 $\times$ 10)** | 240 | 188 Pass / 52 Fail (78.33%) | 240/240 Pass | [`qa-chatbot-intent-dataset.json`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/qa-test/qa-chatbot-intent-dataset.json) |
| **2. Multi-turn Context & Pronoun** | 3 | 3/3 Pass (100%) | 3/3 Pass | [`test_user_intent_multiturn.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/tests/unit/features/ai_chat/test_user_intent_multiturn.py) |
| **3. Adversarial / Prompt Injection** | 3 | 3/3 Pass (100%) | 6/6 Pass | [`test_user_intent_adversarial.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/tests/unit/features/ai_chat/test_user_intent_adversarial.py) |
| **4. Edge Cases (Emoji, Code, Không dấu)** | 6 | 6/6 Pass (100%) | 12/12 Pass | [`test_user_intent_edge_cases.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/tests/unit/features/ai_chat/test_user_intent_edge_cases.py) |
| **5. Input Validation (Rỗng, 4000 chars)** | 15 | 15/15 Pass (100%) | 6/6 Pass | [`test_user_intent_edge_cases.py`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/tests/unit/features/ai_chat/test_user_intent_edge_cases.py) |
| **Tổng cộng** | **267** | **215 Pass / 52 Fail (80.52%)** | **267/267 Pass (100%)** | — |

---

## 2. Chi Tiết Lỗi Thực Tế Từ Google Gemini Thật (Baseline 2026-08-14)

1. **Lỗi Ambiguity (38 ca):** Gemini tự tin trả lời thay vì hỏi lại (`CLARIFY`).
2. **Lỗi Physical Action (10 ca):** Yêu cầu ngoài đời thực (bay đến nhà, bật đèn) bị phân loại thành `action_request`.
3. **Lỗi General Knowledge (4 ca):** Câu hỏi xổ số ngoài lề bị phân loại thành `knowledge_query`.

Toàn bộ phản hồi thật được lưu tại:
- Báo cáo kết quả đầy đủ: [`QA-TEST-RESULTS-REPORT.md`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/qa-test/QA-TEST-RESULTS-REPORT.md)
- Log thô 240 kịch bản: [`docs/evaluations/CHAT/live-real-gemini-eval-full.json`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/evaluations/CHAT/live-real-gemini-eval-full.json)
- Log thô 12 kịch bản mở rộng: [`docs/evaluations/CHAT/live-real-gemini-extended.json`](file:///D:/User/ProjectGithub/hiepnguyenn-99/EMAIL-AGENT-v1/docs/evaluations/CHAT/live-real-gemini-extended.json)
