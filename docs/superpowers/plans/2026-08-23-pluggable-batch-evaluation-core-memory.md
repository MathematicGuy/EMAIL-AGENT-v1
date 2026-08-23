# Pluggable Batch Evaluation Core and Memory Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Level 1 asynchronous batch-evaluation control plane and use it to run isolated, multi-key SQLite memory-evaluation shards without changing memory scoring semantics.

**Architecture:** A new `features/batch_evaluation` deep module owns typed contracts, static plug-in registration, durable job orchestration, worker resolution, queue/shard execution, credential leasing, cancellation, recovery, and deterministic collection. A dedicated SQLite repository and private filesystem artifact adapter persist control-plane state without product SQL migrations. The first production plug-in extracts the current memory CLI's live workflow into a reusable shard executor; real Chat RAGAS integration is deferred, while a fake stateless plug-in proves that the core `request_batch` interface is reusable.

**Tech Stack:** Python 3.11+, FastAPI, asyncio, standard-library SQLite/JSON/path utilities, existing Mistral ordinary chat-completion transport, existing memory-evaluation modules, pytest, Ruff, mypy strict.

**Spec:** `tasks/specs/SPEC-pluggable-batch-evaluation-api.md`

**Design preview:** `docs/references/pluggable-batch-eval-level1-plugin-simulation-brainstorming.md`

## Global Constraints

- Always run Python through `uv run`; never use the machine's Anaconda interpreter.
- One Level 1 job accepts exactly one evaluation type, provider, target model, dataset reference, credential-pool alias, and execution mode.
- `max_workers` is an integer `>= 1`, defaults to `1`, and resolves to `min(requested, healthy compatible credentials, ready work)`.
- Requesting four workers with three healthy keys starts three workers and records `WORKER_COUNT_REDUCED`; ready-work scarcity alone does not produce this warning.
- Discover `MISTRAL_API_KEY`, `MISTRAL_API_KEY2`, … `MISTRAL_API_KEYN`; one key may hold only one lease.
- Never expose raw credentials in requests, dataclass representations, persistence, logs, warnings, errors, or artifacts.
- Independent key quotas are verified by an opt-in smoke test; key count alone is not proof.
- Use ordinary Mistral chat completions only. Do not call Mistral's provider Batch API.
- The API accepts registered plug-in ids and trusted dataset references only; it never accepts Python module names, commands, executable code, raw keys, or arbitrary filesystem paths.
- The API is disabled by default and, when enabled, requires an internal evaluator bearer token.
- Durable job/unit/attempt state is authoritative. Unknown provider outcomes are never blindly replayed.
- `request_batch` uses a bounded durable pull queue. `workflow_shards` uses deterministic fixed shards with no work stealing.
- The first concurrent memory implementation is SQLite-only. PostgreSQL memory evaluation always resolves to one worker.
- Keep every memory probe's FULL, ABLATED, and CONTROL arms on one shard and in their existing order.
- Preserve existing memory scorer, verdict, seed, mask, nonce, metadata-only baseline, private transcript, and teardown behavior.
- Give every memory shard and retry attempt a unique SQLite file, live session, adapter set, transcript, nonce, and private artifact directory.
- Scratch SQLite files are temporary resources and are deleted after success, partial success, failure, or cancellation.
- Incomplete units or shards cannot produce a complete-looking `succeeded` result.
- Do not implement the real Chat RAGAS plug-in in this plan. Prove stateless pluggability with a fake test plug-in only.
- Do not add frontend work, product-chat behavior changes, provider failover, PostgreSQL migrations, or SQL migrations under `src/cowork_agent/persistence/migrations/`.
- Preserve unrelated working-tree changes and stage only files owned by each task.
- Test narrow first using `tests/README.md`: R2 for feature logic, R4 for LLM integration, R7 for persistence, R9 for scripts, and R11 for API routes. Run the full suite once at the final checkpoint.

## File Structure

### Create

- `src/cowork_agent/features/batch_evaluation/__init__.py` — public Level 1 exports.
- `src/cowork_agent/features/batch_evaluation/contracts.py` — frozen records, enums, ports, and plug-in protocol.
- `src/cowork_agent/features/batch_evaluation/registry.py` — startup-only plug-in registry.
- `src/cowork_agent/features/batch_evaluation/planning.py` — worker resolution and deterministic sharding.
- `src/cowork_agent/features/batch_evaluation/queue.py` — bounded queue projection over durable unit state.
- `src/cowork_agent/features/batch_evaluation/credentials.py` — exclusive lease pool and key states.
- `src/cowork_agent/features/batch_evaluation/artifacts.py` — safe manifest and private artifact storage.
- `src/cowork_agent/features/batch_evaluation/runner.py` — generic lane and job execution for both modes.
- `src/cowork_agent/features/batch_evaluation/service.py` — submission, idempotency, status, result, and cancellation application seam.
- `src/cowork_agent/features/batch_evaluation/supervisor.py` — in-process task ownership and restart recovery.
- `src/cowork_agent/features/batch_evaluation/bootstrap.py` — local/app composition of the core and registered plug-ins.
- `src/cowork_agent/features/batch_evaluation/plugins/__init__.py` — built-in plug-in exports.
- `src/cowork_agent/features/batch_evaluation/plugins/memory_eval.py` — memory dataset catalog, shard plan, execution, aggregation, and cleanup policy.
- `src/cowork_agent/features/ai_chat/memory_eval/live_execution.py` — reusable live shard execution extracted from the CLI.
- `src/cowork_agent/integrations/llm/evaluation_mistral.py` — lease-bound, attempt-observable Mistral reply factory.
- `src/cowork_agent/persistence/repositories/evaluation_jobs.py` — dedicated SQLite job/unit/attempt/step store.
- `src/cowork_agent/api/evaluation_jobs.py` — internal evaluation API routes and safe error envelopes.
- `scripts/smoke_test_mistral_evaluation_keys.py` — opt-in independent-key concurrency check.
- Matching focused tests under `tests/unit/features/batch_evaluation/`, `tests/unit/persistence/`, `tests/unit/integrations/llm/`, `tests/unit/scripts/`, and `tests/integration/api/`.

### Modify

- `src/cowork_agent/config.py` — internal evaluation API/runtime settings.
- `src/cowork_agent/app.py` — lifespan composition and evaluation router registration.
- `src/cowork_agent/integrations/llm/providers/mistral.py` — preserve safe HTTP status/retry metadata on `MistralAPIError`.
- `src/cowork_agent/features/ai_chat/memory_eval/runner.py` — extract row production while preserving `run_probe_set()`.
- `scripts/evaluate_memory.py` — delegate reusable live work and expose `--max-workers`.
- `evaluations/MEMORIES/RUNBOOK.md` — batch invocation, SQLite-only concurrency, and smoke-test gate.
- `tests/README.md` — route and invariant ownership for the new feature.

### Explicitly deferred

- A production `ChatRagasPlugin` and changes to `scripts/evaluate_chat_rag.py`.
- Multi-model orchestration.
- PostgreSQL-parallel memory shards.
- A frontend evaluation dashboard.

