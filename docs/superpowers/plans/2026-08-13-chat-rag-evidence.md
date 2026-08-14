# Chat RAG Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the exact ranked RAG chunks used by every chat answer, with preview and full-text dialog, retaining the evidence when a session is reopened.

**Architecture:** A bounded `ChatRagEvidence` is attached to `ChatTurn` and emitted on the typed SSE `completed` event. A Postgres turn repository becomes durable history while the in-memory buffer remains a bounded live cache. React hydrates the persisted evidence and renders a collapsed panel below the matching assistant response.

**Tech Stack:** Python, FastAPI, psycopg/Postgres migrations, React, TypeScript, Vitest, Testing Library, Tailwind, Lucide.

## Global Constraints

- Preserve Email RAG/AI Chat separation and never persist raw Gmail content.
- Persist at most five chunks; preview is first 400 normalized characters; full chunk text maximum is 16,000 characters.
- Evidence and scores are server-derived only; preserve existing project citation compatibility.
- Panel is collapsed by default, keyboard accessible, and has explicit no-results/timeout state.

## File Structure

- `src/cowork_agent/domain/_chat_contracts_memory.py`: evidence model and `ChatTurn.rag_evidence`.
- `src/cowork_agent/domain/_chat_contracts_chat.py`: `completed(..., rag_evidence=...)`.
- `src/cowork_agent/persistence/migrations/011_chat_turn_evidence.sql` and `.down.sql`: turn/evidence durable storage.
- `src/cowork_agent/persistence/repositories/chat_turns.py`: scope-authorized append/read.
- `src/cowork_agent/features/ai_chat/session_buffer.py`, `controller.py`, `api/chat.py`, `app.py`: persist, stream, and read evidence.
- `frontend/src/dashboard/types.ts`, `hooks/useStreamingChat.ts`, `components/RagEvidencePanel.tsx`, and `components/ChatStreamView.tsx`: hydrate and render evidence.

### Task 1: Add bounded domain and SSE evidence contracts

**Files:** Modify `src/cowork_agent/domain/_chat_contracts_memory.py`, `src/cowork_agent/domain/_chat_contracts_chat.py`; test `tests/unit/domain/test_chat_contracts.py`.

**Produces:** `ChatRagEvidence(source, retrieval_status, chunk_id, document_id, document_title, section, source_url, relevance_score, rerank_score, preview, content)`; `ChatTurn.rag_evidence`; completed-event `rag_evidence` and retrieval status.

- [ ] Write failing tests that a turn and completed event round-trip one evidence record with score/content.
- [ ] Run `python -m pytest tests/unit/domain/test_chat_contracts.py -q`; expect missing evidence contract failure.
- [ ] Implement immutable model, finite score/text bounds, top-five evidence bound, exact serializer/deserializer, and reject evidence on non-completed event variants.
- [ ] Re-run the same command; expect PASS.
- [ ] Commit `feat(chat): add bounded RAG evidence contract`.

### Task 2: Persist authorized chat turns and evidence

**Files:** Create `src/cowork_agent/persistence/migrations/011_chat_turn_evidence.sql`, `.down.sql`, and `repositories/chat_turns.py`; modify `features/ai_chat/ports.py`, `session_buffer.py`, and `app.py`; test `tests/integration/persistence/test_chat_turn_repository.py` and `tests/unit/features/ai_chat/test_session_buffer.py`.

**Produces:** `PostgresChatTurnRepository.append(scope, turn)` and `.read(scope) -> tuple[ChatTurn, ...]`; durable buffer writes database first, then mirrors cache.

- [ ] Write failing tests for evidence round-trip and foreign-session isolation.
- [ ] Run `python -m pytest tests/integration/persistence/test_chat_turn_repository.py tests/unit/features/ai_chat/test_session_buffer.py -q`; expect missing repository/migration failure.
- [ ] Create a `chat_turns` table keyed by `(session_id, turn_id)`, with `rag_evidence jsonb NOT NULL DEFAULT '[]'`, citation coordinates, created timestamp, a five-item JSON array check, cascading session FK, and session/time index. Down migration drops index then table.
- [ ] Serialize with `ChatTurn.to_dict()`, deserialize with `ChatTurn.from_dict()`, query only the verified session scope, and order history by creation time.
- [ ] Re-run the same tests; expect PASS.
- [ ] Commit `feat(chat): persist RAG evidence with session turns`.

