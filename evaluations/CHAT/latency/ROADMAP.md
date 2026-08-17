# Optimization roadmap — chat history switch

Scope: the 2–5 s pause when clicking another saved chat. Measure with
`npx playwright test --project=chat-latency` and log every attempt in
[TRACK.md](./TRACK.md). Change one step at a time.

## What the current code does

```text
click Recents item
  → Dashboard.handleSelectRecent
    → useStreamingChat.loadExistingChat
      → isHistoryLoading = true          // also used by the session list
      → GET /v1/cowork/chat/sessions/{id}/messages
      → deserialize every turn + rag_evidence.content
      → replace messages
      → isHistoryLoading = false
```

There is **no client cache**. Previous messages stay on screen until the
new payload arrives (stale flash). The Recents list and the transcript
share one loading flag.

Backend `list_messages` (`src/cowork_agent/api/chat.py`) loads every turn,
then resolves cited project documents so it can stamp `unavailable` on
coordinates.

Measured on 2026-08-17 (Chromium, mocked API,
`baselines/baseline-2026-08-17-harness.json`):

| Hypothesis | Result | Keep? |
|---|---|---|
| React / markdown is the 2–5 s | Instant switch 91 ms. 352 KB / 16 turns / 80 evidence blobs paint in 107 ms after the JSON arrives. | No. Do not start with virtualization. |
| User wait ≈ API wait | 2513 ms mocked API → 2620 ms click→visible (60 ms UI after). | Yes. Optimize GET `/messages` and perceived wait. |
| Repeat visit is free | A→B→A still pays the injected 400 ms (459–491 ms). Three GETs. | Yes. In-memory cache. |
| Stale transcript during wait | Previous chat stayed visible 814–818 ms on a ~420 ms API. | Yes. Skeleton / clear-on-switch. |
| Fat JSON is expensive locally | 352 KB mocked in 19 ms. | Local mock does not model WAN/Supabase. Slim payload still matters live. |

Ranked now that we have numbers:

1. **GET `/messages` wall time** (live 2–5 s). Remote Postgres + serialize
   + transfer. Synthetic 2500 ms delay reproduces the reported UX exactly.
   On `DATABASE_URL` the handler takes **three sequential pool checkouts**
   (cookie session → `chat_sessions.require` → `list_turns`) plus an
   optional fourth for cited documents. Pool is `min_size=1` with
   `check=check_connection` (a **network ping on every checkout**). That
   is many WAN RTTs, not three. Shared Supavisor `:6543` is transaction
   mode (serverless). See Step 4 research.
2. **No cache** — switching back costs the same as a cold visit.
3. **Stale transcript + shared loading flag** — the click is not
   acknowledged within the 200 ms INP budget. The Recents highlight also
   waits on HTTP (`activeConversationId` is set only after the GET).
4. **Payload size on a real network** — not proven locally; isolate with
   a live run (`payload_bytes` vs `request_duration_ms`).
5. **Cross-project wipe race** — changing `projectId` clears `messages`
   in a microtask and can erase a transcript that just loaded.
6. **Frontend parse** — leftover tens of milliseconds. Last.

## UX budgets we are steering toward

| Moment | Budget | Why |
|---|---|---|
| Click → skeleton / "this chat is loading" | ≤ 200 ms | Interaction feels acknowledged |
| Repeat visit to a chat already loaded this session | ≤ 100 ms | Memory is free compared to 2–5 s |
| Cold visit, last ~20 turns, slim payload | ≤ 300 ms LAN / ≤ 800 ms remote | Conversation is usable |
| Older turns / full evidence | After first paint | Progressive disclosure |

## Steps

### Step 0 — Harness and baseline (this change)

**Do:** Playwright scenarios + TRACK.md. No product optimization.

**Acceptance:** Synthetic suite green. Latest-run table filled. Live row
optional if the stack is up.

**Status:** Done. Instant 91 ms, 2500 ms API → 2620 ms UX, A→B→A refetch,
heavy payload 162 ms. Live not yet run.