---

### Task 1: Typed Core Contracts and Static Plug-in Registry

**Files:**
- Create: `src/cowork_agent/features/batch_evaluation/__init__.py`
- Create: `src/cowork_agent/features/batch_evaluation/contracts.py`
- Create: `src/cowork_agent/features/batch_evaluation/registry.py`
- Create: `tests/unit/features/batch_evaluation/test_contracts.py`
- Create: `tests/unit/features/batch_evaluation/test_registry.py`

**Interfaces:**
- Produces: `ExecutionMode`, `JobState`, `UnitState`, `AttemptState`, `StepState`, `FailureClass`, and `CredentialState` string enums.
- Produces: `EvaluationBudget(max_provider_requests: int, max_total_tokens: int)`.
- Produces: `EvaluationRequest.from_dict(payload: Mapping[str, object]) -> EvaluationRequest`.
- Produces: `canonical_request_hash(request: EvaluationRequest) -> str`.
- Produces: `EvaluationPlugin` protocol with `preflight`, `build_work_units`, `execute_work`, `aggregate`, `cleanup`, and `classify_failure`.
- Produces: `PluginRegistry.register(plugin)`, `require(evaluation_type)`, and `list_types()`.
- Consumers: Tasks 2–11.

- [ ] **Step 1: Write failing contract tests**

```python
def test_request_defaults_to_one_worker_and_rejects_zero() -> None:
    payload = valid_request()
    payload["execution_options"] = {}
    assert EvaluationRequest.from_dict(payload).max_workers == 1

    payload["execution_options"] = {"max_workers": 0}
    with pytest.raises(ValueError, match="max_workers"):
        EvaluationRequest.from_dict(payload)


def test_canonical_hash_ignores_json_key_order_but_not_values() -> None:
    first = EvaluationRequest.from_dict(valid_request())
    reordered = EvaluationRequest.from_dict(dict(reversed(list(valid_request().items()))))
    assert canonical_request_hash(first) == canonical_request_hash(reordered)
```

Also assert exact one-value fields, positive budgets, supported execution modes, safe identifier syntax, frozen records, and recursive rejection of secret-looking parameter keys such as `api_key`, `token`, and `authorization`.

- [ ] **Step 2: Run tests and verify the package is missing**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation/test_contracts.py tests/unit/features/batch_evaluation/test_registry.py
```

Expected: collection fails because `cowork_agent.features.batch_evaluation` does not exist.

- [ ] **Step 3: Implement the smallest stable contract surface**

```python
@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    evaluation_type: str
    provider: str
    target_model: str
    dataset_ref: str
    credential_pool: str
    execution_mode: ExecutionMode
    max_workers: int
    max_attempts_per_unit: int
    budget: EvaluationBudget
    parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PluginPlan:
    dataset_ref: str
    ready_work: int
    private_plan: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class WorkUnit:
    unit_id: str
    ordinal: int
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class WorkContext:
    job_id: str
    attempt_id: str
    lane_id: str
    credential_alias: str
    plugin_plan: PluginPlan = field(repr=False, compare=False)
    provider_client: object = field(repr=False, compare=False)
    scratch_dir: Path = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class WorkUnitOutcome:
    unit_id: str
    ordinal: int
    state: UnitState
    provider_requests: int
    total_tokens: int
    private_result: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ArtifactBundle:
    public_result: Mapping[str, object]
    private_artifact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    removed_resources: int
    warnings: tuple[EvaluationWarning, ...]


@dataclass(frozen=True, slots=True)
class FailureClassification:
    failure_class: FailureClass
    retryable: bool
    credential_state: CredentialState | None


@dataclass(frozen=True, slots=True)
class EvaluationWarning:
    code: str
    message: str
    details: Mapping[str, int | str]


@dataclass(frozen=True, slots=True)
class WorkerResolution:
    requested_workers: int
    effective_workers: int
    healthy_credentials: int
    ready_work: int
    warning: EvaluationWarning | None


@dataclass(frozen=True, slots=True)
class ProviderAttemptEvent:
    credential_alias: str
    request_attempt_id: str
    outcome: str
    status_code: int | None
    retry_after_seconds: int | None
    latency_ms: int


class EvaluationPlugin(Protocol):
    evaluation_type: str
    version: str
    supported_modes: frozenset[ExecutionMode]
    parameter_schema: Mapping[str, object]

    async def preflight(self, request: EvaluationRequest) -> PluginPlan: ...
    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]: ...
    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome: ...
    def aggregate(self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]) -> ArtifactBundle: ...
    async def cleanup(self, context: WorkContext) -> CleanupOutcome: ...
    def classify_failure(self, error: BaseException) -> FailureClassification: ...
```

Keep `WorkUnit.payload` metadata-only. Private evaluation content is resolved by the plug-in from `dataset_ref` and stable item ids.

- [ ] **Step 4: Implement duplicate-safe startup registration**

```python
class PluginRegistry:
    def register(self, plugin: EvaluationPlugin) -> None:
        if plugin.evaluation_type in self._plugins:
            raise ValueError(f"duplicate evaluation type: {plugin.evaluation_type}")
        self._plugins[plugin.evaluation_type] = plugin
```

Assert unknown types fail before any provider call and `list_types()` exposes only type, version, modes, and parameter schema.

- [ ] **Step 5: Run focused tests and static checks**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation/test_contracts.py tests/unit/features/batch_evaluation/test_registry.py
uv run ruff check src/cowork_agent/features/batch_evaluation tests/unit/features/batch_evaluation
uv run mypy src
```

- [ ] **Step 6: Commit the contract slice**

```powershell
git add src/cowork_agent/features/batch_evaluation tests/unit/features/batch_evaluation
git commit -m "feat: define batch evaluation contracts"
```

---

### Task 2: Worker Resolution, Deterministic Sharding, and Durable Queue Projection

**Files:**
- Create: `src/cowork_agent/features/batch_evaluation/planning.py`
- Create: `src/cowork_agent/features/batch_evaluation/queue.py`
- Create: `tests/unit/features/batch_evaluation/test_planning.py`
- Create: `tests/unit/features/batch_evaluation/test_queue.py`

**Interfaces:**
- Consumes: Task 1 `WorkUnit`, `WorkUnitOutcome`, and `EvaluationWarning`.
- Produces: `resolve_worker_count(requested, healthy_credentials, ready_work) -> WorkerResolution`.
- Produces: `DataSharder.partition(items, shard_count) -> tuple[tuple[IndexedItem[T], ...], ...]`.
- Produces: `UnitStore.claim_ready_unit(job_id: str, worker_id: str) -> WorkUnit | None` and `complete_unit(job_id: str, outcome: WorkUnitOutcome) -> None` protocol methods.
- Produces: `DurableWorkUnitQueue.claim_next(job_id, worker_id) -> WorkUnit | None`.
- Consumers: Task 5 runner and Task 8 memory plug-in.

- [ ] **Step 1: Write failing worker-resolution tests**

