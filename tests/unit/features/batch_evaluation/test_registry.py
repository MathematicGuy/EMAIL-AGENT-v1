from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import assert_type

import pytest

from cowork_agent.features.batch_evaluation.contracts import (
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
)
from cowork_agent.features.batch_evaluation.registry import PluginRegistry


class FakePlugin:
    evaluation_type = "memory-eval"
    version = "1.0"
    supported_modes = frozenset({ExecutionMode.WORKFLOW_SHARDS})
    parameter_schema: Mapping[str, object] = {"type": "object"}

    async def preflight(self, request: EvaluationRequest) -> PluginPlan:
        return PluginPlan(dataset_ref=request.dataset_ref, ready_work=1, private_plan=None)

    def build_work_units(self, plan: PluginPlan, lane_count: int) -> tuple[WorkUnit, ...]:
        return (WorkUnit(unit_id="unit-1", ordinal=0, payload={"item_id": "case-1"}),)

    async def execute_work(self, unit: WorkUnit, context: WorkContext) -> WorkUnitOutcome:
        return WorkUnitOutcome(
            unit_id=unit.unit_id,
            ordinal=unit.ordinal,
            state=UnitState.SUCCEEDED,
            provider_requests=1,
            total_tokens=1,
            private_result=None,
        )

    def aggregate(
        self, plan: PluginPlan, outcomes: Sequence[WorkUnitOutcome]
    ) -> ArtifactBundle:
        return ArtifactBundle(public_result={}, private_artifact_ids=())

    async def cleanup(self, context: WorkContext) -> CleanupOutcome:
        return CleanupOutcome(removed_resources=0, warnings=())

    def classify_failure(self, error: BaseException) -> FailureClassification:
        return FailureClassification(
            failure_class=FailureClass.EVALUATION,
            retryable=False,
            credential_state=None,
        )


def test_registry_registers_and_requires_a_static_plugin() -> None:
    registry = PluginRegistry()
    plugin = FakePlugin()

    registry.register(plugin)

    assert registry.require("memory-eval") is plugin
    assert_type(registry.require("memory-eval"), EvaluationPlugin)


def test_registry_rejects_duplicate_and_unknown_types_without_calling_plugins() -> None:
    registry = PluginRegistry()
    plugin = FakePlugin()
    registry.register(plugin)

    with pytest.raises(ValueError, match="duplicate evaluation type: memory-eval"):
        registry.register(plugin)
    with pytest.raises(ValueError, match="unknown evaluation type: unknown-eval"):
        registry.require("unknown-eval")


def test_registry_lists_only_safe_plugin_type_metadata_in_stable_order() -> None:
    first = FakePlugin()
    second = FakePlugin()
    second.evaluation_type = "chat-ragas"
    second.supported_modes = frozenset(
        {ExecutionMode.REQUEST_BATCH, ExecutionMode.WORKFLOW_SHARDS}
    )
    registry = PluginRegistry()
    registry.register(first)
    registry.register(second)

    listed = registry.list_types()

    assert tuple(item["type"] for item in listed) == ("chat-ragas", "memory-eval")
    assert listed[0] == {
        "type": "chat-ragas",
        "version": "1.0",
        "modes": ("request_batch", "workflow_shards"),
        "parameter_schema": {"type": "object"},
    }
    assert set(listed[1]) == {"type", "version", "modes", "parameter_schema"}
    with pytest.raises(TypeError):
        listed[0]["type"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        listed[0]["parameter_schema"]["type"] = "changed"  # type: ignore[index]