**Tradeoff:** None. Cost is one dedicated Playwright project.

### Step 1 — Perceived latency: dedicated transcript loading state

**Do:** Split `isHistoryLoading` into `isSessionListLoading` vs
`isTranscriptLoading`. On switch, immediately show a transcript skeleton
(or a dimmed pane + spinner) and stop painting the previous chat as if it
were the new one.

**Files:** `useStreamingChat.ts`, `Dashboard.tsx`, `ChatStreamView.tsx`,
`Taskbar.tsx`.

**Measure:** `stale_content_visible_ms` → 0. `loading_indicator_observed`
stays true. Primary metric may not move.

**Status:** Done on `fe`. Recents highlight and transcript skeleton flip on
click. Stale flash 0 ms. Recents list spinner no longer fires on switch.

**Tradeoff:** Extra state. Recents no longer flicker a list-level spinner
when you are only changing transcript. This is a keep even if p50 is flat
because the click is no longer a lie.

**Revert if:** Skeleton is slower than today's stale flash on warm data
(unexpected) or Recents list regresses.

### Step 2 — In-memory session cache

**Do:** Keep a `Map<sessionId, ChatMessage[]>` (LRU, ~20 sessions).
`loadExistingChat` paints cache immediately, then revalidates in the
background. Abort in-flight fetches already exists.

**Measure:** `mocked-repeat-b-to-a` click→visible ≤ 100 ms and
`messages_fetch_count_after` can stay 3 (revalidate) while visible-time
drops. Update the "three GETs, wait for the third" assertion to "three
GETs, paint from cache before the third returns".

**Status:** Done on `fe`. Repeat B→A click→visible **78 ms** (budget ≤ 100)
while the background GET still takes ~419 ms. Three GETs remain
(revalidate). Snapshot `baselines/baseline-2026-08-17-step1-2.json`.

**Tradeoff:** Stale-while-revalidate can briefly show a turn that was
deleted elsewhere. Acceptable for single-user local/demo. Do not persist
the cache to `localStorage` (raw chat bodies).

**Revert if:** Repeat-visit p50 does not beat noise, or a user can see
another session's messages.

### Step 3 — Slim `GET .../messages` payload

**Do:** Stop sending `rag_evidence.content` on the list endpoint. Keep
`preview` + coordinates. Load full evidence when the panel opens.
Optionally cap default turns (`limit=30&before=`).

**Ask before shipping** if this needs a SQL migration or a new route —
the list query itself does not, a second evidence route might.

**Status:** Done on `fe` without a new route or migration. Default list
omits `content`. `?include_content=true` loads full chunks when the
drawer opens. Heavy payload **352 673 → 31 633 bytes**.

**Measure:** `payload_bytes` on `mocked-heavy-payload` and live p50.
Expect the live 2–5 s to drop if the wait was serialize + transfer +
parse, not the SQL round-trip alone.

**Tradeoff:** Evidence drawer needs a follow-up GET. Opening it is rarer
than switching chats. Do **not** drop `preview` or citations — the
transcript still has to look complete.

**Revert if:** Evidence panel is empty for stored turns, or Chat RAG
invariants in `tests/unit/domain` / controller tests break.

### Step 4 — Backend `list_messages` cheaper (Supabase WAN)

**Do not start** until a live `CHAT_LATENCY_LIVE=1` run shows
`request_duration_ms` still > 300 ms. Mocks will not move.

