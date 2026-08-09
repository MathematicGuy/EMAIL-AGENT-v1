# SPEC — Cowork Demo Frontend (Final Showcase after PRD-v1 + PRD-v2)

| Field | Value |
|---|---|
| Document status | Spec — implementation gated on PRD-v1 and PRD-v2 completion |
| Version | 1.2 |
| Date | 2026-08-09 |
| Milestone position | Final phase after `V2-M6` (master-comparison §7: `DEMO`) |
| Depends on | PRD-v1 §15 acceptance passed; PRD-v2 §16 acceptance passed |
| Governs | `src/cowork_agent/gui/` (demo surface) |
| Vocabulary | All terms follow `CONTEXT.md` / `UBIQUITOUS_LANGUAGE.md` |

---

## 1. Purpose

A demonstration frontend that lets a human exercise the complete Cowork AI
Chat and executable-tool value loop end-to-end in a browser:

```text
Connect Gmail → open AI Chat Assistant → converse or invoke @Email
→ see a grounded Action Plan card in the chat thread
→ approve/complete/reject in chat → see memory inform the next turn
```

It exists to **prove the product story**, not to be the production Cowork
surface. It consumes only implemented, PRD-gated backend capabilities — it
must never scaffold or mock unimplemented milestone work
(AGENTS.md invariant 4).

## 2. Positioning and hard rules

1. The demo is a **client of the FastAPI backend**; it contains no workflow,
   routing, generation, or memory-policy logic. All intelligence stays in
   Agent Core.
2. **Read-only Gmail** — the demo never offers actions that send, delete,
   move, or modify mail.
3. **Raw email bodies are never rendered or cached** by the demo beyond what
   the backend already exposes (subject, sender, pointer). Development-only
   `processedEmails` metadata may be shown only behind the existing
   `APP_ENV` development gate.
4. **Attachments are reported as present only** (ADR-003); no attachment UI.
5. No scheduler, no recurring-run UI, no notification settings.
6. The demo must not introduce durable storage of its own; all state comes
   from backend endpoints.
7. Chat responses use the backend SSE contract. Tool calls, tool output, and
   memory citations render as typed embedded components, not parsed prose.
8. The UI must distinguish assistant text, tool execution status, Action Plan
   cards, and memory-recall indicators without exposing full prompts or raw
   memory payloads.

## 3. Delivery structure (two increments)

### 3.1 Increment A — Core Chat and `@Email` tool

| Screen | Capability shown | PRD-v1 basis |
|---|---|---|
| AI Chat Assistant | Primary `st.chat_message` / `st.chat_input` thread, session sidebar, history, streaming assistant response | PRD-v2 V2-M1, V2-M4 |
| In-chat `@Email` | Explicit tool trigger, visible execution status, and structured Action Plan cards with citation chips in the active thread | PRD-v1 FR-01..FR-13; PRD-v2 V2-M4 |
| Connect | Mailbox Connection list, Gmail OAuth connect/disconnect | PRD-v1 FR-03 |
| Knowledge | Corpus readiness, documents, and grounded query inspection for enterprise RAG | V1-M3 (`HybridSemanticMemory`) |
| Run audit | Chat, SSE, tool-route, validation, and stage-latency metadata only | PRD-v1 FR-16; PRD-v2 FR-17 |

### 3.2 Increment B — Chat memory and transparency

| Screen | Capability shown | PRD-v2 basis |
|---|---|---|
| Preferences | Explicit AI Chat persona/profile editor: language, tone, brevity, priority rules, and default tool permission | FR-03, FR-04, FR-15 |
| In-chat task controls | Inline Approve / Complete / Reject controls on `@Email` cards with validation and eligibility state | FR-07, §12 |
| Memory transparency | In-thread badges for active profile rules, eligible episodic hits, and semantic citations—never raw context | FR-12, FR-14, FR-17 |
| Episode insight | Provenance view for chat summaries and tool plans: status, eligibility, source session/turn, and versions | FR-06, FR-14 |
| Deletion | Delete a preference or episode with confirmation and post-deletion refresh | FR-15 |

Increment B screens are feature-flagged off until their backend endpoints
exist; the demo must run cleanly on an Increment-A-only backend.

## 4. Technology decision

**Streamlit** (existing `gui/app.py` + `scripts/run_gui.py`, `[gui]` extra).

| Option | Verdict | Reason |
|---|---|---|
| Streamlit (chosen) | ✔ | Already a dependency and wired; Python-only repo; fastest path to a credible demo; browser-verifiable via localhost | 
| React/Vite SPA | ✗ for demo | Adds a second toolchain for a showcase; justified only if the demo becomes the production surface |

Escalation rule: if the demo is ever promoted toward the production Cowork
surface, record an ADR and re-spec with a real design system; this spec does
not authorize that promotion.

