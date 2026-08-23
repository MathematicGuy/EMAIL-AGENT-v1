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
    #: The turn produced no text at all. Not a grade — the absence of one.
    #: MISS means "memory had it and the answer did not"; a turn where the
    #: provider never replied says nothing about memory either way, and folding
    #: the two together fed three conclusions of the first Vietnamese run on
    #: evidence that was a brief outage.
    NO_ANSWER = "no_answer"


# How models decline when they genuinely have nothing. Deliberately about the
# ABSENCE OF KNOWLEDGE, not politeness — "I'm sorry" also opens plenty of
# confidently wrong answers, so it is not on this list.
#
# This list will never be complete, so a verdict resting on it is returned with
# certain=False and counted in the report's `needs_reading`. A missed phrasing
# scores an honest refusal as INVENTED — the worst direction to be wrong in — so
# those rows are flagged for a human to read in runs/ rather than silently
# trusted.
#
# The one exception is a decline that matched AND whose probe declared
# `invented_any`: that verdict does not rest on this list alone. See `score`.
# Vietnamese says "I have nothing" as a phrase for HAVING NOTHING followed by a
# word for WHAT is missing. Those two choices are independent, so the phrasings
# are a grid — and a grid written out as flat strings comes out half filled. It
# did: "không có thông tin", "không có dữ liệu" and "chưa có thông tin" were on
# the list and "chưa có dữ liệu" was not. One reply used the missing cell, was
# graded INVENTED, and made semantic memory the single most severe conclusion in
# the whole report.
#
# Generating the grid is the fix. A new way of saying it is one entry in one of
# these two tuples, and every combination of it exists from that moment on.
_HAVING_NOTHING: tuple[str, ...] = (
    "không có",
    "chưa có",
    "không tìm thấy",
    "chưa tìm thấy",
    "không nhận được",
    "chưa nhận được",
    "không thấy",
    "chưa thấy",
    "không thể thấy",
    "không thể tìm thấy",
    "không truy cập được",
    "chưa truy cập được",
    # Document-centric active forms. Passives stay standalone in REFUSAL_PHRASES.
    "không cung cấp",
    "chưa cung cấp",
    "không đề cập",
    "chưa đề cập",
    "không đề cập đến",
    "chưa đề cập đến",
    # Quantity hedge between lack verb and noun (do not free-particle "đủ").
    "không có đủ",
    "chưa có đủ",
)
_WHAT_IS_MISSING: tuple[str, ...] = (
    "thông tin",
    "dữ liệu",
    "tài liệu",
    "dữ kiện",
    "chi tiết",
    "ghi nhận",
    "nội dung",
)
#: The two must be ADJACENT to count. A looser rule — the words appearing
#: anywhere in the same reply — would pass "tôi không chắc, nhưng chính sách
#: cho phép ba tháng", which is an invention wearing a hedge, and every
#: restraint question would pass forever.
_VIETNAMESE_REFUSAL_GRID: tuple[str, ...] = tuple(
    f"{lack} {thing}" for lack in _HAVING_NOTHING for thing in _WHAT_IS_MISSING
)

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
    #
    # These are the phrasings that carry no noun of their own. The ones that do
    # are generated below, because writing them out by hand is what broke.
    "không tìm thấy",
    "không thể thấy",
    "không được cung cấp",
    "chưa tìm thấy",
    "chưa được cung cấp",
    "không rõ",
    "khong co thong tin",
    "khong ro",
    *_VIETNAMESE_REFUSAL_GRID,
)


def refusal_phrases_for(probe: Probe) -> tuple[str, ...]:
    """The shared list, plus the grid over whatever noun this probe declares.

    `_WHAT_IS_MISSING` is a closed list of words for a KIND OF KNOWLEDGE, so it
    only catches a decline phrased about knowing — "I have no information about
    your job title". A model that declines by naming the thing asked for —
    "tôi không có chức danh cụ thể" — hits no cell of the shared grid and was
    graded INVENTED for it, which is the worst direction to be wrong in.

    That noun is different for every restraint probe, so widening the shared
    list would mean pasting each probe's vocabulary into the scorer and would
    still miss the next probe. The probe declares it instead, and it combines
    with every way of having nothing, on the same adjacency rule.
    """

    if not probe.refusal_about:
        return REFUSAL_PHRASES
    return REFUSAL_PHRASES + tuple(
        f"{lack} {thing}" for lack in _HAVING_NOTHING for thing in probe.refusal_about
    )


