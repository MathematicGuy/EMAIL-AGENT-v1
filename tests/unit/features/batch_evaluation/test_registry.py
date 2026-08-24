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


def test_registry_rejects_duplicate_and_unknown_types_without_echoing_input() -> None:
    registry = PluginRegistry()
    plugin = FakePlugin()
    registry.register(plugin)

    with pytest.raises(ValueError, match="duplicate evaluation type") as duplicate_error:
        registry.register(plugin)
    assert plugin.evaluation_type not in str(duplicate_error.value)

    unknown_type = "unknown-apiKey-private-value"
    with pytest.raises(ValueError, match="unknown evaluation type") as unknown_error:
        registry.require(unknown_type)
    assert unknown_type not in str(unknown_error.value)
    assert unknown_error.value.__cause__ is None


@pytest.mark.parametrize(
    "key", ["APIKey", "apiKey", "api_key", "AccessToken", "accessToken", "access_token"]
)
def test_registry_rejects_compact_or_camel_case_secret_schema_keys(key: str) -> None:
    plugin = FakePlugin()
    plugin.parameter_schema = {key: {"type": "string"}}

    with pytest.raises(ValueError, match="secret") as error:
        PluginRegistry().register(plugin)
    assert key not in str(error.value)
    assert error.value.__cause__ is None


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
