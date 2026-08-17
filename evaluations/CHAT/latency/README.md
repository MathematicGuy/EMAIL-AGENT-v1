# Chat history loading latency

Measures the user-facing wait when switching between saved chats: click a
recent conversation → first message of that conversation is visible.

This is **UI latency**, not Chat routing quality or Chat-RAG grounding.
Reports are metadata-only: timings, payload bytes, turn counts. No message
text, prompts, or retrieved chunks.

## Commands

From the repository root, always `uv run` is for Python. This harness is
Playwright:

```powershell
# Synthetic / CI. Mocks the chat API. Writes evaluations/CHAT/latency/runs/*.json
# and refreshes the latest-run table in TRACK.md.
npx playwright test --project=chat-latency

# Live stack. Requires frontend + API and at least two saved chats.
$env:CHAT_LATENCY_LIVE = "1"
npx playwright test --project=chat-latency -g @live
```

`pnpm --dir frontend dev` is started automatically unless something is
already bound to `http://127.0.0.1:5173`.

## What is measured

| Field | Meaning |
|---|---|
| `click_to_first_message_visible_ms` | **Primary UX metric.** Click → target history visible. |
| `request_duration_ms` | GET `/v1/cowork/chat/sessions/{id}/messages` |
| `response_to_first_message_visible_ms` | JSON arrived → first target message painted |
| `payload_bytes` / `turn_count` | Size of the history response |
| `loading_indicator_observed` | Sidebar showed "Đang tải lịch sử…" |
| `stale_content_visible_ms` | Previous chat stayed on screen after the click |

## Files

| Path | Role |
|---|---|
| [TRACK.md](./TRACK.md) | Ledger of kept / reverted attempts and the latest synthetic run |
| [ROADMAP.md](./ROADMAP.md) | Step-by-step optimization plan with tradeoffs |
| `runs/*.json` | Local run artifacts (gitignored) |
| [baselines/](./baselines/) | Committed snapshots used for comparison |

## Budgets (synthetic)

These gate the **frontend + mocked network**, not a live Supabase round-trip.

| Scenario | Budget |
|---|---|
| Instant mocked switch | click→visible < 1500 ms, UI-after-API < 800 ms |
| 2500 ms mocked API | loading indicator shown; click→visible ≥ 2400 ms |
| A → B → A | three GETs today (no client cache); stale flash > 0 |
| Heavy payload (16 turns × 5 evidence) | still < 3000 ms click→visible |

Live numbers are recorded, not gated, until a committed live baseline exists.
