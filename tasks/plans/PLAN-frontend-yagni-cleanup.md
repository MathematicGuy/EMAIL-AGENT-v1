# PLAN — Frontend YAGNI Cleanup

> **Created:** 2026-08-16
> **Scope:** `frontend/src` only. No backend changes.
> **Trigger:** `frontend/` was split out of a larger "F-Cowork" monorepo
> (`apps/web`), see `frontend/README.md`. It still carries modules, API
> clients, and dependencies for features that never existed in this
> repo's FastAPI backend (`src/cowork_agent`), which implements exactly
> three feature planes per `AGENTS.md`: Email RAG (`email_action_plan`),
> AI Chat + 4-type memory (`ai_chat`), and gated User Documents
> (`user_documents`).

## Method

Cross-referenced every frontend API call path in `frontend/src` against
the real backend route table (`src/cowork_agent/api/chat.py`,
`api/projects.py`, and inline routes in `app.py` — the only three files
that register any `APIRouter`/route). Backend only exposes: `/health`,
`/api/v1/health`, `/api/v1/reports*`, `/v1/cowork/chat/*` (sessions,
messages, projects, documents, document-health, task-episode lifecycle,
profile), `/v1/conversations` (dead stub), `/v1/mail-todo/*`. There is
no `/v1/tasks`, `/v1/resources`, `/v1/memories`, `/v1/memory-claims`,
`/v1/memory-summary`, `/v1/memory-context-previews`,
`/internal/v1/context-snapshots`, `/api/v1/connectors/*`, or
`/api/v1/automations/*` anywhere in `src/`.

## Completed

### 1. Misaligned "Memory & context" right-side panel — REMOVED

`frontend/src/modules/memory/` (`MemoryPanel.tsx` 870 lines, `api.ts`
273 lines, `types.ts`, tests) rendered a `fixed inset-0 … justify-end`
right-side drawer opened from the `BrainCircuit` icon in
`dashboard/components/Header.tsx`. It called a completely different
memory contract than this backend implements
(`listMemories`/`listMemoryClaims`/`getProjectMemorySummary`/
`resolveContextSnapshot` → `/v1/memories`, `/v1/memory-claims`,
`/v1/memory-summary`, `/v1/memory-context-previews`,
`/internal/v1/context-snapshots`), all against a hardcoded
`DEMO_SCOPE = { actorId: 'demo-user', projectId: 'demo-project',
workspaceId: 'demo-workspace' }`. None of those routes exist in
`src/cowork_agent` — every open of the panel failed. It also mixed
hardcoded Tailwind `zinc-*` colors with this app's `--color-*` theme
variables, which is the visible "misalignment" against the rest of the
themed UI. The real AI Chat memory system (declarative profile +
episodes) is exposed at `/v1/cowork/chat/profile` and
`/v1/cowork/chat/episodes` (see `docs/references/understand/
memory-system-and-chat-demo-analysis.md`, §3) and currently has no
frontend surface — building that is a separate, deliberate future
workstream, not a restoration of this panel.

**Removed:** `frontend/src/modules/memory/` (whole directory) and its
wiring — `onOpenMemory` prop/handler/button in `Dashboard.tsx` and
`Header.tsx`, the `BrainCircuit` import, the `isMemoryPanelOpen` state,
and the corresponding assertions in `Dashboard.test.tsx` and mocks in
`DashboardPlanCallbacks.test.tsx`.

**Verified:** `pnpm check-types`, `pnpm lint`, `pnpm test` (18 files /
76 tests) all pass after removal.

## Remaining candidates (not yet executed — needs a go-ahead)

Ranked by confidence; each row is independently removable unless noted.

