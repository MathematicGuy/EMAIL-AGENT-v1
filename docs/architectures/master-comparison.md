# Master Comparison — Current Architecture vs Target Cowork Agent

**Alignment status:** Corrected against `TARGET-ARCHITECTURE.md`<br>
**Authoritative target:** `TARGET-ARCHITECTURE.md` — Baseline target architecture<br>
**Current-code baseline:** commit `cf2fd49801d5932b26de82af9d104d730cf58271`, branch `main`<br>
**Date:** 2026-08-07

## Label legend

| Label | Meaning |
|---|---|
| **[S]** | Source-derived observation verified against the current code review |
| **[T]** | Required by the authoritative target architecture |
| **[D]** | Migration or implementation recommendation consistent with the target |
| **[A]** | Assumption that must be validated |
| **[U]** | Unresolved implementation question that does not override the target |

---

# 0. Alignment Decision

The original `master-comparison.md` was **conceptually aligned but not contract-aligned** with the target. It correctly identified the current system, preserved the one-shot deterministic pattern, kept RAG retrieval-only for the Cowork workflow, and protected raw email from durable memory. However, it diverged from the target on several load-bearing decisions.

## Corrected target execution shape

```text
One or more bounded classifier batch calls
→ one route decision per selected email
→ deterministic thread/incident correlation into task candidates
→ zero or one RAG retrieval per task candidate
→ one Action Plan generation call per task candidate
→ validate and persist output
→ write system-generated episode
→ delete ephemeral run state
```

This means:

- The classifier is a **separate structured LLM call** from Action Plan generation. **[T]**
- The classifier may produce a minimal `candidate_action_item`, but it does **not** produce the final draft plan. **[T]**
- Every resolved task candidate reaches exactly one final Action Plan generation call. Classifier
  invocations may be bounded batches, but each selected email still receives one route decision.
  **[T+D]**
- RAG failure produces a partial plan with explicit missing context; the system must not restore an unsupported pass-one draft or invent company procedure. **[T]**

## Critical corrections applied

| Area | Original comparison | Target-aligned correction |
|---|---|---|
| Classifier/generator calls | Reused the existing extraction-and-plan call as classifier and draft generator | Split into bounded classifier batch calls and exactly one final generator call per resolved task candidate |
| RAG path | Draft first, retrieve, regenerate only on RAG route | Classify first, retrieve conditionally, then generate once |
| RAG failure | Keep the pass-one draft | Generate a partial plan and expose missing context |
| Scheduler | Deferred | Required baseline entry path alongside `@Email` |
| Queue and DLQ | Deferred after deleting the fake queue | Delete fake queue, then implement a real queue worker and DLQ as target control plane |
| Long-term and episodic storage | SQLite recommended | PostgreSQL is the target initial durable store; SQLite may remain local-development only |
| Attachments | Existing extraction retained and extended | Attachment processing is out of scope; record presence only |
| Classifier contract | Existing enum plus two fields | Use the target actionability, route, reason-code, query, document-type, and numeric-confidence contract |
| Memory boundary | “Not a memory subsystem” | Implement a logical Memory Gateway/Facade and policy layer; it may remain in-process |
| Evaluation | Deferred | Build a labeled routing evaluation dataset before declaring routing stable |

## Decisive ownership answer

The current code generates Action Plans inside the Email workflow: the provider produces candidate steps, provider-specific code shapes them, and `DigestWorker` stores the result. **[S]** The target changes ownership as follows:

```text
Email Module → fetch and normalize only
Classifier → actionability and knowledge sufficiency only
RAG Module → retrieve company chunks and citations only
Agent Core → orchestrate and own final Action Plan generation
Validators → enforce schema, grounding, and citation rules
```

Moving deterministic plan-shaping functions out of `gemini.py` remains a valid cleanup, but it is not enough by itself. The existing extraction call must be split or replaced so the target has a classifier stage and a separate generator stage. **[D]**

## Target precedence rule

Where `docs/references/ARCHITECHTURE.md`, earlier RAG blueprints, or the original comparison conflict with `TARGET-ARCHITECTURE.md`, this document treats `TARGET-ARCHITECTURE.md` as authoritative. In particular, the previous draft-then-retrieve-then-reground flow is superseded by classify-then-retrieve-then-generate-once.

---

## Step 1 — The existing architecture, as provided

No redesign in this section. Everything is source-derived unless labelled otherwise.

### 1.1 Services

One deployable: the FastAPI process created by `create_app()` (`server.py:44-292`). The Streamlit app in `gui/` is a test client, not a backend. There is no second service, no worker process, and no internal service boundary. External dependencies are Google OAuth, Gmail API v1, and one of Gemini or Groq.

### 1.2 Modules

| Module | Responsibility |
|---|---|
| `api/server.py` | Composition root, all HTTP routes, background dispatch |
| `api/handlers.py` | `_jsonable` serializer; also an unwired `MailTodoApi` class |
| `application/services.py` | `CreateDigestRun`, `DigestWorker`, `GetDigestResult` |
| `application/ports.py` | Nine protocols: mailbox, connections, attachments, actions, queue, publisher, runs, results, outbox |
| `application/contracts.py` | `ThreadContext`, `ExtractedAction`, `EmailExtraction`, `ExtractionBatch`, `ExtractionLimits` |
| `domain/models.py` | `EmailEnvelope`, `ActionItem`, `ActionPlanStep`, `DigestRun`, `EvidenceRef`, enums |
| `domain/policies.py` | Query normalization, limit validation, priority calculation, action fingerprinting |
| `infrastructure/gmail.py` | OAuth driver, mailbox adapter, message parser, error mapping |
| `infrastructure/gemini.py` | Transport, key rotator, prompt, schema, parser, **plan shaping and incident merging** |
| `infrastructure/groq.py` | Transport and error mapping only; everything else imported from `gemini.py` |
| `infrastructure/memory.py` | In-memory run/result repositories, queue, outbox, attachment extractor, test fakes |
| `infrastructure/connections.py` | SQLite mailbox connection repository |
| `infrastructure/security.py` | Fernet token cipher, HMAC-signed OAuth state manager |
| `infrastructure/config.py` | `GmailSettings`, `GeminiSettings`, `GroqSettings` from environment |

