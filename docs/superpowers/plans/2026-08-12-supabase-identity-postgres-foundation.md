# Supabase Identity and Postgres Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Supabase Postgres the durable identity, workspace, mailbox-connection, and opaque FastAPI session store for Gmail OAuth.

**Architecture:** Gmail is the identity provider. Its verified callback email maps to an internal user and default workspace; each opaque session is bound to that user and workspace. FastAPI stores only a SHA-256 hash of a random session token and sends the plaintext only in a Secure, HttpOnly, SameSite=Lax cookie. FastAPI resolves that cookie on every protected request. The browser never receives a Supabase key or calls its data API.

**Tech Stack:** Python 3.11+, FastAPI, psycopg async pool, Supabase-managed PostgreSQL, pytest, ruff, mypy.

## Global Constraints

- Supabase Auth, PostgREST browser access, and service-role keys in the client are out of scope.
- Never persist, log, or return plaintext session tokens after the callback response.
- Gmail refresh tokens remain encrypted; raw Gmail bodies, attachments, prompts, and copied RAG chunks remain excluded from Postgres.
- Internal immutable IDs—not Gmail email—are authorization keys.
- PostgreSQL is required for this runtime; SQLite is an explicit local fallback when `DATABASE_URL` is empty.

---

### Task 1: Opaque session configuration and helpers

**Files:**
- Create: `src/cowork_agent/security/sessions.py`
- Modify: `src/cowork_agent/config.py`
- Test: `tests/unit/security/test_sessions.py`, `tests/unit/test_config.py`

**Interfaces:** `SessionSettings.from_env()`; `new_session_token() -> str`; `session_token_hash(token: str) -> str`; `session_expiry(now, ttl_seconds) -> datetime`.

- [ ] **Step 1: Write failing tests**

```python
def test_session_hash_is_deterministic_and_not_plaintext() -> None:
    assert session_token_hash("token") == session_token_hash("token")
    assert session_token_hash("token") != "token"
```

- [ ] **Step 2: Confirm RED**

Run: `python -m pytest tests/unit/security/test_sessions.py tests/unit/test_config.py -q`

Expected: import failure because session helpers do not exist.

- [ ] **Step 3: Implement minimal helpers**

```python
def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
```

Add `APP_SESSION_TTL_SECONDS=2592000`, `APP_SESSION_COOKIE_NAME=cowork_session`, and `APP_SESSION_COOKIE_SECURE=true` settings using the existing configuration validators.

- [ ] **Step 4: Confirm GREEN**

Run: `python -m pytest tests/unit/security/test_sessions.py tests/unit/test_config.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/security/sessions.py src/cowork_agent/config.py tests/unit/security/test_sessions.py tests/unit/test_config.py
git commit -m "feat(auth): add opaque session configuration"
```

### Task 2: Identity/workspace/session migrations

**Files:**
- Create: `src/cowork_agent/persistence/migrations/005_identity_workspace_sessions.sql`
- Create: `src/cowork_agent/persistence/migrations/005_identity_workspace_sessions.down.sql`
- Test: `tests/unit/persistence/test_identity_session_migration.py`, `tests/integration/persistence/test_identity_repositories.py`

**Interfaces:** tables `app_users`, `workspaces`, `workspace_members`, `app_sessions` (bound to both `user_id` and `workspace_id`); `mailbox_connections.workspace_id`.

- [ ] **Step 1: Write a failing migration contract test**

```python
def test_session_schema_stores_hash_only() -> None:
    sql = _migration("005_identity_workspace_sessions.sql")
    assert "CREATE TABLE app_sessions" in sql
    assert "token_hash char(64)" in sql
    assert "session_token" not in sql
```

- [ ] **Step 2: Confirm RED**

Run: `python -m pytest tests/unit/persistence/test_identity_session_migration.py -q`

Expected: the migration file is absent.

- [ ] **Step 3: Implement forward and down migrations**

```sql
CREATE TABLE app_sessions (
  token_hash char(64) PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at)
);
```

Create the other identity tables, add the mailbox workspace column, indexes for live sessions/memberships, and a reverse-order down migration. Do not edit migration 001–004.

- [ ] **Step 4: Confirm GREEN**

Run: `python -m pytest tests/unit/persistence/test_identity_session_migration.py tests/integration/persistence/test_identity_repositories.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/persistence/migrations/005_identity_workspace_sessions.sql src/cowork_agent/persistence/migrations/005_identity_workspace_sessions.down.sql tests/unit/persistence/test_identity_session_migration.py tests/integration/persistence/test_identity_repositories.py
git commit -m "feat(storage): add identity workspace session schema"
```

### Task 3: Postgres identity, session, and mailbox repositories

**Files:**
- Create: `src/cowork_agent/persistence/repositories/identity.py`
- Modify: `src/cowork_agent/persistence/repositories/postgres.py`
- Modify: `src/cowork_agent/persistence/repositories/__init__.py`
- Test: `tests/integration/persistence/test_identity_repositories.py`

**Interfaces:** `resolve_or_create_principal(email) -> VerifiedPrincipal`; `create(user_id, now) -> tuple[str, datetime]`; `resolve(token, now) -> VerifiedPrincipal | None`; `revoke(token, now) -> bool`; a Postgres mailbox repository satisfying the existing `MailboxConnectionRepository` port.

