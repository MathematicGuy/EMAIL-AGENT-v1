# Cowork Agent Project Structure Specification

**Document status:** Approved structure baseline  
**Purpose:** Reorganize the current project so it supports the Target Cowork architecture without introducing Reflexion, multi-agent orchestration, or an AI evaluation framework.  
**Recommended repository path:** `docs/architectures/target-architectures/project-structure-spec.md`

---

## 1. Scope

This specification covers the folder structure, module ownership, dependency boundaries, compatibility requirements, migration sequence, and acceptance criteria for the deterministic Cowork Email-to-Action-Plan feature.

The target workflow is:

```text
Trigger
→ Gmail fetch
→ Email normalization
→ Load context
→ Intent and Knowledge-Sufficiency Classification
→ Route: NO_ACTION / DIRECT_PLAN / RETRIEVE_RAG
→ Optional RAG retrieval
→ Action Item and Action Plan generation
→ Output validation
→ Persist task
→ Persist system_generated episode
→ Clear temporary email state
```

### In scope

- One modular Python application
- Existing Gmail integration
- Existing RAG implementation
- Agent Core for routing and final generation
- Four-type memory system
- Task and run persistence
- Retry, timeout, fallback, and cleanup boundaries
- Observability and development tracing
- Software unit, integration, and contract tests

### Out of scope for this phase

- Reflexion
- Multi-agent architecture
- Model-as-judge
- Retrieval benchmark datasets
- RAG experiment runner
- AI evaluation reports
- Automatic promotion of experimental RAG strategies
- Human approval UI implementation

---

## 2. Architecture Compatibility Audit

### Overall result

The proposed structure is compatible with the Target Cowork architecture.

**Compatibility estimate:** 85–90% before the required corrections in this document.

The structure supports all seven target architecture areas:

| Target architecture area | Project module | Compatibility |
|---|---|---|
| Entry and Control Plane | `app.py`, `orchestration/`, `scripts/` | Supported |
| Gmail / Email Module | `integrations/gmail/` | Supported after adding `provider.py` |
| Agent Core | `features/email_action_plan/` | Supported after separating classifier and router |
| Four-Type Memory System | `memory/` | Supported after adding shared scope and metadata contracts |
| RAG Module | `rag/` | Strongly supported |
| Output / Task Persistence | `persistence/` | Supported |
| Observability and Operations | `ops/`, `.runtime/traces/` | Supported after environment-specific trace policy |

### Feature compatibility matrix

| Feature | Status | Required action |
|---|---|---|
| Scheduled Gmail processing | Supported | Implement through `orchestration/scheduler.py` and `worker.py` |
| Manual workflow execution | Supported | Use `scripts/run_email.py` |
| Gmail OAuth | Supported | Keep in `integrations/gmail/auth.py` |
| Gmail API access | Supported | Keep in `integrations/gmail/client.py` |
| Email normalization | Supported | Keep in `integrations/gmail/normalizer.py` |
| Email provider boundary | Missing | Add `integrations/gmail/provider.py` |
| Actionability classification | Partial | Add `features/email_action_plan/classifier.py` |
| Knowledge-sufficiency classification | Partial | Include in the classifier contract |
| Deterministic route resolution | Supported | Keep in `router.py` |
| `NO_ACTION` route | Supported | Implement in workflow and router |
| `DIRECT_PLAN` route | Supported | Implement in workflow and generator |
| `RETRIEVE_RAG` route | Supported | Call semantic memory/RAG only when selected |
| Final Action Plan ownership | Supported | Keep in Agent Core, not RAG |
| Output schema validation | Supported | Keep in `validators.py` |
| Grounding validation | Partial | Add explicit validation rules |
| Task persistence | Supported | Use `persistence/repositories/tasks.py` |
| Run persistence | Supported | Use `persistence/repositories/runs.py` |
| Short-term memory | Supported | Clarify ownership versus runtime state |
| Long-term memory | Supported | Keep under `memory/long_term/` |
| Episodic memory | Supported | Enforce `system_generated` and non-retrievable defaults |
| Semantic memory | Supported | Implement through RAG |
| Tenant and user isolation | Partial | Add shared memory and RAG scope models |
| TTL and deletion | Partial | Add explicit policies and cleanup behavior |
| Retry and timeout handling | Partial | Define per-component policy in `orchestration/retry.py` |
| Dead-letter queue | Partial | Include in `orchestration/queue.py` |
| Idempotency | Missing | Add to worker and run repository |
| Development traces | Supported | Add explicit environment and retention controls |
| Human approval | Deferred | Preserve episode status fields for future use |
| AI evaluations | Deferred | Do not create `evals/` in this phase |

