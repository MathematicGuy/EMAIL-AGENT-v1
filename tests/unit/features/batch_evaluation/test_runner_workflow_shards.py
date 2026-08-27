from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
from cowork_agent.features.ai_chat.memory_eval.live_execution import MemoryShardResult
from cowork_agent.features.ai_chat.memory_eval.report import ProbeRow
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.batch_evaluation.artifacts import FilesystemEvaluationArtifactStore
from cowork_agent.features.batch_evaluation.contracts import (
    ArtifactBundle,
    CleanupOutcome,
    EvaluationBudget,
    EvaluationRequest,
    ExecutionMode,
    FailureClass,
    FailureClassification,
    JobState,
    PluginPlan,
    UnitState,
    WorkContext,
    WorkUnit,
    WorkUnitOutcome,
)
from cowork_agent.features.batch_evaluation.credentials import CredentialLeasingPool
from cowork_agent.features.batch_evaluation.plugins import memory_eval
from cowork_agent.features.batch_evaluation.plugins.memory_eval import MemoryEvalPlugin
from cowork_agent.features.batch_evaluation.registry import PluginRegistry
from cowork_agent.features.batch_evaluation.runner import EvaluationJobRunner
from cowork_agent.features.batch_evaluation.service import EvaluationJobService
from cowork_agent.persistence.repositories.evaluation_jobs import (
    EvaluationJob,
    SQLiteEvaluationJobRepository,
)


class RetryableFailure(RuntimeError):
    pass


class FakeReplyFactory:
    max_output_tokens = 1

    def __init__(self) -> None:
        self.bound_aliases: list[str] = []
        self.bound_settled: list[bool] = []
        self.bound_active: list[bool] = []

    def bind(self, lease: object, model: str, attempt_sink: object) -> object:
        del model, attempt_sink
        alias = lease.alias
        self.bound_aliases.append(alias)
        self.bound_settled.append(lease._settled)
        self.bound_active.append(lease._record.active_lease is lease)
        return object()


class ShardPlugin:
    evaluation_type = "shard-eval"
    version = "1"
    supported_modes = frozenset({ExecutionMode.WORKFLOW_SHARDS})
    parameter_schema: Mapping[str, object] = {"type": "object"}

    def __init__(self) -> None:
        self.executions: list[tuple[str, int]] = []
        self.contexts: list[tuple[int, WorkContext]] = []
        self._fail_once = True
        self.build_lane_counts: list[int] = []
        self.aggregate_outcomes: tuple[WorkUnitOutcome, ...] = ()

    async def preflight(self, request: EvaluationRequest) -> PluginPlan:
        return PluginPlan(request.dataset_ref, 4, object())

    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]:
        del plan
        self.build_lane_counts.append(lane_count)
        return tuple(
            WorkUnit(
                unit_id=f"unit-{ordinal}",
                ordinal=ordinal,
                payload={"probe_id": f"probe-{ordinal}"},
            )
            for ordinal in range(4)
        )

    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome:
        self.contexts.append((unit.ordinal, context))
        self.executions.append((context.credential_alias, unit.ordinal))
        if unit.ordinal == 0 and self._fail_once:
            self._fail_once = False
            raise RetryableFailure()
        return WorkUnitOutcome(
            unit_id=unit.unit_id,
            ordinal=unit.ordinal,
            state=UnitState.SUCCEEDED,
            provider_requests=0,
            total_tokens=0,
            private_result=None,
        )

    def aggregate(self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]) -> ArtifactBundle:
        del plan
        self.aggregate_outcomes = tuple(outcomes)
        return ArtifactBundle(
            public_result={"ordinals": tuple(outcome.ordinal for outcome in outcomes)},
            private_artifact_ids=(),
        )

    async def cleanup(self, context: WorkContext) -> CleanupOutcome:
        del context
        return CleanupOutcome(removed_resources=1, warnings=())

    def classify_failure(self, error: BaseException) -> FailureClassification:
        return FailureClassification(
            failure_class=FailureClass.PROVIDER,
            retryable=isinstance(error, RetryableFailure),
            credential_state=None,
        )


