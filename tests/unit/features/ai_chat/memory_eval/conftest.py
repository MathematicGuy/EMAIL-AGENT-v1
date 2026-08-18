from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from cowork_agent.domain.chat_contracts import ChatMemoryScope
from cowork_agent.features.ai_chat.memory_gateway import MemoryGateway
from cowork_agent.features.ai_chat.session_buffer import InMemoryChatSessionBuffer


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
