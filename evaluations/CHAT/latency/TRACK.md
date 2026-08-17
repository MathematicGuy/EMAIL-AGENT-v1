# Chat history latency track

Living ledger for chat-switch UX. Append a row after **every** optimization
attempt, including the ones you revert. Neutral changes are a revert — see
the performance-optimization skill.

**Primary metric:** `click_to_first_message_visible_ms` (user click on a
recent chat → first message of the target chat is visible).

**Do not store** raw messages, prompts, or chunk text in this folder.

## UX targets

| Perceived wait | User experience |
|---|---|
| < 100 ms | Instant. Cache hit or already-rendered. |
| < 300 ms | Smooth. Acceptable for a network round-trip on LAN. |
| < 1000 ms | Noticeable pause. Needs a skeleton, not a blank/stale pane. |
| 2000–5000 ms | Broken UX. Matches the reported switch delay. |

INP-style interaction budget for this click is **200 ms to first paint of
a dedicated loading/skeleton state**, even if the data is still in flight.

## What the 2026-08-17 baseline proved

The reported 2–5 s is **not React**. With an instant mocked API the
transcript appears in **91 ms**. A **2500 ms** mocked `GET .../messages`
becomes a **2620 ms** click→visible wait (60 ms of that is UI). Switching
back to a chat you just left **refetches** and waits again. The previous
transcript stays on screen for the whole wait (814 ms stale flash on a
~420 ms API). A **352 KB** history (16 turns, 80 evidence blobs) still
paints in **107 ms** once the JSON is local — virtualizing the list is
the last lever, not the first.

Live RUM against the real API was collected twice on 2026-08-17:

1. Operator UI (not `fe`): cold/repeat/prefetch all **2.0–2.5 s**. Snapshot:
   `baselines/baseline-2026-08-17-live.json`.
2. `fe` worktree Vite + API (`127.0.0.1:5173` / `:8000`, cache + prefetch
   confirmed in served source): cold **2.92 s** (API 1.72 s, 456 B),
   repeat **68 ms**, hover-prefetch **58 ms**. Stale flash **0 ms**. GET
   `/messages` is still **1.0–1.7 s** in the background. Snapshot:
   `baselines/baseline-2026-08-17-live-fe-cache.json`.

## Attempt ledger

| Date | Change | Baseline → result (p50 click→visible) | Verdict | Why |
|---|---|---|---|---|
| 2026-08-17 | Harness only. No product change. | Instant 91 ms · 2500 ms API → 2620 ms UX · A→B→A 459–491 ms · heavy 352 KB → 162 ms | baseline | Frontend paint is 11–107 ms. The 2–5 s user wait tracks GET `/messages`, not React. Repeat visits refetch. Stale transcript stays up for the whole wait (~800 ms on a 400 ms API). Live stack was down; no RUM row yet. Snapshot: `baselines/baseline-2026-08-17-harness.json`. |
| 2026-08-17 | Step 1 skeleton + split loading flags; Step 2 in-memory LRU cache (20) | Repeat B→A **78 ms** (was 459–491) while API still 419 ms. Stale flash **0 ms**. Instant cold 339 ms. 2500 ms API → 3142 ms UX. | keep | Cache hit paints before refetch. Recents no longer share the transcript spinner. Cold visit still waits on GET — next is slim payload / cheaper `list_messages`. Snapshot: `baselines/baseline-2026-08-17-step1-2.json`. |
| 2026-08-17 | Step 3 slim GET `/messages` (omit `rag_evidence.content`; `include_content=true` on drawer) | Heavy payload **352 673 → 31 633 B**. Repeat B→A 66 ms. Cold mock still ~18 ms API. | keep | Transfer/parse of fat chunks is gone. Synthetic cold time did not move (local mock). Live 2–5 s is still likely sequential Postgres — that is Step 4. Snapshot: `baselines/baseline-2026-08-17-step3.json`. |
| 2026-08-17 | Step 4 one checkout + drop `check_connection` + warmer pool; skip document N+1 on list | Synthetic mocks **will not** show the WAN win. Unit test: require+list = 1 checkout. | keep | Code matches the research ranking. Confirm live `request_duration_ms` on a Seoul RTT before calling the 2–5 s gone. |
| 2026-08-17 | Step 5 prefetch Recents on hover/focus (cap 2) | Repeat-visit path already 66–78 ms from cache. Prefetch warms the next chat. | keep | Extra GETs only for uncached neighbors. |
| 2026-08-17 | Step 6 virtualize long transcripts | Heavy render after slim payload is **73 ms**. | skipped | Neutral complexity. Revisit only if a live 200+ turn thread janks. |
| 2026-08-17 | Live RUM. No new product change. Real API + real persist seed (3 one-turn @mail chats). No route mocks. | Cold **1973 ms** (API 1665 ms, 456 B) · Repeat **1963 ms** (API 1862 ms) · Prefetch **2520 ms** (API 1997 ms) · stale **1882–2411 ms** | baseline (live) | Wait ≈ GET `/messages` to Supabase, not payload size. Running Vite is not the `fe` cache/prefetch UI — restart Vite from `.worktrees/fe/frontend` before attributing Step 1–5. Snapshot: `baselines/baseline-2026-08-17-live.json`. |
| 2026-08-17 | Same live harness against `fe` Vite + `fe` API (cache/prefetch in served source). | Cold **2922 ms** (API 1716 ms) · Repeat **68 ms** (API 1134 ms, UI 0) · Prefetch **58 ms** (API 989 ms, UI 0) · stale **0 ms** | keep (Steps 1–2, 5) | Cache and hover-prefetch match synthetic. Cold visit still waits on GET `/messages` (~1.0–1.7 s) plus ~1.2 s after the JSON. Snapshot: `baselines/baseline-2026-08-17-live-fe-cache.json`. |