class TrackingRepository(SQLiteEvaluationJobRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.claimed_unit_ids: list[str] = []
        self.claimed_assignments: list[tuple[str, str]] = []

    async def claim_ready_unit(self, job_id: str, worker_id: str) -> WorkUnit | None:
        del job_id, worker_id
        raise AssertionError("fixed workflow shards must never claim from the global queue")

    async def claim_ready_unit_by_id(
        self, job_id: str, unit_id: str, worker_id: str
    ) -> WorkUnit | None:
        self.claimed_unit_ids.append(unit_id)
        self.claimed_assignments.append((unit_id, worker_id))
        return await super().claim_ready_unit_by_id(job_id, unit_id, worker_id)


class TrackingCredentialPool(CredentialLeasingPool):
    def __init__(self) -> None:
        super().__init__(
            "MISTRAL_API_KEY",
            ("secret-one", "secret-two"),
            clock=lambda: 0.0,
        )
        self.leased_aliases: list[str] = []

    async def lease(self):  # type: ignore[no-untyped-def]
        lease = await super().lease()
        self.leased_aliases.append(lease.alias)
        return lease


def request() -> EvaluationRequest:
    return EvaluationRequest(
        evaluation_type="shard-eval",
        provider="mistral",
        target_model="small-model",
        dataset_ref="dataset-v1",
        credential_pool="mistral-eval",
        execution_mode=ExecutionMode.WORKFLOW_SHARDS,
        max_workers=2,
        max_attempts_per_unit=2,
        budget=EvaluationBudget(max_provider_requests=20, max_total_tokens=20),
        parameters={},
    )


@dataclass(frozen=True, slots=True)
class MemoryRunHarness:
    artifacts: FilesystemEvaluationArtifactStore
    repository: TrackingRepository
    pool: TrackingCredentialPool
    factory: FakeReplyFactory
    runner: EvaluationJobRunner
    job: EvaluationJob
    observed_probe_ids: list[tuple[str, ...]]


async def prepare_memory_recovery_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_workers: int = 2,
) -> MemoryRunHarness:
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    repository = TrackingRepository(tmp_path / "evaluation-jobs.db")
    await repository.initialize()
    registry = PluginRegistry()
    plugin = MemoryEvalPlugin(
        environment_resolver=lambda: LiveEnvironment(
            postgres_url=None,
            sqlite_path=Path("sqlite-template.db"),
            gemini_ready=True,
            embeddings_ready=True,
            embedding_key_name="GEMINI_API_KEY",
        )
    )
    registry.register(plugin)
    pool = TrackingCredentialPool()
    factory = FakeReplyFactory()
    service = EvaluationJobService(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
    )
    runner = EvaluationJobRunner(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        scratch_root=tmp_path / "scratch",
        reply_factory=factory,
    )
    observed_probe_ids: list[tuple[str, ...]] = []

    async def fake_execute_memory_shard(
        probe_set: object,
        environment: LiveEnvironment,
        reply: object,
        *,
        report_nonce: str,
        **_: object,
    ) -> MemoryShardResult:
        del environment, reply
        observed_probe_ids.append(
            tuple(probe.probe_id for probe in probe_set.probes)  # type: ignore[union-attr]
        )
        return MemoryShardResult(
            rows=tuple(
                ProbeRow(
                    probe_id=probe.probe_id,
                    targets=probe.targets,
                    test=probe.test,
                    full=Outcome.PASS,
                    ablated=Outcome.MISS,
                    control=Outcome.MISS,
                    certain=True,
                    latency_ms=1,
                )
                for probe in probe_set.probes  # type: ignore[union-attr]
            ),
            seed_failure_ids=(),
            private_transcript=(
                {"question": "private recovery prompt", "reply": "private recovery reply"},
            ),
            nonce=f"identity-{len(observed_probe_ids)}",
            provider_findings=(),
            scratch_removed=True,
            report_nonce=report_nonce,
        )

    monkeypatch.setattr(memory_eval, "execute_memory_shard", fake_execute_memory_shard)
    job = await service.submit(
        EvaluationRequest(
            evaluation_type="memory-eval",
            provider="mistral",
            target_model="mistral-small-latest",
            dataset_ref="v1-four-scopes",
            credential_pool="mistral-eval",
            execution_mode=ExecutionMode.WORKFLOW_SHARDS,
            max_workers=max_workers,
            max_attempts_per_unit=1,
            budget=EvaluationBudget(max_provider_requests=300, max_total_tokens=300_000),
            parameters={},
        ),
        idempotency_key=f"memory-recovery-{max_workers}",
    )
    return MemoryRunHarness(
        artifacts=artifacts,
        repository=repository,
        pool=pool,
        factory=factory,
        runner=runner,
        job=job,
        observed_probe_ids=observed_probe_ids,
    )