**No RAG module. No agent module. No memory module. No scheduler.**

### 1.3 APIs

Nine endpoints: `GET /health`; OAuth connect and callback; connection list and delete; unread preview; `POST /v1/mail-todo/runs`; run status; run result. No ingestion, retrieval, knowledge, chat, notification, approval, or preference endpoint exists.

### 1.4 Databases

| Store | Type | Durability |
|---|---|---|
| `mailbox_connections` | SQLite, default `.data/mail_todo.db` | Durable |
| Runs and idempotency map | `InMemoryRunRepository` | Process lifetime |
| Action items, warnings, processed metadata | `InMemoryResultRepository` | Process lifetime |
| `migrations/001_mail_todo.sql` | PostgreSQL DDL for connections, schedules, runs, schedule occurrences, action items, attachment extractions, outbox | **Not wired** — `create_app()` instantiates no PostgreSQL adapter |

### 1.5 Queues

**There is no queue.** `InMemoryQueue` records run IDs and nothing reads them. The instance is constructed inline at `server.py:70` and never stored on `app.state`, so its `run_ids` list is unreachable by any other code in the process. `InMemoryOutbox` is constructed inline at `server.py:88`; `DigestWorker` calls `.add()` on it (`services.py:243`) but its `pending()` and `mark_published()` can never be called. **Both are provably write-only.** Actual dispatch is `background_tasks.add_task(worker.execute, run.id)` at `server.py:231`, issued by the route, not by `CreateDigestRun`.

### 1.6 State ownership

| State | Owner | Lifetime |
|---|---|---|
| OAuth client secrets, cipher key, state secret | Process environment via `GmailSettings` | Process |
| Encrypted refresh token, mailbox ownership | `SQLiteMailboxConnectionRepository` | Until disconnect |
| OAuth nonce and PKCE verifier | `OAuthStateManager`, in memory | Invalid at TTL; evicted only by a later sweep, its own consumption, or restart |
| Raw email, normalized envelope, thread context | `GmailMailboxAdapter` → `DigestWorker`, transient | One worker call |
| Extracted attachment text | `SafeTextAttachmentExtractor` result, transient | One worker call |
| Run state, counters, safe error | `InMemoryRunRepository` | Process |
| Action items, warnings, processed metadata | `InMemoryResultRepository` | Process |
| Completion event | `InMemoryOutbox` | Process, unreadable |
| Candidate plan steps | LLM, then `gemini.py` shaping functions | One call |
| Final `ActionItem.action_plan` | Assigned unchanged by `DigestWorker` at `services.py:218` | With the action item |

### 1.7 Retry behavior

- **Gmail: none.** No retry, backoff, jitter, or explicit 429 handling.
- **Gemini: key rotation only, on HTTP 429 only** (`gemini.py:136-152`). Attempts are `min(GEMINI_MAX_ATTEMPTS_PER_REQUEST, key count)` (`config.py:106`), so a single-key deployment retries zero times. `GEMINI_ROTATE_ON_RATE_LIMIT=false` re-raises immediately. Non-429 errors are never retried.
- **Groq: none.**
- **Batches are not checkpointed.** `gemini.py:124-127` loops batches and lets any failure escape, discarding every earlier successful batch in the run.
- **Idempotent replay re-registers a background task**, but `claim` refuses a run that is no longer `queued` (`memory.py:43-48`), so the duplicate execution is a no-op.

### 1.8 Timeout behavior

- Gemini: `HttpOptions(timeout=timeout_seconds * 1000)` (`gemini.py:70-73`).
- Groq: `urllib` request timeout.
- Gmail: **no application timeout configured.**
- Attachments: `ExtractionLimits.timeout_seconds = 60` is defined at `contracts.py:34` and **never read** by `SafeTextAttachmentExtractor`. It is a dead knob.

### 1.9 Persistence paths

Exactly one write reaches durable storage: the encrypted refresh token and mailbox connection row, on OAuth callback. Everything a run produces is in-process. **Raw email bodies and attachment text are never written anywhere** — no store, no log statement, no API field carries them. This is the strongest thing the current system has going for it against the target.

### 1.10 Observability paths

One log statement: `logger.exception("Digest run %s failed", run.id)` (`services.py:238`). One event write that nothing can read (`services.py:243`). Safe error codes on the run, returned by polling. `processedEmails` metadata exposed in responses only when `APP_ENV` is development, dev or local (`server.py:311-312`). No metrics, no traces, no correlation IDs beyond `run_id` in that single log line, no audit trail, no external backend.

### 1.11 Gmail data flow

`POST /runs` → SQLite ownership check → `CreateDigestRun` → `BackgroundTasks` → `DigestWorker._fetch_threads` → paged `users.messages.list` with `is:unread in:inbox` forced by `normalize_query` → per-thread `users.threads.get(format="full")` → retain only messages the unread search selected → parse into `EmailEnvelope` → per-attachment download and bounded extraction → `DigestWorker` assembles `ThreadContext` → provider adapter → shaped `ExtractionBatch` → filter, fingerprint, prioritize, sort → `InMemoryResultRepository`.

### 1.12 RAG data flow

**None.** The nearest flow is attachment extraction, which is not RAG: text is read, passed into one prompt, and discarded. It is never chunked, embedded, indexed, or searched.

### 1.13 Where generation occurs

Answered above. Prompt build and shaping in `gemini.py`; candidate authoring in the external provider; assignment in `services.py:218`.

### 1.14 Where routing occurs

**There is no route resolver.** The only routing-shaped logic is a filter in the middle of the worker loop (`services.py:167-172`): skip unless `classification == "actionable"`, then skip any candidate with no evidence or `confidence == "low"`. That is a two-way keep/drop gate, evaluated after generation has already happened, not a decision that selects a path before it.

### 1.15 Where memory currently exists, even if not called memory

This is the most useful part of the extraction, because three of the four target memory types already have a seed.

