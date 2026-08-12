# ADR-004 — Chat-native TaskEpisodes replace in-chat `@Email` episodes

- Status: Accepted
- Date: 2026-08-11
- Decision makers: Product/Engineering team
- Supersedes: PRD-v2 and target-architecture decisions that make `@Email` the
  producer or owner of AI Chat TaskEpisodes

## Context

The runnable AI Chat product currently provides chat plus four-type memory. Its
accepted v2 request contract has no tool field. Requests containing retired
`tool_choices` are rejected by strict deserialization as an unexpected field;
they do not enter a tool-dispatch path. The earlier PRD-v2 contract nevertheless defines
`TaskEpisode` only as an `@Email` Action Plan with Gmail, run, tool, and task-row
identity.

The in-chat `@Email` feature and its Action Plan card lifecycle are retired.
The standalone PRD-v1 Email Agent remains unchanged and memory-free. Reusing
the old episode shape under a generic name would preserve false Email ownership
and allow unrelated chat records to inherit an invalid task foreign key.

## Decision

AI Chat uses a chat-native `TaskEpisode`:

- The Chat Controller may propose a task only after an explicit user request
  such as "create a task" or "make an action plan". No background extraction,
  implicit promotion, or model-only persistence is allowed.
- The initial episode is `system_generated` and
  `retrieval_eligible = false`. The user may later approve, complete, or reject
  it through the originating chat session.
- Approval and completion set `retrieval_eligible = true`; rejection keeps it
  false. Storage derives eligibility atomically from validation status.
- The payload contains only a bounded task title, compact request paraphrase,
  action steps, optional company-RAG citations, missing-information notes, chat
  session/turn provenance, lifecycle timestamps, and model/prompt metadata.
- `run_id`, `source_tool`, `gmail_message_id`, and `gmail_url` are removed.
  TaskEpisodes have no foreign key to PRD-v1 Email Agent task rows.
- `record_id` is an opaque, stable idempotency key derived from tenant, user,
  originating chat session, and originating chat turn. The exact derivation is
  deterministic and must not expose raw content.
- Single-record transition and deletion require the originating session.
  Eligible retrieval may cross sessions only for the same tenant, user, and
  `feature: ai_chat` scope. User-wide deletion spans that user's chat sessions
  and never deletes semantic company RAG.
- The persisted provenance source is `system_generated_chat_task`. Optional
  citations point only to company-RAG documents; they never contain copied
  document text, raw chat transcripts, email bodies, or attachment content.

## Alternatives considered

### Keep the Email-derived TaskEpisode without executable `@Email`

Rejected. No valid producer remains, and its task-row ownership rule belongs to
the standalone Email Agent rather than AI Chat.

### Remove TaskEpisodes and retain chat summaries only

Rejected. Explicit user-created tasks need lifecycle, retrieval eligibility,
and cross-session recall distinct from bounded conversation summaries.

### Automatically extract tasks from ordinary chat

Rejected. It creates model-owned durable memory without an explicit user
request and weakens the system-generated trust boundary.

## Consequences

- PRD-v2, target architecture, gap analysis, domain contracts, ports, tests,
  and later PostgreSQL migration must be realigned before persistence lands.
- Existing Email-shaped `TaskEpisode` data is not production-backed, so no
  durable backfill is required. Contract migration remains breaking for local
  code and fixtures and must land tests first.
- The standalone Email Agent remains available through its existing PRD-v1
  APIs; it is not callable from AI Chat.
- Migration `004` is deferred until the generic domain and port contracts are
  accepted. It must include a matching down migration.

## Implementation guardrails

- Contract and focused tests land before migration or repository code.
- Do not add an executable tool path, Gmail selector, mailbox connection field,
  task-row foreign key, scheduler, autonomous scan, or Qdrant episode store.
- Raw email, attachment content, full chat transcripts, and copied RAG chunks
  never enter TaskEpisode persistence, logs, telemetry, or fixtures.
- PostgreSQL remains authoritative durable storage; semantic company RAG
  remains separate and rebuildable.

## Links

- `../PRD-v2-Memory-Extension.md`
- `../architectures/TARGET-ARCHITECTURE.md`
- `../master-comparison.md`
- `../references/PRD2-chat-memory-orchestration.md`