## 5. Information architecture

```text
Cowork Demo
├── 1. AI Chat Assistant  (multi-turn chat, @Email, inline Action Plan cards)
├── 2. Connect            (Gmail OAuth and Mailbox Connections)
├── 3. Knowledge          (RAG readiness, documents, grounded query)
├── 4. Memory             (Preferences | Episodes | Deletion)
└── 5. Run audit          (chat, SSE, tool, route, and latency metadata)
```

Make AI Chat Assistant the default landing screen. Connect and Knowledge
remain supporting screens. The former standalone Run and Tasks experiences
move into the chat thread as tool status and Action Plan card components.

## 6. UX and quality bar

Apply `frontend-ui-engineering` principles within Streamlit's constraints:

1. **States are mandatory** for every data region: loading (skeleton/
   `st.status`), empty (explicit message + next action), error (safe error
   code + message, never a stack trace), success.
2. **Semantic color usage**: priority and status use color + text/icon pair;
   never color alone (WCAG 2.1 AA). Partial Plans get a distinct, labeled
   warning treatment.
3. **Realistic content**: use live backend data or fixtures from
   `tests/fixtures/emails/`; no lorem ipsum.
4. **No AI aesthetic**: keep the existing restrained palette
   (blue accent, neutral grays, priority border accents); no purple
   gradients, no oversized cards.
5. **Keyboard/screen-reader sanity**: native Streamlit widgets over raw HTML;
   where custom HTML is unavoidable (task cards), keep contrast ≥ 4.5:1 and
   provide text equivalents for badge meaning.
6. **Polling discipline**: bounded polling with deadline (existing pattern),
   terminal-state detection, and a visible timeout path.
7. **Idempotency**: Run creation always sends an `Idempotency-Key`; a retried
   click must never create a second Run.
8. **Bilingual copy**: current GUI is Vietnamese; keep one language per
   screen section and externalize strings so the demo can be switched to
   English for stakeholder showcases.
9. **RAG result consistency**: retrieval results on the Knowledge screen
   use the same `.citation-chip` styling as task-level citations; corpus
   status uses semantic color + text pairs (green + "Ready", amber +
   "Degraded", red + "Unavailable"). Grounded answers display inline
   citation chips that match the chunk provenance returned by retrieval.
10. **Native chat semantics**: use `st.chat_message` for user/assistant roles
    and `st.chat_input` for the composer. Preserve focus and message order
    while incremental SSE deltas arrive.
11. **Tool progress**: show a concise `st.status` state for queued/running/
    completed/failed `@Email` execution. Never render raw email payloads.
12. **Action Plan card**: group title, priority, deadline, ordered steps,
    missing-information warning, Gmail pointer, citations, and lifecycle
    controls in one keyboard-operable in-thread component.
13. **Memory transparency**: label declarative, episodic, and semantic sources
    with text plus icon; disclose source type and provenance, not full context.

## 7. Backend API contract assumptions

The demo consumes endpoints; it defines none. Expected inventory:

| Capability | Exists today | Expected from milestones |
|---|---|---|
| Create/list chat sessions | ✗ | V2-M1 / V2-M4 — `POST/GET /v1/cowork/chat/sessions` |
| Send message and stream response | ✗ | V2-M4 — `POST /v1/cowork/chat/sessions/{session_id}/messages` (SSE) |
| Chat history | ✗ | V2-M1 / V2-M4 — `GET /v1/cowork/chat/sessions/{session_id}/messages` |
| Execute `@Email` tool | existing standalone run API only | V2-M4 — internal `POST /v1/cowork/tools/email` wrapper |
| Health, OAuth connect/callback, connections list/delete, unread preview | ✔ `app.py` | — |
| Create/Run-status/Run-result | ✔ `/v1/mail-todo/runs*` | Compatibility mapper preserves these through V1-M4 |
| Task list/detail from persisted Tasks | ✗ | V1-M4 (or compatibility result shape suffices for Increment A) |
| Route/telemetry summary | partial (dev `processedEmails`) | V1-M4 basic telemetry exposure |
| Knowledge readiness (`GET /v1/mail-todo/knowledge/ready`) | ✗ (wired internally, not exposed as REST) | V1-M3 prerequisite — expose corpus readiness + chunk/doc counts |
| Document list (`GET /v1/mail-todo/knowledge/documents`) | ✗ | V1-M3 prerequisite — list loaded documents with title, section count, source URL |
| Grounded query (`POST /v1/mail-todo/knowledge/chat`) | ✗ | V1-M3 prerequisite — ad-hoc query returning grounded answer + citation chips + retrieval chunks with scores |
| Profile read/write/delete | ✗ | V2-M2 |
| Approve/complete/reject transitions | ✗ | V2-M4 in-chat episode transition endpoint |
| Episode view + deletion | ✗ | V2-M3 / V2-M6 |