| Target memory type | What already plays that role | Gap |
|---|---|---|
| **Short-term** | `EmailEnvelope` + `ThreadContext` + local vars in `DigestWorker.execute`. Ephemeral by construction, discarded at run end, never persisted. **Already satisfies the target's privacy boundary.** | Not a named or inspectable object; no explicit clear step; no TTL because scope ends the lifetime |
| **Long-term declarative** | `DigestWorker.execute(user_timezone: str = "UTC")` at `services.py:102` — a preference, hardcoded as a default parameter and never supplied by any caller | No store, no other preference exists |
| **Episodic** | `ActionItem` is already an episode: title, summary, plan, evidence, sender, deep link, fingerprint, `freshness` new/seen/changed, confidence, priority. `fingerprint_seen` (`memory.py:72-77`) scans prior items — this is episodic recall | Process-local, so recall dies at restart. No `status`, no `retrieval_eligible` |
| **Semantic** | Nothing | Entirely absent |

### 1.16 Cannot be determined from the material

- Deployment replica count, log destination, log retention. **[U]**
- Whether any external authentication layer fronts the API. `user_id` is an unverified query parameter (`server.py:107`). **[U]**
- Whether `migrations/001_mail_todo.sql` targets this runtime or the larger system in `ARCHITECHTURE.md`. **[U]**
- Whether Outlook support, `knowledge/`, and combined runs exist in another repository. **[U]**

---

---

# Step 2 — Current vs Authoritative Target

`K` = keep, `M` = modify, `R` = remove, `Miss` = missing today.

| Concern | Current implementation | Authoritative target | K | M | R | Miss | Alignment action |
|---|---|---|:-:|:-:|:-:|:-:|---|
| Gmail ingestion | Read-only Gmail adapter with OAuth, paging, and normalization | External Email Module with read-only Gmail access | yes | yes | | | Keep adapter; add target retries, timeout, and partial-batch contract |
| Email envelope | `EmailEnvelope` and `ThreadContext` | `EphemeralEmailEnvelope` with Gmail pointer and normalized body | yes | yes | | | Consolidate into the target contract; delete state after run |
| Attachments | Existing bounded text extraction | Attachments present may be reported, but processing is out of scope | | | yes | | Disable attachment extraction in this baseline; set `attachments_processed=false` |
| Scheduler | No runtime scheduler | Daily Scheduler Service plus manual `@Email` invocation | | | | yes | Implement scheduler as a target entry path |
| Queue and DLQ | Fake write-only in-memory queue; actual FastAPI background task | Real Job Queue and Dead-Letter Queue consumed by a run worker | | yes | yes | yes | Remove fake queue; add real worker-backed queue and DLQ |
| Run coordinator | `DigestWorker` owns the workflow | Agent Worker / Run Coordinator owns lifecycle | yes | yes | | | Evolve `DigestWorker`; no additional deployable Agent service is required |
| Classifier call | Classification and plan extraction occur in the same LLM response | One structured Actionability + Knowledge-Sufficiency classifier call | | yes | | | Split classifier from final generation |
| Classifier contract | `actionable/informational/newsletter/automated_no_action`; enum confidence | Target actionability labels, route, candidate action, gaps, query, expected docs, reason codes, numeric confidence | | yes | | | Introduce target DTO and map old values during migration |
| Route resolver | Keep/drop filter after generation | Deterministic resolver before retrieval and generation | | yes | | | Implement pure `resolve_route()` over rules + classifier + confidence |
| Direct route | Existing plan is produced in extraction call | `DIRECT_PLAN` calls the final Action Plan Generator once | | yes | | | Do not reuse classifier output as final plan |
| RAG route | Absent | `RETRIEVE_RAG` calls `SemanticMemoryPort`, then generator once | | | | yes | Add retrieval-only semantic provider |
| RAG ownership | No RAG; provider adapter shapes plans | RAG returns chunks/citations only; Agent Core generates final plan | | yes | | | Move plan ownership to Agent Core |
| Classifier failure | Provider failure ends or degrades run inconsistently | Retry classifier once; then route conservatively to RAG | | yes | | | Implement exact fallback sequence |
| RAG failure | No path | Retry once; structured empty result; partial plan; expose missing context | | | | yes | Never restore an unsupported draft or invent company procedure |
| Final generator | Existing extraction call creates plans | One Action Plan generation call after route and optional retrieval | | yes | | | Add separate generator contract and prompt |
| Output validation | Provider parser and deterministic shaping | Schema validator + grounding validator + citation validator | | yes | | yes | Centralize validators after generation |
| Short-term memory | In-process local variables | Redis or in-process run state with cleanup and safety TTL | yes | yes | | | Name the boundary and add explicit cleanup/finalizer |
| Long-term declarative | Hardcoded timezone default | PostgreSQL profile store, loaded compactly every run | | yes | | yes | Implement profile repository behind Memory Gateway |
| Episodic memory | In-memory `ActionItem` history | PostgreSQL episodes; write every generated task as `system_generated` | yes | yes | | | Persist derived task output, never raw body |
| Episodic eligibility | No durable status policy | Approved/completed only are retrieval-eligible | | yes | | yes | Default `retrieval_eligible=false` |
| Semantic memory | Absent | Existing/pluggable RAG module through `SemanticMemoryPort` | | | | yes | No direct Agent write into semantic memory |
| Memory facade | No logical facade | Memory Gateway/Facade with namespace and policy enforcement | | | | yes | Implement in-process facade first; it need not be a separate service |
| Durable task output | In-memory | Task DB with title, minimal paraphrase, plan, citations, Gmail pointer | | yes | | | Use idempotent PostgreSQL persistence |
| Raw email durability | Raw body is not persisted | Raw body remains short-term only; dev traces are the explicit exception | yes | | | | Preserve this boundary |
| Development trace | Minimal development-only processed metadata | Full-content trace allowed only in development, encrypted and TTL-limited | yes | yes | | | Add mandated marker and hard environment guard |
| Production trace | One exception log and polling status | Metadata-only trace, event stream, metrics, and alerts | | yes | | yes | Structured logs may bootstrap, but are not the final target |
| Human approval | Absent | Future gate; current output remains `system_generated` and ineligible | yes | yes | | | Do not block baseline on approval UI |
| Reflexion/multi-agent | Absent | Explicitly out of scope | yes | | | | Keep absent |
| Routing evaluation | Absent | Labeled routing evaluation dataset | | | | yes | Add before production route tuning |

---

# Step 3 — Simplify Without Violating the Target

## 3.1 Remove fake asynchronous components, then implement the real control plane

