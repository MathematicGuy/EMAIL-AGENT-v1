"""The Markdown line vocabulary shared by normalization and chunking.

Both stages have to agree on what a fence, a heading and a table row look
like: the normalizer decides which lines it must leave alone, and the chunker
decides which lines open a new block. Two copies of these patterns drift into
two different answers for the same line, so they are defined once here.
"""

from __future__ import annotations

import re
from typing import Final

#: Matches an ATX heading line without extracting it — the "leave this alone"
#: test. Deliberately looser than :data:`ATX_HEADING`: a bare ``#`` with no
#: title is already a heading line for the purposes of not rewriting it.
ATX_PREFIX: Final = re.compile(r"^\s*#{1,6}\s")
#: Matches an ATX heading that carries a title, capturing depth and text.
ATX_HEADING: Final = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
FENCE: Final = re.compile(r"^\s*(?:```|~~~)")
TABLE_ROW: Final = re.compile(r"^\s*\|")
TABLE_SEPARATOR: Final = re.compile(r"^\s*\|[\s:|-]+\|?\s*$")
LIST_ITEM: Final = re.compile(r"^\s*(?:[-*+]|\d+(?:\.\d+)*[.)])\s+")


def normalize_newlines(text: str) -> str:
    """Fold CRLF and lone CR to ``\\n`` so every stage counts lines alike."""
    return text.replace("\r\n", "\n").replace("\r", "\n")