---

## 3. Required Architecture Corrections

These corrections are required before the structure is considered fully compatible.

### 3.1 Separate classifier from router

Add:

```text
features/email_action_plan/classifier.py
```

Responsibilities:

- Call the structured LLM classifier
- Determine actionability
- Determine email knowledge sufficiency
- Identify knowledge gaps
- Produce a retrieval query when needed
- Return reason codes and confidence
- Handle one classifier repair retry

Keep `router.py` responsible for:

- Hard policy rules
- Confidence thresholds
- Route selection
- Fail-open retrieval behavior
- Final route decision

Expected usage:

```python
classification = await classifier.classify(email, profile)
decision = router.resolve(classification, policies)
```

### 3.2 Add Gmail provider boundary

Add:

```text
integrations/gmail/provider.py
```

Responsibilities:

- Implement the project-level `EmailProvider` contract
- Coordinate OAuth, Gmail API calls, and normalization
- Return the project’s internal temporary email model
- Hide Gmail SDK details from Agent Core

Agent Core must not directly depend on Gmail SDK classes.

### 3.3 Clarify runtime state versus short-term memory

`runtime/state.py` owns the in-process workflow object.

Example state:

- `run_id`
- current workflow step
- route
- retry counters
- status
- temporary references
- error state

`memory/short_term/store.py` owns temporary data that may need external persistence:

- raw email
- normalized email
- classifier output
- retrieved chunks
- generated candidate
- temporary context

Rule:

```text
runtime state = workflow control object
short-term memory = temporary data store with TTL
```

An in-memory implementation may be used initially. Redis should only be added when cross-process recovery or distributed workers require it.

### 3.4 Add shared memory scope and metadata contracts

Add:

```text
memory/scope.py
```

At minimum define:

```python
@dataclass(frozen=True)
class MemoryScope:
    tenant_id: str
    user_id: str
    feature: str
    memory_type: str
    run_id: str | None = None
```

Common metadata must include:

- source type
- source ID
- source URL when available
- created timestamp
- updated timestamp
- expiry timestamp
- confidence
- validation status
- retrieval eligibility
- pipeline version

### 3.5 Define queue, retry, DLQ, and idempotency behavior

`orchestration/queue.py` owns:

- publishing jobs
- consuming jobs
- dead-letter routing after retry exhaustion

`orchestration/retry.py` owns:

- retry eligibility
- exponential backoff
- timeout budgets
- maximum attempts

`orchestration/worker.py` owns:

- idempotency
- run lifecycle
- final cleanup
- terminal failure handling

Queue payloads must contain identifiers, not full email bodies:

```yaml
run_id: string
tenant_id: string
user_id: string
gmail_message_id: string
attempt: integer
```

Recommended idempotency key:

```text
tenant_id + user_id + gmail_message_id + workflow_version
```

---

## 4. Locked Folder Structure

