# Pluggable Batch Evaluation API — Specification (v1)

**Status:** Proposed (2026-08-22).
**Area:** evaluation control plane, provider integrations, and evaluation harnesses.
**Companions:**

- [SPEC-memory-evaluation.md](./SPEC-memory-evaluation.md) — parent memory-evaluation contract.
- [SPEC-memory-eval-harness-v3-scalability.md](./SPEC-memory-eval-harness-v3-scalability.md) — current memory harness correctness and scalability work.
- [Memory evaluation runbook](../../evaluations/MEMORIES/RUNBOOK.md) — current operational safety rules.
- [SPEC-chat-ragas-evaluation.md](./SPEC-chat-ragas-evaluation.md) — Chat-RAGAS plug-in contract.
- [RAGAS operations guide](../../docs/evaluations/RAGAS.md) — current evaluator state and adoption rules.

---

## Problem Statement

Evaluation tooling currently consists primarily of separate CLI workflows.
Memory evaluation selects one provider adapter and one API key, then runs every
probe and its three experimental arms sequentially.

<<<<<<< HEAD
The AI evaluation developer has three Mistral API keys with independent usage
limits. Using one key does not consume or reduce the other keys' limits. The
evaluation system should therefore be able to run three safe concurrent lanes,
one per key, while preserving experiment correctness and recording which
non-secret credential alias ran each lane.
=======
The operator has three Mistral API keys with independent usage limits. Using
one key does not consume or reduce the other keys' limits. The evaluation
system should therefore be able to run three safe concurrent lanes, one per
key, while preserving experiment correctness and recording which non-secret
credential alias ran each lane.
>>>>>>> fix/memory-eval-refusal-noun-head

The same batch-processing control plane should later support memory,
retrieval, routing, email-intent, and similar evaluations without building a
new scheduler or endpoint family for every evaluator.

A generic "send every prompt to any available key" implementation is unsafe
for memory evaluation because:

- Each memory probe is a stateful experiment.
- Its `full`, `ablated`, and `control` arms must remain comparable.
- Seeding, querying, scoring, artifact writing, and teardown form one workflow.
- Concurrent workers must never share a scratch database, tenant, session,
  output path, or mutable live session.
- Ordinary Mistral chat-completion requests do not execute the local memory
  setup and teardown workflow; our module must own that orchestration.

The repository already has multi-key parsing and round-robin key rotation, but
the Mistral settings and chat reply adapter currently consume one key.

## Solution

<<<<<<< HEAD
Build an asynchronous evaluation API for AI evaluation developers, backed by:
=======
Build an internal, administrator-only asynchronous evaluation API backed by:
>>>>>>> fix/memory-eval-refusal-noun-head

1. An evaluation plug-in registry.
2. A durable evaluation job store.
3. A credential leasing pool.
4. A shard scheduler.
5. A custom batch execution module with two worker strategies:
   - `request_batch` for independent ordinary provider requests.
   - `workflow_shards` for stateful local evaluation workflows.
6. An artifact store and deterministic result aggregator.

The recommended memory-evaluation integration partitions complete probes into
up to `effective_workers` isolated SQLite workflow shards. Each shard leases
one Mistral key and processes its assigned probes sequentially. Every probe's
three arms remain in the same shard.

### Level 1 — Basic batch-processing system

This is the reusable foundation. The application owns its queue, scheduling,
progress, retries, and aggregation. It uses ordinary Mistral chat-completion
calls and does not call Mistral's built-in Batch API.

