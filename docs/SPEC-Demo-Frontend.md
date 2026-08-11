# SPEC — Cowork Demo Frontend (Final Showcase after PRD-v1 + PRD-v2)

| Field | Value |
|---|---|
| Document status | Spec — implementation gated on PRD-v1 completion and PRD-v2 milestone readiness |
| Version | 2.1 (Updated with Milestone Dependency & Backend API Readiness Analysis) |
| Date | 2026-08-11 |
| Milestone position | Final showcase phase after `V2-M6` (master-comparison §7: `DEMO`) |
| Readiness Status | **Increment A: PARTIALLY READY (65%)** (Connect, Knowledge, Run Audit ready; AI Chat streaming registered, GET history/sessions & LLM binding pending); **Increment B: BLOCKED (0% UI / 35% API)** |
| Depends on | PRD-v1 (DONE 100%), V2-M1 (DONE 100%), V2-M2 (Repo DONE, API pending), V2-M3 (ACTIVE 92%), V2-M4 (VERIFY 35%), V2-M5 (ACTIVE 70%), V2-M6 (ACTIVE 55%) |
| Governs | `src/cowork_agent/gui/` (demo surface) |
| Vocabulary | All terms follow `CONTEXT.md` / `UBIQUITOUS_LANGUAGE.md` |

---

## 1. Purpose

A demonstration frontend that lets a human exercise the complete Cowork AI
Chat and chat-native task value loop end-to-end in a browser:

```text
Open AI Chat Assistant → converse → explicitly request a task or action plan
→ see a bounded chat-native task proposal in the thread
→ approve/complete/reject in chat → see eligible memory inform a later turn
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
7. Chat responses use the backend SSE contract: `delta`, `memory_citation`,
   `completed`, and `error`. Typed UI components must not parse prose for
   lifecycle or memory state.
8. The UI must distinguish assistant text, chat-native task proposals and
   lifecycle state, and memory-recall indicators without exposing full prompts
   or raw memory payloads.

## 3. Delivery structure (two increments)

### 3.1 Increment A — Core Chat

| Screen | Capability shown | PRD-v1 basis |
|---|---|---|
| AI Chat Assistant | Primary `st.chat_message` / `st.chat_input` thread, session sidebar, history, streaming assistant response | PRD-v2 V2-M1, V2-M4 |
| Connect | Mailbox Connection list, Gmail OAuth connect/disconnect, and separate standalone Email Agent state | PRD-v1 FR-03 |
| Knowledge | Corpus readiness, documents, and grounded query inspection for enterprise RAG | V1-M3 (`HybridSemanticMemory`) |
| Run audit | Chat/SSE lifecycle plus separate standalone Email Agent run metadata; no raw email | PRD-v1 FR-16; PRD-v2 FR-17 |

### 3.2 Increment B — Chat memory and transparency

| Screen | Capability shown | PRD-v2 basis |
|---|---|---|
| Preferences | Explicit AI Chat persona/profile editor: language, tone, brevity, and priority rules | FR-03, FR-04, FR-15 |
| In-chat task controls | Inline Approve / Complete / Reject controls on explicit chat-native task proposals with validation and eligibility state | FR-07, §12 |
| Memory transparency | In-thread badges for active profile rules, eligible episodic hits, and semantic citations—never raw context | FR-12, FR-14, FR-17 |
| Episode insight | Provenance view for chat summaries and chat-native TaskEpisodes: status, eligibility, source session/turn, and versions | FR-06, FR-14 |
| Deletion | Delete a preference or episode with confirmation and post-deletion refresh | FR-15 |

Increment B screens are feature-flagged off until their backend endpoints exist; the demo must run cleanly on an Increment-A-only backend.

### 3.3 Milestone Dependency & Implementation Readiness Matrix

| Increment / Screen | Capability | Prerequisite Milestones | Backend API Endpoint Status | Implementation Readiness Verdict |
|---|---|---|---|---|
| **Increment A — Connect** | Gmail OAuth, Mailbox Connections list, Unread preview | **V1-M1** (DONE 100%) | `GET /v1/mail-todo/connections`, `/oauth/gmail/*`, `/unread-preview` (**Implemented**) | **READY NOW** — full backend support in `app.py`. |
| **Increment A — Knowledge** | RAG corpus status, document list, ad-hoc grounded query | **V1-M3** (DONE 100%) | `GET /v1/mail-todo/knowledge/ready`, `/documents`, `POST /chat` (**Implemented**) | **READY NOW** — full backend support in `app.py`. |
| **Increment A — Run Audit** | Standalone Email Agent run metadata & task results | **V1-M4** (DONE 100%) | `GET /v1/mail-todo/runs/{id}`, `/result`, `/tasks` (**Implemented**) | **READY NOW** — full backend support in `app.py`. |
| **Increment A — AI Chat Assistant** | Multi-turn chat thread, session sidebar, streaming responses | **V2-M1** (DONE), **V2-M4** (VERIFY 35%) | `POST /sessions` & `POST /messages` (**Implemented**); `GET /sessions` & `GET /messages` (**Missing**); resolver & reply adapter pending | **PARTIALLY READY** — Streaming chat UI can be built; multi-session history requires V2-M4B getter endpoints & `ChatReplyPort` binding in `app.py`. |
| **Increment B — Preferences** | Persona/profile editor (language, tone, brevity, rules) | **V2-M2** (DONE repo layer) | Profile REST APIs (`GET/POST/PUT/DELETE /v1/cowork/chat/profile`) (**Missing**) | **BLOCKED** — DB repository landed in `PostgresChatProfileRepository`, but HTTP router is missing. |
| **Increment B — In-chat Task Controls** | Inline Approve / Complete / Reject task proposal controls | **V2-M3.4b-B** (ACTIVE), **V2-M4** (PARTIAL) | Task episode transition API (`POST /v1/cowork/chat/episodes/{id}/transition`) (**Missing**) | **BLOCKED** — Gateway lifecycle transition wiring (M3.4b-B) & REST API missing. |
| **Increment B — Memory Transparency** | In-thread badges for profile rules, episodic hits, RAG citations | **V2-M5** (ACTIVE 70%) | Memory citation SSE emission & eligible episodic retrieval (**Missing / Pending V2-M3.4b-C**) | **BLOCKED** — Episodic retrieval pending Gateway wiring and provider SSE citation emission. |
| **Increment B — Episode Insight** | Provenance view for chat summaries & TaskEpisodes | **V2-M3** (ACTIVE 92%), **V2-M6** (ACTIVE 55%) | List TaskEpisodes API (`GET /v1/cowork/chat/episodes`) (**Missing**) | **BLOCKED** — Episode listing REST API missing. |
| **Increment B — Deletion** | Delete preference or single episode with refresh | **V2-M2**, **V2-M3.4b-B**, **V2-M6** | Delete REST APIs (`DELETE /profile`, `DELETE /episodes/{id}`) (**Missing**) | **BLOCKED** — Deletion REST endpoints missing. |

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
├── 1. AI Chat Assistant  (multi-turn chat, explicit task proposals, memory)
├── 2. Connect            (Gmail OAuth, Mailbox Connections, standalone Email Agent state)
├── 3. Knowledge          (RAG readiness, documents, grounded query)
├── 4. Memory             (Preferences | Episodes | Deletion)
└── 5. Run audit          (chat/SSE lifecycle and standalone Email Agent metadata)
```

Make AI Chat Assistant the default landing screen. Connect and Knowledge
remain supporting screens. Standalone Email Agent state and run audit remain
separate from chat; no Email result moves into the chat thread.

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
6. **Polling discipline**: where the separate standalone Email Agent state is
   shown, use bounded polling with a deadline, terminal-state detection, and a
   visible timeout path.
7. **Idempotency**: retrying an explicit chat task request must use the stable
   chat request identity and never create a second logical TaskEpisode.
8. **Bilingual copy**: current GUI is Vietnamese; keep one language per
   screen section and externalize strings so the demo can be switched to
   English for stakeholder showcases.
9. **RAG result consistency**: retrieval results on the Knowledge screen
   use the same `.citation-chip` styling as chat-native task citations; corpus
   status uses semantic color + text pairs (green + "Ready", amber +
   "Degraded", red + "Unavailable"). Grounded answers display inline
   citation chips that match the chunk provenance returned by retrieval.
10. **Native chat semantics**: use `st.chat_message` for user/assistant roles
    and `st.chat_input` for the composer. Preserve focus and message order
    while incremental SSE deltas arrive.
11. **Task proposal card**: group title, compact request paraphrase, ordered
    steps, missing-information warning, company-RAG citations, and lifecycle
    controls in one keyboard-operable in-thread component.
12. **Memory transparency**: label declarative, episodic, and semantic sources
    with text plus icon; disclose source type and provenance, not full context.

## 7. Backend API contract assumptions & live implementation status

The demo consumes FastAPI backend endpoints; it defines none. The table below details the exact backend API contract, its expected milestone owner, and its current live codebase status in `src/cowork_agent/app.py` and `src/cowork_agent/api/chat.py`:

| Endpoint Path | Method | Expected Milestone | Live Codebase Status | Scope / Required For | Notes / File Reference |
|---|---|---|---|---|---|
| `/health` | GET | Baseline | **Implemented** | System | `src/cowork_agent/app.py:310` |
| `/v1/mail-todo/oauth/gmail/connect` | GET | V1-M1 | **Implemented** | Increment A (Connect) | `src/cowork_agent/app.py:314` |
| `/v1/mail-todo/oauth/gmail/callback` | GET | V1-M1 | **Implemented** | Increment A (Connect) | `src/cowork_agent/app.py:319` |
| `/v1/mail-todo/connections` | GET | V1-M1 | **Implemented** | Increment A (Connect) | `src/cowork_agent/app.py:342` |
| `/v1/mail-todo/connections/{id}` | DELETE | V1-M1 | **Implemented** | Increment A (Connect) | `src/cowork_agent/app.py:349` |
| `/v1/mail-todo/connections/{id}/unread-preview` | GET | V1-M1 | **Implemented** | Increment A (Connect) | `src/cowork_agent/app.py:362` |
| `/v1/mail-todo/runs` | POST | V1-M4 | **Implemented** | Increment A (Run Audit) | `src/cowork_agent/app.py:414` |
| `/v1/mail-todo/runs/{id}` | GET | V1-M4 | **Implemented** | Increment A (Run Audit) | `src/cowork_agent/app.py:459` |
| `/v1/mail-todo/runs/{id}/result` | GET | V1-M4 | **Implemented** | Increment A (Run Audit) | `src/cowork_agent/app.py:496` |
| `/v1/mail-todo/runs/{id}/tasks` | GET | V1-M4 | **Implemented** | Increment A (Run Audit) | `src/cowork_agent/app.py:511` |
| `/v1/mail-todo/knowledge/ready` | GET | V1-M3 | **Implemented** | Increment A (Knowledge) | `src/cowork_agent/app.py:528` |
| `/v1/mail-todo/knowledge/documents` | GET | V1-M3 | **Implemented** | Increment A (Knowledge) | `src/cowork_agent/app.py:552` |
| `/v1/mail-todo/knowledge/chat` | POST | V1-M3 | **Implemented** | Increment A (Knowledge) | `src/cowork_agent/app.py:569` |
| `/v1/cowork/chat/sessions` | POST | V2-M1 / V2-M4A | **Implemented** | Increment A (AI Chat) | `src/cowork_agent/api/chat.py:44` — Requires `chat_principal_resolver` in `app.py` |
| `/v1/cowork/chat/sessions` | GET | V2-M1 / V2-M4B | **Missing** | Increment A (AI Chat) | Needed for sidebar session history list |
| `/v1/cowork/chat/sessions/{id}/messages` | POST | V2-M4A | **Implemented** | Increment A (AI Chat) | `src/cowork_agent/api/chat.py:56` (SSE) — Requires `ChatReplyPort` adapter binding |
| `/v1/cowork/chat/sessions/{id}/messages` | GET | V2-M1 / V2-M4B | **Missing** | Increment A (AI Chat) | Needed to reload prior turn history |
| `/v1/cowork/chat/profile` | GET/POST/DELETE | V2-M2 | **Missing REST API** | Increment B (Preferences) | Domain & DB repo landed in `PostgresChatProfileRepository`; HTTP router missing |
| `/v1/cowork/chat/episodes/{id}/transition` | POST | V2-M3.4b-B / V2-M4 | **Missing REST API** | Increment B (Task Controls) | Transition inline proposal: approve / complete / reject |
| `/v1/cowork/chat/episodes` | GET | V2-M3 / V2-M6 | **Missing REST API** | Increment B (Episode Insight) | List TaskEpisodes & provenance |
| `/v1/cowork/chat/episodes/{id}` | DELETE | V2-M3.4b-B / V2-M6 | **Missing REST API** | Increment B (Deletion) | Single TaskEpisode hard/soft deletion |

If a needed read/write endpoint is missing at implementation time, file it against the owning milestone — do not work around it with client-side logic. Increment B features MUST remain feature-flagged off until their respective REST endpoints land.

## 8. Acceptance criteria

**Increment A**
1. A first-time user can create a chat session and exchange multiple ordered
   messages without touching configuration beyond `.env`.
2. An explicit task/action-plan request renders one bounded chat-native task
   proposal in the same thread; retries share one logical `record_id`.
3. Every task proposal card shows title, compact request paraphrase, action
   plan, `missing_information`, lifecycle state, and company citation chips
   where evidence exists.
4. A proposal with missing information is visually distinct and lists its
   unanswered inputs.
5. Assistant text, memory citations, terminal completion, and error state are
   rendered from the typed SSE events `delta`, `memory_citation`, `completed`,
   and `error` only.
6. Backend-down, stream-failed, task-persistence-failed, and empty-result
   states render a clear, actionable message.
7. The Connect and Run audit screens may show standalone PRD-v1 Email Agent
   state, but no email operation can be initiated from AI Chat.
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
13. Approve/complete/reject transitions on an explicit chat-native task proposal update immediately and the
    eligibility indicator matches the PRD-v2 rule table.
14. An approved chat-native TaskEpisode can be recalled on a relevant later turn, while
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
4. Explicitly request the same task or action plan twice from the originating
   session and verify one logical TaskEpisode and one in-thread proposal.
5. Ask a chat question that needs company knowledge; verify citations appear
   only where evidence exists and raw email never appears in snapshots,
   console, network payload inspection, localStorage, or sessionStorage.
6. Force backend, SSE, and task-persistence failure paths and verify actionable
   states.
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
- AI Chat execution extensions, permission controls for them, and standalone
  Email Agent invocation from AI Chat.
- Corpus administration, RAG ingestion tooling, chunk editing, or document
  upload (the Knowledge screen is read-only inspection + ad-hoc query;
  corpus management stays out of scope).
- Mobile app or native packaging; responsive-browser support is sufficient
  (verify 320px, 768px, 1024px, and 1440px widths).