| # | Item | Size | Confidence | Why |
|---|---|---|---|---|
| 1 | `modules/settings/GoogleDriveConnect.tsx` | 311 lines | Very high | Zero imports anywhere; calls `/api/v1/connectors/google-drive/status` (doesn't exist); Google Drive isn't a product feature. |
| 2 | `modules/work-intake/assistantApi.ts` + its test | 686 lines | Very high | Imported only by its own test file. |
| 3 | `modules/documents/{FixturePicker,EvidenceList}.tsx`, `hooks/useDocumentJobDemo.ts`, `data/mockData.ts` | 228 lines | Very high | Zero external imports even within the already-dead documents demo. |
| 4 | `modules/work-intake/` (rest: `WorkIntakePanel.tsx`, `api.ts`, `types.ts`, tests) + its Header/Dashboard wiring | ~2540 lines | High | Entire `/v1/tasks*` contract doesn't exist server-side. Reachable via the Header icon but every request fails. **Caveat:** `dashboard/types.ts` and `TaskWorkflowCard.tsx` borrow a few type names from `work-intake/types.ts` — re-point those imports (or inline the 2-3 needed shapes) before deleting the file. |
| 5 | `modules/workspace/resourceApi.ts` | 66 lines | High | `/v1/resources/*` doesn't exist. Depends on #6/#8 being removed first (its only two call sites). |
| 6 | `modules/documents/` rest (`DocumentsDemo.tsx`, `LiveUploadPanel`, `ResultPanel`, `StatusBadge`, `TopSourcesPreview`, `EvidenceDrawer`, `ProgressBar`, `presets.ts`, `types.ts`) + the `#documents` hash route in `App.tsx` | ~1200 lines | High | Only interactive path (`LiveUploadPanel`) calls the dead `work-intake`/`workspace` APIs from #4/#5. **Caveat:** `modules/documents/components/CitationBadge.tsx` is genuinely used by the real `ChatStreamView.tsx` for RAG citations — keep it (move it, e.g. into `dashboard/components/`, rather than deleting). |
| 7 | `dashboard/components/AutomationsView.tsx` + "schedules"/"dispatch" nav in `Taskbar.tsx`/`Dashboard.tsx` | 527 lines | Medium-high | `/api/v1/automations/*` doesn't exist, and ADR-004/AC-18 explicitly rules out any scheduler or recurring email processing for this product. |
| 8 | `dashboard/components/TaskWorkflowCard.tsx` + its render branch in `ChatStreamView.tsx` + the 4 no-op workflow callbacks in `useStreamingChat.ts` | 192 lines + plumbing | Medium-high | `workflows` state is never populated and the approve/revise/retry handlers are literal no-ops (`(taskId) => { void taskId; }`). Confirm with whoever owns the chat roadmap this isn't a deliberate placeholder before deleting. |
| 9 | `dashboard/components/VoiceModal.tsx` | 90 lines | Medium-high | Entirely fake — 3 hardcoded phrases cycled on a timer, no STT, no backend call. |
| 10 | `dashboard/components/UpgradeModal.tsx` | 109 lines | Medium-high | Static pricing-tier card; no billing/plans concept anywhere in this backend. |
| 11 | `frontend/src/landing/LandingPage.tsx` | 432 lines | Content rewrite, not deletion | It's the real default route — keep it, but the copy ("Build a real-time analytics dashboard with React & Tailwind…") is generic AI-SaaS boilerplate that misdescribes this product. |
| 12 | Unused deps in `package.json`: `html2pdf.js`, `@base-ui/react`, `shadcn`, `class-variance-authority`, `tailwind-merge`, `tw-animate-css`, `clsx`, `@fontsource-variable/geist`; plus `frontend/components.json` (shadcn scaffolding, points at a `@/components/ui` alias that has no generated components) | package-level | Very high | Zero import sites found anywhere in `src/`. |
| 13 | `dashboard/components/ArtifactsView.tsx` — not a removal, a partial fix | n/a | n/a | The view itself is real (`/api/v1/reports` list/save/delete), but its `/download` and `/pdf` buttons hit routes that don't exist. Trim those two buttons rather than the file. |

## Suggested execution order

Items 1–3 are risk-free (zero references) — safe to batch in one pass.
Item 4 unblocks 5, which unblocks 6 — do those three together as one
unit since they're the same dead F-Cowork "Module 1→2→3" pipeline. Item
7–10 are independent single-file removals. Item 12 is a `package.json`
+ lockfile change, best done last and verified with a clean
`pnpm install` + `pnpm build`. After each batch: `pnpm check-types`,
`pnpm lint`, `pnpm test`.

## Confirmed in-scope — keep as-is

- `App.tsx`, `main.tsx`, `lib/apiConfig.ts`, `lib/observability/*`
- `dashboard/hooks/useStreamingChat.ts` (minus the 4 dead workflow
  callbacks), `hooks/useProjects.ts`
- `dashboard/components/ChatStreamView.tsx`, `ChatInputBox.tsx`,
  `HeroSection.tsx`, `RagEvidencePanel.tsx`, `MailInboxView.tsx`,
  `Header.tsx`, `Taskbar.tsx`, `NewProjectModal.tsx`,
  `ModelSelectorModal.tsx`, `CustomizeModal.tsx`
- `modules/mail/*` — matches `/v1/mail-todo/*` exactly
- `modules/project-documents/*` — matches `/v1/cowork/chat/projects/*`
  exactly (this is the real, gated User Documents feature)