Delete the current write-only `InMemoryQueue` and its unused wiring. **[S]** This cleanup remains correct. It must not be interpreted as removing queueing from the target.

Target migration:

```text
Current fake InMemoryQueue
→ delete
→ introduce real Job Queue
→ run coordinator consumes jobs
→ exhausted infrastructure retries enter DLQ
```

A Scheduler Service and `@Email` command both create runs through the same Feature API and queue. **[T]**

## 3.2 Delete the unwired `MailTodoApi`

Delete the unused class and keep the live FastAPI routes and `_jsonable`. **[D]** This is an implementation cleanup and does not affect the target architecture.

## 3.3 Replace the unreadable outbox with observable lifecycle events

The current completion event has no consumer. **[S]** Replace it initially with structured lifecycle events, then wire those events to the target development trace, production trace, and metrics sinks. A logging publisher is a valid first adapter, not the final observability plane. **[D]**

## 3.4 Move deterministic plan policy out of provider adapters

Move sanitization, step caps, de-duplication, incident correlation, and deterministic merge logic into Agent Core/application policy. **[D]** Provider adapters should own only transport and structured parsing.

The final target boundary is:

```text
ClassifierAdapter → EmailRouteDecision
ActionPlanGeneratorAdapter → ActionPlanOutput
Application policy → route resolution and deterministic output validation
```

The old combined `ActionExtractor` may remain behind a compatibility adapter during migration, but it must not remain the final target interface.

## 3.5 Evolve `DigestWorker` into the Agent Worker / Run Coordinator

No new deployable “Agent Core service” is required. **[D]** `DigestWorker` can evolve into the target worker if it explicitly owns:

- run lifecycle and idempotency;
- short-term state creation and cleanup;
- compact profile loading;
- classifier invocation;
- deterministic route resolution;
- optional RAG retrieval;
- bounded classifier invocation with one route decision per selected email;
- deterministic thread/incident correlation into task candidates;
- exactly one final Action Plan generation call per resolved task candidate;
- validation and persistence;
- episode write and lifecycle events.

## 3.6 Add a separate classifier call, not a classifier microservice

The target requires a distinct structured classifier call, but not a separate network service. **[T]** Implement a `RouteClassifierPort` in the application layer and let Gemini/Groq adapters satisfy it.

The existing combined extraction prompt can be split into:

```text
Call 1 — Route classifier
  actionability
  candidate_action_item
  email_is_sufficient
  knowledge_gaps
  retrieval_query
  expected_document_types
  reason_codes
  confidence

Call 2 — Final generator
  email context
  profile context
  eligible episodic context, when selected
  optional RAG context
  structured Action Plan output
```

## 3.7 Keep the route resolver as a pure deterministic function

```text
RETRIEVE_RAG =
    actionability is actionable
    AND email_is_sufficient = false
    AND missing knowledge is likely available in company documents
```

Hard policy rules may force retrieval for categories such as tax, governance, policy, procedures, and forms. The resolver combines those rules, the classifier result, and confidence. **[T]**

## 3.8 Generate once after routing

```text
NO_ACTION
  → build informational/no-action output

DIRECT_PLAN
  → final generator once with email + profile + eligible episode context

RETRIEVE_RAG
  → retrieve once
  → final generator once with retrieved chunks and citations
```

Do not ask the classifier to draft a final plan. Do not preserve a pre-retrieval draft as the RAG fallback. **[T]**

## 3.9 Implement four memory types behind one logical gateway

| Memory | Target behavior | Initial target storage |
|---|---|---|
| Short-term | Current run state, raw email, classifier result, retrieved context, generated candidate; clear at completion | Redis or in-process state |
| Long-term declarative | Compact user/profile configuration; explicit/manual writes only | PostgreSQL |
| Episodic | Derived task episodes; write every result as `system_generated`; retrieve approved/completed only | PostgreSQL |
| Semantic | Company policies, procedures, governance, templates; read only when routed | Existing RAG module |

The Memory Gateway is a logical application facade. It may be implemented in-process, but namespace enforcement, read/write eligibility, provenance, TTL, and deletion policy must be explicit. **[T]**

## 3.10 PostgreSQL is the target; SQLite is a temporary local adapter only

The authoritative target selects PostgreSQL for long-term declarative and episodic memory. **[T]** SQLite may be retained for local development or a short-lived migration adapter, but the comparison must not redefine SQLite as the production target.

## 3.11 Remove attachment processing from the baseline

The current code can extract some attachment text. **[S]** The target explicitly places email attachment processing out of scope. **[T]** For this baseline:

```yaml
attachments_present: boolean
attachments_processed: false
```

Attachment extraction, OCR, sandboxing, and attachment-derived evidence belong to a later architecture revision.

---

# Step 4 — Target-Aligned Recommended Changes

## 4.1 Keep

| Current component | Target-aligned use |
|---|---|
| Gmail OAuth, PKCE, read-only scope, encrypted refresh token | Keep within the Email Module |
| Gmail adapter and body normalization | Keep behind the Email Module API/port |
| `DigestWorker` orchestration seed | Evolve into Agent Worker / Run Coordinator |
| Idempotency key and queued-to-running claim semantics | Preserve and make durable |
| Current raw-email ephemerality | Preserve as a non-negotiable privacy boundary |
| `ActionItem`, evidence, Gmail pointer, fingerprint concepts | Reuse as seeds for task output and episodic records |
| Deterministic policies | Move to Agent Core/application policy |
| Existing development environment gate | Reuse for full-content development traces |

## 4.2 Modify

| Current component | Modification |
|---|---|
| Combined ActionExtractor | Split into `RouteClassifierPort` and `ActionPlanGeneratorPort` |
| Current classification enum | Map to target actionability enum during migration |
| Enum confidence | Convert classifier contract to numeric confidence |
| Provider plan shaping | Move to application validators/policies |
| BackgroundTasks dispatch | Replace with queue-backed worker execution |
| In-memory runs/results | Replace with PostgreSQL task and episode repositories |
| Timeout/retry behavior | Apply target budgets per external operation |
| Completion outbox | Emit target lifecycle events and telemetry |
| Current attachment flow | Disable processing for this baseline |

## 4.3 Add

