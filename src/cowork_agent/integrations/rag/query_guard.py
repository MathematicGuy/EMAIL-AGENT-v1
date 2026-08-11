"""Query Guard for filtering non-retrieval, greeting, and out-of-domain queries."""

from __future__ import annotations

import re

_GREETING_PATTERNS = (
    r"^(hê\s*lô|hello|hi|hey|chào|xin\s*chào|test|testing|abc|xyz|123|ha\s*ha|hehe|ok|alo)\b",
)
_GREETING_REGEX = re.compile("|".join(_GREETING_PATTERNS), re.IGNORECASE)


def is_retrieval_query(query: str) -> bool:
    """Return False if query is a greeting, filler, or lacks retrieval intent."""
    cleaned = query.strip()
    if not cleaned or len(cleaned) < 2:
        return False

    if _GREETING_REGEX.match(cleaned):
        words = cleaned.split()
        if len(words) <= 3:
            return False

    return True
