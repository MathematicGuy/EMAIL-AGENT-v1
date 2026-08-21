# Báo cáo Đánh giá & Tối ưu Toàn bộ Test Suite (Test Suite Audit & Optimization Report)

**Phạm vi:** Đã rà soát và tối ưu trên toàn bộ 3 tầng: `core`, `extended`, `live` — *đã tách rời benchmark LLM thật `scripts/evaluate_user_intent_real_llm.py`*.

---

## 1. Tổng quan phân bổ sau khi tối giản

| Tầng kiểm thử | Số lượng test | Thời gian chạy | Trạng thái mặc định | Mục đích & Phạm vi |
|---|:---:|:---:|:---:|---|
| **Core Suite** | **536 tests** | **~26 s** | **Selected** (chạy mỗi commit) | Toàn bộ invariant nghiệp vụ, bảo mật, contracts (Fakes only, 0 network). |
| **Extended Suite** | **968 tests** *(giảm từ 1.246)* | **~23 s** | **Deselected** (chạy `-m extended`) | PostgreSQL persistence thật, MemEval harness, offline benchmarks. |
| **Live Tier** | **27 tests** | Tùy network | **Deselected** (chạy `-m live`) | Subprocess FastAPI thật, Gmail OAuth live, smoke test môi trường. |

---

## 2. Các hành động đã thực hiện

### ✅ 1. Bỏ toàn bộ Mock Intent & Retrieval Dataset Tests (278 tests)
- **Đã xóa 5 file test mock dataset**:
  1. `tests/unit/features/ai_chat/test_user_intent_vietnamese.py`
  2. `tests/unit/features/ai_chat/test_user_intent_adversarial.py`
  3. `tests/unit/features/ai_chat/test_user_intent_edge_cases.py`
  4. `tests/unit/features/ai_chat/test_user_intent_multiturn.py`
  5. `tests/integration/email_action_plan/test_rag_retrieval_golden.py`
- **Lý do**: Các test này chỉ replay dataset giả lập qua `MockRoutingService`. Đánh giá chất lượng intent thực tế đã được tách riêng và thực thi độc lập với Gemini LLM thật qua CLI script `scripts/evaluate_user_intent_real_llm.py`.
- **Kết quả**: Tầng `extended` giảm từ 1.246 tests xuống **968 tests**, thời gian chạy giảm từ 31s xuống **23s**.

### ✅ 2. Tối ưu thời gian chờ `asyncio.sleep` trong Unit Tests
- **Đã áp dụng**: Thêm autouse fixture mock `asyncio.sleep` trong `tests/unit/integrations/rag/test_embeddings.py`.
- **Kết quả**: Thời gian chạy `test_embeddings.py` giảm từ **10.04s xuống 0.02s**, giữ nguyên 100% logic assert thuật toán xoay API key.

### ✅ 3. Dọn dẹp Deprecation `InRepoSemanticMemory`
- **Đã áp dụng**:
  - `tests/unit/integrations/rag/test_hybrid.py`: Tiêm `dense=TurbovecSemanticMemory(...)`.
  - `tests/integration/email_action_plan/test_workflow.py`: Nâng cấp workflow test sang `TurbovecSemanticMemory`.
  - `tests/unit/integrations/rag/test_advanced_retrieval.py` & `test_query_guard.py`: Dùng `TurbovecSemanticMemory`.
- **Kết quả**: Xóa sạch toàn bộ `DeprecationWarning` liên quan đến Semantic Memory cũ.

### ✅ 4. Thống nhất URL mặc định trong Persistence Tests
- **Đã áp dụng**: Đặt `DEFAULT_PG_TEST_URL` trong `tests/integration/persistence/pg_probe.py` và áp dụng cho:
  - `test_chat_session_repository.py`
  - `test_identity_repositories.py`
  - `test_project_document_repository.py`
- **Kết quả**: Loại bỏ tình trạng 4 module test Postgres bị skip ngầm khi Docker Postgres đang hoạt động.