```mermaid
flowchart LR
    U["Evaluation client"] -->|"POST /v1/evaluation-jobs"| API["Evaluation Job API"]
    API --> JS["Durable job store"]
    API --> REG["Evaluation plug-in registry"]

    REG --> PLAN["Validate and create work plan"]
    PLAN --> SHARDS["Independent shards or work units"]

    SHARDS --> POOL["Credential lease pool"]
    POOL --> K1["Mistral key 1<br/>independent limit"]
    POOL --> K2["Mistral key 2<br/>independent limit"]
    POOL --> KN["Mistral key N<br/>discovered dynamically"]

    K1 --> EXEC["Our batch executor"]
    K2 --> EXEC
    KN --> EXEC

    EXEC -->|"request_batch"| CHAT["Ordinary Mistral<br/>chat-completion calls"]
    EXEC -->|"workflow_shards"| WF["Stateful local workflow workers"]

    CHAT --> DONE["Persist work-item result"]
    WF --> DONE

    DONE --> AGG["Plug-in result aggregator"]
    AGG --> ART["Public metadata + private artifacts"]
    ART -->|"GET status/result"| U
```

### Level 2 — Memory evaluation plugged into the system

Memory evaluation uses workflow shards in the first implementation. A shard
owns complete probes, and each complete probe owns all three arms. Arms are not
distributed independently.

```mermaid
flowchart TD
    REQ["Memory evaluation request"] --> PLUGIN["MemoryEval plug-in"]
    PLUGIN --> PRE["Existing mem-eval preflight"]
    PRE -->|"failed"| ABORT["Fail before model spend"]
    PRE -->|"ready"| LIMIT["Resolve effective_workers"]
    LIMIT --> PART["Partition probes into N deterministic shards"]

    PART --> S1["Shard 1<br/>probes 1, 4, 7..."]
    PART --> S2["Shard 2<br/>probes 2, 5, 8..."]
    PART --> SN["Shard N<br/>remaining probes"]

    S1 --> W1["Worker + key 1<br/>isolated SQLite DB<br/>unique nonce"]
    S2 --> W2["Worker + key 2<br/>isolated SQLite DB<br/>unique nonce"]
    SN --> WN["Worker + key N<br/>isolated SQLite DB<br/>unique nonce"]

    subgraph PROBE["Each probe remains one experiment"]
        FULL["FULL<br/>seed + read all memory"]
        ABLATED["ABLATED<br/>seed + mask target scope"]
        CONTROL["CONTROL<br/>no seed + normal reads"]
        FULL --> ABLATED --> CONTROL
    end

    W1 --> FULL
    W2 --> FULL
    WN --> FULL

    CONTROL --> SCORE["Existing deterministic scorer"]
    SCORE --> MERGE["Merge in original probe order"]
    MERGE --> BASE["Metadata-only baseline"]
    MERGE --> DETAIL["Private detail artifact"]
    MERGE --> REPORT["Optional generated report"]
```

The current injected `AskProbe` remains the highest useful
memory-evaluation test seam. The generic evaluation job service becomes the
highest API-level test seam.

### Level 3 — Chat-RAGAS plugged into the same system

Chat-RAGAS is a stateless `request_batch` plug-in. Tier 1 stays local and
deterministic. Tier 2 creates one work unit per case, uses the custom scheduler
to spread cases across leased keys, and keeps RAGAS concurrency at `1` inside
each worker.

```mermaid
flowchart TD
    REQ["Chat-RAGAS request<br/>--ragas --max-workers N"] --> PLUGIN["ChatRagas plug-in"]
    PLUGIN --> PRE["Validate local dataset, models, privacy, budgets"]
    PRE --> T1["Tier 1<br/>deterministic metrics"]
    PRE --> PLAN["Tier 2<br/>one case = one work unit"]
    PLAN --> LIMIT["effective_workers = min(requested, active keys, ready cases, plug-in limit)"]
    LIMIT --> POOL["Credential lease pool"]
    POOL --> W1["Worker 1 + key alias 1"]
    POOL --> W2["Worker 2 + key alias 2"]
    POOL --> WN["Worker N + key alias N"]
    W1 --> SEQ["Per case, sequential<br/>faithfulness → answer relevancy"]
    W2 --> SEQ
    WN --> SEQ
    SEQ --> RESULT["Metric result or explicit failure"]
    RESULT --> MERGE["Merge by original case order"]
    T1 --> REPORT["Metadata-only report"]
    MERGE --> REPORT
    MERGE --> PRIVATE["Private local detail artifact"]
```