```text
cowork-agent/
├── pyproject.toml
├── README.md
├── Makefile
├── .env.example
├── AGENTS.md
├── CLAUDE.md
│
├── src/
│   └── cowork_agent/
│       ├── __init__.py
│       ├── app.py
│       ├── config.py
│       │
│       ├── domain/
│       │   ├── models.py
│       │   ├── enums.py
│       │   ├── errors.py
│       │   └── identifiers.py
│       │
│       ├── features/
│       │   └── email_action_plan/
│       │       ├── workflow.py
│       │       ├── state.py
│       │       ├── classifier.py
│       │       ├── router.py
│       │       ├── generator.py
│       │       ├── validators.py
│       │       ├── policies.py
│       │       ├── schemas.py
│       │       └── prompts/
│       │           ├── classify.md
│       │           └── generate.md
│       │
│       ├── runtime/
│       │   ├── session.py
│       │   ├── context.py
│       │   ├── state.py
│       │   └── cleanup.py
│       │
│       ├── integrations/
│       │   ├── gmail/
│       │   │   ├── provider.py
│       │   │   ├── client.py
│       │   │   ├── auth.py
│       │   │   ├── normalizer.py
│       │   │   ├── models.py
│       │   │   └── errors.py
│       │   │
│       │   └── llm/
│       │       ├── client.py
│       │       ├── models.py
│       │       └── providers/
│       │           ├── openai.py
│       │           └── anthropic.py
│       │
│       ├── memory/
│       │   ├── service.py
│       │   ├── contracts.py
│       │   ├── scope.py
│       │   ├── router.py
│       │   ├── policies.py
│       │   │
│       │   ├── short_term/
│       │   │   ├── store.py
│       │   │   ├── local.py
│       │   │   └── redis.py
│       │   │
│       │   ├── long_term/
│       │   │   ├── store.py
│       │   │   └── postgres.py
│       │   │
│       │   ├── episodic/
│       │   │   ├── store.py
│       │   │   ├── models.py
│       │   │   └── postgres.py
│       │   │
│       │   └── semantic/
│       │       ├── provider.py
│       │       └── rag.py
│       │
│       ├── rag/
│       │   ├── service.py
│       │   ├── contracts.py
│       │   ├── models.py
│       │   ├── registry.py
│       │   │
│       │   ├── ingestion/
│       │   │   ├── pipeline.py
│       │   │   ├── loaders/
│       │   │   │   ├── filesystem.py
│       │   │   │   ├── drive.py
│       │   │   │   └── pdf.py
│       │   │   ├── parsers/
│       │   │   │   ├── markdown.py
│       │   │   │   └── pdf.py
│       │   │   ├── chunkers/
│       │   │   │   ├── fixed_size.py
│       │   │   │   ├── recursive.py
│       │   │   │   └── semantic.py
│       │   │   ├── enrichers/
│       │   │   │   └── metadata.py
│       │   │   └── embedders/
│       │   │       ├── openai.py
│       │   │       └── local.py
│       │   │
│       │   ├── indexing/
│       │   │   ├── vector/
│       │   │   │   ├── pgvector.py
│       │   │   │   └── chroma.py
│       │   │   └── lexical/
│       │   │       └── bm25.py
│       │   │
│       │   ├── retrieval/
│       │   │   ├── pipeline.py
│       │   │   ├── retrievers/
│       │   │   │   ├── dense.py
│       │   │   │   ├── sparse.py
│       │   │   │   └── hybrid.py
│       │   │   ├── filters/
│       │   │   │   ├── tenant.py
│       │   │   │   └── metadata.py
│       │   │   └── rerankers/
│       │   │       ├── none.py
│       │   │       ├── cross_encoder.py
│       │   │       └── llm.py
│       │   │
│       │   └── context/
│       │       ├── builder.py
│       │       ├── budget.py
│       │       └── citations.py
│       │
│       ├── persistence/
│       │   ├── database.py
│       │   ├── repositories/
│       │   │   ├── tasks.py
│       │   │   └── runs.py
│       │   └── migrations/
│       │
│       ├── orchestration/
│       │   ├── scheduler.py
│       │   ├── queue.py
│       │   ├── worker.py
│       │   └── retry.py
│       │
│       └── ops/
│           ├── tracing.py
│           ├── logging.py
│           ├── metrics.py
│           └── events.py
│
├── tests/
│   ├── unit/
│   │   ├── features/
│   │   ├── memory/
│   │   └── rag/
│   ├── integration/
│   │   ├── gmail/
│   │   ├── rag/
│   │   └── database/
│   ├── contracts/
│   │   ├── chunking.py
│   │   ├── embedding.py
│   │   ├── indexing.py
│   │   ├── retrieval.py
│   │   ├── reranking.py
│   │   └── memory.py
│   └── fixtures/
│       ├── emails/
│       └── documents/
│
├── configs/
│   ├── rag/
│   │   └── baseline.yaml
│   ├── memory/
│   │   └── default.yaml
│   └── environments/
│       ├── development.yaml
│       └── production.yaml
│
├── scripts/
│   ├── ingest.py
│   ├── rebuild_index.py
│   └── run_email.py
│
├── docs/
│   └── architectures/
│       ├── current-architectures/
│       └── target-architectures/
│
└── .runtime/
    ├── indexes/
    ├── caches/
    └── traces/
```

