from __future__ import annotations

import asyncio

from cowork_agent.domain.chat_contracts import ChatMemoryScope, MemoryType
from cowork_agent.features.ai_chat.memory_eval.live_controller import (
    AdapterSet,
    build_arm_controller,
)
from cowork_agent.features.ai_chat.memory_eval.live_seeding import seed_short_term
from cowork_agent.features.ai_chat.memory_eval.probes import SeedSpec


class _Reply:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    async def stream_reply(self, request: object, context: object):  # noqa: ANN201 - structural
        del request, context
        self.calls += 1
        if self._fail:
            raise RuntimeError("model down")
        yield "acknowledged"


def _controller(reply: object):  # noqa: ANN201 - returns a controller pair
    scope = ChatMemoryScope(tenant_id="t", user_id="u", session_id="s")
    return build_arm_controller(scope, AdapterSet(), reply, masked_scope=None)


def test_each_seed_line_is_sent_as_its_own_turn() -> None:
    reply = _Reply()
    controller, _ = _controller(reply)
    spec = SeedSpec(("line one", "line two", "line three"), {}, (), None)
    outcome = asyncio.run(seed_short_term(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is True
    assert outcome.scope is MemoryType.SHORT_TERM
    assert reply.calls == 3


def test_nothing_declared_is_a_skip_not_a_failure() -> None:
    controller, _ = _controller(_Reply())
    outcome = asyncio.run(
        seed_short_term(controller, "s", SeedSpec((), {}, (), None), key_prefix="seed")
    )
    assert outcome.ok is True
    assert outcome.reason == "nothing declared"


def test_a_model_failure_is_reported_as_a_finding() -> None:
    controller, _ = _controller(_Reply(fail=True))
    spec = SeedSpec(("line one",), {}, (), None)
    outcome = asyncio.run(seed_short_term(controller, "s", spec, key_prefix="seed"))
    assert outcome.ok is False
    assert "model down" in outcome.reason


def test_the_buffer_holds_every_seeded_turn() -> None:
    controller, gateway = _controller(_Reply())
    spec = SeedSpec(("alpha", "beta"), {}, (), None)
    asyncio.run(seed_short_term(controller, "s", spec, key_prefix="seed"))
    turns = gateway._read_active_turns()
    assert any("alpha" in (turn.user_message or "") for turn in turns)
    assert any("beta" in (turn.user_message or "") for turn in turns)
