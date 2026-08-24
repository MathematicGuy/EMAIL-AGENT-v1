"""Memory-evaluation semantics for the durable workflow-shard runner."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import cast

from cowork_agent.domain.chat_contracts import MemoryType
from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment, probe_environment
from cowork_agent.features.ai_chat.memory_eval.live_execution import (
    MemoryShardResult,
    build_memory_report,
    execute_memory_shard,
)
from cowork_agent.features.ai_chat.memory_eval.probes import (
    EpisodeSeed,
    Probe,
    ProbeSet,
    ProbeTest,
    SeedSpec,
    load_probe_set,
)
from cowork_agent.features.ai_chat.memory_eval.report import ProbeRow
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.ai_chat.ports import ChatReplyPort

from ..contracts import (
    ArtifactBundle,
    CleanupOutcome,
    EvaluationPlugin,
    EvaluationRequest,
    ExecutionMode,
    FailureClass,
    FailureClassification,
    PluginPlan,
    UnitState,
    WorkContext,
    WorkUnit,
    WorkUnitOutcome,
    canonical_request_hash,
)

_CANONICAL_PROBE_FILES = {
    "v1-four-scopes": ("v1-four-scopes.json", "v1_four_scopes"),
    "v2-four-scopes-wide": ("v2-four-scopes-wide.json", "v2_four_scopes_wide"),
    "v3-four-scopes-hard": ("v3-four-scopes-hard.json", "v3_four_scopes_hard"),
}
_PRIVATE_RESULT_SCHEMA_VERSION = "1"
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_PROBES_DIR = _PROJECT_ROOT / "evaluations" / "MEMORIES" / "probes"


class MemoryProbeCatalog:
    """Load only named, repository-owned memory probe sets."""

    def __init__(self, probes_dir: Path = _DEFAULT_PROBES_DIR) -> None:
        self._probes_dir = probes_dir.resolve()

    def resolve(self, dataset_ref: str) -> ProbeSet:
        """Return one canonical probe set without accepting a submitted path."""

        if not isinstance(dataset_ref, str) or not dataset_ref:
            raise ValueError("memory dataset_ref must be a canonical identifier")
        if any(part in dataset_ref for part in ("/", "\\", "..")):
            raise ValueError("memory dataset_ref must not contain a path")
        entry = _CANONICAL_PROBE_FILES.get(dataset_ref)
        if entry is None:
            raise ValueError("memory dataset_ref is not a canonical probe set")
        filename, expected_probe_set_id = entry
        path = self._contained_file(filename)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("canonical memory probe set is unreadable") from error
        if not isinstance(payload, Mapping):
            raise ValueError("canonical memory probe set must be an object")
        probe_set = load_probe_set(payload)
        if probe_set.probe_set_id != expected_probe_set_id:
            raise ValueError("canonical memory probe set has an unexpected identifier")
        return probe_set

    def _contained_file(self, filename: str) -> Path:
        candidate = (self._probes_dir / filename).resolve()
        try:
            candidate.relative_to(self._probes_dir)
        except ValueError as error:
            raise ValueError("canonical memory probe set escapes its catalog") from error
        return candidate


@dataclass(frozen=True, slots=True)
class _MemoryEvalPlan:
    probe_set: ProbeSet
    provider: str
    model: str
    environment: LiveEnvironment
    report_nonce: str


@dataclass(frozen=True, slots=True)
class _ExecutionEvidence:
    job_id: str
    attempt_id: str
    lane_id: str
    credential_alias: str


@dataclass(frozen=True, slots=True)
class _DecodedShardResult:
    unit_id: str
    ordinal: int
    result: MemoryShardResult
    execution: _ExecutionEvidence


class MemoryEvalPlugin(EvaluationPlugin):
    """Stateless memory-evaluation plug-in for isolated local shards."""

    evaluation_type = "memory-eval"
    version = "1"
    supported_modes = frozenset({ExecutionMode.WORKFLOW_SHARDS})
    parameter_schema: Mapping[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }

    def __init__(
        self,
        *,
        catalog: MemoryProbeCatalog | None = None,
        environment_resolver: Callable[[], LiveEnvironment] | None = None,
    ) -> None:
        self._catalog = catalog or MemoryProbeCatalog()
        self._environment_resolver = environment_resolver or _default_environment

    async def preflight(self, request: EvaluationRequest) -> PluginPlan:
        """Validate the fixed workflow contract and capture an immutable plan."""

        if request.provider != "mistral":
            raise ValueError("memory evaluation requires the mistral provider")
        if request.execution_mode is not ExecutionMode.WORKFLOW_SHARDS:
            raise ValueError("memory evaluation requires workflow_shards mode")
        if request.parameters:
            raise ValueError("memory evaluation does not accept parameters")
        probe_set = _freeze_probe_set(self._catalog.resolve(request.dataset_ref))
        environment = self._environment_resolver()
        if not isinstance(environment, LiveEnvironment):
            raise TypeError("memory environment resolver must return LiveEnvironment")
        if environment.postgres_url is not None and request.max_workers != 1:
            raise ValueError("PostgreSQL memory evaluation requires max_workers=1")
        if (
            request.max_workers > 1
            and environment.postgres_url is None
            and environment.sqlite_path is None
        ):
            raise ValueError("parallel memory evaluation requires an available SQLite store")
        private_plan = _MemoryEvalPlan(
            probe_set=probe_set,
            provider=request.provider,
            model=request.target_model,
            environment=environment,
            # This derives from immutable request intent so recovery can decode
            # existing durable shards after runner preflight is repeated.
            report_nonce=canonical_request_hash(request)[:32],
        )
        return PluginPlan(
            dataset_ref=request.dataset_ref,
            ready_work=len(probe_set.probes),
            private_plan=private_plan,
        )

    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]:
        memory_plan = _memory_plan(plan)
        shard_count = min(lane_count, len(memory_plan.probe_set.probes))
        if shard_count < 1:
            raise ValueError("memory evaluation requires at least one work lane")
        return tuple(
            WorkUnit(
                unit_id=f"memory-shard-{lane_ordinal}",
                ordinal=lane_ordinal,
                payload={
                    "probe_ids": tuple(
                        probe.probe_id
                        for probe in memory_plan.probe_set.probes[lane_ordinal::shard_count]
                    ),
                    "ordinals": tuple(
                        range(lane_ordinal, len(memory_plan.probe_set.probes), shard_count)
                    ),
                },
            )
            for lane_ordinal in range(shard_count)
        )

    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome:
        memory_plan = _memory_plan(context.plugin_plan)
        shard_probe_set = _probe_subset(memory_plan.probe_set, unit)
        environment = _attempt_environment(
            memory_plan.environment,
            context.scratch_dir,
            context.attempt_id,
        )
        result = await execute_memory_shard(
            shard_probe_set,
            environment,
            cast(ChatReplyPort, context.provider_client),
            provider=memory_plan.provider,
            model=memory_plan.model,
            report_nonce=memory_plan.report_nonce,
        )
        if not _is_complete_success(result, shard_probe_set, environment):
            return WorkUnitOutcome(
                unit_id=unit.unit_id,
                ordinal=unit.ordinal,
                state=UnitState.FAILED,
                provider_requests=len(result.private_transcript),
                total_tokens=0,
                private_result=None,
            )
        return WorkUnitOutcome(
            unit_id=unit.unit_id,
            ordinal=unit.ordinal,
            state=UnitState.SUCCEEDED,
            provider_requests=len(result.private_transcript),
            total_tokens=0,
            private_result=_encode_private_result(
                result,
                _ExecutionEvidence(
                    job_id=context.job_id,
                    attempt_id=context.attempt_id,
                    lane_id=context.lane_id,
                    credential_alias=context.credential_alias,
                ),
            ),
        )

    def aggregate(
        self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]
    ) -> ArtifactBundle:
        memory_plan = _memory_plan(plan)
        decoded, failed_units = _successful_shards(outcomes, memory_plan.report_nonce)
        valid, invalid_units = _valid_shards(decoded, memory_plan.probe_set)
        failed_unit_count = failed_units + invalid_units
        completed_probe_count = len(
            {row.probe_id for shard in valid for row in shard.result.rows}
        )
        incomplete = (
            failed_unit_count > 0
            or completed_probe_count != len(memory_plan.probe_set.probes)
        )
        report_inputs = _ordered_report_inputs(valid, memory_plan.probe_set)
        if incomplete or not report_inputs:
            report_inputs.append(
                MemoryShardResult(
                    rows=(),
                    seed_failure_ids=(),
                    private_transcript=(),
                    nonce="incomplete",
                    provider_findings=("aborted: incomplete durable shard outcomes",),
                    scratch_removed=True,
                    report_nonce=memory_plan.report_nonce,
                )
            )
        report = build_memory_report(
            memory_plan.probe_set,
            report_inputs,
            provider=memory_plan.provider,
            model=memory_plan.model,
            ran_at=datetime.now(UTC),
        )
        report["execution_manifest"] = _execution_manifest(
            valid,
            outcomes,
            expected_probe_count=len(memory_plan.probe_set.probes),
            completed_probe_count=completed_probe_count,
            failed_unit_count=failed_unit_count,
        )
        return ArtifactBundle(public_result=report, private_artifact_ids=())

    async def cleanup(self, context: WorkContext) -> CleanupOutcome:
        """The live seam owns its SQLite file; the runner owns the attempt directory."""

        del context
        return CleanupOutcome(removed_resources=0, warnings=())

    def classify_failure(self, error: BaseException) -> FailureClassification:
        if isinstance(error, ValueError):
            return FailureClassification(FailureClass.VALIDATION, False, None)
        if isinstance(error, OSError):
            return FailureClassification(FailureClass.PROVIDER, True, None)
        return FailureClassification(FailureClass.EVALUATION, False, None)


def _memory_plan(plan: PluginPlan) -> _MemoryEvalPlan:
    private_plan = plan.private_plan
    if not isinstance(private_plan, _MemoryEvalPlan):
        raise TypeError("memory evaluation requires its own immutable plan")
    return private_plan


def _default_environment() -> LiveEnvironment:
    return probe_environment(os.environ)


def _attempt_environment(
    environment: LiveEnvironment,
    scratch_dir: Path,
    attempt_id: str,
) -> LiveEnvironment:
    if environment.postgres_url is not None:
        return replace(environment, sqlite_path=None, sqlite_path_owned=False)
    if environment.sqlite_path is None:
        return environment
    return replace(
        environment,
        sqlite_path=scratch_dir / f"memeval-{_safe_filename_id(attempt_id)}.db",
        sqlite_path_owned=True,
    )


def _safe_filename_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", value)


def _freeze_probe_set(probe_set: ProbeSet) -> ProbeSet:
    seed = probe_set.seed
    return ProbeSet(
        schema_version=probe_set.schema_version,
        probe_set_id=probe_set.probe_set_id,
        label=probe_set.label,
        seed=SeedSpec(
            short_term=tuple(seed.short_term),
            long_term=MappingProxyType(dict(seed.long_term)),
            episodic=tuple(
                EpisodeSeed(request=episode.request, approve=episode.approve)
                for episode in seed.episodic
            ),
            semantic_corpus_dir=seed.semantic_corpus_dir,
        ),
        probes=tuple(replace(probe) for probe in probe_set.probes),
    )


def _is_complete_success(
    result: MemoryShardResult,
    probe_set: ProbeSet,
    environment: LiveEnvironment,
) -> bool:
    rows_complete = len(result.rows) == len(probe_set.probes) and all(
        row.probe_id == probe.probe_id
        and row.targets is probe.targets
        and row.test is probe.test
        and all(
            isinstance(outcome, Outcome)
            for outcome in (row.full, row.ablated, row.control)
        )
        for row, probe in zip(result.rows, probe_set.probes, strict=True)
    )
    aborted = any(finding.startswith("aborted: ") for finding in result.provider_findings)
    cleanup_failed = environment.sqlite_path_owned and not result.scratch_removed
    return not aborted and not cleanup_failed and rows_complete


def _probe_subset(probe_set: ProbeSet, unit: WorkUnit) -> ProbeSet:
    if set(unit.payload) != {"probe_ids", "ordinals"}:
        raise ValueError("memory shard payload must contain only probe_ids and ordinals")
    probe_ids = _string_sequence(unit.payload["probe_ids"], "probe_ids")
    ordinals = _ordinal_sequence(unit.payload["ordinals"])
    if not probe_ids or len(probe_ids) != len(ordinals):
        raise ValueError("memory shard payload must pair every probe id with one ordinal")
    by_id = {probe.probe_id: probe for probe in probe_set.probes}
    probes: list[Probe] = []
    for probe_id, ordinal in zip(probe_ids, ordinals, strict=True):
        if ordinal >= len(probe_set.probes):
            raise ValueError("memory shard ordinal is outside its probe set")
        probe = by_id.get(probe_id)
        if probe is None or probe_set.probes[ordinal] is not probe:
            raise ValueError("memory shard payload does not match the immutable plan")
        probes.append(probe)
    if len({probe.probe_id for probe in probes}) != len(probes):
        raise ValueError("memory shard payload repeats a probe")
    return ProbeSet(
        schema_version=probe_set.schema_version,
        probe_set_id=probe_set.probe_set_id,
        label=probe_set.label,
        seed=probe_set.seed,
        probes=tuple(probes),
    )


def _string_sequence(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"memory shard {name} must be a sequence")
    items = tuple(value)
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError(f"memory shard {name} must contain non-empty strings")
    return cast(tuple[str, ...], items)


def _ordinal_sequence(value: object) -> tuple[int, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError("memory shard ordinals must be a sequence")
    items = tuple(value)
    if not all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 0
        for item in items
    ):
        raise ValueError("memory shard ordinals must contain non-negative integers")
    return cast(tuple[int, ...], items)


def _encode_private_result(
    result: MemoryShardResult, execution: _ExecutionEvidence
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": _PRIVATE_RESULT_SCHEMA_VERSION,
        "rows": [_encode_row(row) for row in result.rows],
        "seed_failure_ids": list(result.seed_failure_ids),
        "private_transcript": list(result.private_transcript),
        "nonce": result.nonce,
        "provider_findings": list(result.provider_findings),
        "scratch_removed": result.scratch_removed,
        "report_nonce": result.report_nonce,
        "execution": {
            "job_id": execution.job_id,
            "attempt_id": execution.attempt_id,
            "lane_id": execution.lane_id,
            "credential_alias": execution.credential_alias,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):  # pragma: no cover - json object round-trip invariant
        raise TypeError("memory private result must encode to an object")
    return decoded


def _encode_row(row: ProbeRow) -> dict[str, object]:
    return {
        "probe_id": row.probe_id,
        "targets": row.targets.value,
        "test": row.test.value,
        "full": row.full.value,
        "ablated": row.ablated.value,
        "control": row.control.value,
        "certain": row.certain,
        "latency_ms": row.latency_ms,
    }


def _successful_shards(
    outcomes: Sequence[WorkUnitOutcome], report_nonce: str
) -> tuple[list[_DecodedShardResult], int]:
    decoded: list[_DecodedShardResult] = []
    failed_units = 0
    for outcome in sorted(outcomes, key=lambda item: item.ordinal):
        if outcome.state is not UnitState.SUCCEEDED:
            failed_units += 1
            continue
        try:
            shard = _decode_private_result(
                outcome.private_result, unit_id=outcome.unit_id, ordinal=outcome.ordinal
            )
        except (TypeError, ValueError):
            failed_units += 1
            continue
        if shard.result.report_nonce != report_nonce:
            failed_units += 1
            continue
        decoded.append(shard)
    return decoded, failed_units


def _valid_shards(
    shards: Sequence[_DecodedShardResult], probe_set: ProbeSet
) -> tuple[list[_DecodedShardResult], int]:
    by_id = {probe.probe_id: probe for probe in probe_set.probes}
    seen: set[str] = set()
    valid: list[_DecodedShardResult] = []
    invalid = 0
    for shard in shards:
        rows = shard.result.rows
        if any(
            row.probe_id not in by_id
            or row.probe_id in seen
            or row.targets is not by_id[row.probe_id].targets
            or row.test is not by_id[row.probe_id].test
            for row in rows
        ):
            invalid += 1
            continue
        seen.update(row.probe_id for row in rows)
        valid.append(shard)
    return valid, invalid


def _ordered_report_inputs(
    shards: Sequence[_DecodedShardResult], probe_set: ProbeSet
) -> list[MemoryShardResult]:
    if not shards:
        return []
    rows_by_id = {
        row.probe_id: row
        for shard in shards
        for row in shard.result.rows
    }
    first = shards[0].result
    return [
        MemoryShardResult(
            rows=tuple(
                rows_by_id[probe.probe_id]
                for probe in probe_set.probes
                if probe.probe_id in rows_by_id
            ),
            seed_failure_ids=tuple(
                sorted(
                    {
                        failure
                        for shard in shards
                        for failure in shard.result.seed_failure_ids
                    }
                )
            ),
            private_transcript=(),
            nonce=first.nonce,
            provider_findings=tuple(
                sorted(
                    {
                        finding
                        for shard in shards
                        for finding in shard.result.provider_findings
                    }
                )
            ),
            scratch_removed=all(shard.result.scratch_removed for shard in shards),
            report_nonce=first.report_nonce,
        )
    ]


def _decode_private_result(
    value: object, *, unit_id: str, ordinal: int
) -> _DecodedShardResult:
    payload = _object(value, "memory private result")
    expected = {
        "schema_version",
        "rows",
        "seed_failure_ids",
        "private_transcript",
        "nonce",
        "provider_findings",
        "scratch_removed",
        "report_nonce",
        "execution",
    }
    if set(payload) != expected or payload["schema_version"] != _PRIVATE_RESULT_SCHEMA_VERSION:
        raise ValueError("memory private result schema is invalid")
    rows = tuple(_decode_row(row) for row in _sequence(payload["rows"], "rows"))
    transcript = tuple(
        _json_object(record, "private_transcript")
        for record in _sequence(payload["private_transcript"], "private_transcript")
    )
    result = MemoryShardResult(
        rows=rows,
        seed_failure_ids=_string_sequence(payload["seed_failure_ids"], "seed_failure_ids"),
        private_transcript=transcript,
        nonce=_nonempty_string(payload["nonce"], "nonce"),
        provider_findings=_string_sequence(payload["provider_findings"], "provider_findings"),
        scratch_removed=_boolean(payload["scratch_removed"], "scratch_removed"),
        report_nonce=_nonempty_string(payload["report_nonce"], "report_nonce"),
    )
    execution = _object(payload["execution"], "execution")
    if set(execution) != {"job_id", "attempt_id", "lane_id", "credential_alias"}:
        raise ValueError("memory private execution evidence is invalid")
    return _DecodedShardResult(
        unit_id=unit_id,
        ordinal=ordinal,
        result=result,
        execution=_ExecutionEvidence(
            job_id=_nonempty_string(execution["job_id"], "job_id"),
            attempt_id=_nonempty_string(execution["attempt_id"], "attempt_id"),
            lane_id=_nonempty_string(execution["lane_id"], "lane_id"),
            credential_alias=_nonempty_string(execution["credential_alias"], "credential_alias"),
        ),
    )


def _decode_row(value: object) -> ProbeRow:
    row = _object(value, "memory row")
    expected = {
        "probe_id",
        "targets",
        "test",
        "full",
        "ablated",
        "control",
        "certain",
        "latency_ms",
    }
    if set(row) != expected:
        raise ValueError("memory row schema is invalid")
    try:
        return ProbeRow(
            probe_id=_nonempty_string(row["probe_id"], "probe_id"),
            targets=MemoryType(_nonempty_string(row["targets"], "targets")),
            test=ProbeTest(_nonempty_string(row["test"], "test")),
            full=Outcome(_nonempty_string(row["full"], "full")),
            ablated=Outcome(_nonempty_string(row["ablated"], "ablated")),
            control=Outcome(_nonempty_string(row["control"], "control")),
            certain=_boolean(row["certain"], "certain"),
            latency_ms=_nonnegative_int(row["latency_ms"], "latency_ms"),
        )
    except ValueError as error:
        raise ValueError("memory row contains an invalid enum") from error


def _object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _json_object(value: object, name: str) -> dict[str, object]:
    payload = _object(value, name)
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be JSON-safe") from error
    if not isinstance(decoded, dict):  # pragma: no cover - json object round-trip invariant
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], decoded)


def _sequence(value: object, name: str) -> tuple[object, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence")
    return tuple(value)


def _nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _execution_manifest(
    successful: Sequence[_DecodedShardResult],
    outcomes: Sequence[WorkUnitOutcome],
    *,
    expected_probe_count: int,
    completed_probe_count: int,
    failed_unit_count: int,
) -> dict[str, object]:
    shards: list[dict[str, object]] = []
    for outcome in sorted(outcomes, key=lambda item: item.ordinal):
        shards.append(
            {
                "unit_id": outcome.unit_id,
                "ordinal": outcome.ordinal,
                "state": outcome.state.value,
            }
        )
    return {
        "expected_probe_count": expected_probe_count,
        "completed_probe_count": completed_probe_count,
        "missing_probe_count": expected_probe_count - completed_probe_count,
        "succeeded_unit_count": len(successful),
        "failed_unit_count": failed_unit_count,
        "terminal_unit_count": len(outcomes),
        "shards": shards,
    }