Research (2026-08-17): Context7 + official Supabase/psycopg docs. The
hypothesis “3 queries × 400–800 ms RTT” is **conservative**. Each
`async with pool.connection()` also runs `check=check_connection` (a
network ping on **every** checkout — [psycopg pool](https://www.psycopg.org/psycopg3/docs/advanced/pool.html))
and COMMITs on exit. Three checkouts can be **many WAN RTTs**, not
three. Current `DATABASE_URL` uses Shared Supavisor **transaction**
mode (`…pooler.supabase.com:6543`). Official guidance: that mode is for
**serverless**; a long-lived FastAPI should use **direct :5432** or
**shared session :5432**, not transaction 6543
([connect](https://supabase.com/docs/guides/database/connecting-to-postgres)).

**Do, in this order (no SQL migration):**

1. **One checkout for the whole handler.** Pass one `connection` into
   cookie resolve + `chat_sessions.require` + `list_turns` (and the
   optional document lookup). Today each opens its own
   `pool.connection()`. Biggest code win. No schema change.
2. **Stop paying `check_connection` on every checkout.** Official
   default is no check. `check=None`, or a local `closed` test. Keep
   `prepare_threshold=None` while still on :6543 (transaction mode
   cannot use prepared statements —
   [docs](https://supabase.com/docs/guides/troubleshooting/disabling-prepared-statements-qL8lEL)).
3. **Point `DATABASE_URL` at the persistent-backend string.** Try
   Direct `db.<ref>.supabase.co:5432` if the API host has IPv6; else
   Shared **session** `:5432` (already in `.env.example`). Dedicated
   PgBouncer `:6543` only if paid + IPv6/add-on. Shared poolers live
   on a **separate** server; dedicated/direct skip that hop
   ([latency FAQ](https://supabase.com/docs/guides/database/connecting-to-postgres)).
4. **Warm the client pool.** psycopg default `min_size` is **4**; we
   use **1**. `max_idle=60` / `max_lifetime=300` (defaults 600 / 3600)
   recycle sockets so the next click pays TLS. Raise toward defaults
   only after `pool.get_stats()` (`requests_wait_ms`, `connections_ms`).
5. **Only if still > 300 ms:** pipeline the three SELECTs on that one
   connection ([psycopg pipeline](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html)).
   Then lazy `require_documents`. Then `EXPLAIN` `list_turns` —
   **ask before any migration**.

**Ask before changing SQL migrations.**

**Measure:** live `request_duration_ms` + `pool.get_stats()` around a
switch. Synthetic mocks will not move.

**Tradeoff:** `unavailable` badges may be one request late if documents
are skipped. Never show a deleted citation as live. Sharing one
connection is a keep if live p50 drops by ~2+ RTTs. Bumping `min_size`
to 8 blindly can crowd Auth/Storage/PostgREST on a small compute.

**Status:** Done on `fe` (code + pool knobs). `load_owned_history` uses
one checkout for require + `list_turns`. List no longer N+1s
`require_document`. Pool is `min_size=2`, no per-checkout
`check_connection`, idle/lifetime back to psycopg defaults.
`DATABASE_URL` still operator-owned — `.env.example` already documents
session `:5432`. Pipeline not added. Live `request_duration_ms` not
re-measured in this session.

**Do not:** enable prepared statements on :6543; use
`NullConnectionPool`; pipeline across three checkouts; treat Shared
transaction as “faster” for this FastAPI; colocate by moving the
Supabase **region** without asking (that is a new project).

### Step 5 — Prefetch

**Do:** Prefetch `GET .../messages` on Recents hover or for the next
session in the list, into the Step 2 cache.

**Measure:** hover-then-click p50. Ignore if Step 2 already made repeat
visits instant.

**Tradeoff:** Extra GETs and more memory. Easy to over-fetch on scroll.
Keep a 1–2 session prefetch cap.

**Status:** Done on `fe`. Recents `mouseenter`/`focus` call `prefetchChat`,
capped at two in-flight GETs, skip if already cached or active.
Prefetch fills the Step 2 cache only — it does not change the open
transcript.

**Revert if:** Network/CPU up, primary metric flat.

### Step 6 — Virtualize long transcripts

**Do:** Only if `mocked-heavy-payload` `response_to_first_message_visible_ms`
stays high after slim payloads. Window the DOM; do not fetch less unless
Step 3 already paginates.

**Status:** Skipped. After Step 3, `mocked-heavy-payload`
`response_to_first_message_visible_ms` is **73 ms**. Virtualizing would
not beat that enough to pay scroll-jump / find-in-page cost.

**Tradeoff:** Scroll-jump and find-in-page pain. Last resort.

## Suggested wave order

```text
Step 0  harness          (done in this change)
Step 1  skeleton         perceived UX, low risk
Step 2  memory cache     biggest repeat-visit win
  checkpoint: re-run Playwright, fill TRACK.md
Step 3  slim payload     biggest cold-visit win if payload is fat
Step 4  backend          live only: 1 checkout → drop check → session/direct URL → warm pool
Step 5  prefetch         polish
Step 6  virtualize       only if render is the leftover
```

## Supabase WAN latency — research notes (2026-08-17)

Sources: Context7 `/supabase/supabase`, `/websites/supabase`,
`/websites/psycopg_psycopg3`; plus official pages the research agent
fetched. Not implemented in this note.

### What the docs change about our hypothesis

| Claim we had | Official / code correction |
|---|---|
| 3 sequential checkouts × 1 RTT | Each checkout also runs `check_connection` (network) and COMMIT. Worst case is **many** RTTs, not 3. |
| Shared pooler `:6543` is fine | Official: **transaction** mode is for serverless. Persistent backends should use **direct :5432** or **shared session :5432**. |
| Pooler always helps latency | Dedicated pooler is **on the DB machine** (lower latency). Shared pooler is on a **separate server**. Direct has **no pooler hop**, needs IPv6 or the IPv4 add-on. |
| `min_size=1` is conservative | psycopg default is **4**. After `max_idle=60` we shrink to one socket; the next click can pay a full TLS handshake. |

Project region in current env: **`ap-northeast-2` (Seoul)**. No pool
setting fixes a 400–800 ms path from a far API host
([regions](https://supabase.com/docs/guides/platform/regions)).

### Ranked strategies (config / code / ask-first)

| Rank | Strategy | Expected win | Kind | SQL migration? |
|---|---|---|---|---|
| 1 | One `pool.connection()` for cookie + require + `list_turns` | −2 checkout+check+COMMIT cycles | Code | No |
| 2 | `check=None` (or local-only) | −1–3 RTTs **per** remaining checkout | Config | No |
| 3 | Leave Shared `:6543`; use Direct `:5432` or Shared session `:5432` | Drops shared extra hop; matches “persistent backend” | Config (`DATABASE_URL`) | No |
| 4 | `min_size` 2–4; `max_lifetime` → 3600; don’t idle-out at 60 s | Fewer cold TLS handshakes (p99) | Config | No |
| 5 | Pipeline the 3 SELECTs on that one connection | 3 query RTTs → ~1. Only after (1) | Code | No |
| 6 | Colocate the API with Seoul | Cuts the RTT itself | Ops | No (region move = new project; ask) |
| 7 | Skip `require_documents` on list | −0–1 checkout | Code | No |
| 8 | Slimmer SQL / `limit` | Transfer, not RTT count | Code | **Ask** if new route/table |
| 9 | Prepared statements | Already off. Revisit only on session/direct. Will not turn 2–5 s into 300 ms | Config later | No |

### What not to do

- Enable prepared statements on **:6543**
- Treat Shared transaction as faster than session/direct for this API
- Use `NullConnectionPool` (closes after every use)
- Run Shared + Dedicated at high `pool_size` on a small compute
- Jump `min_size` to 8 without `get_stats()`
- Pipeline across three separate checkouts
- Skip document lookup in a way that paints a deleted citation as live
- Start with virtualization (harness already falsified FE parse)

### Live measurement before any Step 4 code

```powershell
$env:CHAT_LATENCY_LIVE = "1"
npx playwright test --project=chat-latency -g @live
```

Log `request_duration_ms` and, if possible, `pool.get_stats()` so a
checkout wait is distinguishable from query time. Then implement
Step 4.A (one connection) first.

## Out of scope

- Merging Email RAG and Chat workflows
- Qdrant
- Rewriting `data/extracted/*.md`
- Persisting raw email into history
- Putting chat bodies in `localStorage` or `VITE_*`
