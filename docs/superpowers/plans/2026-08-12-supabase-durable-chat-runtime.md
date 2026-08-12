# Supabase Durable Chat Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist chat-session ownership in Supabase Postgres and keep only bounded short-term turns in Redis.

**Architecture:** `chat_sessions` becomes the ownership source. A `ChatController` is built from the verified durable scope for each request, not retained in `app.state`. Redis stores only serialized `ChatTurn` values under validated namespaces with newest-N and sliding TTL.

**Tech Stack:** FastAPI, psycopg, Redis, PostgreSQL migrations, pytest, ruff, mypy.

## Global Constraints

- FastAPI is the only Supabase data boundary; no Supabase key reaches browsers.
- Opaque-session `VerifiedPrincipal` scopes every durable operation; foreign resources return 404.
- Do not persist raw prompts, replies, Gmail content, or copied RAG chunks.
- Preserve SSE and TaskEpisode contracts.
- Redis stores short-term turns only; an outage gives explicit short-term-memory degradation.
- SQLite/in-memory remain local fallbacks without `DATABASE_URL`.

---

## File Structure

- `persistence/migrations/006_durable_chat_sessions*.sql`: durable session metadata and rollback.
- `persistence/repositories/chat_sessions.py`: PostgreSQL session registry.
- `features/ai_chat/session_buffer.py`: Redis buffer and unavailable exception.
- `features/ai_chat/memory_gateway.py`: short-term degradation conversion.
- `api/chat.py`, `app.py`: request-scoped composition.
- `orchestration/worker.py`: PostgreSQL mailbox repository in durable worker.
- Focused unit, API, and Postgres integration tests under `tests/`.

### Task 1: Add durable chat-session storage

**Files:**

- Create: `src/cowork_agent/persistence/migrations/006_durable_chat_sessions.sql`
- Create: `src/cowork_agent/persistence/migrations/006_durable_chat_sessions.down.sql`
- Create: `src/cowork_agent/persistence/repositories/chat_sessions.py`
- Create: `tests/unit/persistence/test_durable_chat_session_migration.py`
- Create: `tests/integration/persistence/test_chat_session_repository.py`

**Interfaces:** `create(tenant_id: str, user_id: str) -> ChatMemoryScope`, `require(session_id: str, *, tenant_id: str, user_id: str) -> ChatMemoryScope`, and `list_for(*, tenant_id: str, user_id: str) -> tuple[ChatMemoryScope, ...]`. The Postgres implementation is async; the existing in-memory registry preserves local behavior.

- [x] **Step 1: Write the failing tests**

```python
async def scenario() -> None:
    scope = await registry.create(tenant_id=workspace, user_id=owner)
    assert await registry.require(scope.session_id, tenant_id=workspace, user_id=owner) == scope
    with pytest.raises(ChatSessionAccessDenied):
        await registry.require(scope.session_id, tenant_id=workspace, user_id=other_user)
```

- [x] **Step 2: Verify RED**

Run: `py -3.11 -m pytest tests/unit/persistence/test_durable_chat_session_migration.py tests/integration/persistence/test_chat_session_repository.py -q`

Expected: the migration/repository imports fail; live integration can skip without `PG_TEST_URL`.

- [x] **Step 3: Implement the minimal schema and repository**

```sql
CREATE TABLE chat_sessions (
    id text PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    feature text NOT NULL CHECK (feature = 'ai_chat'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chat_sessions_owner_idx ON chat_sessions (workspace_id, user_id, created_at, id);
```

Generate the UUID-like `id` in Python; every `require` checks `workspace_members` as well as the session row.

- [x] **Step 4: Verify GREEN**

Run: `py -3.11 -m pytest tests/unit/persistence/test_durable_chat_session_migration.py tests/integration/persistence/test_chat_session_repository.py -q`

- [x] **Step 5: Commit**

```bash
git add src/cowork_agent/persistence/migrations/006_durable_chat_sessions.sql src/cowork_agent/persistence/migrations/006_durable_chat_sessions.down.sql src/cowork_agent/persistence/repositories/chat_sessions.py tests/unit/persistence/test_durable_chat_session_migration.py tests/integration/persistence/test_chat_session_repository.py
git commit -m "feat(chat): persist owned chat sessions"
```

### Task 2: Replace only the short-term buffer with Redis

**Files:**

- Modify: `src/cowork_agent/features/ai_chat/session_buffer.py`
- Modify: `src/cowork_agent/features/ai_chat/memory_gateway.py`
- Modify: `src/cowork_agent/domain/_chat_contracts_common.py`
- Modify: `src/cowork_agent/domain/chat_contracts.py`
- Create: `tests/unit/features/ai_chat/test_redis_session_buffer.py`

**Interfaces:** `RedisChatSessionBuffer.append/read/clear` keeps the synchronous `ChatSessionBufferPort` shape. `ChatSessionBufferUnavailable` denotes a `redis.exceptions.RedisError` without containing chat text.

- [x] **Step 1: Write the failing behavior tests**