### Structure creation rule

The tree is the intended architecture, not a requirement to create every empty file immediately.

Create a file only when:

- existing code maps to it;
- a current feature requires it;
- a real interface boundary requires it;
- a second implementation is planned;
- a test fake is needed.

Example:

If recursive chunking is the only current strategy, create:

```text
rag/ingestion/chunkers/recursive.py
```

Defer:

```text
rag/ingestion/chunkers/fixed_size.py
rag/ingestion/chunkers/semantic.py
```

until their implementations exist.

---

## 5. Module Ownership

### `domain/`

Owns project-wide business types that are not specific to Gmail, RAG, persistence, or a single feature.

Must not depend on infrastructure packages.

### `features/email_action_plan/`

Owns the complete business workflow:

1. receive temporary email context;
2. load memory context;
3. classify actionability and knowledge sufficiency;
4. resolve route;
5. optionally retrieve semantic context;
6. generate Action Item and Action Plan;
7. validate output;
8. persist task;
9. persist generated episode;
10. clear temporary state.

The feature must not directly import Gmail SDK, vector database SDK, Redis SDK, or PostgreSQL driver details.

### `runtime/`

Owns one workflow execution:

- runtime state object
- context assembly
- session lifecycle
- final cleanup

It must not own durable product data.

### `integrations/gmail/`

Owns Gmail-specific concerns:

- OAuth
- Gmail API calls
- pagination
- rate-limit handling
- payload normalization
- Gmail identifiers and source links

It does not own task generation or memory persistence.

### `integrations/llm/`

Owns provider-specific LLM APIs and shared LLM request/response types.

Feature code should depend on the project LLM client contract, not provider SDKs.

### `memory/`

Owns the four memory types and their shared policies.

- short-term: current-run content and context
- long-term: stable user and system profile
- episodic: task experience and outcomes
- semantic: company knowledge through RAG

### `rag/`

Owns:

- document ingestion
- parsing
- chunking
- enrichment
- embedding
- indexing
- retrieval
- filtering
- reranking
- context construction
- citations

It must not own final Action Plan generation for the Cowork workflow.

### `persistence/`

Owns product and operational records:

- task repository
- run repository
- database connection
- migrations

### `orchestration/`

Owns:

- scheduler
- queues
- workers
- retries
- timeout policies
- DLQ behavior
- idempotent execution

### `ops/`

Owns:

- logging
- tracing
- metrics
- structured lifecycle events

Trace data must not become product data or memory automatically.

---

## 6. Dependency Direction

Allowed dependency direction:

```text
domain
↑
features
↑
runtime / orchestration
↑
app entry points
```

Infrastructure modules implement contracts used by the feature.

Expected dependency flow:

```text
Email Action Plan Feature
├── EmailProvider
├── LLMClient
├── MemoryService
├── SemanticMemory
├── TaskRepository
└── RunRepository
```

Forbidden direct dependencies:

```text
features/email_action_plan → Gmail SDK
features/email_action_plan → vector database SDK
features/email_action_plan → Redis client
features/email_action_plan → PostgreSQL driver
rag → email_action_plan generator
memory → Gmail API client
```

---

## 7. Required Contracts