- [ ] **Step 1: Write failing real Postgres tests**

```python
principal = await identities.resolve_or_create_principal("owner@example.com")
assert principal.user_id != "owner@example.com"
token, _ = await sessions.create(principal.user_id, now=NOW)
assert await sessions.resolve(token, now=NOW) == principal
assert await sessions.revoke(token, now=NOW)
assert await sessions.resolve(token, now=NOW) is None
```

- [ ] **Step 2: Confirm RED**

Run: `python -m pytest tests/integration/persistence/test_identity_repositories.py -q`

Expected: repository imports fail.

- [ ] **Step 3: Implement atomically**

Normalize email before lookup. In one database transaction create/select the user, a default workspace, and its membership. Store and look up only `session_token_hash(token)`; reject revoked/expired sessions. Scope mailbox queries and deletes by internal user ID.

- [ ] **Step 4: Confirm GREEN**

Run: `python -m pytest tests/integration/persistence/test_identity_repositories.py tests/integration/persistence/test_postgres_repositories.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/persistence/repositories/identity.py src/cowork_agent/persistence/repositories/postgres.py src/cowork_agent/persistence/repositories/__init__.py tests/integration/persistence/test_identity_repositories.py
git commit -m "feat(auth): persist Gmail principals and app sessions"
```

### Task 4: Gmail callback cookie and FastAPI principal resolver

**Files:**
- Modify: `src/cowork_agent/identity.py`
- Modify: `src/cowork_agent/integrations/gmail/provider.py`
- Modify: `src/cowork_agent/app.py`
- Modify: `src/cowork_agent/api/chat.py`
- Modify: `.env.example`
- Test: `tests/integration/api/test_principal_boundary.py`

**Interfaces:** `GmailConnectionService.complete(..., principal)` persists under an internal user; `principal_from_request(request)` resolves the session cookie; callback response sets the cookie; missing/expired/revoked sessions return 401 while ownership failures remain 404.

- [ ] **Step 1: Write failing API tests**

```python
callback = await client.get(callback_url, follow_redirects=False)
assert "HttpOnly" in callback.headers["set-cookie"]
assert "SameSite=lax" in callback.headers["set-cookie"]
assert (await anonymous.get("/v1/mail-todo/connections")).status_code == 401
```

Add two-user cases proving user B gets 404 for user A’s connection, run, and chat session.

- [ ] **Step 2: Confirm RED**

Run: `python -m pytest tests/integration/api/test_principal_boundary.py -q`

Expected: current callback does not set a cookie and identity comes from the mailbox.

- [ ] **Step 3: Wire the smallest change**

Build Postgres connection/identity/session repositories when `DATABASE_URL` is set. After the verified Gmail callback, resolve/create the internal principal, persist the mailbox under it, create a session, and set:

```python
response.set_cookie(
    key=settings.cookie_name,
    value=token,
    httponly=True,
    secure=settings.cookie_secure,
    samesite="lax",
    max_age=settings.session_ttl_seconds,
    path="/",
)
```

Resolve every protected mail/chat route from that cookie. Never expose the token in JSON, redirects, logs, errors, or frontend URLs.

- [ ] **Step 4: Confirm GREEN**

Run: `python -m pytest tests/integration/api/test_principal_boundary.py tests/unit/integrations/gmail/test_provider.py tests/unit/test_chat_runtime_composition.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/cowork_agent/identity.py src/cowork_agent/integrations/gmail/provider.py src/cowork_agent/app.py src/cowork_agent/api/chat.py .env.example tests/integration/api/test_principal_boundary.py
git commit -m "feat(auth): resolve FastAPI principals from opaque sessions"
```

### Task 5: ADR and Increment 1 verification

**Files:**
- Create: `docs/adr/ADR-006-supabase-managed-data-with-gmail-sessions.md`
- Modify: `docs/adr/README.md` if it indexes ADRs
- Test: `tests/integration/persistence/test_identity_repositories.py`

- [ ] **Step 1: Add ADR-006**

Record Supabase-managed Postgres, Gmail OAuth, FastAPI opaque sessions, internal user/workspace IDs, no browser data API, and rejected Supabase Auth/email-primary-key/SQLite alternatives.

- [ ] **Step 2: Add migration rollback proof**

Test applying migration 005 and its down companion against real PostgreSQL, with only an environmental skip when `PG_TEST_URL` is unavailable.

- [ ] **Step 3: Run final checks**

Run: `python -m pytest tests/unit/security tests/unit/persistence/test_identity_session_migration.py tests/integration/api/test_principal_boundary.py tests/integration/persistence/test_identity_repositories.py -q`

Run: `python -m ruff check .`

Run: `python -m mypy src`

- [ ] **Step 4: Commit**

```bash
git add docs/adr/ADR-006-supabase-managed-data-with-gmail-sessions.md docs/adr/README.md tests/integration/persistence/test_identity_repositories.py
git commit -m "docs(adr): record Supabase Postgres session boundary"
```

## Deferred by Design

Increment 2 replaces in-memory chat sessions with durable sessions and Redis buffering. Increment 3 adds private Supabase Storage, project/document/job durability, Qdrant project retrieval, citations, retention, and three-store deletion. Neither starts until this plan’s migration and authorization-isolation gates are green.