| Component | Purpose |
|---|---|
| Scheduler Service | Daily configured runs |
| Cowork Feature API and Job Queue | Unified manual/scheduled run creation and delivery |
| Dead-Letter Queue | Exhausted job failures without raw email payload by default |
| Short-Term Run State | Explicit runtime state and cleanup |
| Memory Gateway / Facade | Namespace and memory policy enforcement |
| Long-Term Profile Store | Compact preferences/configuration in PostgreSQL |
| Episodic Store | Durable system-generated/approved/completed task episodes in PostgreSQL |
| `RouteClassifierPort` | Structured Actionability + Knowledge-Sufficiency classifier |
| Deterministic Route Resolver | `NO_ACTION`, `DIRECT_PLAN`, `RETRIEVE_RAG` |
| `SemanticMemoryPort` | Retrieval-only company knowledge interface |
| Action Plan Generator | Exactly one final generation call per resolved task candidate |
| Schema/Grounding/Citation Validators | Machine-enforced output guarantees |
| Task Output Repository | Minimal durable task artifact |
| Event Stream, traces, metrics, alerts | Target observability plane |
| Routing evaluation dataset | Measure actionable and RAG routing precision/recall |

## 4.4 Remove or defer

| Item | Decision |
|---|---|
| `InMemoryQueue` production wiring | Remove; retain a deterministic queue fake/local adapter for tests |
| Unwired `MailTodoApi` | Remove |
| `InMemoryOutbox` unreadable sink | Replace |
| Plan policy inside vendor adapters | Remove after relocation |
| Attachment processing | Defer; out of scope |
| Reflexion, ReAct loop, multi-agent orchestration | Do not add |
| Automatic email replies or external task execution | Do not add |
| Automatic preference extraction from emails | Do not add |
| Automatic semantic ingestion from emails | Do not add |
| Retrieval of unvalidated episodes | Do not add |

---

# Step 5 — Target Diagrams

## Diagram 1 — Current to Target Control and Execution Flow

```mermaid
flowchart TB
    subgraph CURRENT["CURRENT"]
        API0["POST /runs"]
        BG0["FastAPI BackgroundTasks"]
        W0["DigestWorker"]
        G0["Gmail adapter"]
        L0["Combined classify + plan extraction"]
        M0[("In-memory results")]
        Q0[("Write-only fake queue")]
    end

    subgraph TARGET["TARGET BASELINE"]
        SCH["Scheduler Service"]
        CMD["@Email"]
        API["Cowork Feature API"]
        Q[("Job Queue")]
        DLQ[("Dead-Letter Queue")]
        W["Agent Worker / Run Coordinator"]
        EMAIL["Email Module"]
        STM[("Short-Term Run State")]
        PROFILE[("Long-Term Profile — PostgreSQL")]
        CLASS["Actionability + Knowledge-Sufficiency Classifier"]
        ROUTE{"Deterministic Route"}
        RAG["SemanticMemoryPort / RAG retrieval"]
        GEN["Action Plan Generator — one call"]
        VAL["Schema + Grounding + Citation Validators"]
        TASK[("Task Output DB")]
        EPI[("Episodic Store — PostgreSQL")]
        CLEAR["Delete ephemeral state"]
    end

    API0 --> BG0 --> W0 --> G0 --> L0 --> M0
    API0 -.-> Q0

    SCH --> API
    CMD --> API
    API --> Q --> W
    Q -. exhausted retries .-> DLQ
    W --> EMAIL --> STM
    PROFILE --> CLASS
    STM --> CLASS --> ROUTE
    ROUTE -->|NO_ACTION| VAL
    ROUTE -->|DIRECT_PLAN| GEN
    ROUTE -->|RETRIEVE_RAG| RAG --> GEN
    GEN --> VAL --> TASK
    VAL --> EPI
    EPI --> CLEAR
    TASK --> CLEAR
```

## Diagram 2 — Agent Core State Machine

```mermaid
flowchart TB
    START["Start run"] --> STATE["Create short-term state"]
    STATE --> PROFILE["Load compact profile"]
    PROFILE --> RULES["Apply deterministic policy guards"]
    RULES --> CLASS["Classifier call — structured"]
    CLASS --> VALIDCLASS{"Classifier valid?"}
    VALIDCLASS -->|no| RETRY["Retry classifier once"]
    RETRY --> VALID2{"Valid after retry?"}
    VALID2 -->|no| CONSERVATIVE["Conservative route to RAG"]
    VALIDCLASS -->|yes| RESOLVE["Resolve route"]
    VALID2 -->|yes| RESOLVE
    CONSERVATIVE --> RETRIEVE
    RESOLVE --> ROUTE{"Route"}
    ROUTE -->|NO_ACTION| BUILD0["Build no-action output"]
    ROUTE -->|DIRECT_PLAN| GEN["Generate Action Plan once"]
    ROUTE -->|RETRIEVE_RAG| RETRIEVE["Retrieve company context"]
    RETRIEVE --> ROK{"Useful context?"}
    ROK -->|yes| GEN
    ROK -->|no / timeout| PARTIAL["Set missing_context and partial mode"]
    PARTIAL --> GEN
    GEN --> SCHEMA["Schema validator"]
    SCHEMA --> GROUND["Grounding and citation validator"]
    GROUND --> BUILD["Build minimal durable task"]
    BUILD0 --> PERSIST["Persist output"]
    BUILD --> PERSIST
    PERSIST --> EPISODE["Write episode: system_generated, ineligible"]
    EPISODE --> CLEAR["Clear raw email and temporary context"]
```

## Diagram 3 — Four-Type Memory System

```mermaid
flowchart TB
    AGENT["Agent Core"] --> GATE["Memory Gateway / Facade"]
    GATE --> NS["Namespace Resolver"]
    NS --> RP["Read Policy"]
    NS --> WP["Write Policy"]

    RP -->|always current run| ST[("Short-Term — Redis/in-process")]
    RP -->|compact profile every run| LT[("Long-Term — PostgreSQL")]
    RP -->|approved/completed only| EP[("Episodic — PostgreSQL")]
    RP -->|route=RETRIEVE_RAG only| SEM["SemanticMemoryPort"]
    SEM --> RAG[("External RAG Module")]

    WP -->|run state only| ST
    WP -->|explicit/manual only| LT
    WP -->|system_generated write| EP
    WP -. no direct Agent write .-> SEM

    POLICY["Provenance · TTL · deletion · eligibility"] --> RP
    POLICY --> WP
    CLEAR["Run finalizer / purge"] --> ST
```