## Latest synthetic run

<!-- LATENCY-TRACK:SYNTHETIC-START -->

Last synthetic run: `2026-08-17T09:56:37.501Z` · browser `chromium` · report `evaluations/CHAT/latency/runs/2026-08-17T09-56-37-501Z.json`

| Scenario | n | p50 click→visible (ms) | p95 | max | p50 API (ms) | p50 UI after API (ms) |
|---|---:|---:|---:|---:|---:|---:|
| mocked-instant-cold-switch | 1 | 317 | 317 | 317 | 3 | 18 |
| mocked-2500ms-user-report | 1 | 3243 | 3243 | 3243 | 2552 | 365 |
| mocked-repeat-first-a | 1 | 1160 | 1160 | 1160 | 412 | 445 |
| mocked-repeat-a-to-b | 1 | 950 | 950 | 950 | 424 | 441 |
| mocked-repeat-b-to-a | 1 | 94 | 94 | 94 | 438 | 0 |
| mocked-heavy-payload | 1 | 428 | 428 | 428 | 8 | 93 |

<!-- LATENCY-TRACK:SYNTHETIC-END -->

## Latest live run

<!-- LATENCY-TRACK:LIVE-START -->

Last live run: `2026-08-17T09:44:49.803Z` · browser `chromium` · report `evaluations/CHAT/latency/runs/2026-08-17T09-44-49-803Z.json`

| Scenario | n | p50 click→visible (ms) | p95 | max | p50 API (ms) | p50 UI after API (ms) |
|---|---:|---:|---:|---:|---:|---:|
| live-cold-switch | 1 | 2922 | 2922 | 2922 | 1716 | 1166 |
| live-repeat-switch | 1 | 68 | 68 | 68 | 1134 | 0 |
| live-prefetch-switch | 1 | 58 | 58 | 58 | 989 | 0 |

<!-- LATENCY-TRACK:LIVE-END -->

## How to log an attempt

1. Run the synthetic suite. Confirm the latest-run table moved.
2. If you have a live stack, run `CHAT_LATENCY_LIVE=1` and paste p50/p95
   into a new ledger row. Do not paste message text.
3. Change **one** thing from [ROADMAP.md](./ROADMAP.md).
4. Re-run the same command, same machine class, same warmup.
5. Keep the change only if the primary metric beat run-to-run noise **and**
   the suite stayed green. Otherwise revert and still log the row.
