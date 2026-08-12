# ADR-005 — Project-scoped chat documents are a second semantic plane

- Status: Accepted
- Date: 2026-08-12
- Decision makers: Product/Engineering team
- Extends: [ADR-004 — Chat-native TaskEpisodes](./ADR-004-chat-native-task-episodes.md)
- Target section: [TARGET-ARCHITECTURE §21](../architectures/TARGET-ARCHITECTURE.md)

## Context

PRD-v1 and PRD-v2 are implemented. AI Chat reads four memory types through the
Memory Gateway, and semantic memory is an administrator-curated company corpus:
PDFs and DOCX files are converted offline by `KnowledgeIngestionService` into
`data/extracted/*.md`, loaded by `load_corpus`, and served tenant-scoped from
Qdrant or an in-repo hybrid index.

The requested capability — a user uploads a PDF and asks questions about it in
chat — does not fit that plane. The corpus is administrator-owned, approved
(`document_status: ready`), tenant-scoped, and rebuildable from the repository.
A self-service upload is user-owned, unreviewed, scoped narrower than a tenant,
not rebuildable, and must be deletable on request.

Three further mismatches exist:

- Semantic retrieval fires only on hard-coded cue phrases such as "company
  policy", which would leave an uploaded document unread for most questions
  about it.
- The corpus chunker splits on Markdown headings and carries no page
  provenance, so a document answer cannot cite "page 7".
- Chat sessions have no container. Documents pinned to a single `session_id`
  would have to be re-uploaded for every new conversation about the same file.

## Decision

Introduce a **Project** container, and store project documents in a **second
semantic retrieval plane** — not a fifth memory type and not a new region of
the company corpus.

**Project container.** A Project is a user-owned workspace holding documents
and chat sessions: `tenant → user → project → { documents, sessions }`. Every
chat session belongs to exactly one project; `project_id` becomes a mandatory
field of the chat session scope, and a per-user default project is created on
first use so the existing session flow keeps working. Documents are members of
a project rather than attachments of a session: upload once, every session in
that project can ground on it, with no attach/detach step. Deleting a project
cascades to its documents and session state.

**Second semantic plane.** The namespace carries
`document_scope: company | project_document`, and a request that omits or
mismatches it fails closed. Both planes remain `memory_type: semantic` and
retrieval-only. Project documents live in their own Qdrant collection, filtered
by `tenant_id`, `user_id`, `project_id`, `ready` status, and expiry, with the
filter assembled before the query is embedded, as on the company path.

**Qdrant is required for this plane.** The company corpus may fall back to the
in-repo hybrid index because it is rebuildable from `data/extracted/`; a project
document exists only in Qdrant. An unavailable vector store therefore degrades
the plane explicitly — empty result plus `degraded: true` — and never
substitutes other evidence.

**Ingestion runs at runtime, off the request path**, reusing the PRD-v1
extractors and their size, page, and encryption guards. Uploads answer `202`, so
a chat turn never blocks on ingestion. `document_id` is derived from tenant,
user, project, and the content digest, so identical bytes are not indexed twice
and no filename or document text is encoded in the identifier.

**OCR is enabled.** Pages that `PdfInspector` reports as needing OCR are sent to
the configured Mistral OCR provider under the existing `max_ocr_pages`,
`timeout_seconds`, and `max_attempts` settings. Native-text pages are never
re-OCR'd. Exceeding the page cap fails as `ocr_page_limit_exceeded`; a provider
failure after bounded retries fails as `ocr_failed`. Partial or empty extraction
output is never indexed.

**Chunking is page-aware** for this plane, so every citation resolves to a page
range.

**Retrieval is deterministic rather than cue-driven:** when the session's
project holds at least one ready document, the plane is queried every turn. The
company plane keeps its existing selective policy.

**Context assembly** gains a `project_document_evidence` label, ranked below the
current user instruction and above company evidence, with an explicit
scope-of-authority rule: the project document is authoritative for its own
content, company RAG remains authoritative for company procedure, and a genuine
contradiction is surfaced with both citations rather than silently resolved.

**Retention defaults to 30 days** from upload, configurable per tenant. Expired
documents are excluded from retrieval before ranking and purged by the existing
background purge.

**Episodes** may cite a project document as coordinates only, discriminated by
`citation_scope`, and persist `project_id`. Episodic *retrieval* scope is
unchanged from PRD-v2 FR-09 (tenant, user, `feature: ai_chat`); recording
`project_id` now allows a stricter project-scoped rule later without a data
migration. Document text never enters an episode, log, telemetry field, or
fixture.