## User Stories

1. As an evaluator, I want to submit an evaluation asynchronously, so that I
   do not hold an HTTP connection for a long-running workload.
2. As an evaluator, I want a stable job identifier, so that I can inspect
   progress later.
3. As an evaluator, I want idempotent submission, so that retrying a timed-out
   request does not create duplicate paid work.
4. As an evaluator, I want to select a registered evaluation type, so that
   memory, retrieval, and routing evaluations share one API.
5. As an evaluator, I want invalid parameters rejected before provider calls,
   so that configuration mistakes do not spend money.
6. As an evaluator, I want preflight checks before execution, so that unusable
   keys, datasets, models, or databases fail early.
7. As an evaluator, I want every independently limited Mistral key up to
   `max_workers` used concurrently when safe, so adding a fourth or fifth key
   increases capacity without a code change.
8. As an evaluator, I want each key represented by a non-secret alias, so that
   I can diagnose failures without exposing credentials.
9. As an evaluator, I want one failed key to stop receiving new work, so that
   the remaining keys can continue.
10. As an evaluator, I want rate-limited keys placed into cooldown, so that
    temporary throttling is not mistaken for permanent exhaustion.
11. As an evaluator, I want job progress expressed in work units and provider
    requests, so that progress is meaningful for different evaluation types.
12. As an evaluator, I want partial failures reported explicitly, so that
    incomplete output is never presented as a valid complete benchmark.
13. As an evaluator, I want deterministic aggregation, so that concurrency
    does not reorder evaluation cases.
14. As a memory evaluator, I want each probe's three arms kept together, so
    that attribution remains valid.
15. As a memory evaluator, I want each shard to use an isolated scratch
    database and namespace, so that workers cannot contaminate each other.
16. As a memory evaluator, I want full reply text stored only in private
    artifacts, so that committed baselines remain metadata-only.
17. As a memory evaluator, I want PostgreSQL parity runs to remain serial
    initially, so that advisory-lock contention does not invalidate runs.
18. As a developer, I want to add a new evaluator by registering a plug-in
    rather than adding new endpoints.
19. As a developer, I want a fake provider-batch adapter, so that scheduling,
    retries, cancellation, and aggregation are testable offline.
<<<<<<< HEAD
20. As an AI evaluation developer, I want cancellation to stop queued work and
    cancel local workers and cancellable in-flight requests, so that unwanted
    spending stops promptly.
21. As an AI evaluation developer, I want secrets excluded from requests,
    persistence, logs, errors, and artifacts.
22. As an AI evaluation developer, I want request and token budgets, so that
    unexpectedly large jobs are rejected or stopped.
=======
20. As an operator, I want cancellation to stop queued work and cancel local
    workers and cancellable in-flight requests, so that unwanted spending
    stops promptly.
21. As an operator, I want secrets excluded from requests, persistence, logs,
    errors, and artifacts.
22. As an operator, I want request and token budgets, so that unexpectedly
    large jobs are rejected or stopped.
>>>>>>> fix/memory-eval-refusal-noun-head
23. As a benchmark reader, I want the execution manifest to record shard and
    credential aliases, so that I can detect whether infrastructure differences
    affected the result.
24. As a benchmark reader, I want the probe-set hash, evaluator version,
    provider, model, and execution mode recorded, so that two runs can be
    compared honestly.

## Implementation Decisions

### API contract

