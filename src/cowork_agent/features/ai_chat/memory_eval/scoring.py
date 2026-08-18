"""Grading one reply against one probe (SPEC §8).

Four outcomes, not pass/fail. A system that says "I don't know" is behaving
correctly under uncertainty; a system that confidently returns a superseded
answer, or invents one, is dangerous. Both look identical on a boolean.

Everything here is a pure function over strings so the whole judgment layer is
testable with no model, no key, and no network. Only the refusal branch is
uncertain, and it says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .probes import Probe


class Outcome(StrEnum):
    PASS = "pass"
    STALE = "stale"
    INVENTED = "invented"
    MISS = "miss"


# How models decline when they genuinely have nothing. Deliberately about the
# ABSENCE OF KNOWLEDGE, not politeness — "I'm sorry" also opens plenty of
# confidently wrong answers, so it is not on this list.
#
# This list will never be complete. Every verdict resting on it is returned
# with certain=False so a judge can settle it (SPEC §8.3); a missed phrasing
# would otherwise score an honest refusal as INVENTED, which is the worst
# direction to be wrong in.
REFUSAL_PHRASES: tuple[str, ...] = (
    "don't know",
    "do not know",
    "not sure",
    "no information",
    "no record",
    "never told",
    "never mentioned",
    "never gave",
    "didn't tell",
    "did not tell",
    "didn't mention",
    "did not mention",
    "haven't told",
    "have not told",
    "you haven't",
    "you have not",
    "not in my memory",
    "don't have",
    "do not have",
    "nothing about",
    "nothing shared",
    "no details",
    "not specified",
    "wasn't specified",
    "unable to find",
    "couldn't find",
    "could not find",
    "khong co thong tin",
    "khong ro",
)


@dataclass(frozen=True, slots=True)
class ScoreResult:
    outcome: Outcome
    certain: bool
    why: str


def _has(haystack: str, needles: Sequence[str]) -> bool:
    low = haystack.casefold()
    return any(needle.casefold() in low for needle in needles)


def score(reply: str, probe: Probe) -> ScoreResult:
    """Grade one reply. Returns the outcome, whether it is certain, and why.

    ``certain=False`` marks a verdict that rests on REFUSAL_PHRASES and is
    therefore worth one judge call. Everything else is a substring check and
    needs no model at all.
    """

    reply = reply or ""

    if probe.expect_refusal:
        if _has(reply, REFUSAL_PHRASES):
            return ScoreResult(Outcome.PASS, False, "declined, as it should")
        return ScoreResult(
            Outcome.INVENTED, False, "answered a question it was never given the answer to"
        )

    # STALE fires only when the expected answer is ABSENT. A reply that gives
    # the right answer and also mentions the superseded one is a good reply.
    if probe.expect_any and not _has(reply, probe.expect_any):
        if probe.stale_any and _has(reply, probe.stale_any):
            return ScoreResult(
                Outcome.STALE, True, f"asserted the superseded answer ({probe.stale_any[0]})"
            )
        return ScoreResult(Outcome.MISS, True, "expected answer absent")

    if probe.expect_all:
        missing = [item for item in probe.expect_all if not _has(reply, (item,))]
        if missing:
            return ScoreResult(Outcome.MISS, True, f"only part of the answer - missing {missing}")

    if not probe.expect_any and probe.stale_any and _has(reply, probe.stale_any):
        return ScoreResult(Outcome.STALE, True, "asserted a superseded answer")

    return ScoreResult(Outcome.PASS, True, "correct")
