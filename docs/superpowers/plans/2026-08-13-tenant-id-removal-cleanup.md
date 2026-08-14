# Complete Removal and Cleanup of `tenant_id` & Document Access Scoping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all `tenant_id` multi-tenancy parameters, ACL payload filters, database schema constraints, and redundant document access restrictions to streamline the codebase for single-user Cowork accounts.

**Architecture:** Refactor identity resolution (`deps.py`), domain dataclasses (`domain/`), RAG vector retrieval filters (`integrations/rag/`), persistence repositories (`persistence/`), and tests (`tests/`). Remove `X-Workspace-ID` header enforcement and pre-scoring vector checks while preserving core Email Action Plan and AI Chat features.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic / Dataclasses, Pytest, Qdrant / Turbovec, PostgreSQL / SQLite.

## Global Constraints
- Do not break existing pytest suites: tests must be updated to align with the simplified domain models.
- Gmail and Company RAG (`data/extracted/*.md`) workflows must remain operational.
- All documents uploaded in Cowork must be directly accessible by the account's single user without tenant boundary checks.

---

### Task 1: Refactor Identity Resolution (`deps.py`) & Remove `X-Workspace-ID`

**Files:**
- Modify: `src/cowork_agent/api/deps.py`
- Modify: `src/cowork_agent/api/chat.py`
- Test: `tests/unit/test_api_deps.py` (or existing deps test)

**Interfaces:**
- Consumes: Request headers
- Produces: `VerifiedPrincipal` with only `user_id` (defaulting to `"default_user"`)

- [ ] **Step 1: Write failing unit test for `VerifiedPrincipal` without `tenant_id`**

```python
def test_get_verified_principal_single_user():
    # Verify VerifiedPrincipal no longer has tenant_id and defaults user_id
    from cowork_agent.api.deps import VerifiedPrincipal
    principal = VerifiedPrincipal(user_id="user_123")
    assert principal.user_id == "user_123"
    assert not hasattr(principal, "tenant_id")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_api_deps.py -v`

- [ ] **Step 3: Modify `deps.py` to remove `tenant_id` and `X-Workspace-ID`**

Remove `tenant_id` from `VerifiedPrincipal` dataclass and remove `X-Workspace-ID` extraction logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_api_deps.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/api/deps.py tests/unit/test_api_deps.py
git commit -m "refactor: remove tenant_id and X-Workspace-ID from api deps"
```

---

### Task 2: Clean Up Domain Dataclasses & Target Contracts

**Files:**
- Modify: `src/cowork_agent/domain/_chat_contracts_memory.py`
- Modify: `src/cowork_agent/domain/project_documents.py`
- Modify: `src/cowork_agent/domain/target_contracts.py`
- Test: `tests/unit/test_domain_models.py`

**Interfaces:**
- Consumes: Cleaned domain specifications
- Produces: `ChatMemoryScope`, `ProjectRef`, `ProjectDocument`, `DocumentChunk`, `EmailActionPlanRun`, `RAGRetrievalRequest` without `tenant_id`

- [ ] **Step 1: Write failing test for domain models without `tenant_id`**

```python
def test_chat_memory_scope_without_tenant():
    from cowork_agent.domain._chat_contracts_memory import ChatMemoryScope
    scope = ChatMemoryScope(user_id="u1", session_id="s1")
    assert scope.user_id == "u1"
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m pytest tests/unit/test_domain_models.py -v`

- [ ] **Step 3: Update dataclass definitions**

Remove `tenant_id` fields, parameter validations, and namespace string format (`f"{user_id}/{session_id}/{feature}"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_domain_models.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/domain/
git commit -m "refactor: remove tenant_id from domain models and contracts"
```

---

### Task 3: Remove `tenant_id` Vector Stores Payload Filters & ACL Checks

**Files:**
- Modify: `src/cowork_agent/integrations/rag/qdrant.py`
- Modify: `src/cowork_agent/integrations/rag/turbovec_memory.py`
- Modify: `src/cowork_agent/integrations/rag/hybrid.py`
- Modify: `src/cowork_agent/integrations/rag/chat_memory.py`
- Modify: `src/cowork_agent/integrations/rag/project_documents.py`
- Test: `tests/unit/test_qdrant_rag.py`

**Interfaces:**
- Consumes: Vector search requests
- Produces: Vector search without tenant payload matching or authorization failure gates

- [ ] **Step 1: Write failing test verifying search operates without `tenant_scope`**

```python
def test_qdrant_search_without_tenant_filter():
    # Verify search executes without tenant_id filter constraint
    pass
```

- [ ] **Step 2: Remove `TENANT_PAYLOAD_KEY`, pre-scoring `tenant_id` checks, and tenant filters from vector retrievers**

Remove `TENANT_PAYLOAD_KEY = "tenant_id"`, remove `authorization_denied` status code for tenant mismatch, and remove `FieldCondition(key="tenant_id")`.

- [ ] **Step 3: Run RAG tests to verify passing status**

Run: `python -m pytest tests/unit/test_qdrant_rag.py -v`

- [ ] **Step 4: Commit**

```bash
git add src/cowork_agent/integrations/rag/
git commit -m "refactor: remove tenant_id vector store filters and ACL pre-scoring checks"
```

---

### Task 4: Update Persistence Repositories & SQL Schemas

**Files:**
- Modify: `src/cowork_agent/persistence/migrations/001_mail_todo.sql` through `007_task_episode_project_scope.sql` (or add clean single-user migration `008_remove_tenant_id.sql`)
- Modify: `src/cowork_agent/persistence/repositories/postgres.py`
- Modify: `src/cowork_agent/persistence/repositories/sqlite.py`
- Modify: `src/cowork_agent/persistence/repositories/chat_sessions.py`
- Test: `tests/unit/test_persistence.py`

**Interfaces:**
- Consumes: SQL queries
- Produces: Table queries scoped by `user_id` / `session_id` without `tenant_id`

- [ ] **Step 1: Write test for repository lookups without `tenant_id`**

```python
def test_repository_get_without_tenant():
    # Verify DB repository methods take user_id instead of (tenant_id, user_id)
    pass
```

- [ ] **Step 2: Update repository methods to strip `tenant_id` from SQL queries**

Update `WHERE tenant_id = %s AND user_id = %s` -> `WHERE user_id = %s`.

- [ ] **Step 3: Run persistence tests**

Run: `python -m pytest tests/unit/test_persistence.py -v`

- [ ] **Step 4: Commit**

```bash
git add src/cowork_agent/persistence/
git commit -m "refactor: simplify persistence queries and schemas by rem  oving tenant_id"
```

---

### Task 5: Verify Full Test Suite & Quality Gates

**Files:**
- Test: All unit and integration test files in `tests/`

- [ ] **Step 1: Run pytest across full test suite**

Run: `python -m pytest -q`

- [ ] **Step 2: Run Ruff linter**

Run: `python -m ruff check .`

- [ ] **Step 3: Run MyPy type checker**

Run: `python -m mypy src`

- [ ] **Step 4: Commit final cleanup**

```bash
git add .
git commit -m "chore: complete tenant_id cleanup across tests and application composition root"
```
