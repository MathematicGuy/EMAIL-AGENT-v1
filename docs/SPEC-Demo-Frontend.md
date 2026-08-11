# SPEC — Cowork Demo Frontend (Final Showcase after PRD-v1 + PRD-v2)

| Field | Value |
|---|---|
| Document status | Active implementation spec — current-session AI Chat may start; full showcase remains gated on the explicit API gaps below and V2-M6 acceptance |
| Version | 2.3 (Increment A chat slice built against the accepted V2-M3/V2-M4/V2-M5 runtime contracts) |
| Date | 2026-08-12 |
| Milestone position | Final showcase phase after `V2-M6` (master-comparison §7: `DEMO`) |
| Readiness Status | **Backend: READY for current-session chat streaming, memory-citation badges, and originating-session task lifecycle; PARTIAL for reload/history and memory administration. Demo UI: §3.4 boundary BUILT, unit-verified, and §9 live-browser verified on 2026-08-12 (see §9 log; citation-chip branch pending embedding quota); Increment B locked.** |
| Depends on | PRD-v1 and V2-M1–V2-M5 (DONE 100%); V2-M6 (ACTIVE 55%); missing frontend-facing read/profile/proposal contracts listed in §3.3 and §7 |
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
| **Increment A — AI Chat Assistant** | Multi-turn chat thread and streaming responses in the active browser session | **V2-M1–V2-M5** (DONE) | `POST /sessions` and `POST /messages` (**Implemented and runtime-bound**); `GET /sessions` and `GET /messages` (**Missing**) | **BUILT (current-session slice)** — the Streamlit thread, streaming, badges, and retry are implemented per §3.5 and unit-verified; persisted/reloadable session history remains blocked on the GET contracts. |
| **Increment B — Preferences** | Persona/profile editor (language, tone, brevity, rules) | **V2-M2** (DONE repo layer) | Profile REST APIs (`GET/POST/PUT/DELETE /v1/cowork/chat/profile`) (**Missing**) | **BLOCKED** — DB repository landed in `PostgresChatProfileRepository`, but HTTP router is missing. |
| **Increment B — In-chat Task Controls** | Inline Approve / Complete / Reject task controls | **V2-M3/V2-M4** (DONE) | Originating-session approve/complete/reject endpoints (**Implemented**) | **PARTIAL** — lifecycle transport is ready, but a structured task-proposal SSE/read payload is missing; the client must not parse assistant prose to build a task card. |
| **Increment B — Memory Transparency** | In-thread badges for declarative, episodic, and semantic sources | **V2-M5** (DONE) | Typed `memory_citation` SSE with `memory_type` and opaque `source_id` (**Implemented**) | **BUILT (badges only)** — in-thread badges render from typed `memory_citation`; detailed stored-memory views remain blocked on read APIs. |
| **Increment B — Episode Insight** | Provenance view for chat summaries and TaskEpisodes | **V2-M3** (DONE), **V2-M6** (ACTIVE 55%) | List TaskEpisodes API (`GET /v1/cowork/chat/episodes`) (**Missing**) | **BLOCKED** — no frontend-safe episode listing/read contract. |
| **Increment B — Deletion** | Delete preference or single episode with refresh | **V2-M2/V2-M3** (DONE), **V2-M6** (ACTIVE) | Originating-session single-episode delete (**Implemented**); profile delete and list-refresh APIs (**Missing**) | **PARTIAL** — current-thread episode deletion is callable once an episode ID is received; full memory administration remains blocked. |

### 3.4 Immediate frontend implementation boundary

The first frontend slice may start now and must stay inside this contract:

1. create one server chat session for the active browser session;
2. submit idempotent messages and render ordered `delta` events;
3. render declarative, episodic, and semantic badges from `memory_citation`;
4. finalize or expose a safe retry state from `completed` / `error`;
5. retain the active session ID only as browser-session UI state;
6. do not claim reloadable history, profile editing, episode browsing, or a
   structured task card until the corresponding backend contracts land.