| Operation | Contract |
|---|---|
| `POST /v1/evaluation-jobs` | Accept a registered evaluation type, provider, model, dataset reference, execution options, and evaluation-specific parameters. Require `Idempotency-Key`. Return `202`, job id, status URL, and result URL. |
| `GET /v1/evaluation-jobs/{job_id}` | Return state, safe progress, timestamps, shard counts, retry counts, and failure classification. Never return prompts, replies, or secrets. |
| `GET /v1/evaluation-jobs/{job_id}/result` | Return the artifact manifest when terminal. Return `409` while work is still running. |
| `POST /v1/evaluation-jobs/{job_id}/cancel` | Request cancellation and return `202`. Cancellation is idempotent. |
| `GET /v1/evaluation-types` | List statically registered evaluation types, versions, supported execution modes, and validated parameter schemas. |

The endpoint accepts a credential-pool alias such as `mistral-eval`, never
actual API keys.

<<<<<<< HEAD
The first release is for AI evaluation developers only. It is not part of the
normal Cowork Agent user experience.
=======
The first release is local or administrator-only. It does not expose an
evaluation endpoint to ordinary product users.
>>>>>>> fix/memory-eval-refusal-noun-head

### Plug-in interface

Each registered evaluation plug-in declares:

- A stable evaluation type and version.
- Its validated parameter schema.
- Whether it supports `request_batch`, `workflow_shards`, or both.
- How to perform preflight checks.
- How to construct a deterministic work plan.
- Which work units may run concurrently.
- How to execute one work unit.
- How to aggregate successful and failed units.
- Which artifacts are public metadata and which are private.
- How to clean up temporary resources.
- How to classify retryable, permanent, provider, product, and evaluation
  failures.

Plug-ins are registered at application startup. The API cannot upload Python,
specify a module path, or submit a shell command.

### Credential configuration and leasing

Use the existing multi-key environment parser with one documented naming
convention:

- `MISTRAL_API_KEY`
- `MISTRAL_API_KEY2`
- `MISTRAL_API_KEY3`
- `MISTRAL_API_KEY4`
- `MISTRAL_API_KEY5`

The shared parser also accepts underscore-number variants, but project
documentation and examples should use the convention above consistently.

Numbered suffixes are not capped at five; four and five are concrete scale-up
examples. These keys have independent usage limits. The scheduler therefore
allows one concurrent Mistral lane per active key, bounded to one leased lane
per key by default.
It must not apply a shared-workspace quota assumption to this configured pool.
Provider-reported global limits, if any are encountered at runtime, still take
precedence and must be surfaced rather than ignored.

Current status: the shared utility can parse these names, but the Mistral
evaluation adapter still uses the single `MISTRAL_API_KEY` field.
Implementation connects the evaluation-only Mistral transport to the pool.

Worker resolution is deterministic:

```text
effective_workers = min(
  requested_max_workers or active_key_count,
  active_key_count,
  ready_work_unit_count,
  plugin_concurrency_limit,
)
```

`max_workers < 1` is rejected before job creation. Requesting more workers than
available keys or ready units is valid and clamps to the effective value; the
manifest records the reason. No code path assumes exactly three keys.

The existing round-robin rotator is evolved into or wrapped by a lease-aware
pool with these states:

- `available`
- `leased`
- `cooling_down`
- `disabled`

One workflow shard or request work unit retains the same credential lease
for its external lifecycle. Only a salted fingerprint or configured alias is
persisted.

Failure rules:

- `401/403` authentication failure disables that credential.
- `429` applies provider-directed backoff and cooldown to that credential.
- Transport and `5xx` errors retry with bounded exponential backoff.
- An ambiguous request timeout is recorded as an attempt with an unknown
  provider outcome. It is retried only under the job's explicit retry budget.
- Local work-item ids deduplicate stored results, even though a timed-out retry
  may still produce a second billed provider call.
- Reassignment to another key is recorded in the execution manifest.
- Job-level consecutive-provider-failure protection remains in place.

### Job states

The canonical successful progression is:

`accepted -> validating -> queued -> running -> collecting -> succeeded`

Terminal alternatives are:

- `partially_succeeded`
- `failed`
- `cancelled`