If a needed read endpoint is missing at implementation time, file it against
the owning milestone — do not work around it with client-side logic.

## 8. Acceptance criteria

The demo spec is accepted when:

**Increment A**
1. A first-time user can connect Gmail, create a chat session, and exchange
   multiple ordered messages without touching configuration beyond `.env`.
2. Invoking `@Email` in chat visibly triggers the tool and renders its result
   in the same thread; duplicate triggers share one idempotency key.
3. Every Action Plan card shows title, summary, plan, priority, Gmail pointer;
   grounded steps show Citation chips linking to source documents.
4. A Partial Plan is visually distinct and lists `missing_information`.
5. Correlated Tasks show the source-email count; the Gmail deep link opens
   the source message.
6. Assistant text, tool status, tool output, citations, and terminal stream
   state are rendered from typed SSE events.
7. Backend-down, stream-failed, tool-failed, and empty-result states render a clear,
   actionable message.
8. No raw email body or full assembled prompt appears in the DOM, console,
   network response history, or browser storage.
9. The Knowledge screen shows corpus readiness (ready / degraded / unavailable)
   and a document list with title, section count, and source URL for each
   loaded document.
10. An ad-hoc grounded query returns an answer with inline citation chips
   linking to source chunks; the retrieval panel shows chunk title, section,
   relevance score, and reranker status for each result.
11. An empty corpus, retrieval failure, or no-match query renders a clear
    actionable message (not a stack trace) with the appropriate semantic
    color treatment.

**Increment B**
12. Persona/preferences can be created, edited, and deleted; a subsequent
    chat turn visibly reflects them.
13. Approve/complete/reject transitions on an in-chat card update immediately and the
    eligibility indicator matches the PRD-v2 rule table.
14. An approved tool episode can be recalled on a relevant later turn, while
    unapproved and rejected episodes remain unavailable.
15. Memory badges identify the active profile, episode, or semantic source
    without revealing full stored payloads.
16. Episodes show provenance fields; deleted memory no longer appears after
    refresh.

## 9. Live verification plan

Performed with `frontend-ui-engineering` plus the `playwright-cli` skill:

1. Start backend (`mail-todo-api`) and GUI (`python scripts/run_gui.py`).
2. Open the UI with `playwright-cli`, snapshot the accessible tree, and check
   console plus network requests before interaction.
3. Create a chat session, send two turns, reload, and verify ordered history.
4. Invoke `@Email` twice with the same idempotency key and verify one tool run
   and one in-thread Action Plan card.
5. Exercise `DIRECT_PLAN` and `RETRIEVE_RAG`; verify citations appear only
   where evidence exists and raw email never appears in snapshots, console,
   network payload inspection, localStorage, or sessionStorage.
6. Force backend, SSE, and tool failure paths and verify actionable states.
7. Navigate to the Knowledge screen; confirm corpus readiness shows "Ready";
   verify the document list renders with title, section count, and source URL.
   Run an ad-hoc grounded query matching a known corpus document (e.g. a
   question about administrative procedures) and verify: (a) the answer
   includes inline citation chips, (b) the retrieval panel shows chunks
   with scores and reranker status. Run a query with no matching content
   and verify the empty-state message. Force a corpus-unavailable state
   (e.g. empty `data/extracted/` directory) and verify the degraded indicator.
8. Increment B: approve an episode in chat, send a relevant next turn, and
   verify the eligible episode is recalled and labeled; reject another and
   verify it is excluded.
9. Test keyboard navigation and resize to 320, 768, 1024, and 1440 pixels.
10. Record Playwright screenshots, console/network findings, and a short
    checklist result in the PR or commit evidence.

## 10. Skills workflow for implementation

| Step | Skill |
|---|---|
| Task breakdown of this spec | `planning-and-task-breakdown` |
| Build screens slice by slice | `incremental-implementation`, `frontend-ui-engineering` |
| Verify against Streamlit/FastAPI docs | `source-driven-development` |
| Runtime verification | `playwright-cli` snapshots, interactions, console/network checks, and screenshots (per §9) |
| Review before merge | `code-review-and-quality` |
| Commit | `git-workflow-and-versioning` |

## 11. Non-goals

- Production Cowork surface, auth/login UI, multi-user management.
- Attachment viewing, email composition/reply, scheduling UI.
- Corpus administration, RAG ingestion tooling, chunk editing, or document
  upload (the Knowledge screen is read-only inspection + ad-hoc query;
  corpus management stays out of scope).
- Mobile app or native packaging; responsive-browser support is sufficient
  (verify 320px, 768px, 1024px, and 1440px widths).