## Alternatives considered

### Scope documents to a single chat session

Rejected. The user would re-upload the same file for every new conversation
about it, and session-lifetime deletion would destroy documents the user still
wants. A project container keeps documents durable and shared across the
sessions that are actually about them.

### Scope documents to the whole user, with no container

Rejected. Every unrelated conversation would then retrieve against every
document the user ever uploaded, degrading precision and leaking unrelated
context into answers. A project is the natural retrieval boundary.

### Index user uploads into the existing company collection with an extra filter

Rejected. It puts unreviewed user data one predicate away from tenant-wide
company knowledge; a single missing filter leaks a personal document to every
user in the tenant. It also breaks the "corpus is rebuildable from
`data/extracted`" property, because a re-index would either destroy user data or
force the corpus loader to understand user ownership.

### Reuse the offline ingestion CLI and write uploads into `data/extracted/`

Rejected. That directory is committed, administrator-owned, tenant-scoped
knowledge with no per-user ACL, no expiry, and no deletion path. It would make
every upload permanent company knowledge.

### Add a fifth memory type

Rejected. The read is retrieval-only semantic evidence with citations, which is
what the semantic type already means. A fifth type would duplicate gateway
policy without changing behavior; a scope discriminator carries the distinction
that matters.

### Stuff the whole document into the prompt instead of retrieving

Rejected. It does not survive the page and size caps already enforced in
ingestion, gives no page-level citations, makes cost scale with document size on
every turn, and removes the threshold that prevents ungrounded answers.

### Extend the existing cue-phrase trigger to cover documents

Rejected. Cue phrases are the wrong control here: putting a document in the
project *is* the intent signal, and a phrase list would silently drop most
questions about it.

### Keep OCR deferred and reject scanned PDFs

Rejected for this extension. Scanned PDFs are a large share of real uploads, and
the OCR provider, page cap, timeout, and attempt budget already exist in
`KnowledgeIngestionSettings`. Rejecting them would make the feature fail on the
documents users most want to ask about.

## Consequences

- New contracts land before code: project record, document record, ingestion
  status machine, retrieval request/response with page provenance,
  `citation_scope`, and `document_scope`.
- The chat session scope gains `project_id`, which touches the session
  registry, the memory namespace, and the demo frontend session flow. The
  default project keeps the existing `POST /sessions` contract working.
- The chat API gains project CRUD plus document upload, status, list, and
  delete routes. No new SSE event type is added; document evidence is disclosed
  through the existing `memory_citation` event.
- The Mistral OCR client, currently a deliberate gap (`mistral_not_configured`),
  must be implemented against the existing `OcrPage` contract and bounded
  settings. OCR sends page images to an external provider, which must be
  disclosed in product copy.
- The system stores user content durably for the first time. Encryption at
  rest, per-request access checks, 30-day retention, deletion propagation, and
  metadata-only telemetry become mandatory rather than advisory.
- Qdrant becomes a hard dependency of this feature, unlike the company corpus.
- The standalone PRD-v1 Email Agent, the company corpus, the declarative
  profile, and the TaskEpisode trust boundary are unchanged.

## Implementation guardrails

- Do not merge the two planes: a user upload never reaches the company corpus,
  and a company document is never re-scoped to a project.
- Build the ACL filter before embedding the query, and treat a project with no
  ready documents as a disabled plane rather than an unfiltered query.
- Never index partial or empty extraction output, and never re-OCR a page that
  already yielded native text.
- Run extraction and OCR inside the ingestion job; `PdfInspector` shells out to
  local commands and must never run on the request path.
- Keep document text out of episodes, logs, telemetry, traces, and fixtures.
- Keep safety counters at zero under test: cross-tenant, cross-user,
  cross-project, expired, and deleted-document retrieval.
- No executable in-chat tool, scheduler, autonomous scan, or Gmail attachment
  ingestion enters through this extension.

## Links

- `../architectures/TARGET-ARCHITECTURE.md` §21
- `../PRD-v1-Core-Email-and-RAG.md`
- `../PRD-v2-Memory-Extension.md`
- `./ADR-002-sandboxed-attachment-extraction.md`
- `./ADR-003-defer-attachment-processing.md`
- `./ADR-004-chat-native-task-episodes.md`
