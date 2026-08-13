# ADR-006 — A single user-document plane with classifier-gated retrieval

- Status: Superseded by [ADR-007](./ADR-007-project-scoped-classifier-gated-user-documents.md)
- Date: 2026-08-12
- Decision makers: Product/Engineering team
- Supersedes: the withdrawn project-scoped chat-document design — a `Project`
  container, two coexisting chat document planes, and deterministic
  always-on retrieval

## Context

AI Chat must let a user upload their own documents, ask grounded questions about
them, and receive page-level citations. Two design questions decide the shape of
that feature: where an uploaded document lives, and what decides whether a given
turn retrieves from it.

An earlier accepted design answered both with structure. It introduced a
`Project` container that every chat session was bound to, gave chat two
retrieval planes discriminated by a `document_scope` field, and retrieved on
every turn whenever the active project had ready documents. Retrieval routing
elsewhere in the runnable system is currently decided by hard-coded cue phrases
in `retrieval_policy`.

Two facts made that design worth reopening:

- The container earns nothing for the target user. A single user's uploads are
  small enough that grouping them does not improve ranking; the container adds a
  key, an API surface, a migration, a binding rule, and a failure branch.
- Retrieving on every turn is expensive and noisy, and cue phrases do not
  resolve the cases that actually matter — a topic shift away from the
  documents, a message that mentions a document without needing it, or a bare
  "what did it say about that". Every phrase list strong enough to handle those
  is already a classifier, just one that cannot be improved by prompting or
  measured against a labeled fixture set.

## Decision

**1. One user-document plane, no container.**
An uploaded document is scoped by `tenant_id` + `user_id` + `document_id` and is
visible in every chat session belonging to that user. There is no `Project`
entity, no session-to-project binding, and no folder hierarchy. Narrowing a
search is served by an optional `document_ids` filter on the retrieval request.

**2. The user-document plane and the company corpus stay separate.**
Both are `memory_type: semantic` and are read through retrieval-only ports, but
they are never merged. A user upload cannot enter the company corpus, and
company documents are never re-scoped to a user. The company corpus continues to
serve the standalone PRD-v1 Email Agent; chat-side company retrieval is
flag-controlled and disabled in this baseline.

**3. The intent classifier is the sole routing authority.**
Every turn makes exactly one structured LLM call producing two independent
boolean axes, `needs_rag` and `needs_tool`, plus `needs_clarification`, a
`retrieval_query`, a confidence value, and reason codes. A deterministic truth
table resolves those into a route; `intent` is an observability label and never
participates in the decision. No keyword or regex layer may conclude on the
classifier's behalf, including concluding "yes".

**4. Hard cases are solved by prompt structure, not phrase lists.**
The classifier prompt is assembled in five fixed tiers — decision principle,
precedence rules, bounded evidence, calibrated exemplars, output schema. The
precedence tier settles trap cases in a stated order. Evidence given to the
classifier is bounded to the current message, the bounded session turns, and the
titles of ready documents; never document text. Prompts are versioned, and
changing one requires re-running a labeled fixture set without regressing the
published thresholds.

**5. Deterministic layers may only narrow, never originate.**
Three remain: a precondition gate that downgrades `RAG` to `CHAT` when the user
has no ready documents, schema validation on the structured output, and a
feature-flag downgrade that forces `needs_tool` to `false` while the action axis
is disabled. None of them can route a turn to retrieval.

**6. Failure is asymmetric.**
Retrieval routing fails **open**: a classifier timeout or invalid schema is
retried once and then treated as `needs_rag = true` when ready documents exist,
because answering confidently without reading a document the user uploaded is
the more damaging error. The action axis fails **closed**. Missed-RAG rate is
the deciding launch metric.

**7. Qdrant is mandatory for this plane, with ACL applied first.**
Unlike the company corpus there is no in-repo fallback index; a user document
exists only in Qdrant. The tenant, user, `ready`-status, and unexpired filters
are assembled **before** the query is embedded, so a chunk belonging to another
user is never scored. An unavailable store returns an explicitly degraded empty
result and the turn says so; it never silently substitutes other evidence.

**8. OCR is in scope; ingestion runs off the request path.**
Extraction reuses the PRD-v1 `PdfInspector` and `DocxExtractor` guards, and
pages reported as needing OCR are sent to the configured Mistral OCR provider
under the existing page, timeout, and attempt caps. Upload responds `202` and
indexing runs as a job. Chunking is page-aware so every chunk carries
`page_start` and `page_end`. Partial or empty extraction output is never
indexed.