```python
@pytest.mark.parametrize(
    ("requested", "healthy", "ready", "effective", "warns"),
    [(1, 3, 10, 1, False), (2, 3, 10, 2, False), (4, 3, 10, 3, True), (4, 5, 2, 2, False)],
)
def test_worker_resolution(requested, healthy, ready, effective, warns) -> None:
    result = resolve_worker_count(requested, healthy, ready)
    assert result.effective_workers == effective
    assert (result.warning is not None) is warns
```

The warning must contain `WORKER_COUNT_REDUCED`, requested/effective counts, and available credential count only when credentials are the limiting factor.

- [ ] **Step 2: Write failing sharder and queue tests**

```python
def test_round_robin_shards_preserve_original_ordinals() -> None:
    shards = DataSharder().partition(tuple("abcdefg"), 3)
    assert [[item.ordinal for item in shard] for shard in shards] == [[0, 3, 6], [1, 4], [2, 5]]


async def test_queue_claims_from_store_and_never_replays_completed_unit() -> None:
    store = FakeUnitStore(ready=(unit("a", 0), unit("b", 1)))
    queue = DurableWorkUnitQueue(store, capacity=1)
    assert (await queue.claim_next("job-1", "lane-1")).unit_id == "a"
    await store.complete("job-1", "a")
    assert (await queue.claim_next("job-1", "lane-1")).unit_id == "b"
```

- [ ] **Step 3: Implement pure worker resolution and round-robin sharding**

```python
effective = min(requested, healthy_credentials, ready_work)
warning = (
    EvaluationWarning.worker_count_reduced(requested, effective, healthy_credentials)
    if requested > healthy_credentials
    else None
)
```

Reject non-positive values at the request boundary. Return no empty shards.

- [ ] **Step 4: Implement a bounded queue as a projection of store truth**

The queue may cache ids for wakeups, but every claim must call the store's atomic `claim_ready_unit`. A restart reconstructs the projection from durable `ready` rows; no in-memory `asyncio.Queue` state is authoritative.

- [ ] **Step 5: Run focused tests**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation/test_planning.py tests/unit/features/batch_evaluation/test_queue.py
uv run ruff check src/cowork_agent/features/batch_evaluation/planning.py src/cowork_agent/features/batch_evaluation/queue.py tests/unit/features/batch_evaluation
uv run mypy src
```

- [ ] **Step 6: Commit the planning slice**

```powershell
git add src/cowork_agent/features/batch_evaluation/planning.py src/cowork_agent/features/batch_evaluation/queue.py tests/unit/features/batch_evaluation
git commit -m "feat: add evaluation work planning"
```

---

### Task 3: Exclusive Credential Leasing and Attempt-Observable Mistral Replies

**Files:**
- Create: `src/cowork_agent/features/batch_evaluation/credentials.py`
- Create: `src/cowork_agent/integrations/llm/evaluation_mistral.py`
- Modify: `src/cowork_agent/integrations/llm/providers/mistral.py`
- Create: `tests/unit/features/batch_evaluation/test_credentials.py`
- Create: `tests/unit/integrations/llm/test_evaluation_mistral.py`

**Interfaces:**
- Consumes: existing `parse_api_keys_from_env()` and `MistralChatReply`.
- Produces: `CredentialLeasingPool.from_env(prefix, environ, clock)`.
- Produces: async `lease()` returning a repr-safe `CredentialLease` with alias only publicly visible.
- Produces: `MistralEvaluationReplyFactory.bind(lease, model, attempt_sink) -> ChatReplyPort`.
- Produces: `ProviderAttemptEvent` with safe status, retry-after, latency, and local request-attempt id.
- Consumers: Task 5 lanes and Task 11 smoke test.

- [ ] **Step 1: Write failing leasing tests**

```python
async def test_three_keys_can_be_leased_once_each_without_secret_repr() -> None:
    pool = CredentialLeasingPool.from_env(
        "MISTRAL_API_KEY",
        {"MISTRAL_API_KEY": "secret-a", "MISTRAL_API_KEY2": "secret-b", "MISTRAL_API_KEY3": "secret-c"},
        clock=FakeClock(),
    )
    leases = await asyncio.gather(pool.lease(), pool.lease(), pool.lease())
    assert {lease.alias for lease in leases} == {"mistral-1", "mistral-2", "mistral-3"}
    assert "secret-" not in repr(leases)
```

Also cover duplicate keys, one-key fallback, exclusive leasing, release, cooldown recovery, permanent disablement, and cancellation release.

- [ ] **Step 2: Preserve HTTP classification without changing safe product errors**

Extend `MistralAPIError` with optional `status_code` and `retry_after_seconds`, both safe integers. `_post_json()` sets the status from `HTTPError.code`; existing callers still receive the same exception type and safe message.

Parse an integer `Retry-After` value from `HTTPError.headers` when present; ignore date-form or malformed values in Level 1. Never retain the response body or headers on the exception.

```python
class MistralAPIError(RuntimeError):
    def __init__(self, detail: str, *, safe_message: str | None = None,
                 status_code: int | None = None, retry_after_seconds: int | None = None) -> None:
        super().__init__(detail)
        self.safe_message = safe_message or DEFAULT_SAFE_MESSAGE
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
```

- [ ] **Step 3: Write failing attempt-observation tests**

Use a fake completion transport that raises HTTP `429`, `401`, and timeout errors. Assert the wrapper records classification without recording prompt, reply, raw body, or secret.

- [ ] **Step 4: Implement the lease-bound reply factory**

Construct a transient `MistralSettings` from non-secret option fields plus the lease's hidden key, then delegate to `MistralChatReply.from_settings()`. Wrap `stream_reply()` so failures inspect the cause chain and emit `ProviderAttemptEvent`; re-raise the existing safe exception unchanged.

- [ ] **Step 5: Run R4 and credential tests**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation/test_credentials.py tests/unit/integrations/llm/test_evaluation_mistral.py tests/unit/integrations/llm/test_mistral.py
uv run ruff check src/cowork_agent/features/batch_evaluation/credentials.py src/cowork_agent/integrations/llm/evaluation_mistral.py src/cowork_agent/integrations/llm/providers/mistral.py
uv run mypy src
```

- [ ] **Step 6: Commit the leasing slice**

```powershell
git add src/cowork_agent/features/batch_evaluation/credentials.py src/cowork_agent/integrations/llm/evaluation_mistral.py src/cowork_agent/integrations/llm/providers/mistral.py tests/unit/features/batch_evaluation/test_credentials.py tests/unit/integrations/llm
git commit -m "feat: add evaluation credential leasing"
```

---

### Task 4: Durable SQLite Job Store and Privacy-Split Artifact Store

**Files:**
- Create: `src/cowork_agent/persistence/repositories/evaluation_jobs.py`
- Create: `src/cowork_agent/features/batch_evaluation/artifacts.py`
- Create: `tests/unit/persistence/test_evaluation_job_repository.py`
- Create: `tests/unit/features/batch_evaluation/test_artifacts.py`

