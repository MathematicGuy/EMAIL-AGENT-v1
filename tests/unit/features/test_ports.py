"""Port-shape guards for the email action plan feature."""

import inspect

import pytest

from cowork_agent.features.email_action_plan.ports import SemanticMemoryPort

pytestmark = pytest.mark.extended


def test_semantic_memory_port_is_retrieval_only() -> None:
    # PRD-v1 FR-08 / §15 criterion 9: the semantic boundary exposes a
    # retrieval-only operation; no public surface (callable or not) may
    # accrete onto the port silently.
    members = {name for name in vars(SemanticMemoryPort) if not name.startswith("_")}
    assert members == {"retrieve"}
    assert inspect.iscoroutinefunction(SemanticMemoryPort.retrieve)
