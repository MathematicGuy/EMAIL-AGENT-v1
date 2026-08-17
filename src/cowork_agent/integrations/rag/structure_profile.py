"""One shared answer to "is this line a heading, and how deep?".

Extraction, normalization and chunking each need that judgement, and they must
never disagree: a paragraph promoted to a heading during DOCX extraction has to
land at the same depth as the same text recovered by regex from OCR output.
Keeping the rule table here is what makes the three stages consistent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

#: Statute titles run long — ``Điều 100. Bồi thường về đất khi Nhà nước thu hồi
#: đất phi nông nghiệp...`` reaches ~330 characters and is still a heading.
DEFAULT_MAX_HEADING_CHARS: Final = 350
#: A continuation title (``QUY ĐỊNH CHUNG`` under ``Chương I``) is always short.
DEFAULT_MAX_TITLE_CHARS: Final = 120
DEFAULT_FORMATTED_LEVEL: Final = 3

_ORDINAL: Final = r"[IVXLCDMivxlcdm\d]+"


@dataclass(frozen=True, slots=True)
class HeadingRule:
    """One structural keyword and the depth it opens."""

    pattern: re.Pattern[str]
    level: int


@dataclass(frozen=True, slots=True)
class HeadingMatch:
    """Depth of a recognised heading and where its keyword prefix ends."""

    level: int
    prefix_end: int


DEFAULT_RULES: Final = (
    HeadingRule(re.compile(rf"^(?:Phần|PHẦN|Part|PART)\s+{_ORDINAL}\b\s*[.:)-]?"), 1),
    HeadingRule(re.compile(rf"^(?:Chương|CHƯƠNG|Chapter|CHAPTER)\s+{_ORDINAL}\b\s*[.:)-]?"), 2),
    HeadingRule(re.compile(rf"^(?:Mục|MỤC|Section|SECTION)\s+{_ORDINAL}\b\s*[.:)-]?"), 3),
    HeadingRule(re.compile(r"^(?:Điều|ĐIỀU|Article|ARTICLE)\s+\d+\s*[.:)-]?"), 4),
    HeadingRule(re.compile(r"^(?:Bước|BƯỚC|Step|STEP)\s+\d+\s*[.:)-]?"), 4),
)
# There is deliberately no rule for bare numbering (``1. Title``). Measured over
# data/extracted it fired 8 times and every hit was an enumerated clause inside
# an article, never a heading — and because the guard is length-based it
# promoted only the short items, splitting one article at arbitrary points.
# Genuine outline headings reach us as ATX from extraction anyway.


def _compile_scanner(rules: tuple[HeadingRule, ...]) -> re.Pattern[str]:
    """Compile a rule table into one alternation, so a line is scanned once.

    Every rule is anchored on a literal keyword, so trying them in turn re-walks
    the same prefix once per rule. Named groups let a single match report which
    rule fired.
    """
    return re.compile(
        "|".join(
            f"(?P<rule{index}>{rule.pattern.pattern})"
            for index, rule in enumerate(rules)
        )
    )


@dataclass(frozen=True, slots=True)
class StructureProfile:
    """Heading vocabulary for a corpus, ordered from the outermost division in."""

    rules: tuple[HeadingRule, ...] = DEFAULT_RULES
    max_heading_chars: int = DEFAULT_MAX_HEADING_CHARS
    max_title_chars: int = DEFAULT_MAX_TITLE_CHARS
    formatted_level: int = DEFAULT_FORMATTED_LEVEL
    #: Bound to the instance so recognising a line costs one attribute read.
    #: A module-level cache keyed on ``rules`` would re-hash the rule table on
    #: every line instead, which costs more than the scan it saves.
    scanner: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scanner", _compile_scanner(self.rules))

    def match_heading(self, text: str) -> HeadingMatch | None:
        """Recognise ``text`` as a plain-text heading, or return ``None``.

        A structural keyword plus a length ceiling is what separates a heading
        from prose that merely opens with one: ``Điều 1. Phạm vi điều chỉnh`` is
        a heading, while a run-on paragraph reciting ``Bước 1: ... Bước 2: ...``
        is not.
        """
        candidate = text.strip()
        if not candidate or "\n" in candidate or len(candidate) > self.max_heading_chars:
            return None
        match = self.scanner.match(candidate)
        if match is None or match.lastgroup is None:
            return None
        index = int(match.lastgroup.removeprefix("rule"))
        return HeadingMatch(level=self.rules[index].level, prefix_end=match.end())

    def heading_level(self, text: str) -> int | None:
        """Depth of ``text`` read as a plain-text heading, or ``None`` for prose."""
        match = self.match_heading(text)
        return None if match is None else match.level

    def formatted_heading_level(self, text: str) -> int | None:
        """Depth for a line the *source* already marked as a heading.

        Typography (a fully bold paragraph, a Word Heading style) establishes
        that the line is a heading; the rule table only refines how deep it sits.
        Returns ``None`` for text too long to be one, so the length ceiling is
        enforced here rather than again in every caller that reads typography.
        """
        if len(text.strip()) > self.max_heading_chars:
            return None
        return self.heading_level(text) or self.formatted_level

    def is_titleless(self, text: str) -> bool:
        """True when a heading names its division but carries no title yet.

        Vietnamese legal documents put ``Chương I`` and ``QUY ĐỊNH CHUNG`` on
        separate lines; recognising the first as bare lets the normalizer adopt
        the second instead of leaving a breadcrumb that reads ``Chương I``.
        """
        match = self.match_heading(text)
        if match is None:
            return False
        return not text.strip()[match.prefix_end :].strip()


DEFAULT_PROFILE: Final = StructureProfile()
