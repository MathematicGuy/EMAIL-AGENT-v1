"""Grading one reply against one probe (SPEC §8).

Four outcomes, not pass/fail. A system that says "I don't know" is behaving
correctly under uncertainty; a system that confidently returns a superseded
answer, or invents one, is dangerous. Both look identical on a boolean.

Everything here is a pure function over strings so the whole judgment layer is
testable with no model, no key, and no network. Only the refusal branch is
uncertain, and it says so rather than pretending otherwise.
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
# This list will never be complete, so every verdict resting on it is returned
# with certain=False and counted in the report's `needs_reading`. A missed
# phrasing scores an honest refusal as INVENTED — the worst direction to be
# wrong in — so those rows are flagged for a human to read in runs/ rather than
# silently trusted.
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
    # The product answers in Vietnamese unconditionally — the chat system
    # prompt says so, and says stored_preference.language never overrides it.
    # An English-only list therefore scored every honest Vietnamese refusal as
    # INVENTED, which is the worst direction to be wrong in. The unaccented
    # forms below are kept but match nothing the model actually writes:
    # casefold does not strip diacritics, so "khong ro" never meets "không rõ".
    "không có thông tin",
    "không tìm thấy",
    "không thể thấy",
    "không có dữ liệu",
    "không được cung cấp",
    "chưa có thông tin",
    "chưa tìm thấy",
    "chưa được cung cấp",
    "không rõ",
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

    ``certain=False`` marks a verdict that rests on REFUSAL_PHRASES and should
    be read by a human before it is believed. Everything else is a substring
    check against text the probe set declared, and is certain by construction.
    """

    reply = reply or ""

    if not reply.strip():
        # No text at all is not an answer, and on a restraint probe the refusal
        # branch below would call it INVENTED — the headline failure — for a
        # turn where the model never spoke. An errored turn looks exactly like
        # this; `ask_once` returns the error separately so a reader can tell
        # which it was. Uncertain by construction: it needs a human.
        return ScoreResult(Outcome.MISS, False, "no reply text: the turn produced nothing")

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

    return ScoreResult(Outcome.PASS, True, "correct")
