# SPEC — Cowork Demo Frontend (Final Showcase after PRD-v1 + PRD-v2)

| Field | Value |
|---|---|
| Document status | Spec — implementation gated on PRD-v1 and PRD-v2 completion |
| Version | 1.0 |
| Date | 2026-08-07 |
| Milestone position | Final phase after `V2-M6` (master-comparison §7: `DEMO`) |
| Depends on | PRD-v1 §15 acceptance passed; PRD-v2 §16 acceptance passed |
| Governs | `src/cowork_agent/gui/` (demo surface) |
| Vocabulary | All terms follow `CONTEXT.md` / `UBIQUITOUS_LANGUAGE.md` |

---

## 1. Purpose

A demonstration frontend that lets a human exercise the complete Cowork
Agent value loop end-to-end in a browser:

```text
Connect Gmail → @Email → watch the Run → see grounded Tasks with Citations
→ approve/complete/reject → see memory improve the next Run
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

## 3. Delivery structure (two increments)

### 3.1 Increment A — PRD-v1 showcase (after PRD-v1 §15 passes)

| Screen | Capability shown | PRD-v1 basis |
|---|---|---|
| Connect | Mailbox Connection list, Gmail OAuth connect/disconnect | FR-03 |
| Run | `@Email` invocation: connection picker, max-emails slider, idempotent create, live progress polling, safe error display | FR-01, FR-02 |
| Tasks | Task list per Run: title, request summary, Action Plan steps, priority (incl. `urgent`), deadline, actionability + route badges, classifier confidence | FR-12, FR-13 |
| Task detail | Ordered plan steps with per-step Citation chips, supporting documents with links, Gmail deep-link pointer, missing-information warning panel for Partial Plans, "correlated from N emails" indicator (`source_message_ids`) | FR-10, FR-11, FR-12, FR-13 |
| Run audit | Route/reason-code summary, retrieval status and result count, validation status, stage latencies (metadata only) | FR-16 |

### 3.2 Increment B — PRD-v2 showcase (after PRD-v2 §16 passes)

| Screen | Capability shown | PRD-v2 basis |
|---|---|---|
| Preferences | Explicit Profile editor: language, timezone, output style, priority rules, important people; save/delete | FR-03, FR-04, FR-15 |
| Task lifecycle | Approve / Complete / Reject controls per Task with Validation Status badge and eligibility indicator | FR-07, §12 |
| Memory insight | Episode provenance view per Task: status, `retrieval_eligible`, created/updated, pipeline/model version | FR-06, FR-14 |
| Memory effect | Side-by-side or toggle comparison of the latest Run with/without episodic context where the backend exposes it; preference-application indicator | FR-12, §15 evaluation |
| Deletion | Delete preference, delete episode(s), with confirmation and post-deletion refresh | FR-15 |

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
├── 1. Connect        (Mailbox Connections)
├── 2. Run            (@Email invocation + live progress)
├── 3. Tasks          (list → detail)
├── 4. Memory         (Increment B: Preferences | Episodes | Deletion)
└── 5. Run audit      (route/telemetry summary, dev-gated extras)
```

Keep the proven 3-step spine of the current GUI for Increments A screens;
add Memory as a separate page/section so Increment A stays untouched.

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

## 7. Backend API contract assumptions

The demo consumes endpoints; it defines none. Expected inventory:

| Capability | Exists today | Expected from milestones |
|---|---|---|
| Health, OAuth connect/callback, connections list/delete, unread preview | ✔ `app.py` | — |
| Create/Run-status/Run-result | ✔ `/v1/mail-todo/runs*` | Compatibility mapper preserves these through V1-M4 |
| Task list/detail from persisted Tasks | ✗ | V1-M4 (or compatibility result shape suffices for Increment A) |
| Route/telemetry summary | partial (dev `processedEmails`) | V1-M4 basic telemetry exposure |
| Profile read/write/delete | ✗ | V2-M2 |
| Approve/complete/reject transitions | ✗ | V2-M4 |
| Episode view + deletion | ✗ | V2-M3 / V2-M6 |

If a needed read endpoint is missing at implementation time, file it against
the owning milestone — do not work around it with client-side logic.

## 8. Acceptance criteria

The demo spec is accepted when:

**Increment A**
1. A first-time user can connect Gmail, run `@Email`, and see Tasks without
   touching any config file beyond `.env`.
2. Every Task card shows title, summary, plan, priority, Gmail pointer;
   grounded steps show Citation chips linking to source documents.
3. A Partial Plan is visually distinct and lists `missing_information`.
4. Correlated Tasks show the source-email count; the Gmail deep link opens
   the source message.
5. Duplicate Run clicks produce one Run (idempotency key).
6. Backend-down, Run-failed, and empty-result states each render a clear,
   actionable message.
7. No raw email body appears anywhere in the UI or browser storage.

**Increment B**
8. Preferences can be created, edited, and deleted; a subsequent Run visibly
   reflects them (or the backend indicates application).
9. Approve/complete/reject transitions update the badge immediately and the
   eligibility indicator matches the PRD-v2 rule table.
10. Episodes show provenance fields; deleted memory no longer appears after
    refresh.

## 9. Live verification plan

Performed with `frontend-ui-engineering` + browser verification
(browser-use MCP / `RunPreview`):

1. Start backend (`mail-todo-api`) and GUI (`python scripts/run_gui.py`).
2. Open the preview browser; capture screenshots at each step of §8.
3. Walk the full §1 loop twice: once for a self-contained email
   (`DIRECT_PLAN`), once for a company-knowledge email (`RETRIEVE_RAG`),
   asserting Citations appear only on the second.
4. Force a failure path (backend stopped / invalid connection) and verify
   the error state.
5. Check console/network for errors and for absence of raw email payloads.
6. Increment B: approve an episode, re-run, verify retrieval-eligible
   history influences/labels the new plan per backend output.
7. Record screenshots + a short checklist result in the PR/commit message.

## 10. Skills workflow for implementation

| Step | Skill |
|---|---|
| Task breakdown of this spec | `planning-and-task-breakdown` |
| Build screens slice by slice | `incremental-implementation`, `frontend-ui-engineering` |
| Verify against Streamlit/FastAPI docs | `source-driven-development` |
| Runtime verification | browser-use MCP + `RunPreview` (per §9) |
| Review before merge | `code-review-and-quality` |
| Commit | `git-workflow-and-versioning` |

## 11. Non-goals

- Production Cowork surface, auth/login UI, multi-user management.
- Attachment viewing, email composition/reply, scheduling UI.
- Corpus administration, RAG ingestion tooling.
- Mobile app or native packaging; responsive-browser support is sufficient
  (verify 768px and 1440px widths).