def _declined_about_a_split_noun(reply: str, nouns: Sequence[str]) -> bool:
    """Whether a decline names the HEAD of a declared noun and its rest later.

    The adjacency rule wants the whole declared phrase to sit next to the word
    for having nothing. Models do not oblige: asked which form to use, the v3
    reply said "Không có biểu mẫu CỤ THỂ được đề cập cho việc đổi laptop hỏng"
    — the probe declares "biểu mẫu đổi laptop", the modifier split the phrase
    in two, no cell matched, and a grounded refusal that named no form code was
    graded INVENTED. That is the worst direction to be wrong in.

    So the phrase may be cut anywhere: the part before the cut has to sit next
    to a word for having nothing, and the part after it has to appear somewhere
    in the same reply. The rest is what keeps this honest. Head alone would
    pass "hiện không có chính sách" on a question about sabbatical leave, which
    is a recitation about a DIFFERENT policy wearing a decline — sem_restraint_01
    is built to catch exactly that. Requiring "nghỉ dài hạn" to be present too
    means the decline has to be about the thing that was asked.
    """

    low = reply.casefold()
    for noun in nouns:
        words = noun.casefold().split()
        # Stop one token short of the whole phrase: that cell is already
        # generated by refusal_phrases_for, and this only adds the split forms.
        for cut in range(1, len(words)):
            if " ".join(words[cut:]) not in low:
                continue
            head = " ".join(words[:cut])
            if any(f"{lack} {head}" in low for lack in _HAVING_NOTHING):
                return True
    return False


@dataclass(frozen=True, slots=True)
class ScoreResult:
    outcome: Outcome
    certain: bool
    why: str


def _has(haystack: str, needles: Sequence[str]) -> bool:
    low = haystack.casefold()
    return any(needle.casefold() in low for needle in needles)


def _refusal_is_certain(reply: str, probe: Probe) -> bool:
    """Whether an accepted refusal needs no human to confirm it.

    A declared `invented_any` settles it: the specific invention this question
    was written to catch was checked and absent. Probes that cannot declare one
    — the seed holds no near-miss to name — used to end here uncertain every
    run, which is a person rereading the same honest refusal forever. For those,
    the probe can declare the SHAPE of the answer it withheld instead: a reply
    carrying no digit cannot have supplied a case number or a phone number,
    whatever words it chose. Anything with a digit in it falls through to
    uncertain, which is the safe direction.
    """

    if probe.invented_any:
        return True
    return probe.answer_would_be_numeric and not any(char.isdigit() for char in reply)


def score(reply: str, probe: Probe) -> ScoreResult:
    """Grade one reply. Returns the outcome, whether it is certain, and why.

    ``certain=False`` marks a verdict that rests on REFUSAL_PHRASES alone and
    should be read by a human before it is believed. Everything else is a
    substring check against text the probe set declared, and is certain by
    construction.
    """

    reply = reply or ""

    if not reply.strip():
        # No text at all is not an answer, so it is not graded as one. An
        # errored turn looks exactly like this; `ask_once` returns the error
        # separately so a reader can tell which it was.
        #
        # This is CERTAIN. There is no doubt about what happened and nothing in
        # runs/ for a human to read — the reply is empty. What it needs is a
        # rerun, which is what the row's `unreadable` conclusion asks for.
        return ScoreResult(Outcome.NO_ANSWER, True, "no reply text: the turn produced nothing")

    if probe.expect_refusal:
        if probe.invented_any and _has(reply, probe.invented_any):
            return ScoreResult(
                Outcome.INVENTED, False, "answered a question it was never given the answer to"
            )
        if _has(reply, refusal_phrases_for(probe)) or _declined_about_a_split_noun(
            reply, probe.refusal_about
        ):
            # Certain only when the probe declared the invention it was guarding
            # against — checked above, and absent. The doubt SPEC §6.3 describes
            # runs one way: a phrasing nobody wrote down grades an honest
            # refusal as invention, and that lands on the branch below, not this
            # one. What is left here is the opposite worry, a reply that
            # declines and invents anyway. The adjacency rule already rejects
            # the hedged form, and a declared `invented_any` rules out the
            # specific invention this question was written to catch. With both
            # closed there is nothing left for a person to settle.
            #
            # A probe that declared neither an invention to check nor the
            # shape of the answer it withheld checked nothing, so the row stays
            # uncertain. See `_refusal_is_certain`.
            return ScoreResult(
                Outcome.PASS, _refusal_is_certain(reply, probe), "declined, as it should"
            )
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