This boundary is intentionally narrower than the final showcase. It permits real
frontend progress while preventing client-side storage or prose parsing from
becoming an accidental backend.

### 3.5 As-built status of the §3.4 boundary

All six boundary clauses are implemented in `src/cowork_agent/gui/`:

| Clause | Where | Status |
|---|---|---|
| 1. One server chat session per browser session | `app.py::_ensure_chat_session`, `chat_client.create_chat_session` | **Built** — created once, cached in `st.session_state`, reset only by the explicit "new session" control. |
| 2. Idempotent messages, ordered `delta` rendering | `chat_client.stream_chat_turn`, `ChatTurnAccumulator`, `app.py::_run_chat_turn` | **Built** — body is exactly `session_id` / `user_message` / `idempotency_key`; deltas dedupe by `event_id` and append in arrival order. |
| 3. Declarative / episodic / semantic badges | `chat_client.MEMORY_BADGES`, `app.py::memory_badges_html` | **Built** — icon + localized text per kind, repeat count suffix; `source_id` is never rendered. |
| 4. Terminal state and safe retry | `ChatTurnAccumulator.is_terminal`, `app.py::chat_error_text` | **Built** — a failed turn keeps its idempotency key in `chat_pending_turn`, and the retry control reuses it; transport faults become a synthetic `error` event, never an exception in the DOM. |
| 5. Session ID as browser-session UI state only | `st.session_state` keys `chat_session_id` / `chat_messages` / `chat_pending_turn` | **Built** — no cookie, no localStorage, no file, no DB. |
| 6. No unbuilt capability claimed | `app.py::_screen_memory`, `MISSING_INCREMENT_B_ENDPOINTS` | **Built** — the Memory screen renders a lock notice plus the literal list of missing §7 contracts and no mocked profile, episode, or task-card UI (AGENTS.md invariant 4). |

Out of scope of the built slice, by the same boundary: reloadable history,
profile editing, episode browsing, and the structured task-proposal card.

Fail-closed parsing is the demo's only trust boundary against the stream:
`chat_client.parse_stream_event` drops any frame whose `event_type` is unknown
or whose variant payload is missing, so a malformed frame can never render as
assistant content.

Automated coverage: `tests/unit/gui/test_chat_client.py` (transport, SSE
framing, accumulation, failure mapping) and `tests/unit/gui/test_app.py`
(presentation helpers, string-catalog parity, escaping). Ruff and mypy are
clean over `src/cowork_agent/gui/` and `tests/unit/gui/`.

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

## 7. Backend API contract and live implementation status

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
| `/v1/cowork/chat/sessions` | POST | V2-M1 / V2-M4 | **Implemented and composed** | Increment A (AI Chat) | Uses the verified single-active-mailbox principal and creates an in-memory active session. |
| `/v1/cowork/chat/sessions` | GET | V2-M1 / V2-M4B | **Missing** | Increment A (AI Chat) | Needed for sidebar session history list |
| `/v1/cowork/chat/sessions/{id}/messages` | POST | V2-M4/V2-M5 | **Implemented and composed** | Increment A (AI Chat) | SSE; configured Gemini/Groq/Faucet reply adapter; exact body fields are `session_id`, `user_message`, and `idempotency_key`. |
| `/v1/cowork/chat/sessions/{id}/messages` | GET | V2-M1 / V2-M4B | **Missing** | Increment A (AI Chat) | Needed to reload prior turn history |
| `/v1/cowork/chat/profile` | GET/POST/PUT | V2-M2 | **Missing REST API** | Increment B (Preferences) | Domain & DB repo landed in `PostgresChatProfileRepository`; HTTP router missing |
| `/v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/approve` | POST | V2-M3/V2-M4 | **Implemented** | Increment B (Task Controls) | Originating session only; returns `episode_id`, `validation_status`, and `retrieval_eligible`. |
| `/v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/complete` | POST | V2-M3/V2-M4 | **Implemented** | Increment B (Task Controls) | Originating session only; invalid or inaccessible records return 404. |
| `/v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}/reject` | POST | V2-M3/V2-M4 | **Implemented** | Increment B (Task Controls) | Originating session only; rejected memory remains retrieval-ineligible. |
| `/v1/cowork/chat/sessions/{session_id}/task-episodes/{episode_id}` | DELETE | V2-M3/V2-M4 | **Implemented** | Increment B (Deletion) | Originating session only; successful deletion returns 204. |
| `/v1/cowork/chat/episodes` | GET | V2-M3 / V2-M6 | **Missing REST API** | Increment B (Episode Insight) | List TaskEpisodes & provenance |
| `/v1/cowork/chat/profile` | DELETE | V2-M2/V2-M6 | **Missing REST API** | Increment B (Deletion) | Required for complete profile administration. |

