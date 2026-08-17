# Implementation Plan: Parallel chat lifecycle

## Overview

Replace the single global chat stream with durable per-turn backend lifecycle
records and page-session per-chat frontend runtimes. Preserve existing API
shapes by adding optional fields and lifecycle operations.

## Architecture decisions

- Contract first: migration and typed lifecycle fields precede UI work.
- No client queue or global concurrency cap.
- One active generation per chat, enforced in the per-chat runtime.
- A server `generating` row without a matching live runtime renders as
  `interrupted` after reload.
- No browser storage for drafts.

## Task list

### Phase 1: Durable lifecycle foundation

- [ ] Add migration 014 with nullable assistant response, lifecycle status,
  idempotency key, error code, constraints, and rollback.
- [ ] Extend domain/history ports and Postgres repository with lifecycle
  begin/complete/fail/cancel operations.
- [ ] Add persistence tests for backfill, ownership, idempotency, and updates.

### Checkpoint: persistence

- [ ] Focused domain and persistence tests pass.
- [ ] Migration is additive for existing completed rows.

### Phase 2: Streaming lifecycle

- [ ] Persist the user turn before routing/provider work and emit its identity.
- [ ] Complete or fail the same durable turn; expose chat-scoped cancellation.
- [ ] Extend history responses with optional lifecycle fields.
- [ ] Add controller/API reproduction tests before implementation.

### Checkpoint: backend

- [ ] AI Chat unit and API integration routes pass.
- [ ] Ruff and mypy pass for changed backend surfaces.

### Phase 3: Per-chat frontend runtime

- [ ] Add failing hook tests for rapid cross-chat parallel submission.
- [ ] Replace global transcript/draft/controller/generation state with a
  session-keyed page-memory store.
- [ ] Optimistically add new chats to history before the stream begins.
- [ ] Preserve per-chat drafts and partial response state across navigation.
- [ ] Implement isolated stop and same-turn retry.

### Phase 4: Sidebar and completion UX

- [ ] Render generating and terminal state per chat.
- [ ] Add inactive completion unread marker and subtle accessible notification.
- [ ] Preserve focus and current active chat on background completion.

### Checkpoint: frontend

- [ ] Focused Vitest tests pass.
- [ ] `corepack pnpm test -- --run`, `check-types`, and `lint` pass.
- [ ] Original two-chat browser race passes with no console errors.

### Final checkpoint

- [ ] Full offline backend suite passes or unrelated failures are isolated.
- [ ] Runtime behaviour matches the confirmed design.
- [ ] Diff contains no unrelated cleanup, secrets, or generated artifacts.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Duplicate turn on retry | High | Unique `(session_id, idempotency_key)` and update-in-place |
| One stream overwrites another | High | Runtime map keyed by session ID; no shared AbortController |
| Reload looks permanently active | Medium | Server `generating` maps to interrupted without live runtime |
| Existing history breaks | High | Additive response fields and migration backfill |
| Scope expands into usage accounting | Medium | Preserve backend error codes; do not implement quotas |

## Open questions

None. The behavioural choices and migration authority are confirmed.
