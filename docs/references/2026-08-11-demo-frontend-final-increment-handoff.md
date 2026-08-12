# DEMO frontend final-increment handoff (inline execution)

Created: 2026-08-11 | Workspace: `E:\VIN-INTERNSHIP\EMAIL-AGENT-v1`
Purpose: everything needed to implement the final DEMO frontend milestone
YOURSELF (inline execution) in a new or continued session. PRD-v2 backend is
fully accepted; only the Streamlit showcase remains.

> Saved in the repo root because the session permission mode blocks writes to
> the OS temp directory. Untracked; do not commit unless requested.

## 1. Session-start checklist (in order)

- [ ] `cd E:\VIN-INTERNSHIP\EMAIL-AGENT-v1`; work in worktree
      `.claude\worktrees\demo-frontend` on branch `feature/demo-frontend-chat`
      (HEAD `3cc725b`). The root worktree stays on `dev`.
- [ ] Read `AGENTS.md` (project rules, dependency direction, verification rule).
- [ ] Read `docs/SPEC-Demo-Frontend.md` fully — it GOVERNS `src/cowork_agent/gui/`
      and is the single authority for this milestone. Version 2.3.
- [ ] Read `docs/references/PRD2-chat-memory-orchestration.md` — current-state
      authority for the accepted backend (do not re-verify accepted milestones).
- [ ] Preflight: `docker start cowork-pg` if down; `git status --short --branch`
      in the worktree must be clean at start.
- [ ] Begin with §4 slice 1 (§9 live browser verification of the built §3.4 slice).

## 2. Git state at handoff

```text
dev                        021b74d  feat(chat): complete V2-M6 memory evaluation and governance
feature/demo-frontend-chat 3cc725b  merge(dev): bring completed V2-M6 governance into the demo-frontend branch
                           021b74d  (same as dev)
                           495d0e8  feat(gui): build the SPEC §3.4 AI Chat demo slice
                           ff614f0  chore(docs): remove obsolete repository RTK instructions
```

- `dev` is ahead of `origin/dev` by 1 commit (`021b74d`); NOT pushed (user did
  not ask). Do not push without explicit instruction.
- `495d0e8` (demo slice) touched only `src/cowork_agent/gui/`,
  `docs/SPEC-Demo-Frontend.md`, `tests/unit/gui/` — zero overlap with V2-M6;
  the merge was conflict-free. Post-merge proof: 253 passed
  (`tests/unit/gui` + `tests/unit/features/ai_chat` +
  `test_chat_runtime_composition.py`), ruff clean on gui + ai_chat.
- `.claude/worktrees/` and `.claude/.headroom_wrap_marker.json` are harness
  artifacts — never commit them.

## 3. Accepted backend (do NOT rework)

PRD-v2 is COMPLETE: AC-01..AC-18 DONE; V2-M1..M6 accepted; fresh final review
verdict `ship` (2026-08-11, read-only reviewer, hashes matched).

V2-M6 evidence (recorded in PRD2 dashboard §11):
- 224 unit passed; 41 live-DB/API passed with zero skips against `cowork-pg`.
- Launch gate: `scripts/run_paired_chat_evaluation.py --json` exit 0 with
  product-approved Moderate-MVP thresholds; all five hard safety counters zero.
- `.env.example` holds product-approved values: 90-day retention
  (`CHAT_PROFILE_RETENTION_SECONDS=7776000`, `CHAT_EPISODE_RETENTION_SECONDS=7776000`)
  and EVAL_* thresholds (deltas 0.05, scores 0.6, degradation 0.25).
- Ops CLIs, explicit-invocation only (no scheduler):
  `scripts/purge_chat_memory.py`, `scripts/backup_restore_chat_memory.py`.

Chat backend APIs READY for the demo client:
- `POST /v1/cowork/chat/sessions` — create session (verified principal: exactly
  one active Gmail mailbox connection, else 503).
- `POST /v1/cowork/chat/sessions/{id}/messages` — SSE stream
  (`delta` / `memory_citation` / `completed` / `error`; idempotency key;
  retired `tool_choices` rejected 422 before dispatch).
- Originating-session lifecycle: approve / complete / reject / delete-single
  endpoints for task episodes (implemented).
- MISSING (gate Increment B): `GET /sessions`, `GET /messages` (reload/history),
  profile CRUD HTTP (`/v1/cowork/chat/profile`), episode list/read
  (`GET /v1/cowork/chat/episodes`), structured task-proposal SSE/read payload.

## 4. Remaining DEMO work (SPEC-Demo-Frontend.md governs)

Slice order (each slice: tests-first where applicable, ruff + mypy clean,
smallest-scope pytest, update SPEC §3.5/§8.1 as-built tables afterward):

