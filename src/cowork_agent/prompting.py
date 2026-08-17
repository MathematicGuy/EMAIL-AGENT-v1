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
from typing import Final

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