@pytest.mark.asyncio
async def test_fixed_shards_keep_one_lease_execute_assigned_work_sequentially_and_retry_fresh(
    tmp_path: Path,
) -> None:
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    repository = TrackingRepository(tmp_path / "evaluation-jobs.db")
    await repository.initialize()
    registry = PluginRegistry()
    plugin = ShardPlugin()
    registry.register(plugin)
    pool = TrackingCredentialPool()
    factory = FakeReplyFactory()
    service = EvaluationJobService(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
    )
    runner = EvaluationJobRunner(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        scratch_root=tmp_path / "scratch",
        reply_factory=factory,
    )
    job = await service.submit(request(), idempotency_key="shards-key")

    await runner.run(job.job_id)

    assert plugin.build_lane_counts == [2, 2]
    assert sorted(repository.claimed_unit_ids) == ["unit-0", "unit-1", "unit-2", "unit-3"]
    assert len(pool.leased_aliases) == 2
    assert len(factory.bound_aliases) == 5
    assert factory.bound_settled == [False] * 5
    assert factory.bound_active == [True] * 5
    by_alias = {
        alias: [
            ordinal for execution_alias, ordinal in plugin.executions if execution_alias == alias
        ]
        for alias in factory.bound_aliases
    }
    assert {frozenset(ordinals) for ordinals in by_alias.values()} == {
        frozenset({0, 2}),
        frozenset({1, 3}),
    }
    attempts = await repository.list_attempts(job.job_id, "unit-0")
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    unit_zero_contexts = [
        context
        for ordinal, context in plugin.contexts
        if ordinal == 0 and context.job_id == job.job_id
    ]
    assert len({context.attempt_id for context in unit_zero_contexts}) >= 2
    assert len({context.scratch_dir for context in unit_zero_contexts}) >= 2
    assert [outcome.ordinal for outcome in plugin.aggregate_outcomes] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_missing_grouped_memory_unit_is_failed_without_reexecution_or_private_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted_unit_id = "memory-shard-1"
    remaining_slice = slice(0, None, 2)
    expected_lane = "lane-1"
    harness = await prepare_memory_recovery_run(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "evaluation-jobs.db") as database:
        database.execute(
            "DELETE FROM evaluation_units WHERE job_id = ? AND unit_id = ?",
            (harness.job.job_id, deleted_unit_id),
        )

    await harness.runner.run(harness.job.job_id)

    canonical = memory_eval.MemoryProbeCatalog().resolve("v1-four-scopes")
    assert harness.observed_probe_ids == [
        tuple(probe.probe_id for probe in canonical.probes[remaining_slice])
    ]
    remaining_unit_id = "memory-shard-0"
    assert harness.repository.claimed_unit_ids == [remaining_unit_id]
    assert harness.repository.claimed_assignments == [(remaining_unit_id, expected_lane)]
    assert len(harness.pool.leased_aliases) == 1
    assert len(harness.factory.bound_aliases) == 1
    terminal = await harness.repository.get_job(harness.job.job_id)
    assert terminal is not None
    assert terminal.state.value == "partially_succeeded"
    manifest = harness.artifacts.read_manifest(
        harness.artifacts.manifest_reference(harness.job.job_id)
    )
    assert manifest["aborted"] is True
    assert manifest["execution_manifest"]["completed_probe_count"] == 4
    assert manifest["execution_manifest"]["missing_probe_count"] == 4
    assert manifest["execution_manifest"]["failed_unit_count"] == 1
    shard_states = {
        shard["unit_id"]: shard["state"] for shard in manifest["execution_manifest"]["shards"]
    }
    assert shard_states == {remaining_unit_id: "succeeded", deleted_unit_id: "failed"}
    serialized = str(manifest)
    assert "private recovery prompt" not in serialized
    assert "private recovery reply" not in serialized