**Interfaces:**
- Consumes: Task 1 records and Task 2 `UnitStore` protocol.
- Produces: `SQLiteEvaluationJobRepository(path: Path)`.
- Produces: atomic `create_or_get(request, idempotency_key, request_hash)`.
- Produces: atomic job transitions, unit claims, attempt/step writes, cancellation request, and recovery queries.
- Produces: `FilesystemEvaluationArtifactStore(root: Path)` with metadata manifests and private details.
- Consumers: Tasks 5–10.

- [ ] **Step 1: Write failing repository tests**

```python
async def test_idempotency_is_atomic_and_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    repo = SQLiteEvaluationJobRepository(tmp_path / "evaluation-jobs.db")
    await repo.initialize()
    first, created = await repo.create_or_get(request(), "same-key", "hash-a")
    replay, replay_created = await repo.create_or_get(request(), "same-key", "hash-a")
    assert created is True and replay_created is False and replay.job_id == first.job_id
    with pytest.raises(IdempotencyConflict):
        await repo.create_or_get(request(), "same-key", "hash-b")
```

Also assert monotonic states, atomic unit claims, attempt ids, step states, cancellation idempotency, and orphaned `running` attempts becoming `unknown` rather than `ready`.

- [ ] **Step 2: Implement dedicated WAL-backed tables**

Create tables inside this repository, not product migrations:

```sql
evaluation_jobs(job_id, idempotency_key UNIQUE, request_hash, request_json,
                state, requested_workers, effective_workers, warnings_json,
                cancel_requested_at, created_at, updated_at, completed_at)
evaluation_units(job_id, unit_id, ordinal, state, safe_payload_json,
                 PRIMARY KEY(job_id, unit_id))
evaluation_attempts(attempt_id PRIMARY KEY, job_id, unit_id, credential_alias,
                    attempt_number, state, failure_class, started_at, completed_at)
evaluation_steps(attempt_id, step_id, ordinal, state, safe_metadata_json,
                 PRIMARY KEY(attempt_id, step_id))
```

Persist only safe request fields and plug-in metadata. Never persist dataset contents, replies, raw provider errors, or keys.

- [ ] **Step 3: Write failing artifact privacy tests**

Recursively reject private keys from metadata manifests and assert private details are written only below the ignored runtime root.

```python
with pytest.raises(UnsafeArtifact):
    store.write_manifest("job-1", {"question": "private"})
```

- [ ] **Step 4: Implement atomic artifact writes**

Write to a sibling temporary file, flush, then replace. Return relative artifact references only; API responses never contain absolute local paths.

- [ ] **Step 5: Run R7 and feature tests**

```powershell
uv run pytest -q tests/unit/persistence/test_evaluation_job_repository.py tests/unit/features/batch_evaluation/test_artifacts.py
uv run ruff check src/cowork_agent/persistence/repositories/evaluation_jobs.py src/cowork_agent/features/batch_evaluation/artifacts.py tests/unit/persistence/test_evaluation_job_repository.py
uv run mypy src
```

- [ ] **Step 6: Commit persistence and artifacts**

```powershell
git add src/cowork_agent/persistence/repositories/evaluation_jobs.py src/cowork_agent/features/batch_evaluation/artifacts.py tests/unit/persistence/test_evaluation_job_repository.py tests/unit/features/batch_evaluation/test_artifacts.py
git commit -m "feat: persist evaluation job state"
```

---

### Task 5: Evaluation Job Service and Generic Lane Runner

**Files:**
- Create: `src/cowork_agent/features/batch_evaluation/service.py`
- Create: `src/cowork_agent/features/batch_evaluation/runner.py`
- Create: `tests/unit/features/batch_evaluation/test_evaluation_job_service.py`
- Create: `tests/unit/features/batch_evaluation/test_runner_request_batch.py`
- Create: `tests/unit/features/batch_evaluation/test_runner_workflow_shards.py`

**Interfaces:**
- Consumes: Tasks 1–4 registry, plans, queue, pool, job store, and artifact store.
- Produces: `EvaluationJobService.submit/get_status/get_result/request_cancel/list_types`.
- Produces: `EvaluationJobRunner.run(job_id) -> None`.
- Produces: generic `LaneExecutor` for `request_batch` and fixed `workflow_shards`.
- Consumers: Tasks 6, 9, and 10.

- [ ] **Step 1: Write failing service tests around externally visible behavior**

```python
async def test_submit_validates_before_persisting_or_spending() -> None:
    plugin = FakePlugin(preflight_error=ValueError("bad dataset"))
    service = service_with(plugin=plugin)
    with pytest.raises(EvaluationValidationError):
        await service.submit(request(), idempotency_key="key-1")
    assert service.store.jobs == ()
    assert plugin.provider_calls == 0
```

Cover idempotent replay, hash conflict, default worker count, unknown type, one target model, non-terminal result conflict, safe status serialization, and idempotent cancellation.

- [ ] **Step 2: Write failing `request_batch` stress-contract tests with a fake plug-in**

The fake plug-in creates one atomic unit per integer and records two sequential steps inside each unit. Complete units with deliberately reversed delays and assert output order follows original ordinals. Simulate one cooldown and one disabled key; healthy lanes must continue. This is the RAGAS-shaped pluggability proof without implementing RAGAS.

- [ ] **Step 3: Write failing fixed-shard tests**

Assert each workflow shard retains one lease, executes assigned units sequentially, never steals work, and starts a retry with a fresh attempt id and scratch context.

- [ ] **Step 4: Implement budget-aware lane execution**

Use one `asyncio.Task` per effective lane. The lease-bound reply wrapper reserves request/token budget at every provider attempt. Stop new claims immediately on cancellation or exhausted budget. Persist attempt and step outcomes before aggregation.

```python
if request.execution_mode is ExecutionMode.REQUEST_BATCH:
    await self._run_pull_lanes(job, plugin, plan, resolution)
else:
    await self._run_fixed_shards(job, plugin, plan, resolution)
```

- [ ] **Step 5: Implement honest terminal classification**

All units complete → `succeeded`; mixed complete/failed → `partially_succeeded`; none complete → `failed`; cancellation → `cancelled`. Cleanup failure prevents a clean-success claim. Aggregate only successful complete outcomes while passing failed outcomes to the plug-in for denominators and manifest reporting.

- [ ] **Step 6: Run R2 feature tests**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation/test_evaluation_job_service.py tests/unit/features/batch_evaluation/test_runner_request_batch.py tests/unit/features/batch_evaluation/test_runner_workflow_shards.py
uv run ruff check src/cowork_agent/features/batch_evaluation tests/unit/features/batch_evaluation
uv run mypy src
```

- [ ] **Step 7: Commit the runnable core**

```powershell
git add src/cowork_agent/features/batch_evaluation/service.py src/cowork_agent/features/batch_evaluation/runner.py tests/unit/features/batch_evaluation
git commit -m "feat: execute pluggable evaluation jobs"
```

---

### Task 6: In-Process Supervisor, Cancellation, and Restart Recovery

**Files:**
- Create: `src/cowork_agent/features/batch_evaluation/supervisor.py`
- Create: `tests/unit/features/batch_evaluation/test_supervisor.py`
- Modify: `src/cowork_agent/features/batch_evaluation/service.py`

**Interfaces:**
- Consumes: Task 5 `EvaluationJobRunner` and Task 4 recovery queries.
- Produces: `EvaluationSupervisor.start(job_id)`, `cancel(job_id)`, `recover()`, and `close()`.
- Consumers: app lifespan and local CLI bootstrap.

- [ ] **Step 1: Write failing supervisor tests**

```python
async def test_recovery_marks_orphaned_attempt_unknown_before_policy_runs() -> None:
    store = FakeStore(orphaned_attempts=(running_attempt("attempt-1"),))
    supervisor = EvaluationSupervisor(store, FakeRunner())
    await supervisor.recover()
    assert store.attempt("attempt-1").state is AttemptState.UNKNOWN
