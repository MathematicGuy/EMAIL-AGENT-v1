# ADR-010 — Local PostgreSQL for MVP control-plane latency; do not port chat memory to SQLite

- Status: Accepted
- Date: 2026-08-17
- Decision makers: Product/Engineering team
- Relates to: ADR-001, ADR-004, ADR-006 (Supabase + Gmail sessions), ADR-007,
  ADR-008
- Does not supersede: ADR-006. SQLite stays the *absent-`DATABASE_URL`*
  fallback. This ADR decides how a durable local MVP should run the same
  control plane, and what must not be rewritten to chase chat-history latency.
- Measured against: [evaluations/CHAT/latency/ROADMAP.md](../../evaluations/CHAT/latency/ROADMAP.md)

## Context

Switching Recents chats on the live MVP waits 2–5 s. The browser does not
talk to Supabase. It calls FastAPI `GET /v1/cowork/chat/sessions/{id}/messages`,
and FastAPI then talks to whatever `DATABASE_URL` points at.

The 2026-08-17 chat-latency harness already isolated the wait
([TRACK.md](../../evaluations/CHAT/latency/TRACK.md)):

- Instant mocked API → first message visible in **91 ms**. React/markdown is
  not the 2–5 s.
- Injected **2500 ms** API → **2620 ms** click→visible. User wait ≈ API wait.
- Repeat visit A→B→A still refetches. There is no client cache.
- A 352 KB / 16-turn payload paints in **107 ms** once it is local.

So the product question is not “SQLite vs Postgres as a query engine”. It is
“where does the control plane live, and how many WAN round-trips does one
history load pay?”

### What Postgres is used for today

When `DATABASE_URL` is set, FastAPI opens `psycopg_pool.AsyncConnectionPool`
and applies migrations `001`–`013`. PostgreSQL is the control plane. It is
**not** the company semantic store.

| Plane | Live store | Notes |
|---|---|---|
| Identity / workspace / opaque sessions | Postgres (`app_users`, `workspaces`, `workspace_members`, `app_sessions`) | Cookie hash only. Browser never receives a Supabase key (ADR-006). |
| Mailbox connections | Postgres when `DATABASE_URL` is set; else SQLite `.data/mail_todo.db` | Encrypted Gmail refresh token. |
| Email digest runs, tasks, outbox | Postgres (`digest_runs`, `tasks`, `task_run_links`, `outbox_events`) | ADR-001 source of truth. |
| Chat session registry + titles | Postgres (`chat_sessions`) | Project-bound (ADR-007). |
| Chat history (UI transcript) | Postgres (`chat_turns`) | Completed turns only. Not a fifth memory type. |
| Declarative memory | Postgres (`chat_profiles`) | Explicit user config only. |
| Episodic memory | Postgres (`chat_summary_episodes`, `task_episodes`) | Chat-native TaskEpisodes (ADR-004). |
| Projects + document jobs | Postgres (`projects`, `project_documents`, ingest/cleanup/audit tables) | Metadata and job state. |
| User-document lexical + chunk text | Postgres (`project_document_chunks.fts` `tsvector` + GIN) | ADR-008 ACL + FTS in one SQL query. |
| Short-term / working turns | In-process `InMemoryChatSessionBuffer` | Bounded N turns + TTL. `create_chat_session_buffer` ignores `durable`. |
| Company semantic memory | Local Turbovec + BM25 + RRF | `data/extracted/*.md`. Not Postgres. |
| User-document dense vectors | Per-project `.tvim` (local cache; durable copy in Storage) | ADR-008. |
| Source bytes | Supabase Storage | Signed upload. Not in SQL. |

`mail-todo-worker` refuses to start without `DATABASE_URL`.

### What the current SQLite path actually is

Without `DATABASE_URL`, composition binds:

- SQLite: mailbox connections, `runs.db`, `tasks.db`
- In-process only: outbox, chat session registry, chat history, profiles,
  TaskEpisodes, projects

That is not “local Postgres”. Durable chat memory, guest/Gmail sessions, and
user-document FTS are **unbound**. Reloading the API process loses transcripts.
This path is a no-network fallback, not an MVP memory store.

### Why the history GET is slow on hosted Postgres

`list_messages` does three sequential `pool.connection()` checkouts, then an
optional fourth for cited documents:

1. Cookie → `PostgresSessionRepository.resolve` (`app_sessions`)
2. `_require_session` → `PostgresChatSessionRegistry.require` (`chat_sessions`)
3. `PostgresChatHistoryRepository.list_turns` (`chat_turns` + owner join)
4. Optional `require_documents` if citation coordinates exist

