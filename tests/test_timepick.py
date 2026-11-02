"""WO-FS-008 section 3: the time wheels' rules (TM-1 to TM-7)."""

from __future__ import annotations

from datetime import datetime

import pytest

from family_scheduler.app import timepick as tp


def test_the_wheels_hold_1_to_12_and_five_minute_steps() -> None:  # TM-1
    assert list(range(1, 13)) == tp.HOURS
    assert tp.MINUTES == [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]


def test_the_wheels_wrap() -> None:  # TM-2
    assert tp.step(tp.HOURS, 12, 1) == 1 and tp.step(tp.HOURS, 1, -1) == 12
    assert tp.step(tp.MINUTES, 55, 1) == 0 and tp.step(tp.MINUTES, 0, -1) == 55
    assert tp.step(tp.MINUTES, 10, 1) == 15  # one place is five minutes


@pytest.mark.parametrize(("hhmm", "parts", "shown"), [
    ("00:00", (12, 0, False), "12:00 am"),   # midnight
    ("00:05", (12, 5, False), "12:05 am"),
    ("11:55", (11, 55, False), "11:55 am"),
    ("12:00", (12, 0, True), "12:00 pm"),    # noon
    ("12:30", (12, 30, True), "12:30 pm"),
    ("16:15", (4, 15, True), "4:15 pm"),
    ("23:55", (11, 55, True), "11:55 pm"),
])
def test_12_am_and_12_pm(hhmm: str, parts: tuple[int, int, bool], shown: str) -> None:
    assert tp.to_parts(hhmm) == parts
    assert tp.from_parts(*parts) == hhmm
    assert tp.fmt(hhmm) == shown  # TM-7: lowercase


def test_the_times_in_words() -> None:  # TM-3
    assert tp.summary("16:15", "17:00") == "4:15 pm to 5:00 pm (45 min)"
    assert tp.summary("09:00", "10:00") == "9:00 am to 10:00 am (1 h)"
    assert tp.summary("11:30", "13:15") == "11:30 am to 1:15 pm (1 h 45 min)"


@pytest.mark.parametrize(("now", "start", "end"), [
    ((16, 12), "16:15", "17:15"),  # the next 5-minute step
    ((16, 15), "16:20", "17:20"),  # already on a step: the next one
    ((8, 58), "09:00", "10:00"),
    ((23, 30), "22:55", "23:55"),  # late at night: still ends on the same day
])
def test_a_new_event_starts_at_the_next_step_and_lasts_an_hour(now: tuple[int, int], start: str, end: str) -> None:
    assert tp.default_times(datetime(2026, 10, 30, *now)) == (start, end)  # TM-4


def test_the_end_follows_the_start() -> None:  # TM-4: the length stays the same
    assert tp.follow_start("16:00", "16:30", "17:00") == "17:30"
    assert tp.follow_start("16:00", "15:00", "16:45") == "15:45"
    assert tp.follow_start("16:00", "23:30", "17:00") == "23:55"  # as far as the day allows


def test_the_end_must_be_after_the_start() -> None:  # TM-5
    assert tp.problem("16:00", "17:00") == ""
    assert tp.problem("16:00", "16:00") == "The end must be after the start, on the same day."
    assert tp.problem("16:00", "15:00") != ""


def test_an_odd_minute_is_kept_for_that_event_only() -> None:  # TM-6
    values = tp.minute_values(12)
    assert values == [0, 5, 10, 12, 15, 20, 25, 30, 35, 40, 45, 50, 55]
    assert tp.step(values, 10, 1) == 12 and tp.step(values, 12, 1) == 15
    assert tp.minute_values(15) == tp.MINUTES  # every other event: fives only
    assert list(range(0, 60, 5)) == tp.MINUTES  # not changed by the odd-minute event