```

Also assert duplicate `start()` does not create duplicate tasks, cancellation stops new claims, cleanup completes before terminal state, and shutdown releases every lease.

- [ ] **Step 2: Implement task ownership with explicit cleanup**

Keep `job_id -> asyncio.Task[None]` only as a process-local optimization. Done callbacks remove tasks; store state remains authoritative. `close()` requests cancellation, awaits tasks, and never drops cleanup exceptions silently.

- [ ] **Step 3: Implement recovery policy**

Queued jobs restart. Running/collecting jobs first classify orphaned attempts as unknown; only attempts explicitly safe under retry budget become fresh attempts. Unknown completed-provider outcomes remain failed/partial and are not replayed automatically.

- [ ] **Step 4: Run focused tests**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation/test_supervisor.py tests/unit/features/batch_evaluation/test_evaluation_job_service.py
uv run ruff check src/cowork_agent/features/batch_evaluation/supervisor.py src/cowork_agent/features/batch_evaluation/service.py
uv run mypy src
```

- [ ] **Step 5: Commit supervision and recovery**

```powershell
git add src/cowork_agent/features/batch_evaluation/supervisor.py src/cowork_agent/features/batch_evaluation/service.py tests/unit/features/batch_evaluation
git commit -m "feat: recover evaluation jobs safely"
```

---

### Task 7: Extract a Mergeable Memory Row and Live-Shard Execution Seam

**Files:**
- Modify: `src/cowork_agent/features/ai_chat/memory_eval/runner.py`
- Create: `src/cowork_agent/features/ai_chat/memory_eval/live_execution.py`
- Modify: `scripts/evaluate_memory.py`
- Create: `tests/unit/features/ai_chat/memory_eval/test_live_execution.py`
- Modify: `tests/unit/features/ai_chat/memory_eval/test_live_runner.py`

**Interfaces:**
- Produces: `run_probe_rows(probe_set, ask) -> tuple[ProbeRow, ...]`.
- Preserves: current `run_probe_set(...) -> dict[str, object]` public behavior.
- Produces: `MemoryShardResult(rows, seed_failure_ids, private_transcript, nonce, provider_findings, scratch_removed)`; private fields use `repr=False`.
- Produces: `execute_memory_shard(probe_set: ProbeSet, environment: LiveEnvironment, reply: ChatReplyPort, *, provider: str, model: str) -> MemoryShardResult`.
- Produces: `build_memory_report(probe_set: ProbeSet, shard_results: Sequence[MemoryShardResult], provider: str, model: str, ran_at: datetime) -> dict[str, object]`.
- Consumers: Task 8 memory plug-in and existing CLI.

- [ ] **Step 1: Write a characterization test for the existing serial report**

Capture the current `run_probe_set()` output for a two-probe fake set, including all three arms, verdicts, nonce, seed failures, and report sorting. This test must pass before refactoring.

- [ ] **Step 2: Write failing row-seam tests**

```python
rows = await run_probe_rows(probe_set, scripted_ask)
assert [row.probe_id for row in rows] == [probe.probe_id for probe in probe_set.probes]
assert calls == [
    ("probe-1", Arm.FULL), ("probe-1", Arm.ABLATED), ("probe-1", Arm.CONTROL),
    ("probe-2", Arm.FULL), ("probe-2", Arm.ABLATED), ("probe-2", Arm.CONTROL),
]
```

- [ ] **Step 3: Extract row production without changing report assembly**

`run_probe_set()` calls `run_probe_rows()` and then the existing `build_report()`. Do not alter scoring, arm masking, or final report ordering.

- [ ] **Step 4: Move CLI-private live orchestration into `live_execution.py`**

Move `_build_adapters`, transcript attachment, aborted-result handling, and live execution behind typed functions. `execute_memory_shard()` returns rows, seed failures, transcript, nonce, attempt-safe provider findings, and cleanup outcome. It does not write a final whole-dataset report.

- [ ] **Step 5: Preserve the existing CLI through a compatibility wrapper**

Keep `scripts.evaluate_memory.run_live()` with its current signature. It delegates one full-set shard to `execute_memory_shard()` and then `build_memory_report()`, so existing script tests and external commands remain valid.

- [ ] **Step 6: Prove cleanup and SQLite deletion on every path**

Tests cover success, provider failure, cancellation, teardown failure, and unique scratch paths. The executor calls memory teardown first, closes any pool, then unlinks the scratch SQLite file in `finally`.

- [ ] **Step 7: Run memory and script routes**

```powershell
uv run pytest -q tests/unit/features/ai_chat/memory_eval tests/unit/scripts/test_evaluate_memory.py tests/unit/scripts/test_evaluate_memory_provider.py
uv run ruff check src/cowork_agent/features/ai_chat/memory_eval scripts/evaluate_memory.py tests/unit/features/ai_chat/memory_eval
uv run mypy src
```

- [ ] **Step 8: Commit the behavior-preserving extraction**

```powershell
git add src/cowork_agent/features/ai_chat/memory_eval/runner.py src/cowork_agent/features/ai_chat/memory_eval/live_execution.py scripts/evaluate_memory.py tests/unit/features/ai_chat/memory_eval tests/unit/scripts/test_evaluate_memory.py tests/unit/scripts/test_evaluate_memory_provider.py
git commit -m "refactor: expose memory evaluation shard execution"
```

---

### Task 8: Production Memory-Evaluation Plug-in

**Files:**
- Create: `src/cowork_agent/features/batch_evaluation/plugins/__init__.py`
- Create: `src/cowork_agent/features/batch_evaluation/plugins/memory_eval.py`
- Create: `tests/unit/features/batch_evaluation/plugins/test_memory_eval.py`
- Modify: `tests/unit/features/batch_evaluation/test_runner_workflow_shards.py`

**Interfaces:**
- Consumes: Tasks 1–7 core and memory shard seam.
- Produces: `MemoryProbeCatalog.resolve(dataset_ref) -> ProbeSet` for canonical ids only.
- Produces: stateless `MemoryEvalPlugin` version `1`, mode `workflow_shards`.
- Produces: deterministic round-robin `WorkUnit` descriptors containing original ordinals and probe ids only.
- Produces: whole-probe-set aggregation through existing `build_report()`.
- Consumers: Task 9 bootstrap.

- [ ] **Step 1: Write failing catalog and preflight tests**

