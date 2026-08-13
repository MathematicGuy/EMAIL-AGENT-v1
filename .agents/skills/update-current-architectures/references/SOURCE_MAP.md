# Source Code Mapping & Architecture Stream Guide

This map pairs each architecture document in `docs/architectures/current-architectures/` with its authoritative source code modules in `src/cowork_agent/` and reference sections in `TARGET-ARCHITECTURE.md`.

---

## Stream 1: Email Action Plan & RAG Subsystem

- **Document:** `01-email-action-plan-and-rag.md`
- **Primary Source Code Directories:**
  - `src/cowork_agent/features/email_action_plan/` (`workflow.py`, `routing.py`, `validation.py`, `ports.py`)
  - `src/cowork_agent/integrations/rag/` (`turbovec_memory.py`, `qdrant.py`, `hybrid.py`, `bootstrap.py`)
  - `src/cowork_agent/integrations/gmail/` (`provider.py`, `fakes.py`, `auth.py`)
  - `data/extracted/` (Company Markdown RAG corpus)
- **Target Reference:** [TARGET-ARCHITECTURE.md §1 & §2](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md) (Stateless standalone Email Agent)
- **Relevant ADRs:** ADR-003 (Attachment scope), ADR-004 (Email Agent decoupling)

---

## Stream 2: AI Chat & Typed Memory Core

- **Document:** `02-ai-chat-and-typed-memory.md`
- **Primary Source Code Directories:**
  - `src/cowork_agent/features/ai_chat/` (`controller.py`, `memory_gateway.py`, `episode_policy.py`, `retrieval_policy.py`, `session_buffer.py`)
  - `src/cowork_agent/features/user_documents/` (`ports.py`, document ingestion & search)
  - `src/cowork_agent/integrations/project_documents/` (`project_documents.py`)
  - `src/cowork_agent/api/chat.py` (Chat SSE stream & session endpoints)
- **Target Reference:** [TARGET-ARCHITECTURE.md §2 & §3](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md)
- **Relevant ADRs:** [ADR-004](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-004-chat-native-task-episodes.md) (Chat-native TaskEpisodes), [ADR-007](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/tasks/adr/ADR-007-project-scoped-classifier-gated-user-documents.md) (User Documents extension)

---

## Stream 3: Control Plane, Persistence & Presentation UIs

- **Document:** `03-control-plane-persistence-and-uis.md`
- **Primary Source Code Directories:**
  - `src/cowork_agent/app.py` (FastAPI lifespans & route mounts)
  - `src/cowork_agent/identity.py` & `src/cowork_agent/config.py`
  - `src/cowork_agent/persistence/` (`repositories/local.py`, `repositories/postgres.py`, `migrations/001_mail_todo.sql`)
  - `src/cowork_agent/orchestration/` (`worker.py`, `project_document_worker.py`)
  - `frontend/` (React 19 + Vite + Tailwind 4 web application)
  - `src/cowork_agent/gui/` & `scripts/run_gui.py` (Streamlit developer GUI)
- **Target Reference:** [TARGET-ARCHITECTURE.md §1 & §2](file:///e:/VIN-INTERNSHIP/EMAIL-AGENT-v1/docs/architectures/TARGET-ARCHITECTURE.md) (Control plane & dual storage)

---

## Consolidated Dashboard

- **Document:** `README.md`
- **Source Input:** Synthesizes findings from Streams 1, 2, and 3.
- **Responsibility:** Maintains the Level 1 System Overview diagram, Module Status Matrix, and Architecture Diff Matrix comparing current live code to `TARGET-ARCHITECTURE.md`.