### ✅ 5. Chuẩn hóa Bộ Quy tắc Kiểm thử (Loại bỏ Rule #10 trong `tests/README.md`)
- **Đã áp dụng**: Loại bỏ Rule #10 ("Use TurbovecSemanticMemory for RAG tests") khỏi cẩm nang 9 nguyên tắc cốt lõi trong `tests/README.md §4`.
- **Lý do loại bỏ**:
  1. *Là chi tiết triển khai tạm thời, không phải nguyên lý kiểm thử:* 9 quy tắc còn lại là các nguyên lý kiến trúc bền vững (tốc độ, cô lập I/O, phân tầng, tài nguyên). Rule #10 chỉ là ghi chú migration cho một class cụ thể.
  2. *Tránh rác tài liệu (Doc Rot):* Khi class cũ `InRepoSemanticMemory` bị gỡ bỏ hoàn toàn khỏi codebase, rule này sẽ trở nên thừa thãi.
  3. *Đã có công cụ tự động kiểm soát:* Linter, Mypy và `DeprecationWarning` tại runtime đã tự động bắt lỗi khi dùng sai class, không cần gánh thêm một điều luật thủ công vào tài liệu.

---

## 3. Kết quả xác thực hệ thống

```bash
# Core suite
uv run pytest -q
# 536 passed, 9 skipped in 25.99s

# Extended suite (đã tinh gọn)
uv run pytest -m extended -q
# 968 passed, 10 skipped in 23.68s

# Linting & Types
uv run ruff check .
# All checks passed!

uv run mypy src
# Success: no issues found in 157 source files
```

---

## 4. Đính chính sau code review (post-review correction)

Bản kiểm toán ở trên đã đánh dấu `extended` cho 89/158 module test, trong đó có
**46 module là unit test thuần của logic production** (fakes 100%, 0 I/O, dưới 1 s):
`test_bm25`, `test_rrf`, `test_query_guard`, `test_markdown_chunking`,
`test_retention`, `test_memory_gateway`, `test_chat_reply`, `test_intent_service`,
`test_ports`, `test_routing`, `test_citation_accuracy`, toàn bộ
`knowledge_ingestion/*`, `integrations/llm/*`, `integrations/rag/*` …

Những module đó chính là "toàn bộ invariant nghiệp vụ, bảo mật, contracts" mà bảng
ở §1 mô tả cho tầng **Core** — nhưng lại bị **bỏ chọn mặc định**, nên một hồi quy
trong BM25, RRF, query guard hay retention policy sẽ merge xanh mà không ai biết.

**Đã sửa:** 46 module này được trả về tầng core. Định nghĩa marker `extended` được
thu hẹp lại thành *"cần PostgreSQL thật, hoặc chạy harness đánh giá/benchmark chứ
không phải mã production"*.

### Phân bổ sau đính chính

| Tầng | Số lượng test | Thời gian | Trạng thái mặc định |
|---|:---:|:---:|---|
| **Core** | **1.142** *(từ 536)* | **~20 s** | Selected |
| **Extended** | **383** *(từ 968)* | **~12 s** | Deselected (`-m extended`) |
| **Live** | 27 | Tùy network | Deselected (`-m live`) |

```bash
uv run pytest -q
# 1142 passed, 9 skipped in 19.83s

uv run pytest -m extended -q
# 383 passed, 10 skipped in 12.40s
```

### Đính chính hành động §2.4 (URL mặc định Postgres)

`DEFAULT_PG_TEST_URL` **đã được gỡ bỏ**. Ba module persistence quay lại
`os.getenv("PG_TEST_URL", "")`.

Lý do: các module này có autouse fixture chạy `DROP SCHEMA public CASCADE`. Khi URL
mặc định trỏ tới container dev, `pytest -m extended` không kèm `PG_TEST_URL` sẽ
**xóa sạch database dev của lập trình viên** thay vì skip như trước. Docstring của
`pg_probe.py` vốn đã ghi rõ điều này ("must not start guessing at the dev
container") nhưng không được cập nhật theo thay đổi.

Muốn chạy tầng persistence, hãy trỏ `PG_TEST_URL` tới một database dùng-một-lần:

```bash
PG_TEST_URL=postgresql://cowork:cowork_dev_only@127.0.0.1:5432/cowork_test uv run pytest -m extended -q
```