The pool is `min_size=1`, `check=AsyncConnectionPool.check_connection` (a
network ping on **every** checkout), `max_idle=60`, `max_lifetime=300`.
`.env.example` documents a Supabase pooler URL. Transaction-mode Supavisor
(`:6543`) is the serverless pooler; a long-lived FastAPI should use a
**direct** or **session** `:5432` connection
([Supabase connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres)).
At 400–800 ms WAN RTT, three checked-out transactions are already 1.2–2.4 s
before JSON is serialized.

Chat **ingest** (`write_turn`) runs after the assistant text is produced. It
does not delay first SSE tokens. It does add one more WAN checkout before
`completed`. Agent **retrieval** during a turn hits the in-process buffer
(free), then Postgres for profile / eligible episodes / project FTS, then
local Turbovec. Those reads pay the same WAN tax as history load.

## Decision

1. **Keep PostgreSQL as the only durable control-plane engine** for identity,
   runs/tasks/outbox, chat sessions, chat history, declarative and episodic
   memory, projects, and project-document FTS. Hosted that remains Supabase
   Postgres. The browser still never calls PostgREST, Storage, or Auth
   (ADR-006).

2. **The recommended local / MVP durable runtime is the same engine on
   localhost**, not a SQLite rewrite. Set `POSTGRES_MODE=local` (Docker
   official Postgres **or** `supabase start` on `127.0.0.1:5432`). FastAPI
   applies the existing `001`–`013` migrations. `mail-todo-worker` keeps
   working. Chat history and typed memory persist across process restarts
   with LAN-millisecond SQL. Flip `POSTGRES_MODE=cloud` to use
   `DATABASE_URL_CLOUD` (session or direct `:5432`). The two databases
   are not synced.

3. **Do not port chat history, typed memory, or project-document FTS to
   SQLite.** The existing SQLite adapters stay mailbox / runs / tasks only.
   Promoting them would fork thirteen Postgres-specific migrations
   (`jsonb`, `timestamptz`, `text[]`, `tsvector` generated columns, GIN,
   `websearch_to_tsquery` / `ts_rank_cd`) and break ADR-008’s single ACL+FTS
   query. SQLite.org’s own guidance is that SQLite is an embedded engine, not
   a network client/server replacement; ADR-006 already rejected it for
   deployed multi-user persistence.

4. **Do not treat a new database as the first latency fix.** History-switch
   UX is still owned by
   [evaluations/CHAT/latency/ROADMAP.md](../../evaluations/CHAT/latency/ROADMAP.md):
   dedicated transcript loading state, in-process session cache, slim
   `GET /messages` (drop `rag_evidence.content` from the list payload), then
   one checkout + `check=None` + session/direct `:5432` + a warm pool. Those
   steps apply whether Postgres is in Seoul or on `127.0.0.1`.

5. **Chat history is not a fifth memory type.** Short-term working turns stay
   in-process. Declarative and episodic rows stay in Postgres. Company
   semantic memory stays Turbovec. User documents stay the separate plane
   (Postgres chunks + `.tvim`). Do not merge Email RAG and Chat stores.

### Target local MVP topology

```text
React SPA
   │  REST / SSE only
   ▼
FastAPI  (mail-todo-api + optional mail-todo-worker)
   │
   ├─ localhost Postgres   control plane + chat history + FTS
   ├─ InMemoryChatSessionBuffer   working turns
   ├─ Turbovec .tvim + BM25        company RAG (and project dense)
   └─ optional local Storage       project source bytes
```

Hosted topology is unchanged except the connection string: session or direct
`:5432`, not transaction `:6543`, for this long-lived process.

## Alternatives considered

### Promote today’s SQLite fallback to a full chat/memory store

- Pros: zero extra process; already used for mailbox/runs/tasks; microsecond
  local I/O.
- Cons: no durable chat/identity/project/FTS adapters exist; dialect rewrite
  of migrations `002`–`013`; FTS5 is not `tsvector` + GIN +
  `websearch_to_tsquery`; worker still requires `DATABASE_URL`; dual-engine
  tests forever. SQLite is the right embedded engine for a single-file local
  app, not a drop-in for this schema.
- Rejected for durable chat and memory. Keep as the no-`DATABASE_URL` smoke
  path only.

### Dual-write (SQLite hot path + remote Postgres SoT)

