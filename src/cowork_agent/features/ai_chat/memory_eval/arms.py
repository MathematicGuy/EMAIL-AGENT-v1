"""Ablation arms and the gateway seam that makes them possible (SPEC §5).

`retrieval_policy.select_memory_reads` is called inside the controller, so
there is no parameter to pass an arm through. Rather than add a test-only
override to production code, the harness swaps in a gateway subclass that
reports one scope as unavailable.

Masking the READ rather than the store is the honest model of an arm: the
question is "what does the reply look like when this scope cannot be read",
and that is exactly what a gateway expresses.
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum
from typing import Any

from cowork_agent.domain.chat_contracts import (
    EpisodicMemoryRead,
    MemoryContextRequest,
    MemoryContextResponse,
    MemoryReadOptions,
    MemoryType,
    SemanticMemoryRead,
)

from ..memory_gateway import MemoryGateway

# The same explicitly-disabled read objects retrieval_policy.py builds when a
# cue is absent. Reusing the exact shapes keeps a masked arm indistinguishable
# from a genuine no-cue turn.
_DISABLED_EPISODIC = EpisodicMemoryRead(enabled=False, retrieval_eligible_only=True, max_items=1)
_DISABLED_SEMANTIC = SemanticMemoryRead(enabled=False)


class Arm(StrEnum):
    FULL = "full"
    ABLATED = "ablated"
    CONTROL = "control"


def mask_reads(reads: MemoryReadOptions, scope: MemoryType | None) -> MemoryReadOptions:
    """Return `reads` with one scope forced off. `None` returns it unchanged."""

    if scope is None:
        return reads
    if scope is MemoryType.SHORT_TERM:
        return replace(reads, short_term=False)
    if scope is MemoryType.LONG_TERM:
        return replace(reads, long_term=False)
    if scope is MemoryType.EPISODIC:
        return replace(reads, episodic=_DISABLED_EPISODIC)
    return replace(reads, semantic=_DISABLED_SEMANTIC)


def mask_request(request: MemoryContextRequest, scope: MemoryType | None) -> MemoryContextRequest:
    """Mask one scope out of a context request, preserving scope and session."""

    return replace(request, reads=mask_reads(request.reads, scope))


class ArmScopedMemoryGateway(MemoryGateway):
    """A gateway that reports one scope as unavailable, for ablation arms.

    Only `read_context` is overridden. Writes, project-document reads and
    episode transitions are inherited unchanged, because an arm is a statement
    about what can be READ, not about what the system may store.
    """

    def __init__(self, *args: Any, masked_scope: MemoryType | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._masked_scope = masked_scope

    async def read_context(self, request: MemoryContextRequest) -> MemoryContextResponse:
        return await super().read_context(mask_request(request, self._masked_scope))
