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

## Latest synthetic run

<!-- LATENCY-TRACK:SYNTHETIC-START -->

Last synthetic run: `2026-08-17T07:43:58.423Z` · browser `chromium` · report `evaluations/CHAT/latency/runs/2026-08-17T07-43-58-423Z.json`

| Scenario | n | p50 click→visible (ms) | p95 | max | p50 API (ms) | p50 UI after API (ms) |
|---|---:|---:|---:|---:|---:|---:|
| mocked-instant-cold-switch | 1 | 91 | 91 | 91 | 11 | 38 |
| mocked-2500ms-user-report | 1 | 2620 | 2620 | 2620 | 2513 | 60 |
| mocked-repeat-first-a | 1 | 491 | 491 | 491 | 414 | 32 |
| mocked-repeat-a-to-b | 1 | 467 | 467 | 467 | 422 | 11 |
| mocked-repeat-b-to-a | 1 | 459 | 459 | 459 | 414 | 11 |
| mocked-heavy-payload | 1 | 162 | 162 | 162 | 19 | 107 |

<!-- LATENCY-TRACK:SYNTHETIC-END -->

## How to log an attempt

1. Run the synthetic suite. Confirm the latest-run table moved.
2. If you have a live stack, run `CHAT_LATENCY_LIVE=1` and paste p50/p95
   into a new ledger row. Do not paste message text.
3. Change **one** thing from [ROADMAP.md](./ROADMAP.md).
4. Re-run the same command, same machine class, same warmup.
5. Keep the change only if the primary metric beat run-to-run noise **and**
   the suite stayed green. Otherwise revert and still log the row.