Cancellation passes through `cancellation_requested`. State transitions are
monotonic and persisted before being returned to clients.

### Execution modes

#### `request_batch`

- For stateless, independently serializable provider requests.
- Implemented entirely by our batch module; it does not upload JSONL files or
  create Mistral batch jobs.
- Partitions inputs into locally tracked work items with stable ids.
- Sends one ordinary Mistral chat-completion request per work item.
- Runs up to `effective_workers` request lanes concurrently, one per
  independently limited key, with per-key concurrency initially fixed at `1`.
- Persists attempt state, safe error classification, latency, token usage when
  returned, and the private result artifact.
- Uses bounded queues and budgets so a large evaluation cannot create
  unbounded tasks or provider spend.

#### `workflow_shards`

- For evaluations requiring local state, database operations, retrieval,
  multi-turn calls, or teardown.
- Executes a plug-in-defined shard locally.
- May use normal provider completions through a leased key.
- Uses the same custom scheduler, leasing, progress, retry, and aggregation
  machinery as `request_batch` while retaining its stateful local steps.

### Memory-evaluation adapter

The first concurrent implementation is SQLite-only:

- Partition probes deterministically into at most `effective_workers` shards.
- Keep all three arms of a probe in the same shard.
- Give every shard a separate scratch SQLite file, live session, adapter set,
  transcript, nonce, and artifact path.
- Let each worker process its assigned probes sequentially.
- Use one Mistral credential lease per worker.
- Start up to `effective_workers` concurrently because the keys have
  independent usage limits.
- Merge rows in the original probe-set order.
- Preserve the existing scorer, verdict derivation, seeding rules, masking
  behavior, and teardown.
- Preserve the existing metadata-only baseline and private detail artifact
  distinction.
- Record an execution manifest containing job id, shard ids, credential
  aliases, local request-attempt ids, retries, and per-shard state.

PostgreSQL memory evaluation remains concurrency `1` in this specification.
Parallel PostgreSQL execution requires a separate design proving migration,
connection-pool, and cleanup behavior.

### Chat-RAGAS adapter

The Chat-RAGAS plug-in uses `request_batch` for Tier 2:

- Tier 1 deterministic metrics run locally and do not acquire provider keys.
- The dev CLI may read `--input` locally. The HTTP API accepts only an opaque,
  pre-registered dataset reference; it never accepts an arbitrary filesystem
  path or uploads private document text in the job request.
- Each Tier 2 case is one independently retryable work unit with a stable id.
- A worker retains one credential lease for both judge-LLM and embedding calls
  needed by that case.
- `faithfulness` and `answer_relevancy` run sequentially inside a case in v1.
- RAGAS internal concurrency is `1`; outer scheduler concurrency is the only
  worker fan-out. This prevents `N` custom workers from each spawning another
  RAGAS worker pool.
- The exact RAGAS scorer API is chosen only after pinning a supported version.
  A single-turn scorer or collection item is preferred over bulk `evaluate()`.
- NaN, missing, or non-finite metric values count as explicit metric failures.
- The aggregator reports attempted, succeeded, failed, and skipped counts per
  metric and never labels a partial run as complete.
- Questions, answers, references, and contexts remain in private local
  artifacts; the public baseline contains only ids, counts, scores, timing, and
  safe execution metadata.

### Persistence and artifacts

Use a dedicated, gitignored SQLite control-plane database for evaluation-job
metadata in the first release. Do not reuse the memory-evaluation scratch
database.

The job-store abstraction permits a later PostgreSQL implementation, but no
product database migration is part of this specification.

Artifact paths remain deterministic under the canonical `evaluations/`
workspace:

- Committable baseline: metadata only.
- Private run detail: full questions and replies.
- Job manifest: metadata only.
- Provider raw output and error files: private.
- Generated report: follows the evaluation plug-in's privacy policy.

### Incremental delivery roadmap

