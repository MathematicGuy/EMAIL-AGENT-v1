"""Shared delimiter convention for untrusted and quoted data in LLM prompts.

Content the user or a third party controls must be wrapped in one of these
blocks, and the system instruction receiving the block must name the tag and
state its trust rule. Tag tokens are stripped from the payload before wrapping
because ``json.dumps`` does not escape ``<``, so content could otherwise close
its own block.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Final, TypeVar

T = TypeVar("T")

UNTRUSTED_DATA_TAG: Final = "untrusted_data"
RETRIEVED_CONTEXT_TAG: Final = "retrieved_context"
ROUTE_CONTEXT_TAG: Final = "route_context"

ALL_PROMPT_TAGS: Final = (
    UNTRUSTED_DATA_TAG,
    RETRIEVED_CONTEXT_TAG,
    ROUTE_CONTEXT_TAG,
)

_TAG_TOKEN = re.compile(r"</?\s*(?:" + "|".join(ALL_PROMPT_TAGS) + r")\s*/?>", re.IGNORECASE)
_NEUTRALIZED = "[delimiter-removed]"


def neutralize_delimiters(text: str) -> str:
    """Strip any literal block delimiter from content before it is wrapped."""

    return _TAG_TOKEN.sub(_NEUTRALIZED, text)


def wrap_block(tag: str, body: str) -> str:
    """Wrap already-serialized ``body`` in one delimiter block."""

    return f"<{tag}>\n{neutralize_delimiters(body)}\n</{tag}>"


def wrap_json_block(tag: str, payload: object) -> str:
    """Serialize ``payload`` and wrap it in one delimiter block."""

    return wrap_block(tag, json.dumps(payload, ensure_ascii=False))


def reorder_u_shaped(items: Sequence[T]) -> tuple[T, ...]:
    """Reorder items in a U-shape to mitigate 'Lost in the Middle' effect.

    Places highest-priority items at the beginning and end of the prompt context:
    [1, 2, 3, 4, 5] -> [1, 3, 5, 4, 2]
    """
    n = len(items)
    if n <= 2:
        return tuple(items)

    left: list[T] = []
    right: list[T] = []
    for idx, item in enumerate(items):
        if idx % 2 == 0:
            left.append(item)
        else:
            right.append(item)

    return tuple(left + right[::-1])