**9. Documents are user-owned durable data.**
Document text never enters the company corpus, TaskEpisodes, the declarative
profile, logs, telemetry, or fixtures. A TaskEpisode may cite a user document as
coordinates only, discriminated by `citation_scope`. Retention defaults to 30
days from upload and deletion purges the object store, the extracted text, and
the vector points.

## Alternatives considered

### Keep the `Project` container

Rejected. It buys grouping the target user does not need, at the cost of a new
entity, endpoints, a migration, a session-binding rule, and an "active project
missing" failure branch. An optional `document_ids` filter provides the same
narrowing without any of it. A container can be added later if sharing or
team-scoped corpora arrive; nothing in this decision blocks that.

### Two chat document planes discriminated by `document_scope`

Rejected for the chat baseline. Serving both planes in one turn doubles latency
and forces a conflict-resolution rule before there is evidence that chat-side
company retrieval is useful. The `citation_scope` discriminator is retained in
the contracts so enabling the second plane later is additive.

### Retrieve on every turn whenever ready documents exist

Rejected. It is deterministic and simple, but it spends an embedding and a
vector query on greetings and on turns that have moved off the documents, and it
injects irrelevant evidence into the prompt. It also removes the one signal
worth measuring — whether the system knew it needed to read.

### Keep cue phrases, or add a cheap keyword pre-layer in front of the classifier

Rejected. A pre-layer that can only vote "yes" still originates routes, and it
is precisely the trap cases — mentions without need, topic shifts, bare deictics
— where a phrase list is wrong. Splitting authority across two mechanisms also
makes the fixture set measure the union rather than the thing being improved.
Quality work goes into the prompt instead, gated by `prompt_version`.

### Let `intent` decide the route directly

Rejected. A single enum collapses two independent questions and forces an
ambiguous label for a turn that needs both evidence and an action. Two booleans
resolved by a truth table keep the axes independent and let the action axis be
disabled at runtime without touching the classifier contract.

### Skip OCR in the first release

Rejected. Scanned PDFs are a normal upload, and a document that silently indexes
zero pages is worse than one that fails loudly. The existing ingestion settings
already bound the cost.

## Consequences

- The withdrawn project-scoped design is not implemented; §21 of the target
  architecture was rewritten around this decision, and PRD-v3 plus the
  chat-with-user-documents SPEC become the product and technical authorities.
- `retrieval_policy` cue-phrase gating is replaced by the classifier for chat
  turns. This changes chat behaviour, so it lands only after the labeled fixture
  set and its metrics exist.
- Every chat turn incurs one classifier call. Its p95 latency is a published
  budget, and its failure path is a defined fail-open, not an incident.
- Qdrant becomes a hard dependency for the user-document plane. There is no
  local-only mode for this feature.
- OCR sends page images to an external provider. That transfer is part of the
  documented upload path and must be disclosed in product copy.
- User documents are the first user-owned durable content in the system, so
  deletion, retention, and export obligations now apply to a store that
  previously held only rebuildable or system-generated records.
- The action axis exists in the contract but is unreachable at runtime; the
  `@Email` tool remains out of scope and is the last priority.

## Implementation guardrails

- Contracts and focused tests land before the migration, the ingestion job, or
  the retrieval adapter.
- No keyword, regex, or heuristic layer may set `needs_rag = true`. Deterministic
  code may only narrow capability.
- ACL filters are constructed before the query embedding, and a missing or
  inconsistent scope fails closed before any I/O.
- Document bytes, extracted text, retrieved chunks, and assembled prompts never
  enter durable turn state, telemetry, logs, or fixtures.
- The administrator-operated `KnowledgeIngestionService` CLI is not modified; the
  two ingestion lifecycles stay separate.
- Orchestration-library knowledge stays inside the graph assembly module; node
  behaviour must remain framework-free and unit-testable in isolation.
- These safety counters must be zero under test: cross-tenant retrieval,
  cross-user retrieval, retrieval of an expired or deleted document, and document
  text appearing in an episode, log, or telemetry field.
- Do not add project or folder entities, document sharing, promotion into the
  company corpus, Gmail attachment ingestion, or any executable in-chat tool.

## Links

- `../prds/PRD-v3-chat-with-user-documents.md`
- `../specs/SPEC-chat-with-user-documents.md`
- `../../docs/architectures/TARGET-ARCHITECTURE.md`
- `./ADR-003-defer-attachment-processing.md`
- `./ADR-004-chat-native-task-episodes.md`
- `../../docs/references/user_preference.md`