1. **§9 live browser verification of the built §3.4 slice.** Start backend
   (`mail-todo-api`) + GUI (`python scripts/run_gui.py`); walk SPEC §9 steps in
   a real browser (Chrome DevTools MCP available); capture snapshots/screenshots,
   console/network/storage evidence; confirm no raw email/prompt leakage in DOM,
   localStorage, sessionStorage, network history. Mark §9 run in SPEC when done.
2. **Increment B contract gaps — DECISION POINT with user before coding:**
   the missing REST contracts (GET sessions/messages, profile CRUD, episode
   list/read, structured proposal payload) are BACKEND additions. Confirm with
   the user whether to implement them on this branch (small additive routers
   over existing repositories/gateway) or keep Increment B locked.
   If approved: design DTOs metadata-only, no raw content, same namespace rules.
3. **Increment B UI** (only after its contracts exist): Preferences editor,
   in-chat task controls fed by the structured proposal payload (never parse
   assistant prose), Episode Insight provenance view, Deletion with refresh.
4. **DEMO acceptance:** SPEC §8 criteria 1-16 with browser evidence; update
   SPEC status tables + PRD2 dashboard DEMO row at verified boundaries.

## 5. Hard rules for the demo surface (SPEC §2 + AGENTS.md invariants)

- Pure API client: NO workflow/routing/generation/memory-policy logic in
  `src/cowork_agent/gui/`; NO client mocks of unbuilt capabilities.
- NEVER render raw email bodies, attachment content, full prompts, or opaque
  `source_id`; badges show memory kind only.
- Never parse assistant prose to build task cards; only typed SSE payloads.
- Session id lives in `st.session_state` only (no cookie/localStorage/file/DB).
- Fail-closed stream parsing: unknown event types / missing variant payloads
  are dropped (`chat_client.parse_stream_event`); transport faults become a
  synthetic `error` event, never an exception in the DOM.
- Retry reuses the failed turn's idempotency key (`chat_pending_turn`).
- Gmail stays `gmail.readonly`; the demo must not expose any email mutation.

## 6. Key seams (read before editing)

| Concern | File |
|---|---|
| GUI app / screens | `src/cowork_agent/gui/app.py` (`_ensure_chat_session`, `_run_chat_turn`, `memory_badges_html`, `_screen_memory` lock) |
| SSE transport | `src/cowork_agent/gui/chat_client.py` (`create_chat_session`, `stream_chat_turn`, `ChatTurnAccumulator`, `MEMORY_BADGES`, `parse_stream_event`) |
| GUI tests | `tests/unit/gui/test_app.py`, `tests/unit/gui/test_chat_client.py` |
| Chat API | `src/cowork_agent/api/chat.py` (payloads `extra="forbid"`) |
| Runtime composition | `src/cowork_agent/app.py` `_chat_controller_factory` + lifespan (sink/metrics/retention wired) |
| Spec | `docs/SPEC-Demo-Frontend.md` (§3.3 readiness, §3.4 boundary, §3.5 as-built, §7 missing contracts, §8/8.1 criteria, §9 live plan) |

## 7. Verification commands (smallest scope first)

```powershell
python -m pytest tests/unit/gui -q
python -m pytest tests/unit/features/ai_chat tests/unit/test_chat_runtime_composition.py -q
python -m pytest tests/integration/api/test_chat_api.py tests/integration/persistence -q  # needs cowork-pg
python -m ruff check <changed paths>
python -m mypy src
git diff --check
```

Known OUT-OF-SCOPE blockers (do not repair): repo-wide suite cannot collect
(missing `docx` in knowledge-ingestion; duplicate `test_query_guard` module
names); `python -m mypy src` shows only the 5 pre-existing `docx_extractor`
errors. A skipped PostgreSQL test is never acceptance evidence.

## 8. Orchestration notes

- Model routing (user preference, orchestrator discretion granted):
  Qwen3.7-plus-thinking default implement/research; Qwen3.7-Max hard/review/
  debug. Evidence 2026-08-11: Qwen3.7-Max reliable on full packets;
  plus-thinking stalled 3x on long packets. THIS milestone = inline execution.
- Codex-era artifacts in older docs (`$sol-advisor`, Luna/Terra lanes) ignored.
- Update SPEC as-built tables and PRD2 dashboard only at verified boundaries;
  prune, don't append history.
- Windows gotchas: external writable TEMP + unique `--basetemp` if pytest temp
  teardown fails; LF->CRLF warnings on new files are benign.

## 9. Immediate next action

In the worktree, start slice 1: run the §9 live browser verification of the
built §3.4 chat slice (backend + GUI up, walk the SPEC steps, capture
evidence), then record the outcome in SPEC §9/§8.1. Before any Increment B
code, ask the user about the missing backend REST contracts (§4 slice 2
decision point).