## Diagram 4 — Email Content Boundaries

```mermaid
flowchart LR
    GMAIL["Gmail API"] --> EMAIL["Email Module"]
    EMAIL --> STM["Short-term run state
raw email allowed temporarily"]
    STM --> CLASS["Classifier"]
    STM --> GEN["Generator"]
    STM --> DEV["Development trace
full content allowed + TTL"]
    STM -. forbidden .-> LONG["Long-term profile"]
    STM -. forbidden .-> EPI["Episodic store"]
    STM -. forbidden .-> INDEX["RAG index"]
    STM -. forbidden .-> PROD["Production trace"]
    GEN --> TASK["Task DB
minimal paraphrase + plan + citations + Gmail pointer"]
    TASK --> EPI
```

## Diagram 5 — Migration Order

```mermaid
flowchart TB
    P1["1. Shared contracts"] --> P2["2. EphemeralEmailEnvelope"]
    P2 --> P3["3. Run coordinator + real queue worker"]
    P3 --> P4["4. Short-term state + cleanup"]
    P4 --> P5["5. Compact long-term profile"]
    P5 --> P6["6. Structured route classifier"]
    P6 --> P7["7. Deterministic route resolver"]
    P7 --> P8["8. SemanticMemoryPort around RAG"]
    P8 --> P9["9. Action Plan Generator + validators"]
    P9 --> P10["10. Task persistence + idempotency"]
    P10 --> P11["11. Episodic writes: system_generated"]
    P11 --> P12["12. Enforce retrieval_eligible=false"]
    P12 --> P13["13. Events, dev trace, prod telemetry"]
    P13 --> P14["14. Labeled routing evaluation dataset"]
    P14 --> P15["15. Future approval transitions"]
```

---

# Step 6 — Target-Aligned Contracts

## 6.1 `EphemeralEmailEnvelope`

```yaml
run_id: string
tenant_id: string
user_id: string

gmail_message_id: string
gmail_thread_id: string
gmail_url: string

sender:
  name: string
  email: string
recipients:
  - string
subject: string
received_at: datetime
labels:
  - string

normalized_body: string
body_format: text | html_converted

attachments_present: boolean
attachments_processed: false

fetch_status: complete | partial
```

Lifecycle:

```yaml
scope: current_run
expires_at: datetime
persisted_to_product_db: false
cleanup: finalizer_plus_safety_ttl
```

## 6.2 `EmailRouteDecision`

```yaml
actionability:
  enum:
    - action_required
    - action_suggested
    - informational
    - unclear
    - irrelevant

route:
  enum:
    - no_action
    - direct_plan
    - retrieve_rag

candidate_action_item: string | null
email_is_sufficient: boolean

knowledge_gaps:
  - string

retrieval_query: string | null

expected_document_types:
  - company_policy
  - governance_document
  - procedure
  - guideline
  - template
  - product_documentation

reason_codes:
  - no_action
  - email_self_contained
  - company_procedure_required
  - governance_required
  - policy_required
  - template_required
  - internal_term_unresolved
  - domain_knowledge_required

confidence: number
```

The LLM proposes this structured decision. The deterministic resolver verifies consistency and applies hard rules before selecting the execution route.

## 6.3 `MemoryContextRequest`

```yaml
run_id: string
namespace:
  tenant_id: string
  user_id: string
  feature: email_action_plan

reads:
  short_term: true
  long_term: true
  episodic:
    enabled: boolean
    retrieval_eligible_only: true
    max_items: integer
  semantic:
    enabled: boolean
    condition: route == retrieve_rag

response:
  profile: object | null
  episodes: []
  semantic_context: object | null
  degraded: boolean
  degraded_sources:
    - long_term
    - episodic
    - semantic
```

## 6.4 `SemanticRetrievalRequest`

```yaml
run_id: string
tenant_id: string
user_id: string

query: string
knowledge_gaps:
  - string
expected_document_types:
  - string

filters:
  tenant_scope: string
  document_status:
    - ready

limits:
  top_k: integer
  min_score: number
  timeout_ms: integer

constraint: >
  Return chunks, citation metadata, and scores only.
  Do not generate an Action Plan.
```

## 6.5 `SemanticRetrievalResponse`

```yaml
query_id: string
tenant_id: string

chunks:
  - chunk_id: string
    document_id: string
    document_title: string
    section: string | null
    text: string
    source_url: string
    document_version: string | null
    relevance_score: number
    rerank_score: number | null

retrieval_status:
  enum:
    - success
    - no_results
    - timeout
    - authorization_denied
    - partial

latency_ms: integer
```

## 6.6 `ActionPlanOutput`

```yaml
task:
  task_id: string
  run_id: string

  gmail_message_id: string
  gmail_url: string
  source_message_ids:
    - string
  incident_key: string | null

  title: string
  request_summary: string

  actionability: action_required | action_suggested | informational
  route: no_action | direct_plan | retrieve_rag

  priority: low | medium | high | urgent | null
  deadline: datetime | null

  action_plan:
    - step: integer
      instruction: string
      supporting_citation_ids:
        - string

  supporting_documents:
    - citation_id: string
      document_id: string
      title: string
      section: string | null
      url: string
      relevance_score: number

  missing_information:
    - string

  classifier_confidence: number
  generation_confidence: number | null

  validation_status: system_generated
  created_at: datetime
```

Validation rules:

- The schema must validate before persistence.
- Knowledge-specific instructions must be supported by a retrieved citation.
- RAG timeout or no-result mode must expose missing information.
- The generator must not invent company procedures.
- The output must not contain the full email body.

## 6.7 `TaskEpisode`

