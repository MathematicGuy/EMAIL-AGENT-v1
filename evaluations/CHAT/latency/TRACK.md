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

Live RUM against the real API was **not** collected in this session
(frontend and API were down). Run
`CHAT_LATENCY_LIVE=1 npx playwright test --project=chat-latency -g @live`
on a stack with at least two saved chats to pin `request_duration_ms` vs
`payload_bytes`.

## Attempt ledger

| Date | Change | Baseline → result (p50 click→visible) | Verdict | Why |
|---|---|---|---|---|
| 2026-08-17 | Harness only. No product change. | Instant 91 ms · 2500 ms API → 2620 ms UX · A→B→A 459–491 ms · heavy 352 KB → 162 ms | baseline | Frontend paint is 11–107 ms. The 2–5 s user wait tracks GET `/messages`, not React. Repeat visits refetch. Stale transcript stays up for the whole wait (~800 ms on a 400 ms API). Live stack was down; no RUM row yet. Snapshot: `baselines/baseline-2026-08-17-harness.json`. |
| 2026-08-17 | Step 1 skeleton + split loading flags; Step 2 in-memory LRU cache (20) | Repeat B→A **78 ms** (was 459–491) while API still 419 ms. Stale flash **0 ms**. Instant cold 339 ms. 2500 ms API → 3142 ms UX. | keep | Cache hit paints before refetch. Recents no longer share the transcript spinner. Cold visit still waits on GET — next is slim payload / cheaper `list_messages`. Snapshot: `baselines/baseline-2026-08-17-step1-2.json`. |
| 2026-08-17 | Step 3 slim GET `/messages` (omit `rag_evidence.content`; `include_content=true` on drawer) | Heavy payload **352 673 → 31 633 B**. Repeat B→A 66 ms. Cold mock still ~18 ms API. | keep | Transfer/parse of fat chunks is gone. Synthetic cold time did not move (local mock). Live 2–5 s is still likely sequential Postgres — that is Step 4. Snapshot: `baselines/baseline-2026-08-17-step3.json`. |
| 2026-08-17 | Step 4 one checkout + drop `check_connection` + warmer pool; skip document N+1 on list | Synthetic mocks **will not** show the WAN win. Unit test: require+list = 1 checkout. | keep | Code matches the research ranking. Confirm live `request_duration_ms` on a Seoul RTT before calling the 2–5 s gone. |
| 2026-08-17 | Step 5 prefetch Recents on hover/focus (cap 2) | Repeat-visit path already 66–78 ms from cache. Prefetch warms the next chat. | keep | Extra GETs only for uncached neighbors. |
| 2026-08-17 | Step 6 virtualize long transcripts | Heavy render after slim payload is **73 ms**. | skipped | Neutral complexity. Revisit only if a live 200+ turn thread janks. |

## Latest synthetic run

<!-- LATENCY-TRACK:SYNTHETIC-START -->

Last synthetic run: `2026-08-17T08:29:57.162Z` · browser `chromium` · report `evaluations/CHAT/latency/runs/2026-08-17T08-29-57-162Z.json`

| Scenario | n | p50 click→visible (ms) | p95 | max | p50 API (ms) | p50 UI after API (ms) |
|---|---:|---:|---:|---:|---:|---:|
| mocked-instant-cold-switch | 1 | 342 | 342 | 342 | 18 | 38 |
| mocked-2500ms-user-report | 1 | 3143 | 3143 | 3143 | 2525 | 360 |
| mocked-repeat-first-a | 1 | 1110 | 1110 | 1110 | 420 | 422 |
| mocked-repeat-a-to-b | 1 | 877 | 877 | 877 | 425 | 402 |
| mocked-repeat-b-to-a | 1 | 66 | 66 | 66 | 427 | 0 |
| mocked-heavy-payload | 1 | 363 | 363 | 363 | 18 | 73 |

<!-- LATENCY-TRACK:SYNTHETIC-END -->

## How to log an attempt

1. Run the synthetic suite. Confirm the latest-run table moved.
2. If you have a live stack, run `CHAT_LATENCY_LIVE=1` and paste p50/p95
   into a new ledger row. Do not paste message text.
3. Change **one** thing from [ROADMAP.md](./ROADMAP.md).
4. Re-run the same command, same machine class, same warmup.
5. Keep the change only if the primary metric beat run-to-run noise **and**
   the suite stayed green. Otherwise revert and still log the row.