| Contract | Location | Purpose |
|---|---|---|
| `EmailProvider` | `integrations/gmail/provider.py` | Retrieve and normalize Gmail messages |
| `EphemeralEmailEnvelope` | `integrations/gmail/models.py` | Temporary email data for one run |
| `EmailRouteDecision` | `features/email_action_plan/schemas.py` | Structured classifier and route result |
| `ActionPlanOutput` | `features/email_action_plan/schemas.py` | Final validated generated output |
| `ShortTermStore` | `memory/short_term/store.py` | Temporary run data with TTL |
| `LongTermStore` | `memory/long_term/store.py` | User and system profile |
| `EpisodeStore` | `memory/episodic/store.py` | Task episode persistence and retrieval |
| `SemanticMemory` | `memory/semantic/provider.py` | Company knowledge retrieval boundary |
| `MemoryContextRequest` | `memory/contracts.py` | Scoped memory read request |
| `MemoryScope` | `memory/scope.py` | Tenant, user, feature, run namespace |
| `TaskEpisode` | `memory/episodic/models.py` | Durable episode record |
| `DocumentLoader` | `rag/contracts.py` | Source loading boundary |
| `DocumentParser` | `rag/contracts.py` | Raw-to-document conversion |
| `Chunker` | `rag/contracts.py` | Document splitting strategy |
| `Embedder` | `rag/contracts.py` | Embedding provider boundary |
| `SearchIndex` | `rag/contracts.py` | Index storage and search |
| `Retriever` | `rag/contracts.py` | Candidate retrieval strategy |
| `Reranker` | `rag/contracts.py` | Candidate reranking strategy |
| `SemanticRetrievalRequest` | `rag/models.py` | RAG request |
| `SemanticRetrievalResponse` | `rag/models.py` | Chunks, citations, scores, and status |
| `TraceEvent` | `ops/events.py` | Structured observability event |

Do not define one generic `RAGPhase` interface.

Each RAG capability requires typed, phase-specific inputs and outputs.

---

## 8. Core Workflow Specification

### 8.1 Start run

The worker creates or loads a run record.

Required fields:

- `run_id`
- `tenant_id`
- `user_id`
- `gmail_message_id`
- `workflow_version`
- `status`
- `attempt`
- timestamps

The worker checks the idempotency key before processing.

### 8.2 Fetch and normalize email

The Gmail provider:

1. authenticates;
2. retrieves the Gmail message;
3. normalizes headers and body;
4. returns an `EphemeralEmailEnvelope`;
5. stores raw or normalized email only in temporary state.

### 8.3 Load context

Load:

- long-term profile every run;
- approved or completed episodes only when configured;
- short-term run state;
- no semantic memory yet.

### 8.4 Classify

Classifier output must include:

```yaml
actionability: action_required | action_suggested | informational | unclear | irrelevant
email_is_sufficient: boolean
knowledge_gaps: []
retrieval_query: string | null
reason_codes: []
confidence: number
```

### 8.5 Resolve route

Allowed routes:

```yaml
route: no_action | direct_plan | retrieve_rag
```

Retrieval condition:

```text
RAG_REQUIRED =
ACTIONABLE
AND EMAIL_NOT_SUFFICIENT
AND MISSING_INFORMATION_LIKELY_EXISTS_IN_COMPANY_KB
```

On invalid classifier output:

- perform one repair retry;
- if still invalid and the message is potentially actionable, use conservative RAG retrieval;
- do not invent company-specific procedures.

### 8.6 Optional semantic retrieval

When route is `retrieve_rag`:

1. build a scoped retrieval request;
2. enforce tenant and ACL filters;
3. retrieve candidates;
4. optionally rerank;
5. build a context pack;
6. return citations and scores.

On RAG timeout or no useful context:

- return a structured empty or partial result;
- continue with a partial plan;
- mark missing context;
- do not fabricate procedures.

### 8.7 Generate and validate

Generator inputs:

- normalized email;
- long-term profile;
- approved episodic context when available;
- retrieved RAG context when available;
- system and planning policies.

Output:

- Action Item
- Action Plan steps
- priority
- deadline when supported
- missing information
- citations
- confidence

Validation must check:

- schema
- required fields
- citation references
- unsupported company-specific claims
- completeness

### 8.8 Persist

Persist a minimal product task.

Persist a task episode with:

```yaml
status: system_generated
retrieval_eligible: false
```

Do not persist the raw email body in long-term, episodic, or semantic memory.

### 8.9 Cleanup

Cleanup must run on success and terminal failure.

Delete or expire:

- raw email
- normalized email
- classifier temporary payload
- retrieved chunks
- generated candidate
- temporary prompt context

---

## 9. Memory Rules

### Short-term

Scope:

```text
tenant / user / feature / run
```

Contents:

- raw email
- normalized email
- classifier output
- retrieved context
- generated candidate

