"""Contract tests for the production memory-evaluation batch plug-in."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from cowork_agent.features.ai_chat.memory_eval.live_env import LiveEnvironment
from cowork_agent.features.ai_chat.memory_eval.live_execution import MemoryShardResult
from cowork_agent.features.ai_chat.memory_eval.probes import ProbeSet
from cowork_agent.features.ai_chat.memory_eval.report import ProbeRow
from cowork_agent.features.ai_chat.memory_eval.scoring import Outcome
from cowork_agent.features.batch_evaluation.contracts import (
    EvaluationBudget,
    EvaluationRequest,
    ExecutionMode,
    UnitState,
    WorkContext,
    WorkUnitOutcome,
)
from cowork_agent.features.batch_evaluation.plugins import memory_eval
from cowork_agent.features.batch_evaluation.plugins.memory_eval import (
    MemoryEvalPlugin,
    MemoryProbeCatalog,
)

_PROBES_DIR = Path("evaluations/MEMORIES/probes")


def _request(**overrides: object) -> EvaluationRequest:
    values: dict[str, object] = {
        "evaluation_type": "memory-eval",
        "provider": "mistral",
        "target_model": "mistral-small-latest",
        "dataset_ref": "v1-four-scopes",
        "credential_pool": "mistral-eval",
        "execution_mode": ExecutionMode.WORKFLOW_SHARDS,
        "max_workers": 3,
        "max_attempts_per_unit": 1,
        "budget": EvaluationBudget(max_provider_requests=300, max_total_tokens=300_000),
        "parameters": {},
    }
    values.update(overrides)
    return EvaluationRequest(**values)  # type: ignore[arg-type]


def _sqlite_environment() -> LiveEnvironment:
    return LiveEnvironment(
        postgres_url=None,
        sqlite_path=Path("sqlite-template.db"),
        gemini_ready=True,
        embeddings_ready=True,
        embedding_key_name="GEMINI_API_KEY",
    )


def _plugin(*, environment: LiveEnvironment | None = None) -> MemoryEvalPlugin:
    return MemoryEvalPlugin(
        catalog=MemoryProbeCatalog(_PROBES_DIR),
        environment_resolver=lambda: environment or _sqlite_environment(),
    )


@pytest.mark.parametrize(
    ("dataset_ref", "probe_set_id"),
    (
        ("v1-four-scopes", "v1_four_scopes"),
        ("v2-four-scopes-wide", "v2_four_scopes_wide"),
        ("v3-four-scopes-hard", "v3_four_scopes_hard"),
    ),
)
def test_catalog_resolves_only_canonical_probe_ids(
    dataset_ref: str, probe_set_id: str
) -> None:
    catalog = MemoryProbeCatalog(_PROBES_DIR)

    probe_set = catalog.resolve(dataset_ref)

    assert probe_set.probe_set_id == probe_set_id
    assert probe_set.probes


@pytest.mark.parametrize(
    "dataset_ref",
    ("../v1-four-scopes", "v1-four-scopes.json", "unknown", r"C:\\probes\\v1.json"),
)
def test_catalog_rejects_path_like_and_unknown_dataset_refs(dataset_ref: str) -> None:
    catalog = MemoryProbeCatalog(_PROBES_DIR)

    with pytest.raises(ValueError):
        catalog.resolve(dataset_ref)


@pytest.mark.asyncio
async def test_preflight_is_immutable_per_job_and_rejects_incompatible_requests() -> None:
    plugin = _plugin()
    first = await plugin.preflight(_request(dataset_ref="v1-four-scopes"))
    second = await plugin.preflight(_request(dataset_ref="v2-four-scopes-wide"))

    assert first.dataset_ref == "v1-four-scopes"
    assert second.dataset_ref == "v2-four-scopes-wide"
    assert len(plugin.build_work_units(first, lane_count=1)) == 1
    assert len(plugin.build_work_units(second, lane_count=1)) == 1

    for request in (
        _request(provider="gemini"),
        _request(execution_mode=ExecutionMode.REQUEST_BATCH),
    ):
        with pytest.raises(ValueError):
            await plugin.preflight(request)

    postgres_plugin = _plugin(
        environment=LiveEnvironment(
            postgres_url="postgresql://localhost/memeval",
            sqlite_path=None,
            gemini_ready=True,
            embeddings_ready=True,
            embedding_key_name="GEMINI_API_KEY",
        )
    )
    with pytest.raises(ValueError, match="PostgreSQL"):
        await postgres_plugin.preflight(_request(max_workers=2))

    assert (await postgres_plugin.preflight(_request(max_workers=1))).ready_work > 0

    no_store_plugin = _plugin(
        environment=LiveEnvironment(
            postgres_url=None,
            sqlite_path=None,
            gemini_ready=True,
            embeddings_ready=True,
            embedding_key_name="GEMINI_API_KEY",
        )
    )
    with pytest.raises(ValueError, match="SQLite"):
        await no_store_plugin.preflight(_request(max_workers=2))

    assert (await no_store_plugin.preflight(_request(max_workers=1))).ready_work > 0


@pytest.mark.asyncio
async def test_preflight_detaches_and_deeply_freezes_mutable_probe_seed_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = MemoryProbeCatalog(_PROBES_DIR)
    loaded = catalog.resolve("v1-four-scopes")
    mutable_long_term = dict(loaded.seed.long_term)
    source = replace(loaded, seed=replace(loaded.seed, long_term=mutable_long_term))
    monkeypatch.setattr(catalog, "resolve", lambda _dataset_ref: source)
    plugin = MemoryEvalPlugin(
        catalog=catalog,
        environment_resolver=_sqlite_environment,
    )

    plan = await plugin.preflight(_request())
    private_plan = cast(memory_eval._MemoryEvalPlan, plan.private_plan)
    frozen_long_term = private_plan.probe_set.seed.long_term
    original = dict(frozen_long_term)

    mutable_long_term["review-mutation"] = "must not leak"

    assert dict(frozen_long_term) == original
    with pytest.raises(TypeError):
        frozen_long_term["blocked-write"] = "value"  # type: ignore[index]


@pytest.mark.asyncio
async def test_default_plugin_resolves_the_live_environment_from_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Mapping[str, str]] = []

    def resolve_environment(environ: Mapping[str, str]) -> LiveEnvironment:
        calls.append(environ)
        return _sqlite_environment()

    monkeypatch.setattr(memory_eval, "probe_environment", resolve_environment)
    plugin = MemoryEvalPlugin(catalog=MemoryProbeCatalog(_PROBES_DIR))

    await plugin.preflight(_request())

    assert calls


@pytest.mark.asyncio
async def test_preflight_rejects_an_unknown_catalog_ref_before_environment_resolution() -> None:
    environment_calls = 0

    def resolve_environment() -> LiveEnvironment:
        nonlocal environment_calls
        environment_calls += 1
        return _sqlite_environment()

    plugin = MemoryEvalPlugin(
        catalog=MemoryProbeCatalog(_PROBES_DIR),
        environment_resolver=resolve_environment,
    )

    with pytest.raises(ValueError):
        await plugin.preflight(_request(dataset_ref="unknown"))

    assert environment_calls == 0


@pytest.mark.asyncio
async def test_work_units_round_robin_all_probes_with_only_durable_metadata() -> None:
    plugin = _plugin()
    plan = await plugin.preflight(_request())

    units = plugin.build_work_units(plan, lane_count=3)
    probe_set = MemoryProbeCatalog(_PROBES_DIR).resolve("v1-four-scopes")
    original_ids = tuple(probe.probe_id for probe in probe_set.probes)

    assert len(units) == 3
    assert [unit.ordinal for unit in units] == list(range(len(units)))
    assert all(set(unit.payload) == {"probe_ids", "ordinals"} for unit in units)
    flattened = [
        (ordinal, probe_id)
        for unit in units
        for ordinal, probe_id in zip(
            unit.payload["ordinals"], unit.payload["probe_ids"], strict=True
        )
    ]
    assert [probe_id for _, probe_id in sorted(flattened)] == list(original_ids)
    assert [ordinal for ordinal, _ in sorted(flattened)] == list(range(len(original_ids)))
    assert len({probe_id for _, probe_id in flattened}) == len(original_ids)
    assert [tuple(unit.payload["ordinals"]) for unit in units] == [
        (0, 3, 6),
        (1, 4, 7),
        (2, 5),
    ]
    assert [tuple(unit.payload["probe_ids"]) for unit in units] == [
        original_ids[0::3],
        original_ids[1::3],
        original_ids[2::3],
    ]

    one_per_probe = plugin.build_work_units(plan, lane_count=len(original_ids) + 5)
    assert len(one_per_probe) == len(original_ids)


@pytest.mark.asyncio
async def test_serial_execute_work_keeps_an_explicit_no_store_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable = LiveEnvironment(
        postgres_url=None,
        sqlite_path=None,
        gemini_ready=True,
        embeddings_ready=True,
        embedding_key_name="GEMINI_API_KEY",
    )
    plugin = _plugin(environment=unavailable)
    plan = await plugin.preflight(_request(max_workers=1))
    unit = plugin.build_work_units(plan, lane_count=1)[0]
    seen: list[LiveEnvironment] = []

    async def fake_execute_memory_shard(
        probe_set: object,
        environment: LiveEnvironment,
        reply: object,
        *,
        report_nonce: str,
        **_: object,
    ) -> MemoryShardResult:
        del reply
        seen.append(environment)
        probe = probe_set.probes[0]  # type: ignore[union-attr]
        return MemoryShardResult(
            rows=(
                ProbeRow(
                    probe_id=probe.probe_id,
                    targets=probe.targets,
                    test=probe.test,
                    full=Outcome.PASS,
                    ablated=Outcome.MISS,
                    control=Outcome.MISS,
                    certain=True,
                    latency_ms=1,
                ),
            ),
            seed_failure_ids=(),
            private_transcript=(),
            nonce="identity",
            provider_findings=(),
            scratch_removed=False,
            report_nonce=report_nonce,
        )

    monkeypatch.setattr(memory_eval, "execute_memory_shard", fake_execute_memory_shard)
    await plugin.execute_work(
        unit,
        WorkContext(
            job_id="memory-job",
            attempt_id="attempt-1",
            lane_id="lane-1",
            credential_alias="mistral-1",
            plugin_plan=plan,
            provider_client=object(),
            scratch_dir=tmp_path,
        ),
    )

    assert seen[0].sqlite_path is None


@pytest.mark.asyncio
async def test_execute_work_isolates_concurrent_shards_and_encodes_private_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _plugin()
    plan = await plugin.preflight(_request())
    units = plugin.build_work_units(plan, lane_count=2)
    observed: list[dict[str, object]] = []

    async def fake_execute_memory_shard(
        probe_set: object,
        environment: LiveEnvironment,
        reply: object,
        *,
        provider: str,
        model: str,
        report_nonce: str,
        **_: object,
    ) -> MemoryShardResult:
        assert environment.sqlite_path is not None
        environment.sqlite_path.touch()
        probe = probe_set.probes[0]  # type: ignore[union-attr]
        identity_nonce = uuid4().hex
        output_path = environment.sqlite_path.parent / "private-transcript.json"
        output_path.write_text("private", encoding="utf-8")
        transcript = {
            "question": f"private question {probe.probe_id}",
            "reply": f"private reply {probe.probe_id}",
        }
        observed.append(
            {
                "database": environment.sqlite_path,
                "tenant": f"tenant-{identity_nonce}",
                "user": f"user-{identity_nonce}",
                "session": f"session-{identity_nonce}-{probe.probe_id}",
                "nonce": identity_nonce,
                "transcript": transcript,
                "output": output_path,
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

    contexts: list[WorkContext] = []
    for index, _unit in enumerate(units):
        scratch_dir = tmp_path / f"attempt-{index}"
        scratch_dir.mkdir()
        contexts.append(
            WorkContext(
                job_id="memory-job",
                attempt_id=f"attempt-{index}",
                lane_id=f"lane-{index}",
                credential_alias=f"mistral-{index}",
                plugin_plan=plan,
                provider_client=object(),
                scratch_dir=scratch_dir,
            )
        )

    outcomes = await asyncio.gather(
        *(plugin.execute_work(unit, context) for unit, context in zip(units, contexts, strict=True))
    )

    assert {entry["database"] for entry in observed} == {
        context.scratch_dir / f"memeval-{context.attempt_id}.db" for context in contexts
    }
    assert all(
        cast(Path, entry["database"]).name.startswith("memeval-")
        and cast(Path, entry["database"]).suffix == ".db"
        for entry in observed
    )
    for key in ("tenant", "user", "session", "nonce", "transcript", "output", "reply"):
        values = {
            id(entry[key]) if key in {"transcript", "reply"} else entry[key]
            for entry in observed
        }
        assert len(values) == len(observed)
    assert len({entry["report_nonce"] for entry in observed}) == 1
    assert all(json.loads(json.dumps(outcome.private_result)) for outcome in outcomes)
    assert all(not cast(Path, entry["database"]).exists() for entry in observed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kind",
    ("aborted", "missing-row", "incomplete-row", "cleanup"),
)
async def test_execute_work_returns_failed_for_dishonest_shard_results(
    failure_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plan = await plugin.preflight(_request(max_workers=2))
    unit = plugin.build_work_units(plan, lane_count=2)[0]

    async def fake_execute_memory_shard(
        probe_set: ProbeSet,
        environment: LiveEnvironment,
        reply: object,
        *,
        report_nonce: str,
        **_: object,
    ) -> MemoryShardResult:
        del reply
        assert environment.sqlite_path is not None
        rows = tuple(
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
            for probe in probe_set.probes
        )
        if failure_kind == "missing-row":
            rows = rows[:-1]
        elif failure_kind == "incomplete-row":
            rows = (replace(rows[0], full=cast(Outcome, "not-an-outcome")), *rows[1:])
        return MemoryShardResult(
            rows=rows,
            seed_failure_ids=(),
            private_transcript=({"question": "private", "reply": "private"},),
            nonce="identity",
            provider_findings=("aborted: provider limit",)
            if failure_kind == "aborted"
            else (),
            scratch_removed=failure_kind != "cleanup",
            report_nonce=report_nonce,
        )

    monkeypatch.setattr(memory_eval, "execute_memory_shard", fake_execute_memory_shard)
    outcome = await plugin.execute_work(
        unit,
        WorkContext(
            job_id="memory-job",
            attempt_id="attempt-failed",
            lane_id="lane-1",
            credential_alias="mistral-1",
            plugin_plan=plan,
            provider_client=object(),
            scratch_dir=tmp_path,
        ),
    )

    assert outcome.state is UnitState.FAILED
    assert outcome.private_result is None


@pytest.mark.asyncio
async def test_aggregate_decodes_durable_results_once_in_original_probe_order_and_marks_partials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _plugin()
    plan = await plugin.preflight(_request())
    units = plugin.build_work_units(plan, lane_count=3)
    original_build = memory_eval.build_memory_report
    merged_probe_ids: list[str] = []
    calls = 0

    def capture_report(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        shard_results = args[1]
        merged_probe_ids.extend(
            row.probe_id for shard in shard_results for row in shard.rows
        )
        return original_build(*args, **kwargs)  # type: ignore[arg-type]

    async def fake_execute_memory_shard(
        probe_set: object,
        environment: LiveEnvironment,
        reply: object,
        *,
        report_nonce: str,
        **_: object,
    ) -> MemoryShardResult:
        del environment, reply
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
            private_transcript=({"question": "private prompt", "reply": "private reply"},),
            nonce="identity-nonce",
            provider_findings=(),
            scratch_removed=True,
            report_nonce=report_nonce,
        )

    monkeypatch.setattr(memory_eval, "execute_memory_shard", fake_execute_memory_shard)
    monkeypatch.setattr(memory_eval, "build_memory_report", capture_report)
    contexts = tuple(
        WorkContext(
            job_id="memory-job",
            attempt_id=f"attempt-{index}",
            lane_id=f"lane-{index}",
            credential_alias=f"mistral-{index}",
            plugin_plan=plan,
            provider_client=object(),
            scratch_dir=tmp_path / f"attempt-{index}",
        )
        for index in range(len(units))
    )
    for context in contexts:
        context.scratch_dir.mkdir()
    outcomes = await asyncio.gather(
        *(plugin.execute_work(unit, context) for unit, context in zip(units, contexts, strict=True))
    )
    durable_outcomes = tuple(
        WorkUnitOutcome(
            unit_id=outcome.unit_id,
            ordinal=outcome.ordinal,
            state=outcome.state,
            provider_requests=outcome.provider_requests,
            total_tokens=outcome.total_tokens,
            private_result=json.loads(json.dumps(outcome.private_result)),
        )
        for outcome in reversed(outcomes)
    )

    bundle = plugin.aggregate(plan, durable_outcomes)

    expected_probe_set = MemoryProbeCatalog(_PROBES_DIR).resolve("v1-four-scopes")
    expected_ids = [probe.probe_id for probe in expected_probe_set.probes]
    assert calls == 1
    assert merged_probe_ids == expected_ids
    assert bundle.public_result["nonce"]
    assert bundle.public_result["probe_count"] == len(expected_ids)
    public_text = str(bundle.public_result)
    assert "private prompt" not in public_text
    assert "private reply" not in public_text
    assert str(tmp_path) not in public_text

    partial = plugin.aggregate(
        plan,
        (
            durable_outcomes[0],
            WorkUnitOutcome(
                unit_id="memory-shard-99",
                ordinal=99,
                state=UnitState.FAILED,
                provider_requests=0,
                total_tokens=0,
                private_result=None,
            ),
        ),
    )
    manifest = partial.public_result["execution_manifest"]
    assert partial.public_result["aborted"] is True
    assert manifest["expected_probe_count"] == len(expected_ids)
    assert manifest["completed_probe_count"] < len(expected_ids)
    assert manifest["failed_unit_count"] == 1
    assert {(shard["unit_id"], shard["state"]) for shard in manifest["shards"]} == {
        (durable_outcomes[0].unit_id, "succeeded"),
        ("memory-shard-99", "failed"),
    }