### Task 3: Capture exact retrieval evidence and stream it once

**Files:** Modify `src/cowork_agent/features/ai_chat/controller.py` and project-document evidence projection; test `tests/unit/features/ai_chat/test_controller.py`, `tests/unit/test_chat_runtime_composition.py`.

**Produces:** persisted turn and completed event with identical ordered evidence, including no-result/timeout status.

- [ ] Write failing controller tests asserting company chunks preserve rank/score/content and a no-result turn has empty evidence plus `no_results`.
- [ ] Run `python -m pytest tests/unit/features/ai_chat/test_controller.py tests/unit/test_chat_runtime_composition.py -q`; expect missing evidence assertions.
- [ ] Convert only retrieved company/project chunks to `ChatRagEvidence` before reply generation. Keep retrieval order; derive preview server-side; attach evidence to `ChatTurn`; send it only in the final completed SSE event.
- [ ] Re-run the same tests; expect PASS.
- [ ] Commit `feat(chat): stream exact RAG evidence with replies`.

### Task 4: Return stored evidence through session history

**Files:** Modify `src/cowork_agent/api/chat.py`; test `tests/integration/api/test_chat_api.py`.

**Produces:** session-history turns with stored `rag_evidence`, no Qdrant/LLM call during history reads.

- [ ] Write a failing API test asserting reopened session history returns chunk ID, score, preview, and content.
- [ ] Run `python -m pytest tests/integration/api/test_chat_api.py -q`; expect `rag_evidence` missing.
- [ ] Serialize the saved turn directly, retaining existing citation availability annotations.
- [ ] Re-run the same test; expect PASS.
- [ ] Commit `feat(chat): return saved RAG evidence in session history`.

### Task 5: Hydrate and render the accessible evidence panel

**Files:** Modify `frontend/src/dashboard/types.ts`, `hooks/useStreamingChat.ts`, `components/ChatStreamView.tsx`; create `components/RagEvidencePanel.tsx`; test `hooks/useStreamingChat.test.tsx`, `components/RagEvidencePanel.test.tsx`.

**Produces:** `ChatMessage.ragEvidence` and `<RagEvidencePanel evidence={...} />`.

- [ ] Write a failing hook test for SSE completed evidence and history rehydration, plus a component test for collapsed panel, score/preview, full-text dialog, Escape close, and empty state.
- [ ] Run `pnpm test -- useStreamingChat.test.tsx RagEvidencePanel.test.tsx`; expect missing type/component failure.
- [ ] Parse `completed.rag_evidence`, map `turn.rag_evidence`, then render a native disclosure. Each ranked card shows title, section, score, 400-character preview and `View full chunk`; controlled dialog presents full text and closes via button/Escape.
- [ ] Re-run the same tests; expect PASS.
- [ ] Commit `feat(chat): show retrieved RAG chunks and scores`.

### Task 6: Run verification gates and manual UI check

- [ ] Run `python -m pytest tests/unit/domain/test_chat_contracts.py tests/unit/features/ai_chat/test_controller.py tests/unit/test_chat_runtime_composition.py tests/integration/api/test_chat_api.py tests/integration/persistence/test_chat_turn_repository.py -q`; expect PASS.
- [ ] Run `python -m ruff check . && python -m mypy src`; expect PASS.
- [ ] Run `pnpm test && pnpm check-types && pnpm lint` from `frontend`; expect PASS.
- [ ] In a real RAG answer, verify evidence is collapsed, cards stay rank ordered, dialog is keyboard operable, timeout/no-result is truthful, and reopening the session does not trigger retrieval.
- [ ] Inspect `git diff --check` and commit any documentation-only clarification separately.