If a needed read/write endpoint is missing at implementation time, file it against the owning milestone — do not work around it with client-side logic. Increment B features MUST remain feature-flagged off until their respective REST endpoints land.

### 7.1 SSE event-to-UI mapping

| SSE event | Payload used by UI | Required behavior |
|---|---|---|
| `delta` | `event_id`, `session_id`, `turn_id`, `text` | Deduplicate by event identity and append text in order to the active assistant message. |
| `memory_citation` | `event_id`, `turn_id`, `memory_type`, `source_id` | Render a declarative / episodic / semantic badge. Treat `source_id` as opaque; never fetch or infer raw content from it. |
| `completed` | `event_id`, `turn_id` | Mark the turn terminal, persist only UI-safe browser-session state, and re-enable the composer. |
| `error` | `event_id`, `turn_id`, `code`, `safe_message` | Render the safe message, preserve the idempotency key for retry, and never display exception details. |

There is currently **no structured task-proposal SSE variant**. Although the
controller can persist a bounded TaskEpisode and emit an episodic citation, the
frontend cannot reconstruct the task title, paraphrase, action plan, missing
information, and citations from the public stream. A final task-proposal card
therefore requires either a typed proposal event or a frontend-safe read DTO.
Parsing assistant text is prohibited.

## 8. Acceptance criteria

**Increment A**
1. A first-time user can create a chat session and exchange multiple ordered
   messages without touching configuration beyond `.env`.
2. **GATED CONTRACT:** an explicit task/action-plan request renders one bounded
   chat-native task proposal in the same thread; retries share one logical
   `record_id`. This requires the structured proposal contract identified in §7.1.
3. **GATED CONTRACT:** every task proposal card shows title, compact request paraphrase, action
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

### 8.1 Current status per criterion

| Criteria | Status |
|---|---|
| 1, 5, 6 (chat path) | **Live-verified 2026-08-12** (plus `tests/unit/gui`): session create, ordered streaming turns, typed-SSE-only rendering, backend-down gate, empty-reply copy. Three chat-page defects found and fixed in `gui/`, then live re-verified: advisory-as-terminal, dead-session 404 dead end, generic 503 copy (see §9 fixes list). |
| 2, 3, 4 (task proposal card) | **Blocked by contract.** Gated on the structured proposal event or read DTO in §7.1; no card is built and no prose is parsed. |
| 7, 9, 10, 11 (Connect, Knowledge, Run audit) | **Live-verified 2026-08-12 in available branches**: Connect lists the single readonly connection; Run audit empty state renders; Knowledge degraded indicator, document list, and no-match empty state render. "Ready" + inline citation chips pending embedding quota (environmental). |
| 8 (no raw email or prompt leakage) | **Live-verified 2026-08-12** — DOM, console, browser network history, localStorage, sessionStorage audited clean (Streamlit telemetry keys only); by construction the demo stores only role, text, and `(memory_type, source_id)` pairs in `st.session_state`. |
| 15 (memory badges without payloads) | **Implemented** — badges disclose the source kind only; `source_id` is never rendered. |
| 12, 13, 14, 16 | **Blocked** — Increment B REST contracts are missing; the Memory screen stays locked and lists them. |

## 9. Live verification plan