```yaml
episode_id: string
record_id: string
tenant_id: string
user_id: string
run_id: string

gmail_message_id: string
gmail_url: string

task_title: string
minimal_request_paraphrase: string
action_plan:
  - string

rag_citations:
  - document_id: string
    document_title: string
    section: string | null
    source_url: string

missing_information:
  - string

validation_status:
  enum:
    - system_generated
    - user_approved
    - completed
    - rejected

retrieval_eligible: boolean
retrieval_rule: >
  false when system_generated;
  true only after user_approved or completed.

source_type: system_generated_task | user_approved_task
created_at: datetime
updated_at: datetime
pipeline_version: string
model_id: string | null
prompt_version: string | null

privacy: >
  Store derived task output and Gmail pointer only.
  Do not store the raw email body.
```

## 6.8 `TraceEvent`

```yaml
run_id: string
tenant_id: string
user_id: string
gmail_message_id: string | null

event_name: string
status: success | partial | failed
route: no_action | direct_plan | retrieve_rag | null
reason_codes:
  - string

classifier_confidence: number | null
rag_result_count: integer | null
retrieval_status: string | null
generation_status: string | null
validation_status: string | null

latency_ms:
  email: integer | null
  memory: integer | null
  classifier: integer | null
  rag: integer | null
  generation: integer | null
  persistence: integer | null

content_policy:
  production: metadata_only
  development: full_content_allowed
  development_marker: ALLOW ONLY FOR CURRENT DEVELOPMENT STAGE
  development_ttl_required: true
```

---

# Step 7 — Final Change Plan

## Authority and migration decisions

The target baseline intentionally changes current attachment behavior. ADR-003 supersedes
ADR-002 and only the attachment-processing clauses of ADR-001. The remaining ADR-001 decisions
about an asynchronous pipeline, durable queue, PostgreSQL source of truth, idempotency, worker
claim semantics, and ports/adapters remain authoritative.

The current codebase provides useful seams but not the production control plane:

- `QueuePort`, `RunRepository`, `ResultRepository`, and `CompletionOutboxPort` already exist;
- the application wires in-memory run/result/outbox adapters;
- the HTTP route invokes `DigestWorker` through FastAPI `BackgroundTasks`, bypassing queue
  consumption;
- PostgreSQL DDL exists for runs, schedules, action items, attachment extraction history, and
  outbox events, but production run/result repository adapters are not wired;
- no `tenant_id`, Memory Gateway, semantic-memory port, or RAG adapter exists in this repository.

Therefore the change plan must close the durability and identity gaps before introducing a real
cross-process worker.

## Target execution unit and call cardinality

The target preserves current bounded batching and cross-message correlation:

1. A run may use one or more bounded classifier batch calls.
2. Every selected email receives exactly one `EmailRouteDecision`.
3. Application-owned deterministic logic correlates decisions by thread/incident and forms task
   candidates while preserving `source_message_ids` and `incident_key`.
4. Each task candidate resolves once to `NO_ACTION`, `DIRECT_PLAN`, or `RETRIEVE_RAG`.
5. A `RETRIEVE_RAG` task candidate performs zero or one logical retrieval operation, with only
   the documented bounded technical retry.
6. Agent Core invokes the final generator exactly once per resolved task candidate. It does not
   invoke the generator for `NO_ACTION`.

Observability must report classifier batch count, email decision count, correlated task-candidate
count, retrieval count, and generator count separately.

## Compatibility contract

Compatibility is a required migration contract, not “where possible”:

| Current behavior | Target migration requirement |
|---|---|
| `POST /v1/mail-todo/runs` returns `202` and a pollable run | Preserve endpoint, idempotency, status URL, and terminal-state semantics |
| Result contains `actionItems`, `nextActions`, warnings, and an empty-state message | Provide a versioned compatibility mapper from persisted task outputs |
| Priority includes `urgent` and drives ordering | Retain `urgent` in the target task contract and preserve deterministic ordering |
| Multiple messages may form one correlated incident/action | Preserve `source_message_ids`, `incident_key`, evidence provenance, and dedupe behavior |
| Development responses may expose processed-email metadata | Keep the development-only guard; production remains metadata-minimal |
| Attachment extraction can produce partial runs and warnings | Treat this as an intentional product-scope change under ADR-003; report presence only and do not claim behavior preservation |
| Duplicate create requests enqueue one logical run | Preserve atomic idempotent creation and at-most-one logical enqueue |

The old combined `ActionExtractorPort` may remain behind a temporary compatibility adapter while
callers migrate. Production removal happens only after the compatibility tests pass.

## Milestone 0 — Authority, baseline, and blocking decisions

Complete before implementation refactors:

1. Record ADR-003 and mark ADR-002 superseded.
2. Freeze current API/result fixtures, priority ordering, batching, correlation, dedupe, and
   privacy behavior as compatibility tests.
3. Build the first labeled routing fixture set and capture the existing combined-extractor
   quality, latency, call-count, and cost baseline.
4. Define the task-candidate correlation contract and generator cardinality above.
5. Resolve the verified principal source and mandatory `tenant_id` + `user_id` namespace.
6. Select PostgreSQL deployment/migration ownership and the durable queue/DLQ technology.
7. Decide whether the RAG provider is external or must be built, and identify corpus/ACL
   ownership.

**Exit criteria:**

- no dependent implementation relies on an unverified query-parameter identity;
- queue, database, and RAG ownership decisions have named owners;
- compatibility and routing fixtures can detect regressions before provider prompts change;
- attachment scope is unambiguous across PRD, Target Architecture, ADRs, and this comparison.

## Milestone 1 — Durable control plane without live RAG

1. Define versioned `EphemeralEmailEnvelope`, `EmailRouteDecision`, `ActionPlanOutput`,
   `TaskEpisode`, and trace contracts, including `source_message_ids`, `incident_key`, and
   `urgent`.
2. Introduce a verified principal boundary that supplies `tenant_id` and `user_id`; remove
   authorization decisions based only on caller-provided query parameters.
3. Implement PostgreSQL run/result/outbox repositories using the existing schema as a migration
   input, with atomic idempotent create and compare-and-set claim semantics.
4. Wire a durable queue producer and worker consumer with bounded retry and DLQ behavior.
5. Route both manual and scheduled triggers through the same run-creation service.
6. Replace the FastAPI `BackgroundTasks` worker bypass. Keep a functional local adapter and
   deterministic queue fake for development/tests.