Retention:

- delete at run completion;
- retain a safety TTL in case cleanup fails.

### Long-term

Contents:

- language
- timezone
- output preferences
- sender priority rules
- configured contacts
- stable system settings

Writes:

- explicit configuration;
- manual update;
- approved product workflow only.

Raw email content is prohibited.

### Episodic

Contents:

- task category
- minimal Action Item
- Action Plan
- citations
- Gmail pointer
- status
- outcomes

Initial status:

```yaml
status: system_generated
retrieval_eligible: false
```

Future transitions:

```text
system_generated → user_approved → retrieval eligible
system_generated → completed → retrieval eligible
system_generated → rejected → blocked
```

### Semantic

Source:

- existing RAG system

Contents:

- procedures
- policies
- governance documents
- SOPs
- templates
- guidelines

The Agent may read semantic memory on demand.

The Agent must not write email-derived content directly into the semantic index.

---

## 10. Privacy and Data Persistence Rules

Allowed temporary path:

```text
Gmail
→ Email Provider
→ runtime / short-term memory
→ optional development trace
→ deletion
```

Prohibited automatic paths:

```text
raw email → long-term memory
raw email → episodic memory
raw email → semantic index
raw email → queue payload
raw email → production logs
raw email → task database
```

Minimal durable task or episode fields may include:

```yaml
gmail_message_id: string
gmail_url: string
task_title: string
minimal_request_paraphrase: string
action_plan: object
citations: list
status: string
confidence: number | null
```

Development tracing configuration:

```yaml
development:
  allow_full_email_traces: true
  trace_warning: "ALLOW ONLY FOR CURRENT DEVELOPMENT STAGE"
  trace_ttl_days: 7

production:
  allow_full_email_traces: false
```

Database-layer privacy controls are necessary but not sufficient. The same restrictions must cover logs, queues, caches, workflow checkpoints, model prompts, provider logging, and error payloads.

---

## 11. Reliability Specification

| Component | Retry | Timeout / fallback |
|---|---|---|
| Gmail authentication | Refresh once | Terminal auth failure after refresh |
| Gmail API 429/5xx | Exponential backoff, limited attempts | Mark run retryable |
| Email normalization | No infrastructure retry | Fail message with structured error |
| Classifier | One retry for timeout or invalid schema | Conservative RAG route when appropriate |
| Long-term memory read | Limited technical retry | Use default profile |
| Episodic memory read | Limited technical retry | Continue without episodes |
| RAG retrieval | One technical retry | Partial plan with missing-context flag |
| Generation | One repair retry | Mark run failed after exhaustion |
| Task persistence | Transaction retry where safe | Do not duplicate task |
| Episode persistence | Transactional or outbox pattern | Reconcile asynchronously |
| Notification | Independent retry | Must not roll back persisted task |
| Cleanup | Retry plus TTL purge | Background purge catches failures |

---

## 12. RAG Flexibility Without Evaluations

The implementation must preserve future RAG flexibility without creating an evaluation framework now.

Stable contracts:

- loader
- parser
- chunker
- embedder
- index
- retriever
- reranker

`rag/registry.py` must use explicit mappings.

Example:

```python
CHUNKERS = {
    "recursive": RecursiveChunker,
}

RETRIEVERS = {
    "dense": DenseRetriever,
}
```

No reflection-based dynamic imports.

`configs/rag/baseline.yaml` must describe only the current working RAG path.

Changing parser, chunker, metadata schema, embedding model, or index backend later will require a separate index identity. Evaluation-specific datasets and runners remain deferred.

---

## 13. Software Test Requirements

### Unit tests

Required coverage:

- email normalization
- classifier schema parsing
- route resolution
- memory policies
- TTL calculations
- episode retrieval eligibility
- chunk creation
- tenant filters
- context building
- output schema validation
- cleanup behavior

### Integration tests

Required coverage:

- Gmail provider with mocks or approved sandbox
- RAG ingestion against a test backend
- RAG retrieval with tenant filters
- task persistence
- episode persistence
- idempotent worker execution
- workflow execution using fake LLM outputs

### Contract tests

Contract tests are software compatibility tests, not AI evaluations.

Run the same contract behavior against each implementation of:

