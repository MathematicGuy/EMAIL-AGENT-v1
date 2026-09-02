# System Test Evaluation & Tiering Process

> **Phạm vi tài liệu:** Ghi nhận toàn bộ phương pháp đánh giá tầm quan trọng của Test Hệ Thống (System Invariants, Data Boundaries, Offline Guarantees), quy trình phân tầng ~500 core tests, điều phối multi-agent teamwork và áp dụng context engineering.
> **Lưu ý:** Tài liệu này độc lập hoàn toàn với tài liệu QA Chatbot Intent Test.

---

## 1. Mục tiêu & Nguyên tắc Cốt lõi

1. **Hệ thống Invariant > Intent Test**: Đánh giá dựa trên rủi ro kỹ thuật (bảo mật, cô lập dữ liệu, tính lũy thừa, cơ chế suy thoái), không đánh giá theo intent hội thoại bề mặt.
2. **Offline by Construction (§7 `tests/README.md`)**: Giữ nguyên quy tắc chặn socket ngoài (`test_network_guard.py`), `RAG_STORE_PROVIDER=none` để test chạy độc lập, nhanh (<10s) và không tốn chi phí API ngoài.
3. **One Invariant, One Owner (§3 `tests/README.md`)**: Mỗi invariant hệ thống chỉ có một file test duy nhất chịu trách nhiệm; loại bỏ/tách các assertion trùng lặp.
4. **Phân tầng Core (~500 tests) vs Extended**: Mặc định `uv run pytest -q` chạy ~500 core critical tests. Toàn bộ test suite mở rộng được gán marker `@pytest.mark.extended` để chạy khi cần kiểm tra sâu.

---

## 2. Ma trận Phân cấp Đánh giá Test

| Cấp độ | Nhóm Test | Tiêu chuẩn Đánh giá | File / Route sở hữu Invariant |
|---|---|---|---|
| **Tier 1: Critical (P0)** | **Bảo mật & Ranh giới dữ liệu** | - Ngăn rò rỉ raw email/token ra API/Chat memory.<br>- Chặn outbound socket qua mạng công cộng.<br>- Kiểm soát ACL tài liệu & cách ly dự án. | `tests/integration/api/test_principal_boundary.py`<br>`tests/unit/domain/test_chat_contracts.py`<br>`tests/unit/test_network_guard.py`<br>`tests/unit/integrations/test_project_documents_hybrid.py` |
| **Tier 1: Critical (P0)** | **Toàn vẹn Dữ liệu & Idempotency** | - Migration Postgres an toàn, áp dụng 1 lần không lỗi.<br>- Idempotent run / task deduplication (tránh nhân bản task). | `tests/integration/persistence/test_postgres_repositories.py`<br>`tests/integration/email_action_plan/test_workflow.py` |
| **Tier 2: High (P1)** | **Khả năng Phục hồi & Suy thoái (Resilience)** | - Tự suy thoái về `NullSemanticMemory` khi lỗi API/key.<br>- Xoay vòng API key (Jina 429/403).<br>- Khởi động offline an toàn. | `tests/unit/integrations/test_bootstrap.py`<br>`tests/unit/integrations/rag/test_embeddings.py` |
| **Tier 2: High (P1)** | **Domain Contracts & Pipeline Lõi** | - Frozen models, state machine, contract enum.<br>- Luồng Email -> Classify -> Action Plan -> Persist. | Route `R1` (`tests/unit/domain`)<br>Route `R13` (`tests/integration/email_action_plan`) |
| **Tier 3: Medium (P2)** | **Thuật toán & Tooling** | - BM25, RRF fusion score, harvest metadata ngày tháng.<br>- Script CLI eval metadata. | Route `R3` (`tests/unit/integrations/rag`)<br>Route `R9` (`tests/unit/scripts`) |
| **Tier 4: Extended / Prune (P3)** | **Test Dư thừa / Mở rộng** | - Test lại tính năng framework (Pydantic, FastAPI).<br>- Duplicate assertion vi phạm quy tắc §3. | Gán marker `@pytest.mark.extended` hoặc cắt tỉa theo §4 |

---

## 3. Công thức Chấm điểm Rủi ro (Risk Scoring)

Mỗi test case hoặc test suite được đánh giá qua chỉ số Risk Score:

$$\text{Risk Score} = \text{Severity} \times \text{Blast Radius}$$

- **Severity (1 - 5):**
  - `5`: Rò rỉ email/token bí mật, hỏng cơ sở dữ liệu, cắn tiền API thật.
  - `4`: Sai lệch phân quyền ACL, hỏng idempotency tạo trùng lặp tác vụ.
  - `3`: Luồng orchestration chính bị gián đoạn, cơ chế degrade bị hỏng.
  - `2`: Thuật toán scoring RAG hoặc trích xuất metadata ngày tháng sai lệch nhẹ.
  - `1`: Sai format chuỗi hiển thị / mapping bề mặt.
- **Blast Radius (1 - 3):**
  - `3`: Toàn hệ thống (Domain contracts, Auth boundary, Conftest guards).
  - `2`: Toàn bộ một tính năng (Email pipeline, Chat controller).
  - `1`: Một endpoint hoặc một hàm utility cô lập.

---

## 4. Quá trình Điều phối Multi-Agent (`/teamwork-preview`)

1. **Thiết lập Artifact Nhiệm vụ (`prompt_draft.md`)**:
   - Khởi tạo mục tiêu, phạm vi working directory (`D:\User\ProjectGithub\hiepnguyenn-99\EMAIL-AGENT-v1`), chế độ `integrity_mode: development`.
2. **Khảo sát Ý kiến & Lựa chọn**:
   - **Xử lý ~1,000 tests còn lại:** Chọn phương án gán marker `@pytest.mark.extended` (giữ lại chạy khi cần, mặc định chạy ~500 core tests).
   - **Quy mô Agent Team:** Chọn *Small focused team* (1 implementer tuần tự + review phản biện).
3. **Khởi chạy Delegation**:
   - Đã ủy quyền subagent `teamwork_preview` (ID: `2bcad5c3-ad1d-4415-b137-2b3ae2f37a1f`).
   - SWE Light Orchestrator điều phối Round 0 (Implementer gắn marker tiering và chạy test verification).

---

## 5. Áp dụng Context Engineering

Áp dụng chuẩn 5 tầng phân cấp ngữ cảnh:
- **Level 1 (Rules):** `AGENTS.md` (chuẩn lệnh `uv run`, offline test guarantee, no direct python).
- **Level 2 (Spec):** `tests/README.md` (Route Index R1-R16, Invariant Ownership §3, Pruning Checklist §4).
- **Level 3 (Files):** `tests/conftest.py`, `tests/unit/`, `tests/integration/`.
- **Level 4 (Gates):** `uv run pytest -q`, `uv run ruff check .`, `uv run mypy src`.
- **Level 5 (Session Management):** Gọn nhẹ, tập trung vào single contained fix.