7. Implement explicit short-term run-state cleanup plus a safety TTL.
8. Disable production attachment download/extraction under ADR-003 and record presence only.
9. Remove the unwired `MailTodoApi` after endpoint compatibility tests cover the live FastAPI
   routes.
10. Emit metadata-only run lifecycle events through the durable outbox.

**Exit criteria:**

- an API-created run is visible to a separate worker process;
- duplicate requests create and enqueue one logical run;
- only one worker can claim a run;
- retry exhaustion reaches the DLQ without email body, attachment bytes, or OAuth tokens;
- process restart does not lose run status or completed output;
- attachment presence never invokes download/extraction;
- the existing run/status/result API contract passes its compatibility suite.

## Milestone 2 — Classifier, resolver, and direct plans

1. Split provider integration into `RouteClassifierPort` and `ActionPlanGeneratorPort`.
2. Move parsing-only code behind provider adapters, but move deterministic plan shaping,
   correlation, dedupe, priority, and ordering into application-owned services.
3. Implement bounded classifier batching with one validated decision per selected email.
4. Implement deterministic task-candidate correlation and the route resolver with hard policy
   guards.
5. Implement `NO_ACTION` and `DIRECT_PLAN`.
6. Put `RETRIEVE_RAG` behind a null `SemanticMemoryPort` that returns a structured
   `no_results` response.
7. Implement one final generator invocation per resolved non-`NO_ACTION` task candidate.
8. Add schema, grounding, privacy, and unsupported-procedure validators.
9. Run the frozen compatibility and labeled-routing evaluations after each provider migration.

**Exit criteria:**

- classifier batch count and per-email decisions are observable and schema-valid;
- correlation preserves current related-message/incident behavior;
- `DIRECT_PLAN` performs no semantic retrieval;
- generator count equals the number of resolved non-`NO_ACTION` task candidates;
- a null/no-result semantic response yields an explicit partial plan with missing information;
- raw email is absent from task and episode persistence.

## Milestone 3 — Memory, live RAG, and durable task episodes

1. Implement the logical Memory Gateway with mandatory tenant/user/feature namespace enforcement.
2. Load the compact long-term profile from PostgreSQL with a documented degraded fallback.
3. Implement idempotent task persistence and write episodes as `system_generated`.
4. Enforce `retrieval_eligible=false` at write and read boundaries for unvalidated episodes.
5. Implement the external retrieval-only `SemanticMemoryPort` adapter with authorization/ACL
   filtering before ranking.
6. Add retrieval thresholds, citation packaging, grounding validation, timeout/no-result
   behavior, and partial-plan fallback.
7. Add event stream integration, development trace TTL/purge, production metrics, alerts, and
   retention audits.
8. Add human approval/completion/rejection transitions only after baseline routing and
   persistence are stable.

**Exit criteria:**

- cross-tenant and cross-user access tests fail closed;
- RAG returns context/citations only and never generates the final plan;
- unsupported company-specific steps cannot survive validation;
- system-generated episodes cannot be retrieved;
- task/episode writes are idempotent and contain no raw email body;
- routing, retrieval, citation, latency, and cost gates meet the launch thresholds.

## Recommended implementation order

| Order | Work | Dependency or proof |
|---:|---|---|
| 0 | Resolve authority and record ADR-003 | Attachment scope agreed |
| 1 | Freeze API/behavior compatibility fixtures | Current behavior captured |
| 2 | Build initial labeled routing and cost baseline | Split-call regression gate |
| 3 | Resolve verified principal, PostgreSQL owner, queue technology, and RAG/ACL owner | Named blocking decisions |
| 4 | Define shared versioned contracts and task-candidate cardinality | Contract review |
| 5 | Implement verified tenant/user boundary | Authorization tests |
| 6 | Implement durable PostgreSQL run/result/outbox adapters | Restart and atomic-claim tests |
| 7 | Implement queue producer/consumer, retry, DLQ, and scheduler | Separate-process integration test |
| 8 | Implement ephemeral envelope, short-term state, cleanup, and attachment non-processing | Privacy and no-download tests |
| 9 | Split classifier/generator ports and move deterministic shaping to application services | Provider contract tests |
| 10 | Implement bounded classification, correlation, and deterministic route resolution | Labeled routing evaluation |
| 11 | Implement direct/null-RAG generation and validators | Call-count and fallback tests |
| 12 | Implement idempotent task and system-generated episode persistence | Persistence/privacy tests |
| 13 | Implement Memory Gateway and validated-only episodic policy | Namespace and eligibility tests |
| 14 | Integrate retrieval-only RAG with ACL-first filtering | Retrieval/citation evaluation |
| 15 | Add telemetry, retention, purge, and launch gates | Operational verification |
| 16 | Add future human-approval transitions | Baseline stability evidence |

## Blocking decisions

These decisions do not reopen the target behavior, but they block the dependent implementation:

| Decision | Must be resolved before |
|---|---|
| Verified principal source and tenant/user binding | Any durable task, memory, or RAG access |
| PostgreSQL deployment and migration owner | Durable run/task/profile/episode adapters |
| Queue/DLQ technology and retry ownership | Real worker and scheduler integration |
| Corpus owner and ACL model | Live semantic retrieval |
| External RAG availability versus in-repo build | `SemanticMemoryPort` production adapter |
| Routing/retrieval/citation/latency/cost thresholds | Classifier/RAG launch approval |

## Resolved decisions that should not be reopened

- Agent Core owns final Action Plan generation.
- RAG is retrieval-only for the Cowork workflow.
- Classifier and generator are separate ports and calls.
- Classifier calls may be bounded batches; every selected email receives one route decision.
- Deterministic application logic forms task candidates and preserves cross-message correlation.
- The final generator is called exactly once per resolved non-`NO_ACTION` task candidate.
- `urgent` remains a supported priority during and after compatibility migration.
- Durable run state precedes and is shared by the real queue worker.
- Long-term and episodic production storage is PostgreSQL.
- Scheduler, queue worker, and DLQ are part of the baseline target.
- Production attachment processing is out of scope under ADR-003; presence is recorded only.
- Queue fakes/local adapters remain valid for tests and local development, not production wiring.
- Raw email is not long-term, episodic, or semantic memory.
- System-generated episodes are not retrieval-eligible.
- Reflexion and multi-agent orchestration are out of scope.
