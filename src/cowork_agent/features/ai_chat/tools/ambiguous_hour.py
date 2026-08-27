"""Refuse an hour the user named but did not determine.

`PROGRESS.md` §5 F5/F7: *"Tạo lịch gym 2 giờ thứ Sáu"* names an hour -- `2 giờ`
-- that is 02:00 or 14:00 with nothing in the message to separate them. Two
attempts to fix that in the argument-filling prompt failed, and the second made
it worse: the model stopped defaulting to a working hour and started resolving
to 14:00 and writing a real event. So this is a guard, in code, sitting beside
the range and schema checks -- the shape every other safety property in this
package already has.

It reads the user's message rather than the filled arguments on purpose. The
filler always picks *an* hour; the arguments cannot say whether that hour came
from the user or from the model. Only the message can.

The guard fails closed. A qualifier written without Vietnamese diacritics
(`2 gio sang`) is not recognised, so the message reads as undetermined and the
turn costs a question. That is deliberate: `sáng` stripped of its diacritics is
`sang`, the preposition in *"dời sang 2 giờ"* -- accepting the bare form would
make exactly the reschedule phrasing this guard exists to catch look qualified.
"""

from __future__ import annotations

import re

# Only a 1..12 reading is undetermined. `0` and `13`..`23` already say which
# half of the day they are in.
_FIRST_AMBIGUOUS_HOUR = 1
_LAST_AMBIGUOUS_HOUR = 12

# Every way the QA stories and ordinary Vietnamese or English chat write a clock
# time. Each alternative captures the hour under its own name so the leading
# zero in `09:00` -- the one form that means 24-hour notation on its own -- stays
# readable after the match.
#
# `giờ` is matched with and without its diacritics while the qualifiers below
# are not, and the asymmetry is the point: recognising the hour in `2 gio sang`
# but not the `sang` is what makes an unaccented message read as undetermined
# rather than as qualified.
_MENTION = re.compile(
    r"""
      (?P<colon_hour>\d{1,2}):\d{2}(?!\d)
    | (?P<vn_hour>\d{1,2})\s*gi(?:ờ|o(?![^\W\d_]))
    | (?P<short_hour>\d{1,2})h(?:\d{2})?(?![^\W\d_])
    | \b(?:at|lúc|luc)\s+(?P<lead_hour>\d{1,2})(?!\d)
      (?!\s*(?:tháng|ngày|tuần|phút|giây|thang|ngay|tuan|phut|giay|minutes?|days?|weeks?))
    | (?P<oclock_hour>\d{1,2})\s*o['’]?\s*clock
    """,
    re.VERBOSE | re.IGNORECASE,
)
_HOUR_GROUPS = ("colon_hour", "vn_hour", "short_hour", "lead_hour", "oclock_hour")

# What separates 02:00 from 14:00. The English half is word-bounded because `am`
# is a substring of ordinary words; the Vietnamese half is not, because its
# diacritics already make each one unmistakable.
_QUALIFIER = re.compile(
    r"\b(?:a\.?m\.?|p\.?m\.?|morning|afternoon|evening|night|noon|midnight)\b"
    r"|sáng|trưa|chiều|tối|đêm|khuya",
    re.IGNORECASE,
)

# How far from the hour a qualifier still belongs to it. Vietnamese puts it
# immediately after (`2 giờ sáng`); English can put it either side and a few
# words away (`at 2 in the afternoon`, `tomorrow morning at 9`).
_WINDOW = 24


def ambiguous_hour_question(message: str) -> str | None:
    """The question to ask when `message` names an hour it does not determine.

    ``None`` when every hour it names is determined, or when it names none at
    all -- a message with no clock time is the argument filler's problem, not
    this guard's.
    """

    for match in _MENTION.finditer(message):
        hour = _undetermined_hour(match, message)
        if hour is not None:
            return _question(hour)
    return None


def _undetermined_hour(match: re.Match[str], message: str) -> int | None:
    raw = next(
        (match.group(name) for name in _HOUR_GROUPS if match.group(name) is not None),
        None,
    )
    if raw is None:
        return None
    # `09:00` is 24-hour notation; `9:00` is not. Nobody pads an hour they mean
    # on a 12-hour clock, so the padding is itself the qualifier -- which is why
    # the raw text, and not just its integer value, is kept this far.
    if len(raw) == 2 and raw.startswith("0"):
        return None
    hour = int(raw)
    if not _FIRST_AMBIGUOUS_HOUR <= hour <= _LAST_AMBIGUOUS_HOUR:
        return None
    before = message[max(0, match.start() - _WINDOW) : match.start()]
    after = message[match.end() : match.end() + _WINDOW]
    if _QUALIFIER.search(before) or _QUALIFIER.search(after):
        return None
    return hour


def _question(hour: int) -> str:
    """Both readings, spelled out, and nothing quoted from the message.

    The hour is re-rendered from the parsed integer rather than echoed as
    matched text: this string is handed to the reply model, and a guard is a
    poor place to open a path for message content to reach a prompt.
    """

    other = hour + 12 if hour < 12 else 0
    return (
        f"The hour is undetermined: {hour:02d}:00 or {other:02d}:00. Confirm which one was meant."
    )