1. **Foundation:** job API, job store, static plug-in registry, fake executor,
   status/result/cancellation, and idempotency.
2. **Custom multi-key executor:** lease-aware credential pool, dynamically
   bounded concurrency lanes, `--max-workers`, bounded work queue, ordinary Mistral
   chat-completion transport, and deterministic result collection.
3. **Memory evaluation:** SQLite workflow sharding across available keys,
   deterministic aggregation, and existing report compatibility.
4. **Chat-RAGAS:** case-sharded Tier 2 judge calls with nested RAGAS concurrency
   disabled, explicit partial-failure counts, and metadata-only aggregation.
5. **Future evaluators:** retrieval, routing, email-intent, and other stateless
   evaluations register adapters without new endpoint families.
6. **Advanced option:** only after measuring a real need, extract memory-eval
   prompt preparation from controller execution so more work can use the
   custom `request_batch` strategy without weakening the three-arm contract.

## Testing Decisions

Tests assert externally visible behavior rather than worker implementation
details.

The primary test seam is the evaluation job service with:

- A fake plug-in registry.
- A fake job store.
- A fake credential pool.
- A fake ordinary-completion transport.
- A fake clock and retry scheduler.
- A fake artifact store.

API integration tests cover:

- `202` submission and status URLs.
- Idempotent duplicate submission.
- Validation before job creation or provider spend.
- Status progression.
- Result conflict while running.
- Cancellation.
- Partial failure.
- Secret and content redaction.
- Authorization.

Credential-pool tests cover:

- Dynamic one-, three-, and five-key parsing.
- Deduplication.
- `max_workers=5` produces five simultaneous leases when five independent keys
  and at least five work units are available.
- Requested workers above the active-key or ready-work count clamp safely and
  report requested and effective values.
- No simultaneous duplicate lease of the same key.
- Per-key cooldown and recovery without pausing healthy keys.
- Permanent disablement.
- Cancellation release.
- No raw key in representations or logs.

Memory plug-in tests extend the existing `AskProbe` and live-runner seams:

- A probe's three arms never cross shards.
- Shards never share a database, tenant, user, session, nonce, or output path.
- Concurrent completion order does not change report ordering.
- Per-shard failure produces `partially_succeeded`, not a complete-looking
  report.
- Teardown runs for every shard, including cancellation and failure.
- Baselines remain metadata-only.
- Existing full/ablated/control masking tests remain unchanged.
- The serial path and one-key configuration remain backward compatible.

Chat-RAGAS plug-in tests cover:

- One case maps to one stable work unit and output order is deterministic.
- `--max-workers` values `1`, `3`, and `5` reach the custom scheduler.
- Five keys permit five outer workers; RAGAS internal concurrency remains `1`.
- One key failure does not discard successful case metrics from healthy keys.
- NaN and missing metric values increment failure counts.
- Private question, answer, reference, and context fields never reach the
  metadata-only baseline, job status, logs, or errors.

The repository's narrowest relevant routes are feature tests, LLM integration
tests, script tests, and API integration tests. Live Mistral validation is an
opt-in smoke test and does not run in ordinary CI.

## Stress-test result: Chat-RAGAS and future 4–5-key scale

**Result: conditionally passes after the contract changes in this revision.**
The plug-in boundary is flexible enough for Chat-RAGAS, but the current runtime
does not yet implement the batch scheduler or accept `--max-workers`.