@pytest.mark.asyncio
async def test_all_missing_memory_units_fail_without_claiming_or_spending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await prepare_memory_recovery_run(
        tmp_path,
        monkeypatch,
        max_workers=2,
    )
    with sqlite3.connect(tmp_path / "evaluation-jobs.db") as database:
        database.execute(
            "DELETE FROM evaluation_units WHERE job_id = ?",
            (harness.job.job_id,),
        )

    await harness.runner.run(harness.job.job_id)

    assert harness.observed_probe_ids == []
    assert harness.repository.claimed_unit_ids == []
    assert harness.pool.leased_aliases == []
    assert harness.factory.bound_aliases == []
    terminal = await harness.repository.get_job(harness.job.job_id)
    assert terminal is not None
    assert terminal.state.value == "failed"
    manifest = harness.artifacts.read_manifest(
        harness.artifacts.manifest_reference(harness.job.job_id)
    )
    assert manifest["aborted"] is True
    assert manifest["execution_manifest"]["completed_probe_count"] == 0
    assert manifest["execution_manifest"]["missing_probe_count"] == 8
    assert manifest["execution_manifest"]["failed_unit_count"] == 2
    assert all(shard["state"] == "failed" for shard in manifest["execution_manifest"]["shards"])


@pytest.mark.asyncio
async def test_invalid_durable_memory_unit_fails_closed_without_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for corruption in ("payload", "ordinal", "unexpected"):
        harness = await prepare_memory_recovery_run(tmp_path / corruption, monkeypatch)
        with sqlite3.connect((tmp_path / corruption) / "evaluation-jobs.db") as database:
            if corruption == "payload":
                database.execute(
                    "UPDATE evaluation_units SET safe_payload_json = ? "
                    "WHERE job_id = ? AND unit_id = ?",
                    (
                        '{"probe_ids":["st_recall_01"],"ordinals":[0]}',
                        harness.job.job_id,
                        "memory-shard-0",
                    ),
                )
            elif corruption == "ordinal":
                database.execute(
                    "UPDATE evaluation_units SET ordinal = 1 WHERE job_id = ? AND unit_id = ?",
                    (harness.job.job_id, "memory-shard-0"),
                )
            else:
                database.execute(
                    "INSERT INTO evaluation_units ("
                    "job_id, unit_id, ordinal, state, claimed_by, provider_requests, "
                    "total_tokens, outcome_ref, safe_payload_json"
                    ") VALUES (?, ?, 2, 'ready', NULL, 0, 0, NULL, ?)",
                    (
                        harness.job.job_id,
                        "memory-shard-extra",
                        '{"probe_ids":["st_recall_01"],"ordinals":[0]}',
                    ),
                )

        await harness.runner.run(harness.job.job_id)

        assert harness.observed_probe_ids == []
        assert harness.repository.claimed_unit_ids == []
        assert harness.pool.leased_aliases == []
        assert harness.factory.bound_aliases == []
        terminal = await harness.repository.get_job(harness.job.job_id)
        assert terminal is not None
        assert terminal.state.value == "failed"
        manifest = harness.artifacts.read_manifest(
            harness.artifacts.manifest_reference(harness.job.job_id)
        )
        assert manifest == {"state": "failed"}


@pytest.mark.asyncio
async def test_corrupt_collecting_workflow_recovery_reaches_failed_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = await prepare_memory_recovery_run(tmp_path, monkeypatch)
    await harness.repository.transition_job(harness.job.job_id, JobState.RUNNING)
    await harness.repository.transition_job(harness.job.job_id, JobState.COLLECTING)
    with sqlite3.connect(tmp_path / "evaluation-jobs.db") as database:
        database.execute(
            "UPDATE evaluation_units SET safe_payload_json = ? WHERE job_id = ? AND unit_id = ?",
            (
                '{"probe_ids":["st_recall_01"],"ordinals":[0]}',
                harness.job.job_id,
                "memory-shard-0",
            ),
        )

    await harness.runner.run(harness.job.job_id)

    assert harness.observed_probe_ids == []
    assert harness.pool.leased_aliases == []
    terminal = await harness.repository.get_job(harness.job.job_id)
    assert terminal is not None
    assert terminal.state.value == "failed"