Assert `v1-four-scopes`, `v2-four-scopes-wide`, and `v3-four-scopes-hard` resolve inside `evaluations/MEMORIES/probes/`; reject `../`, absolute paths, unknown ids, provider other than `mistral`, mode other than `workflow_shards`, and parallel PostgreSQL.

- [ ] **Step 2: Write failing shard invariants**

```python
shards = plugin.build_work_units(plan, lane_count=3)
assert flattened_probe_ids(shards) == tuple(probe.probe_id for probe in plan.probe_set.probes)
assert every_probe_appears_once(shards)
assert all(unit.payload.keys() == {"probe_ids", "ordinals"} for unit in shards)
```

Execute fake shards concurrently and assert no database, tenant, user, session, nonce, transcript, or output path is shared.

- [ ] **Step 3: Implement stateless planning and attempt-specific execution context**

The plug-in instance stores only immutable catalog/config dependencies. `preflight()` returns a per-job plan. `execute_work()` reconstructs the shard `ProbeSet`, obtains the lane's lease-bound reply, and passes an attempt-specific scratch path to `execute_memory_shard()`.

- [ ] **Step 4: Implement deterministic whole-set aggregation**

Index returned rows by probe id, rebuild the row sequence in the original probe-set order, then call existing `build_report()` once. Preserve the current report's own deterministic verdict ordering. Missing or failed shard rows force partial/failed classification and appear in the safe execution manifest.

- [ ] **Step 5: Assert artifact privacy and cleanup**

Public baseline contains no probe questions, replies, seed text, absolute paths, or keys. Private transcripts remain below `evaluations/MEMORIES/runs/{job_id}/{attempt_id}/`. Scratch files are absent after every terminal path.

- [ ] **Step 6: Run memory plug-in tests**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation/plugins/test_memory_eval.py tests/unit/features/batch_evaluation/test_runner_workflow_shards.py tests/unit/features/ai_chat/memory_eval
uv run ruff check src/cowork_agent/features/batch_evaluation/plugins tests/unit/features/batch_evaluation/plugins
uv run mypy src
```

- [ ] **Step 7: Commit the memory plug-in**

```powershell
git add src/cowork_agent/features/batch_evaluation/plugins tests/unit/features/batch_evaluation/plugins tests/unit/features/batch_evaluation/test_runner_workflow_shards.py
git commit -m "feat: add batched memory evaluation plugin"
```

---

### Task 9: Runtime Bootstrap and Memory CLI `--max-workers`

**Files:**
- Create: `src/cowork_agent/features/batch_evaluation/bootstrap.py`
- Modify: `scripts/evaluate_memory.py`
- Modify: `tests/unit/scripts/test_evaluate_memory.py`
- Modify: `tests/unit/scripts/test_evaluate_memory_provider.py`

**Interfaces:**
- Consumes: Tasks 3–8.
- Produces: `EvaluationRuntimeConfig(job_db_path: Path, artifact_root: Path)`.
- Produces: `EvaluationRuntime(service: EvaluationJobService, supervisor: EvaluationSupervisor, repository: SQLiteEvaluationJobRepository, credential_pool: CredentialLeasingPool)`.
- Produces: `build_evaluation_runtime(config: EvaluationRuntimeConfig, environ: Mapping[str, str]) -> EvaluationRuntime`.
- Produces: `--max-workers` and optional `--idempotency-key` on the memory CLI.
- Preserves: serial non-Mistral providers and existing output/report behavior.
- Consumers: Task 10 app composition.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_max_workers_defaults_to_one(monkeypatch, tmp_path: Path) -> None:
    captured_requests: list[EvaluationRequest] = []
    configure_one_fake_mistral_key(monkeypatch)
    install_fake_runtime(monkeypatch, captured_requests, effective_workers=1)
    output = tmp_path / "report.json"
    result = run_cli(
        "evaluate_memory",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--provider",
        "mistral",
        "--output",
        str(output),
    )
    assert result.returncode == 0
    assert captured_requests[0].max_workers == 1


def test_four_requested_workers_with_three_keys_reports_reduction(monkeypatch, tmp_path: Path) -> None:
    captured_requests: list[EvaluationRequest] = []
    configure_three_fake_mistral_keys(monkeypatch)
    install_fake_runtime(monkeypatch, captured_requests, effective_workers=3)
    output = tmp_path / "report.json"
    result = run_cli(
        "evaluate_memory",
        "--probe-set",
        str(_probe_set_file(tmp_path)),
        "--provider",
        "mistral",
        "--max-workers",
        "4",
        "--output",
        str(output),
    )
    assert result.returncode == 0
    assert captured_requests[0].max_workers == 4
    assert "WORKER_COUNT_REDUCED" in result.stderr
    assert "effective_workers=3" in result.stderr
```

Use injected fake runtime/transport; no external calls.

- [ ] **Step 2: Implement one composition root**

`build_evaluation_runtime()` creates the registry, memory plug-in, SQLite job store, artifact store, Mistral credential pool/factory, service, runner, and supervisor. It receives explicit paths and environment mappings so tests never touch developer data.

- [ ] **Step 3: Route Mistral memory runs through Level 1**

For `provider=mistral`, submit the job locally, execute it to terminal, write the existing report schema unchanged, print worker warnings to stderr, and preserve private transcript behavior. The API/job manifest owns execution metadata. Existing Gemini/OpenRouter/Vyce serial execution remains unchanged; reject `max_workers > 1` for those providers with a clear no-spend validation error.

- [ ] **Step 4: Run R9 and core tests**

```powershell
uv run pytest -q tests/unit/scripts/test_evaluate_memory.py tests/unit/scripts/test_evaluate_memory_provider.py tests/unit/features/batch_evaluation
uv run ruff check src/cowork_agent/features/batch_evaluation scripts/evaluate_memory.py tests/unit/scripts/test_evaluate_memory.py tests/unit/scripts/test_evaluate_memory_provider.py
uv run mypy src
```

- [ ] **Step 5: Commit runtime and CLI support**

```powershell
git add src/cowork_agent/features/batch_evaluation/bootstrap.py scripts/evaluate_memory.py tests/unit/scripts/test_evaluate_memory.py tests/unit/scripts/test_evaluate_memory_provider.py
git commit -m "feat: run memory evaluation with dynamic workers"
```

---

### Task 10: Internal Evaluation API, Authentication, and App Lifespan Wiring

**Files:**
- Create: `src/cowork_agent/api/evaluation_jobs.py`
- Modify: `src/cowork_agent/config.py`
- Modify: `src/cowork_agent/app.py`
- Create: `tests/integration/api/test_evaluation_jobs_api.py`
- Create: `tests/unit/test_evaluation_config.py`

**Interfaces:**
- Consumes: Task 9 `EvaluationRuntime`.
- Produces: `EvaluationSettings.from_env()` and `EvaluationSettings.to_runtime_config() -> EvaluationRuntimeConfig`.
- Produces: all five SPEC endpoints under `/v1/evaluation-jobs` and `/v1/evaluation-types`.
- Produces: exact safe error envelope `{"error": {"code": str, "message": str, "details"?: object}}`.