Performed with `frontend-ui-engineering` plus an available real-browser
verification lane (Chrome DevTools or Playwright).

**Status: run on 2026-08-12** (live Chrome via DevTools MCP against `mail-todo-api`
+ `scripts/run_gui.py`; evidence in `docs/evidence/demo-frontend-2026-08-12/`).

| Step | Result |
|---|---|
| 1-2 | Backend + GUI up; initial a11y tree, console, and network captured. Console: zero errors/warnings (only Streamlit-internal a11y "issue" notices). Browser network is Streamlit-only — all backend calls are server-side by design. |
| 3 | Session created per browser session; two ordered turns streamed (`01-chat-two-turns.png`). Reload/history remains gated on the missing GET contracts, and a page reload correctly starts a fresh session (no client-side persistence). |
| 4, 8 | Skipped — gated on the structured task-proposal contract (Increment B). |
| 5 | Company-knowledge turn returned no citation because retrieval had no evidence; badges never fabricated. Storage/DOM audit clean: localStorage holds only Streamlit telemetry keys (`ajs_anonymous_id`, `stMetricsConfig`), sessionStorage empty, cookies only Streamlit XSRF/telemetry, no prompt markers or raw email in DOM. |
| 6 | Backend-down renders the actionable gate (`02-backend-down-gate.png`); a mid-stream failure renders the safe error with the idempotency retry control ("Thử lại dùng lại đúng khóa idempotency…"), never a stack trace. |
| 7 | Knowledge screen live-verified in its DEGRADED branch: status "Suy giảm (chỉ BM25, không có embedding)", 6 documents with title/section/source, no-match query renders actionable empty state (`03-knowledge-degraded-no-match.png`). "Ready" status and inline citation chips NOT live-verified: Gemini embedding quota exhausted at startup composed `NullSemanticMemory` (environmental, not a code defect). |
| 9 | 320/768/1024/1440 widths usable, sidebar collapses at 320, keyboard Tab reaches controls (`07`-`09` screenshots). |
| 10 | This log + screenshots committed as evidence. |

Screens: Memory lock lists exactly the §7 missing contracts (`04-memory-locked.png`);
Connect shows the single readonly Gmail connection (`05-connect-screen.png`);
Run audit renders its empty state (`06-run-audit-empty.png`).

Chat-page fixes (2026-08-12, all scoped to `src/cowork_agent/gui/`, covered by
`tests/unit/gui`, evidence in `docs/evidence/demo-frontend-2026-08-12/`):

1. **Advisory is non-terminal** — `optional_memory_degraded` renders as a
   warning while the reply still streams (`ADVISORY_ERROR_CODES`, accumulator
   advisory fields; `10-advisory-non-terminal.png`).
2. **Dead-session renewal** — an `http_404` with no content (server forgot the
   session, e.g. backend restart) renews the session once and retries the same
   idempotency key (`_renew_chat_session`; `11-session-renewal.png`).
3. **Actionable 503 + env loading** — `http_503` chat turns show the identity
   copy (`chat_error_identity`: exactly-one-active-Gmail-connection rule), and
   `read_settings` now calls `load_dotenv(override=False)` so `.env` values
   such as `APP_HOST_URL` apply to the GUI process.

The steps below remain the normative plan; gated steps (4, 8) stay open until
the structured proposal contract lands.

1. Start backend (`mail-todo-api`) and GUI (`python scripts/run_gui.py`).
2. Open the UI in a real browser, snapshot the accessible tree, and check
   console plus network requests before interaction.
3. Create a chat session and send two turns; verify ordered streaming in the
   active browser session. Reload/history verification remains gated on the
   missing GET contracts.
4. After the structured proposal contract lands, explicitly request the same task or action plan twice from the originating
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
8. Increment B: after the structured proposal contract lands, approve an episode in chat, send a relevant next turn, and
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
| Runtime verification | Real-browser Chrome DevTools or Playwright snapshots, interactions, console/network checks, and screenshots (per §9) |
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