- chunking
- embedding
- indexing
- retrieval
- reranking
- memory stores

Do not add benchmark datasets, model judges, retrieval-quality metrics, or an `evals/` folder in this phase.

---

## 14. Migration Plan

### Phase 1 — Inventory and mapping

Produce:

| Current path | Target path | Action | Reason |
|---|---|---|---|

Allowed actions:

- keep
- move
- rename
- wrap
- split
- defer

No source file should be moved before the mapping is reviewed.

### Phase 2 — Create shared contracts

Create:

- Email provider contract
- Gmail internal models
- RAG shared models and contracts
- Memory store contracts
- Memory scope
- Task and run repository contracts

### Phase 3 — Wrap existing integrations

Wrap existing Gmail code behind `EmailProvider`.

Wrap existing RAG code behind `RAGService` and `SemanticMemory`.

Preserve behavior.

### Phase 4 — Build the Agent Core boundary

Separate:

- classifier
- router
- generator
- validators
- policies
- workflow

Keep final generation inside Agent Core.

### Phase 5 — Add memory boundaries

Implement:

- local short-term store first;
- existing long-term store;
- episodic persistence with retrieval disabled;
- semantic memory through RAG.

### Phase 6 — Add persistence and orchestration controls

Implement:

- run repository
- task repository
- idempotency
- retries
- DLQ
- cleanup
- trace controls

### Phase 7 — Verify

Run:

- import checks
- formatter
- linter
- type checker
- unit tests
- contract tests
- credential-free integration tests

---

## 15. Acceptance Criteria

The reorganization is complete when all statements below are true.

### Structure

- [ ] Existing Gmail and RAG code is mapped before being moved.
- [ ] No duplicate Gmail or RAG implementation exists.
- [ ] No unnecessary microservice is introduced.
- [ ] Folder names and filenames follow the minimal naming convention.
- [ ] No empty experimental implementation is created.

### Agent Core

- [ ] Classifier and router are separate.
- [ ] Agent Core owns the final Action Plan generation.
- [ ] Routes are limited to `no_action`, `direct_plan`, and `retrieve_rag`.
- [ ] Invalid classifier output has a bounded fallback.

### Gmail

- [ ] Agent Core depends on `EmailProvider`, not Gmail SDK internals.
- [ ] Normalized Gmail data uses a typed internal model.
- [ ] Queue messages contain Gmail identifiers, not full email bodies.

### Memory

- [ ] Four memory types have distinct ownership.
- [ ] Every memory operation is tenant- and user-scoped.
- [ ] Durable memory records contain provenance and lifecycle metadata.
- [ ] New episodes are `system_generated` and not retrieval eligible.
- [ ] Raw emails do not enter long-term, episodic, or semantic memory.

### RAG

- [ ] RAG returns chunks, scores, status, and citations.
- [ ] RAG does not generate the final Cowork Action Plan.
- [ ] Tenant and ACL filters are applied before returning context.
- [ ] Baseline configuration reproduces current behavior.

### Persistence and operations

- [ ] Worker execution is idempotent.
- [ ] Retry and timeout behavior is explicit and bounded.
- [ ] DLQ behavior is defined.
- [ ] Cleanup runs on success and terminal failure.
- [ ] Production traces exclude full email content.
- [ ] `.runtime/` is ignored by Git.

### Testing

- [ ] Unit tests pass.
- [ ] Contract tests pass.
- [ ] Credential-free integration tests pass.
- [ ] No AI evaluation framework was added.

---

## 16. Deferred Upgrade Path

After the reorganized baseline works:

1. freeze the baseline RAG configuration;
2. create approved routing and retrieval datasets;
3. add deterministic retrieval metrics;
4. add experiment-specific index identities;
5. add experiment runners;
6. add model judges only after deterministic metrics;
7. require explicit approval before changing production defaults.

This upgrade must be a separate architecture change and must not block the initial reorganization.

---

## 17. Source Basis

This specification consolidates:

- `master-comparison-architecture.md`
- `project-structure-scraffold.md`
- the completed compatibility audit for the proposed Cowork project structure

Where the current repository implementation has not yet been provided, this document defines target requirements rather than claiming implementation status.