- [ ] **Step 1: Write failing settings and authorization tests**

Use these settings:

```text
EVALUATION_API_ENABLED=0
EVALUATION_API_TOKEN=test-evaluation-token-with-32-characters
EVALUATION_JOB_DB_PATH=.data/evaluation-jobs.db
EVALUATION_ARTIFACT_ROOT=.data/evaluation-jobs
```

Assert disabled-by-default, missing token rejection at startup when enabled, `401` for missing bearer token, `403` for a wrong token, constant-time comparison, and no token in repr/logs.

- [ ] **Step 2: Write failing API contract tests**

Cover `202` submission/status/result URLs, idempotent replay, hash conflict `422`, validation before job creation/spend, status progression, non-terminal result `409`, unknown job `404`, idempotent cancellation `202`, type listing, max-worker cases, safe error shape, and recursive content/secret redaction.

- [ ] **Step 3: Implement transport-only routes**

Parse the request body explicitly through `EvaluationRequest.from_dict()` so this router can own the SPEC error envelope without changing validation responses for unrelated APIs. Read `Idempotency-Key` and bearer token from headers. Never return private artifacts or absolute paths.

- [ ] **Step 4: Wire the lifespan**

When enabled, `create_app()` builds and initializes the evaluation runtime, calls `supervisor.recover()` at startup, stores the service/supervisor on `app.state`, and awaits `supervisor.close()` at shutdown. Include the router only when enabled.

- [ ] **Step 5: Run R11 and settings tests**

```powershell
uv run pytest -q tests/integration/api/test_evaluation_jobs_api.py tests/unit/test_evaluation_config.py
uv run ruff check src/cowork_agent/api/evaluation_jobs.py src/cowork_agent/config.py src/cowork_agent/app.py tests/integration/api/test_evaluation_jobs_api.py tests/unit/test_evaluation_config.py
uv run mypy src
```

- [ ] **Step 6: Commit the API slice**

```powershell
git add src/cowork_agent/api/evaluation_jobs.py src/cowork_agent/config.py src/cowork_agent/app.py tests/integration/api/test_evaluation_jobs_api.py tests/unit/test_evaluation_config.py
git commit -m "feat: expose internal evaluation job API"
```

---

### Task 11: Opt-In Mistral Key-Independence Smoke Test and Runbook

**Files:**
- Create: `scripts/smoke_test_mistral_evaluation_keys.py`
- Create: `tests/unit/scripts/test_smoke_test_mistral_evaluation_keys.py`
- Modify: `evaluations/MEMORIES/RUNBOOK.md`
- Modify: `tests/README.md`

**Interfaces:**
- Consumes: Task 3 pool/factory and safe provider attempt events.
- Produces: CLI metadata report containing alias, status class, latency, and cross-key `429` timing only.
- Produces: operator gate before enabling more than one worker.

- [ ] **Step 1: Write failing smoke-script tests with fake transport**

Assert one simultaneous request per selected alias, `--workers` bounded by discovered keys, no prompt/reply/key in JSON or stderr, and exit `1` when cross-key `429` behavior does not demonstrate independence.

- [ ] **Step 2: Implement the opt-in smoke command**

```powershell
uv run python scripts/smoke_test_mistral_evaluation_keys.py --workers 3 --output .data/evaluation-key-smoke.json
```

Use one fixed synthetic non-private prompt, ordinary chat completions, and no retries. The report says whether independence was demonstrated; it never claims that configuration alone proves it.

- [ ] **Step 3: Update the runbook and invariant ownership**

Document key naming, smoke-test gate, `--max-workers`, SQLite-only parallelism, PostgreSQL concurrency `1`, warning interpretation, private artifact paths, and cleanup expectations. Add test ownership rows so API redaction, lease secrecy, shard isolation, and baseline privacy are asserted once at their lowest useful layer.

- [ ] **Step 4: Verify ignore rules are real**

```powershell
git check-ignore .data/evaluation-jobs.db .data/evaluation-key-smoke.json evaluations/MEMORIES/runs/example/private.json
git status --short
```

Expected: all generated/private examples are ignored; only intended source/docs/tests are visible.

- [ ] **Step 5: Run R9 plus smoke tests**

```powershell
uv run pytest -q tests/unit/scripts/test_smoke_test_mistral_evaluation_keys.py tests/unit/scripts/test_evaluate_memory.py tests/unit/features/batch_evaluation
uv run ruff check scripts/smoke_test_mistral_evaluation_keys.py tests/unit/scripts/test_smoke_test_mistral_evaluation_keys.py
uv run mypy src
```

- [ ] **Step 6: Commit smoke validation and runbook**

```powershell
git add scripts/smoke_test_mistral_evaluation_keys.py tests/unit/scripts/test_smoke_test_mistral_evaluation_keys.py evaluations/MEMORIES/RUNBOOK.md tests/README.md
git commit -m "test: add evaluation key independence smoke gate"
```

---

### Task 11.5: Behavior-Preserving Code Simplification

**Files:**
- Modify only batch-evaluation, memory-evaluation, API, bootstrap, and smoke-test files added or materially changed by Tasks 1–11.
- Do not modify unrelated providers, frontend code, SQL migrations, or deferred RAGAS production code.

**Interfaces:**
- Consumes: the verified implementation from Tasks 1–11.
- Preserves: every public contract, runtime behavior, report schema, persistence transition, warning code, cleanup guarantee, and test expectation.
- Produces: smaller and clearer implementation code with no feature expansion.

- [ ] **Step 1: Invoke `agent-skills:code-simplification` and identify narrow opportunities**

Restrict review to this branch's newly implemented or materially changed files. Prefer clearer names, flatter control flow, removal of local duplication, and smaller private helpers. Reject any suggestion that changes behavior or public interfaces.

- [ ] **Step 2: Apply simplifications in separate refactor commits**

Keep behavior-preserving cleanup separate from feature/fix commits so it can be reviewed or reverted independently. Do not rewrite tests merely to accommodate a changed contract.