- Pros: local reads without giving up hosted durability.
- Cons: two writers, conflict rules, backfill, and a new failure mode on
  every turn. Overkill for an MVP whose measured wait is WAN checkouts, not
  SQL.
- Rejected.

### PGlite (WASM Postgres) in-process or via `PGLiteSocketServer`

PGlite can speak the Postgres wire protocol and load extensions such as
pgvector. It is built for browser / Node / Bun, not as the system catalog
for this FastAPI `psycopg` pool. A socket shim adds a second runtime to
avoid Docker, with weaker ops (backup, worker, Storage).
- Rejected for the MVP control plane. Acceptable later only as a unit-test
  fixture (`py-pglite`), not as the product store.

### Frontend IndexedDB / `localStorage` as the history store

- Pros: Recents switch would be instant after the first visit.
- Cons: raw transcripts on the client; ADR-007 already limits browser
  storage to the active Project ID; the latency ROADMAP forbids
  `localStorage` for chat bodies. Cache **in process** after `GET /messages`
  is enough for repeat visits.
- Rejected as a store. An in-memory LRU cache remains in-scope (ROADMAP
  Step 2).

### Redis for short-term / history

TARGET §2.4 allows Redis *or* in-process for short-term. Live composition
already chose in-process (`docs/superpowers/plans/2026-08-12-postgres-only-runtime.md`).
Redis does not remove the durable `chat_turns` read on a cold Recents click.
- Rejected as the history-latency project.

### Keep pointing local MVP at hosted Supabase and only tune the pool

Necessary for the hosted path (session/direct `:5432`, one checkout,
`check=None`, `min_size` 2–4). Insufficient alone for a local demo: the
operator still pays intercontinental RTT on every Recents click and every
memory read.
- Accepted as the **hosted** hardening. Not a substitute for localhost
  Postgres during local MVP.

## Consequences

- Local durable chat requires `DATABASE_URL` to a local Postgres. Omitting
  the URL still boots, but transcripts and typed memory will not survive
  restart. That is the existing fallback, not a bug to “fix” with a second
  schema.
- Chat-history and agent-memory ingest/retrieval latency on a local MVP
  drop from multi-hundred-millisecond WAN checkouts to localhost SQL, without
  forking repositories.
- Hosted latency still needs ROADMAP Steps 1–4. Changing the engine without
  collapsing three checkouts into one leaves most of the 2–5 s on the table.
- Adding a `docker-compose` / `supabase start` recipe is the implementation
  follow-up. It is not a SQL migration. Do not rewrite `001`–`013` for
  SQLite.
- Project-document hybrid search stays Postgres FTS + Turbovec (ADR-008).
  pgvector / ParadeDB remain the deferred GitHub #10 discussion, not this
  ADR.

## Implementation guardrails

- Do not add `SQLiteChatHistoryRepository` or a SQLite dialect of
  `project_document_chunks.fts`.
- Do not persist chat bodies, prompts, or RAG chunk text in `localStorage`,
  `IndexedDB`, or any `VITE_*` value.
- Do not point the browser at Supabase PostgREST.
- Ask before changing SQL migrations or RAG bootstrap fallbacks.
- Measure Recents switch with `npx playwright test --project=chat-latency`
  and log [TRACK.md](../../evaluations/CHAT/latency/TRACK.md). A local
  Postgres cutover should move live `request_duration_ms`; it will not move
  the mocked 2500 ms scenario.

## Suggested rollout (no code in this ADR)

1. **Local cutover (ops):** run Postgres on `127.0.0.1:5432`, set
   `DATABASE_URL`, boot API + worker, confirm migrations apply, create two
   chats, run `CHAT_LATENCY_LIVE=1`.
2. **Hosted cutover (ops):** same app, session or direct `:5432`, not
   transaction `:6543`.
3. **ROADMAP Steps 1–2:** skeleton + in-memory cache (frontend only).
4. **ROADMAP Steps 3–4:** slim payload; one checkout / pool settings. Ask
   before a new evidence route or any migration.

## Links

- [ADR-006 — Supabase managed data with Gmail sessions](./ADR-006-supabase-managed-data-with-gmail-sessions.md)
- [ADR-008 — Turbovec + Postgres FTS project document plane](./ADR-008-turbovec-project-document-plane.md)
- [Architecture — control plane and persistence](../../docs/architectures/c3-api-platform.md)
- [Architecture — the four typed memory scopes](../../docs/architectures/c3-api-ai-chat.md)
- [Chat history latency roadmap](../../evaluations/CHAT/latency/ROADMAP.md)