```python
def test_redis_buffer_keeps_newest_turns_and_refreshes_ttl() -> None:
    buffer.append(namespace, turn_one)
    buffer.append(namespace, turn_two)
    buffer.append(namespace, turn_three)
    assert buffer.read(namespace) == (turn_two, turn_three)
    assert fake_redis.ttl(buffer.redis_key(namespace)) == 60

def test_gateway_reports_short_term_degradation_when_redis_is_unavailable() -> None:
    response = asyncio.run(gateway.read_context(request))
    assert response.degraded_sources == (DegradedMemorySource.SHORT_TERM,)
```

- [x] **Step 2: Verify RED**

Run: `py -3.11 -m pytest tests/unit/features/ai_chat/test_redis_session_buffer.py -q`

Expected: missing Redis adapter and short-term degradation source.

- [x] **Step 3: Implement the minimal adapter**

Store `ChatTurn.to_dict()` JSON only at `cowork:chat:short-term:{namespace.logical_key()}`. On append use `LPUSH`, `LTRIM(0, max_turns - 1)`, and `EXPIRE(ttl_seconds)` in a pipeline; `read` reverses decoded `LRANGE` results to chronological order. Preserve exact namespace/turn validation. Catch `RedisError`, raise `ChatSessionBufferUnavailable`, and make `MemoryGateway` continue without a short-term append/read while adding `DegradedMemorySource.SHORT_TERM`.

- [x] **Step 4: Verify GREEN**

Run: `py -3.11 -m pytest tests/unit/features/ai_chat/test_redis_session_buffer.py tests/unit/features/ai_chat/test_session_buffer.py tests/unit/features/ai_chat/test_controller.py -q`

- [x] **Step 5: Commit**

```bash
git add src/cowork_agent/features/ai_chat/session_buffer.py src/cowork_agent/features/ai_chat/memory_gateway.py src/cowork_agent/domain/_chat_contracts_common.py src/cowork_agent/domain/chat_contracts.py tests/unit/features/ai_chat/test_redis_session_buffer.py
git commit -m "feat(chat): keep short-term turns in Redis"
```

### Task 3: Compose controllers per request and fix durable worker mailboxes

**Files:**

- Modify: `src/cowork_agent/api/chat.py`
- Modify: `src/cowork_agent/app.py`
- Modify: `src/cowork_agent/orchestration/worker.py`
- Modify: `tests/integration/api/test_chat_api.py`
- Modify: `tests/unit/test_chat_runtime_composition.py`
- Modify: `.env.example`

**Interfaces:** Session-bound endpoints await `registry.require` and call `chat_controller_factory(scope)` afresh. PostgreSQL runtime does not expose a controller map. The durable worker passes `PostgresMailboxConnectionRepository(pool)` into `GmailMailboxAdapter`.

- [x] **Step 1: Write failing API/composition tests**

```python
def test_message_request_rebuilds_controller_from_a_durably_owned_scope() -> None:
    response = asyncio.run(post_message_with_registry_only())
    assert response.status_code == 200
    assert factory_scopes == [ChatMemoryScope("tenant-1", "user-1", "session-1")]
```

- [x] **Step 2: Verify RED**

Run: `py -3.11 -m pytest tests/integration/api/test_chat_api.py tests/unit/test_chat_runtime_composition.py -q`

Expected: failure because controllers are retained and the worker still initializes SQLite mailbox storage.

- [x] **Step 3: Implement composition**

Use a registry protocol in `api/chat.py`, await durable registry calls, and remove controller retention. In `create_app`, choose PostgreSQL sessions whenever `DATABASE_URL` exists; require `REDIS_URL` for that durable chat runtime, build a separate Redis buffer client, and close it in lifespan cleanup. Change the worker to Postgres mailbox storage. Document server-side-only `DATABASE_URL`, `REDIS_URL`, `CHAT_MEMORY_MAX_TURNS`, and `CHAT_MEMORY_TTL_SECONDS`.

- [x] **Step 4: Verify GREEN and static checks**

Run: `py -3.11 -m pytest tests/integration/api/test_chat_api.py tests/unit/test_chat_runtime_composition.py tests/unit/features/ai_chat/test_controller.py -q`

Run: `py -3.11 -m ruff check src/cowork_agent/app.py src/cowork_agent/api/chat.py src/cowork_agent/features/ai_chat src/cowork_agent/persistence/repositories/chat_sessions.py src/cowork_agent/orchestration/worker.py`

Run: `py -3.11 -m mypy src/cowork_agent/app.py src/cowork_agent/api/chat.py src/cowork_agent/features/ai_chat src/cowork_agent/persistence/repositories/chat_sessions.py src/cowork_agent/orchestration/worker.py`

- [x] **Step 5: Commit**

```bash
git add src/cowork_agent/api/chat.py src/cowork_agent/app.py src/cowork_agent/orchestration/worker.py tests/integration/api/test_chat_api.py tests/unit/test_chat_runtime_composition.py .env.example docs/superpowers/plans/2026-08-12-supabase-durable-chat-runtime.md
git commit -m "feat(chat): compose durable request-scoped runtime"
```

## Plan self-review

- Durable ownership, Redis-only working memory, SSE preservation, 404 isolation, and worker mailbox persistence each have an explicit task and test.
- Project documents, Storage, Qdrant retrieval, expiry, and deletion are intentionally the distinct Increment 3 plan so this increment remains independently deployable.
- Names and return types match the current `ChatMemoryScope` and `ChatSessionBufferPort` contracts.
