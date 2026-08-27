"""The ambiguous-hour guard: which messages determine an hour and which do not.

`PROGRESS.md` §5 F5/F7. The property is one-directional on purpose -- a message
that does not determine its hour must be refused, and a message that does must
not be. The second half is the one that costs something when it is wrong, so it
carries every phrasing the QA stories use.
"""

from cowork_agent.features.ai_chat.tools import ambiguous_hour_question

DETERMINED = [
    # Every happy-path phrasing in tests/fixtures/tool_intent/tool_intent_qa.json.
    "Tạo lịch tập gym lúc 2 giờ sáng thứ Sáu.",
    "Create a gym session Friday at 2AM.",
    "Đặt lịch họp team 3 giờ chiều mai.",
    "Thêm lịch khám răng ngày 3 tháng 9 lúc 9h sáng.",
    "Tạo lịch gym 2 giờ sáng thứ Sáu, tập khoảng 90 phút.",
    "Tạo lịch gym thứ 2, 4, 6 hàng tuần lúc 6 giờ sáng.",
    "Dời lịch gym thứ Sáu sang 3 giờ chiều giúp tôi.",
    # A 24-hour clock says which half of the day it is in by itself.
    "Họp lúc 14:00 ngày mai.",
    "Đặt lịch 20h tối nay.",
    "Đặt lịch 20h.",
    # ... and so does a padded hour: nobody writes 09 on a 12-hour clock.
    "Họp lúc 09:00 ngày mai.",
    # An English qualifier a few words away still belongs to the hour.
    "Meeting tomorrow at 3 in the afternoon.",
    "Tomorrow morning at 9, please.",
    # No hour at all is the argument filler's problem, not this guard's.
    "Nhắc tôi đi tập gym nhé.",
    "Đừng tạo lịch nhé, chỉ tư vấn thôi: nên tập gym lúc mấy giờ là tốt nhất?",
    "Dựa vào tài liệu này, giúp tôi tạo lịch học trong 4 tuần tới.",
    "Google Calendar có tính năng nhắc lặp lại hàng tuần không?",
    "",
]

UNDETERMINED = [
    # tq-005 itself.
    "Tạo lịch gym 2 giờ thứ Sáu.",
    # The reschedule phrasing: `sang` is a preposition here, not `sáng`.
    "Dời lịch gym sang 2 giờ.",
    "Book a slot at 3.",
    "Họp lúc 9:00 ngày mai.",
    "Đặt lịch 8h thứ Ba.",
    "Set it for 12 o'clock.",
]


def test_a_message_that_determines_its_hour_is_left_alone() -> None:
    for message in DETERMINED:
        assert ambiguous_hour_question(message) is None, f"Determined: {message!r}"


def test_a_stated_but_undetermined_hour_is_refused() -> None:
    for message in UNDETERMINED:
        assert ambiguous_hour_question(message) is not None, f"Undetermined: {message!r}"


def test_the_question_offers_both_readings_and_quotes_nothing() -> None:
    """The text reaches the reply model, so it says what to ask -- and says it
    without carrying any of the message across."""

    question = ambiguous_hour_question("Tạo lịch gym 2 giờ thứ Sáu.")

    assert question is not None
    assert "02:00" in question and "14:00" in question
    assert "gym" not in question


def test_twelve_pairs_with_midnight_rather_than_twenty_four() -> None:
    question = ambiguous_hour_question("Set it for 12 o'clock.")

    assert question is not None
    assert "12:00" in question and "00:00" in question


def test_a_qualifier_written_without_diacritics_fails_closed() -> None:
    """`sáng` stripped of its diacritics is `sang`, the preposition in 'dời sang
    2 giờ'. Reading the bare form as a qualifier would wave through exactly the
    phrasing this guard exists to catch, so an unaccented message costs a
    question instead."""

    assert ambiguous_hour_question("Tao lich gym 2 gio sang thu Sau.") is not None
