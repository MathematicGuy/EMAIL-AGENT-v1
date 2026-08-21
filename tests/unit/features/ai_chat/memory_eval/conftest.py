from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer

REPO_ROOT = Path(__file__).resolve().parents[5]
PROBE_SET_DIR = REPO_ROOT / "evaluations" / "MEMORIES" / "probes"
#: Every committed probe set. The invariants in this directory used to name
#: `v1-four-scopes.json` as a literal, which meant a second set shipped
#: unguarded: a dead cue, a question that reads as a task directive, or a recall
#: expectation absent from its own corpus would all fail silently and be
#: reported as memory findings. Discovery makes a new set inherit the guards.
PROBE_SET_PATHS = tuple(sorted(PROBE_SET_DIR.glob("*.json")))


@pytest.fixture(params=PROBE_SET_PATHS, ids=lambda path: path.name)
def probe_set_path(request: pytest.FixtureRequest) -> Path:
    """One committed probe set, so a failure names the file it came from."""

    path: Path = request.param
    return path


@pytest.fixture
def memory_gateway_factory() -> Callable[..., MemoryGateway]:
    """Build a MemoryGateway with a real session buffer and injectable adapters."""

    def _factory(**adapters: Any) -> MemoryGateway:
        return MemoryGateway(
            scope=ChatMemoryScope(tenant_id="t", user_id="u", session_id="s"),
            session_buffer=InMemoryChatSessionBuffer(max_turns=20, ttl_seconds=1800),
            **adapters,
        )

    return _factory