- [ ] **Step 3: Re-run unchanged focused tests and static checks**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation tests/unit/features/ai_chat/memory_eval tests/unit/integrations/llm/test_evaluation_mistral.py tests/unit/persistence/test_evaluation_job_repository.py tests/unit/scripts/test_evaluate_memory.py tests/unit/scripts/test_smoke_test_mistral_evaluation_keys.py tests/integration/api/test_evaluation_jobs_api.py
uv run ruff check .
uv run mypy src
```

Expected: the existing tests pass unchanged; no baseline/report/API output changes.

- [ ] **Step 4: Review the simplification diff**

Use a fresh code review focused on accidental behavior changes, obscured ownership boundaries, and deleted edge-case handling. Route any defect back to Task 11.5 and repeat the focused checks.

---

### Task 12: End-to-End Acceptance and Final Quality Gate

**Files:**
- Modify only if an acceptance failure requires an in-scope correction; do not perform unrelated cleanup.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: evidence that core + memory meet the SPEC without a real RAGAS plug-in.

- [ ] **Step 1: Run focused acceptance scenarios with fakes**

```powershell
uv run pytest -q tests/unit/features/batch_evaluation tests/unit/features/ai_chat/memory_eval tests/unit/integrations/llm/test_evaluation_mistral.py tests/unit/persistence/test_evaluation_job_repository.py tests/unit/scripts/test_evaluate_memory.py tests/integration/api/test_evaluation_jobs_api.py
```

Verify one-key serial, three-key concurrent, four-requested/three-effective warning, cooldown, disablement, cancellation, restart recovery, partial success, deterministic order, secret redaction, and SQLite cleanup.

- [ ] **Step 2: Run the complete backend quality gate**

```powershell
uv run ruff check .
uv run mypy src
uv run pytest -q
```

Expected: all commands exit `0`; the final pytest banner still names live tests as deselected.

- [ ] **Step 3: Perform a no-network dry-run through the memory CLI**

```powershell
uv run python scripts/evaluate_memory.py --dry-run --probe-set evaluations/MEMORIES/probes/v1-four-scopes.json
```

Expected: exit `0`, unchanged memory report schema, metadata-only stdout, and no new tracked artifact.

- [ ] **Step 4: Review SPEC coverage**

Check every acceptance criterion in `SPEC-pluggable-batch-evaluation-api.md`. Mark real Chat RAGAS implementation criteria deferred by the user's scope note; confirm their core prerequisites are covered by the fake `request_batch` stress-contract test. Record any genuine core/memory gap before claiming completion.

- [ ] **Step 5: Review the final diff**

```powershell
git diff --check
git status --short
git diff --stat
```

Confirm no SQL migration, frontend file, private artifact, raw key, RAGAS production plug-in, or unrelated user change entered the implementation.

- [ ] **Step 6: Route any acceptance correction back to its owning task**

Task 12 creates no acceptance-only commit. If review finds a gap, return to the task that owns the affected files, add the missing red-green test there, rerun that task's focused checks, and amend that task's commit before repeating this acceptance checkpoint.

## Dependency Order

```text
Task 1 contracts/registry
  ├─> Task 2 planning/queue
  ├─> Task 3 credentials/Mistral observation
  └─> Task 4 job/artifact persistence
         └─> Task 5 service/runner
                └─> Task 6 supervisor/recovery
Task 7 memory row/live extraction
  └─> Task 8 memory plug-in
         └─> Task 9 runtime/CLI
                └─> Task 10 API/app wiring
Task 3 ─> Task 11 smoke gate
Tasks 1–11 ─> Task 11.5 code simplification
Task 11.5 ─> Task 12 acceptance
```

Tasks 2, 3, 4, and 7 are file-disjoint after Task 1 and can be implemented in parallel. Tasks 5–6 and Tasks 8–10 are sequential dependency chains. The parent agent retains ownership of SPEC alignment, cross-task interface consistency, final integration, and Definition of Done.

## SPEC Coverage Self-Review

| SPEC requirement group | Owning tasks | First-delivery status |
|---|---|---|
| Static registry, one-type job API, validation, idempotency, safe errors | 1, 5, 10 | Planned |
| Durable job/unit/attempt/step state, result manifests, restart recovery | 4–6 | Planned |
| Dynamic `max_workers`, reduction warning, bounded pull queue, fixed shards | 2, 5, 9 | Planned |
| Exclusive Mistral leases, cooldown/disablement, retry and budget accounting | 3, 5, 11 | Planned |
| Cancellation, honest partial success, cleanup before terminal state | 5–8, 10 | Planned |
| SQLite memory shard isolation, three-arm order, deterministic report semantics | 7–9 | Planned |
| Metadata-only public artifacts and private memory transcripts | 1, 4, 8, 10–11 | Planned |
| `request_batch` pluggability and sequential steps inside one stateless case | 2, 5 | Planned with a fake RAGAS-shaped plug-in |
| Production Chat RAGAS sample/metric adapter | Separate future plan | Deferred by the approved core/memory scope |

Task 11.5 changes no SPEC coverage; it is a behavior-preserving maintainability gate. The self-review found no uncovered core or memory acceptance requirement. The only deliberately uncovered SPEC criteria are the real Chat RAGAS adapter assertions; the fake stateless contract covers the shared Level 1 behavior they depend on without pretending RAGAS is implemented.

## Checkpoints

### Checkpoint A — After Tasks 1–4

- [ ] Contracts, resolution, queue, leasing, persistence, and artifact tests pass offline.
- [ ] No raw secret or private evaluation content is representable in safe records.
- [ ] No product SQL migration exists.

### Checkpoint B — After Tasks 5–6

- [ ] Fake `request_batch` work proves dynamic pulling, sequential internal steps, deterministic output, and healthy-lane continuation.
- [ ] Fixed workflow shards retain leases and never steal work.
- [ ] Cancellation and recovery preserve unknown-attempt safety.

### Checkpoint C — After Tasks 7–9

- [ ] Existing serial memory tests remain green.
- [ ] Complete probes remain together and shard state is isolated.
- [ ] Mistral memory CLI supports dynamic workers and warning downgrade.

### Checkpoint D — After Tasks 10–12

- [ ] API contract, authentication, idempotency, status, result, cancellation, and safe errors pass R11.
- [ ] Smoke gate and runbook explain when multi-key execution is allowed.
- [ ] Ruff, mypy, and the full non-live pytest suite pass.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Current memory CLI orchestration is private script code | High | Extract a typed live-shard seam first and keep a compatibility wrapper with characterization tests. |
| Shard reports cannot be safely merged after aggregation | High | Merge `ProbeRow` values by original probe order, then call existing `build_report()` exactly once. |
| Mistral chat wrapper hides HTTP status behind safe exceptions | High | Preserve safe status metadata on the cause and emit attempt events from an evaluation-only wrapper. |
| Concurrent shards collide in SQLite or identity state | High | Use job/shard/attempt-specific paths plus existing per-run nonce and per-arm identity; assert all isolation fields. |
| Crash recovery duplicates paid calls | High | Mark orphaned attempts `unknown`; replay only explicitly safe attempts under budget. |
| Key count is mistaken for quota independence | Medium | Require opt-in concurrent smoke evidence and expose provider-wide throttling in the manifest. |
| Core accidentally becomes RAGAS-specific | Medium | Use only a fake stateless plug-in in core tests; defer real RAGAS code. |
| API validation changes unrelated endpoints | Medium | Parse and envelope errors inside the evaluation router instead of installing global handlers. |
| Private content enters control-plane persistence | High | Store only stable ids/ordinals/safe classifications; enforce recursive metadata validation. |
| Implementation expands into distributed infrastructure | Medium | Keep in-process asyncio supervision, dedicated SQLite, and narrow ports; no Redis/Celery in Level 1. |

## Scope Decision

This plan intentionally stops after the reusable core, production memory-evaluation plug-in, and fake stateless stress contract. The real Chat RAGAS adapter remains governed by `SPEC-chat-ragas-evaluation.md` and receives a separate implementation plan after its runtime and dependency contract are ready.
