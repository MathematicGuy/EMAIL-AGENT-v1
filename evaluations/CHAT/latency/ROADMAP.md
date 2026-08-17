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
   `check_connection`. A 400–800 ms Supabase RTT × 3 is already 1.2–2.4 s
   before JSON leaves the server.
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

### Step 4 — Backend `list_messages` cheaper

**Do:** Only after Step 3 numbers say the API is still the p50. Candidates:
one connection for principal + require + list_turns (today each opens
its own `pool.connection()`), skip `require_documents` on list (stamp
`unavailable` lazily), raise pool `min_size` above 1 if live traces show
checkout wait. Select fewer columns only if `EXPLAIN` says so.

**Ask before changing SQL migrations.**

**Measure:** live `request_duration_ms`. Synthetic mocks will not move.

**Tradeoff:** `unavailable` badges may be one request late. Document
lookup is correctness for deleted project docs — do not skip it in a
way that shows a dead citation as live. Sharing one connection is a
keep if live p50 drops by ~2 RTTs; bumping `min_size` costs idle
connections.

### Step 5 — Prefetch

**Do:** Prefetch `GET .../messages` on Recents hover or for the next
session in the list, into the Step 2 cache.

**Measure:** hover-then-click p50. Ignore if Step 2 already made repeat
visits instant.

**Tradeoff:** Extra GETs and more memory. Easy to over-fetch on scroll.
Keep a 1–2 session prefetch cap.

**Revert if:** Network/CPU up, primary metric flat.

### Step 6 — Virtualize long transcripts

**Do:** Only if `mocked-heavy-payload` `response_to_first_message_visible_ms`
stays high after slim payloads. Window the DOM; do not fetch less unless
Step 3 already paginates.

**Tradeoff:** Scroll-jump and find-in-page pain. Last resort.

## Suggested wave order

```text
Step 0  harness          (done in this change)
Step 1  skeleton         perceived UX, low risk
Step 2  memory cache     biggest repeat-visit win
  checkpoint: re-run Playwright, fill TRACK.md
Step 3  slim payload     biggest cold-visit win if payload is fat
Step 4  backend          only if live request_duration stays > 300 ms
Step 5  prefetch         polish
Step 6  virtualize       only if render is the leftover
```

## Out of scope

- Merging Email RAG and Chat workflows
- Qdrant
- Rewriting `data/extracted/*.md`
- Persisting raw email into history
- Putting chat bodies in `localStorage` or `VITE_*`