@pytest.mark.asyncio
async def test_memory_plugin_runner_keeps_concurrent_shard_state_private_and_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = FilesystemEvaluationArtifactStore(tmp_path / "artifacts")
    repository = TrackingRepository(tmp_path / "evaluation-jobs.db")
    await repository.initialize()
    registry = PluginRegistry()
    plugin = MemoryEvalPlugin(
        environment_resolver=lambda: LiveEnvironment(
            postgres_url=None,
            sqlite_path=Path("sqlite-template.db"),
            gemini_ready=True,
            embeddings_ready=True,
            embedding_key_name="GEMINI_API_KEY",
        )
    )
    registry.register(plugin)
    pool = TrackingCredentialPool()
    factory = FakeReplyFactory()
    service = EvaluationJobService(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
    )
    runner = EvaluationJobRunner(
        registry=registry,
        repository=repository,
        credential_pool=pool,
        artifact_store=artifacts,
        scratch_root=tmp_path / "scratch",
        reply_factory=factory,
    )
    observed: list[dict[str, object]] = []

    async def fake_execute_memory_shard(
        probe_set: object,
        environment: LiveEnvironment,
        reply: object,
        *,
        model: str,
        report_nonce: str,
        **_: object,
    ) -> MemoryShardResult:
        assert environment.sqlite_path is not None
        environment.sqlite_path.touch()
        probe = probe_set.probes[0]  # type: ignore[union-attr]
        identity_nonce = uuid4().hex
        output = environment.sqlite_path.parent / "output.json"
        output.write_text("private output", encoding="utf-8")
        transcript = {"question": "private prompt", "reply": "private reply"}
        observed.append(
            {
                "database": environment.sqlite_path,
                "ordinals": tuple(
                    index
                    for index, candidate in enumerate(
                        memory_eval.MemoryProbeCatalog().resolve("v1-four-scopes").probes
                    )
                    if candidate.probe_id in {item.probe_id for item in probe_set.probes}
                ),
                "tenant": f"tenant-{identity_nonce}",
                "user": f"user-{identity_nonce}",
                "session": f"session-{identity_nonce}-{probe.probe_id}",
                "nonce": identity_nonce,
                "transcript": transcript,
                "output": output,
                "reply": reply,
                "report_nonce": report_nonce,
            }
        )
        environment.sqlite_path.unlink()
        return MemoryShardResult(
            rows=tuple(
                ProbeRow(
                    probe_id=item.probe_id,
                    targets=item.targets,
                    test=item.test,
                    full=Outcome.PASS,
                    ablated=Outcome.MISS,
                    control=Outcome.MISS,
                    certain=True,
                    latency_ms=1,
                )
                for item in probe_set.probes  # type: ignore[union-attr]
            ),
            seed_failure_ids=(),
            private_transcript=(transcript,),
            nonce=identity_nonce,
            provider_findings=(),
            scratch_removed=True,
            report_nonce=report_nonce,
        )

    monkeypatch.setattr(memory_eval, "execute_memory_shard", fake_execute_memory_shard)
    job = await service.submit(
        EvaluationRequest(
            evaluation_type="memory-eval",
            provider="mistral",
            target_model="mistral-small-latest",
            dataset_ref="v1-four-scopes",
            credential_pool="mistral-eval",
            execution_mode=ExecutionMode.WORKFLOW_SHARDS,
            max_workers=2,
            max_attempts_per_unit=1,
            budget=EvaluationBudget(max_provider_requests=300, max_total_tokens=300_000),
            parameters={},
        ),
        idempotency_key="memory-shards-key",
    )

    await runner.run(job.job_id)

    assert len(observed) == 2
    assert {entry["ordinals"] for entry in observed} == {
        (0, 2, 4, 6),
        (1, 3, 5, 7),
    }
    for key in ("database", "tenant", "user", "session", "nonce", "transcript", "output", "reply"):
        values = [entry[key] for entry in observed]
        unique_values = {id(value) if key in {"transcript", "reply"} else value for value in values}
        assert len(unique_values) == len(values)
    assert len({entry["report_nonce"] for entry in observed}) == 1
    assert all(not Path(entry["output"]).exists() for entry in observed)
    assert not list((tmp_path / "scratch").iterdir())
    manifest = artifacts.read_manifest(artifacts.manifest_reference(job.job_id))
    serialized = str(manifest)
    assert "private prompt" not in serialized
    assert "private reply" not in serialized
    assert "output.json" not in serialized
    private_files = list(
        (tmp_path / "artifacts" / ".runtime" / "evaluation-artifacts" / "private").rglob("*.json")
    )
    assert private_files
    assert "private prompt" in private_files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_memory_plugin_runner_never_succeeds_with_aborted_or_unclean_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for idx, (failures, expected_state) in enumerate(
        [
            ({0: "aborted"}, "partially_succeeded"),
            ({0: "aborted", 1: "cleanup"}, "failed"),
        ]
    ):
        base_dir = tmp_path / f"case_{idx}"
        artifacts = FilesystemEvaluationArtifactStore(base_dir / "artifacts")
        repository = TrackingRepository(base_dir / "evaluation-jobs.db")
        await repository.initialize()
        registry = PluginRegistry()
        plugin = MemoryEvalPlugin(
            environment_resolver=lambda: LiveEnvironment(
                postgres_url=None,
                sqlite_path=Path("sqlite-template.db"),
                gemini_ready=True,
                embeddings_ready=True,
                embedding_key_name="GEMINI_API_KEY",
            )
        )
        registry.register(plugin)
        pool = TrackingCredentialPool()
        service = EvaluationJobService(
            registry=registry,
            repository=repository,
            credential_pool=pool,
            artifact_store=artifacts,
        )
        runner = EvaluationJobRunner(
            registry=registry,
            repository=repository,
            credential_pool=pool,
            artifact_store=artifacts,
            scratch_root=base_dir / "scratch",
            reply_factory=FakeReplyFactory(),
        )
        canonical = memory_eval.MemoryProbeCatalog().resolve("v1-four-scopes")
        probe_ordinals = {probe.probe_id: index for index, probe in enumerate(canonical.probes)}

        def make_fake_executor(ords: dict[str, int], fails: dict[int, str]):
            async def fake_execute_memory_shard(
                probe_set: object,
                environment: LiveEnvironment,
                reply: object,
                *,
                report_nonce: str,
                **_: object,
            ) -> MemoryShardResult:
                del reply
                assert environment.sqlite_path is not None
                lane = ords[probe_set.probes[0].probe_id]  # type: ignore[union-attr]
                failure = fails.get(lane)
                transcript = ({"question": "private failure", "reply": "private failure"},)
                return MemoryShardResult(
                    rows=tuple(
                        ProbeRow(
                            probe_id=probe.probe_id,
                            targets=probe.targets,
                            test=probe.test,
                            full=Outcome.PASS,
                            ablated=Outcome.MISS,
                            control=Outcome.MISS,
                            certain=True,
                            latency_ms=1,
                        )
                        for probe in probe_set.probes  # type: ignore[union-attr]
                    ),
                    seed_failure_ids=(),
                    private_transcript=transcript,
                    nonce=f"identity-{lane}",
                    provider_findings=("aborted: provider limit",) if failure == "aborted" else (),
                    scratch_removed=failure != "cleanup",
                    report_nonce=report_nonce,
                )

            return fake_execute_memory_shard

        monkeypatch.setattr(
            memory_eval,
            "execute_memory_shard",
            make_fake_executor(probe_ordinals, failures),
        )
        job = await service.submit(
            EvaluationRequest(
                evaluation_type="memory-eval",
                provider="mistral",
                target_model="mistral-small-latest",
                dataset_ref="v1-four-scopes",
                credential_pool="mistral-eval",
                execution_mode=ExecutionMode.WORKFLOW_SHARDS,
                max_workers=2,
                max_attempts_per_unit=1,
                budget=EvaluationBudget(max_provider_requests=300, max_total_tokens=300_000),
                parameters={},
            ),
            idempotency_key=f"memory-failure-{expected_state}-{idx}",
        )

        await runner.run(job.job_id)

        terminal = await repository.get_job(job.job_id)
        assert terminal is not None
        assert terminal.state.value == expected_state
        manifest = artifacts.read_manifest(artifacts.manifest_reference(job.job_id))
        assert manifest["aborted"] is True
        assert manifest["execution_manifest"]["failed_unit_count"] == len(failures)
        serialized = str(manifest)
        assert "private failure" not in serialized
        assert all(
            set(shard) == {"unit_id", "ordinal", "state"}
            for shard in manifest["execution_manifest"]["shards"]
        )
