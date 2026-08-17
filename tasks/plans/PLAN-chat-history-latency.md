# Implementation Plan: Chat history switch latency

> **Created:** 2026-08-17
> **Status:** Step 0 (harness + baseline) lands in this change. Steps 1–6
> are not implemented until a human picks the next wave.
> **Track file:** `evaluations/CHAT/latency/TRACK.md`
> **Roadmap:** `evaluations/CHAT/latency/ROADMAP.md`

## Overview

Users wait 2–5 seconds after clicking another saved chat. This plan
separates **measurement** from **optimization**. We do not change the
load path until the Playwright harness has a committed baseline.

## Architecture Decisions

- Synthetic Playwright tests mock `/backend/v1/cowork/chat/*` so CI does
  not depend on Gmail, Gemini, or Postgres. They still exercise the real
  React switch path.
- Live mode (`CHAT_LATENCY_LIVE=1`) is opt-in and records, it does not
  fail CI on a 2–5 s budget.
- Reports are metadata-only (timings, bytes, turn counts).
- One optimization per TRACK.md row. Neutral is a revert.

## Task List

### Phase 0: Measure (this change)

- [x] Task 0.1: Dedicated Playwright project `chat-latency`
- [x] Task 0.2: TRACK.md / ROADMAP.md under `evaluations/CHAT/latency/`
- [x] Task 0.3: Run the suite and paste the first synthetic numbers

### Checkpoint: Baseline

- [x] Synthetic suite green (4 passed, live skipped)
- [x] TRACK.md latest-run table filled
- [ ] Human reviews the roadmap before Step 1

### Phase 1: Perceived UX (roadmap Step 1)

- [x] Task 1: Split list vs transcript loading; no stale flash

### Phase 2: Repeat-visit cache (roadmap Step 2)

- [x] Task 2: In-memory LRU of loaded transcripts

### Phase 3: Cold-visit payload (roadmap Step 3–4)

- [x] Task 3: Slim `GET .../messages` (no `rag_evidence.content` on list)
- [x] Task 4: Backend list cheaper — one checkout, no check ping, warmer pool

### Phase 4: Polish (roadmap Step 5–6)

- [ ] Task 5: Hover prefetch, capped
- [ ] Task 6: Virtualize — only if render p50 stays high

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Gating CI on live 2–5 s | High | Live is skip-by-default |
| Caching wrong session | High | Key cache by session id; abort in-flight |
| Slim payload breaks evidence | High | Keep preview; lazy-load content |
| SQL migration by accident | High | Ask before migrations |

## Open Questions

- Is the 2–5 s mostly remote Supabase or a fat JSON body? Step 0 live
  run + `request_duration_ms` vs `payload_bytes` answers this.
- Should Step 3 add a new evidence route or reuse an existing one?