| Stress condition | Result | Required contract |
|---|---|---|
| Reuse the endpoint for Chat-RAGAS | Pass | Register `chat-ragas` as a `request_batch` plug-in; do not add another endpoint family. |
| Keep local dataset paths out of the HTTP API | Pass with guard | CLI resolves `--input`; API receives only an opaque pre-registered dataset reference. |
| Preserve Tier 1 behavior | Pass | Run deterministic metrics locally without keys. |
| Parallelize Tier 2 safely | Pass with design change | One case per work unit; one leased key per outer worker; metrics sequential inside the case. |
| Scale from 3 to 4–5 keys | Pass with design change | Dynamic key discovery and `effective_workers`; no hard-coded three-lane scheduler. |
| Prevent hidden 25-way fan-out at 5 workers | Pass with guard | RAGAS internal concurrency must be `1`, so total outer concurrency stays at five. |
| Survive one judge/key failure | Pass with guard | Per-case and per-metric outcomes plus `partially_succeeded`; no aggregate-only result. |
| Keep private documents out of artifacts | Pass | Reuse metadata-only baseline and private local artifact split. |

Repository verification on 2026-08-23 found:

- `parse_api_keys_from_env()` already accepts arbitrary numbered Mistral keys,
  including `MISTRAL_API_KEY4` and `MISTRAL_API_KEY5`.
- `APIKeyRotator` is round-robin and async-safe, but it is not a lease-aware
  worker pool and does not prevent simultaneous reuse of one key.
- `scripts/evaluate_chat_rag.py` currently rejects `--max-workers`.
- `run_ragas()` currently bulk-evaluates the complete dataset, so it cannot yet
  expose case-level scheduling, credential leases, or accurate partial-failure
  counts.

## Out of Scope

- Uploading executable evaluation plug-ins through the API.
- Running arbitrary commands or accepting arbitrary filesystem paths.
- A public multi-tenant evaluation service.
- Changes to product chat behavior.
- Changes to memory scoring or verdict semantics.
- Concurrent PostgreSQL memory-evaluation shards.
- Using multiple keys to evade provider terms or limits outside the explicitly
  independent limits assigned to these keys.
- Automatic cross-provider failover within one benchmark.
- Any use of Mistral's built-in Batch API, including batch file upload, inline
  batch submission, provider batch-job polling, or provider batch result files.
- A frontend dashboard.
- SQL migrations without separate approval.
- Committing private questions, replies, provider outputs, or API keys.

## Further Notes

Mistral's batch product is conceptual inspiration only. This specification does
not depend on or call it. Our application owns the job lifecycle and invokes
the same ordinary chat-completion transport used by non-batch Mistral features.
The custom module is responsible for work-item ids, concurrency, retries,
progress, cancellation, result persistence, and deterministic aggregation.

Current repository seams supporting this design:

- The memory runner executes probes and arms through an injected `AskProbe`.
- Each probe and arm already receives an isolated identity.
- The CLI builds one Mistral reply adapter from one Mistral settings object.
- Mistral settings currently load one `MISTRAL_API_KEY`.
- Multi-key parsing and async-safe round-robin rotation already exist.
- The memory-evaluation runbook currently requires one run at a time because
  of PostgreSQL advisory locks and previously observed same-provider
  contention. This specification replaces provider-side single-lane behavior
  only for the explicitly independent Mistral keys and keeps PostgreSQL serial.

### Acceptance criteria

- [ ] One to five (and later numbered) Mistral keys are discovered without exposing values.
- [ ] `--max-workers` and `execution.max_workers` accept positive integers and
      expose requested/effective values.
- [ ] The scheduler leases up to `effective_workers` independent keys concurrently.
- [ ] A SQLite memory evaluation runs with isolated workflow shards up to the effective limit.
- [ ] Every probe retains all three arms on one shard.
- [ ] One-key behavior remains supported.
- [ ] PostgreSQL mode enforces concurrency `1`.
- [ ] Failed or cancelled shards always clean up.
- [ ] Aggregated output is deterministic.
- [ ] Incomplete execution cannot appear as a successful complete benchmark.
- [ ] Another evaluator can be added through a registered plug-in without a
      new endpoint family.
- [ ] Chat-RAGAS runs one case per work unit with RAGAS internal concurrency `1`.
- [ ] Offline tests cover scheduler, isolation, redaction, retry, and state
      transition behavior.
