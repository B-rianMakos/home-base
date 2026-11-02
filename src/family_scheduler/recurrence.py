"""Repeat rules for events and tasks (EV-3, TK-2, EV-10, EV-13)."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrule

REPEATS = ("none", "daily", "weekly", "monthly", "yearly")
_FREQ: dict[str, Any] = {"daily": DAILY, "weekly": WEEKLY, "monthly": MONTHLY, "yearly": YEARLY}
_STEP = {
    "daily": relativedelta(days=1),
    "weekly": relativedelta(weeks=1),
    "monthly": relativedelta(months=1),
    "yearly": relativedelta(years=1),
}
_NOUN = {"daily": "daily", "weekly": "weekly", "monthly": "monthly", "yearly": "yearly"}

Interval = tuple[datetime, datetime]


def rrule_line(repeat: str) -> str | None:
    """The RRULE sent to Google Calendar, or None for a one-off event."""
    if repeat == "none":
        return None
    return f"RRULE:FREQ={repeat.upper()}"


def occurrences(
    day: date,
    start: time,
    end: time,
    repeat: str,
    tz_name: str,
    window_start: datetime,
    window_end: datetime,
) -> list[Interval]:
    """UTC start/end of every occurrence that overlaps the window.

    Occurrences are generated on the wall clock and then converted, so a 4:00 pm event
    stays at 4:00 pm local time across daylight saving changes, as Google does.
    Monthly events on the 29th-31st skip months without that day, also as Google does.
    """
    tz = ZoneInfo(tz_name)

    def utc(d: date, t: time) -> datetime:
        return datetime.combine(d, t, tzinfo=tz).astimezone(window_start.tzinfo)

    if repeat == "none":
        s, e = utc(day, start), utc(day, end)
        return [(s, e)] if s < window_end and e > window_start else []
    local_from = window_start.astimezone(tz).replace(tzinfo=None) - timedelta(days=1)
    local_to = window_end.astimezone(tz).replace(tzinfo=None)
    rule = rrule(_FREQ[repeat], dtstart=datetime.combine(day, start))
    found: list[Interval] = []
    for occ in rule.between(local_from, local_to, inc=True):
        s, e = utc(occ.date(), start), utc(occ.date(), end)
        if s < window_end and e > window_start:
            found.append((s, e))
    return found


def occurs_on(day: date, repeat: str, on: date) -> bool:
    if repeat == "none":
        return day == on
    if on < day:
        return False
    rule = rrule(_FREQ[repeat], dtstart=datetime.combine(day, time()))
    hit = rule.after(datetime.combine(on, time()), inc=True)
    return hit is not None and hit.date() == on


def next_occurrence_date(day: date, repeat: str, on_or_after: date) -> date | None:
    if repeat == "none":
        return day if day >= on_or_after else None
    rule = rrule(_FREQ[repeat], dtstart=datetime.combine(day, time()))
    hit = rule.after(datetime.combine(max(day, on_or_after), time()), inc=True)
    return hit.date() if hit else None


def count_in_next_year(day: date, repeat: str, today: date) -> int:
    """How many occurrences fall in the next 12 months (for the EV-10 message)."""
    if repeat == "none":
        return 1
    rule = rrule(_FREQ[repeat], dtstart=datetime.combine(day, time()))
    start = datetime.combine(max(day, today), time())
    end = datetime.combine(today + relativedelta(years=1), time()) - timedelta(seconds=1)
    return len(rule.between(start, end, inc=True))


def describe(repeat: str) -> str:
    return _NOUN.get(repeat, "")


def repeat_anchor(due: date, repeat: str) -> tuple[int | None, int | None]:
    """WO-FS-013 MR-1: the repeat day a task keeps: (day, None) monthly, (day, month) yearly, else (None, None)."""
    if repeat == "monthly":
        return due.day, None
    if repeat == "yearly":
        return due.day, due.month
    return None, None


def on_day(year: int, month: int, day: int) -> date:
    """The day in that month, or the month's last day if it's shorter (MR-2, MR-3)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_due_date(due: date, repeat: str, today: date, repeat_day: int | None = None,
                  repeat_month: int | None = None) -> date:
    """Next due date of a repeating task (TK-2). The only place the rule lives (WO-FS-013 CO-1).

    Counted from the old due date, not from today, so a Monday task stays on Mondays, and it keeps advancing until the
    date is today or later (MR-5). Monthly and yearly tasks land on their repeat day, or the last day of a shorter
    month, so Jan 31 -> Feb 28 -> Mar 31 and Feb 29 comes back in leap years (MR-2 to MR-4). Without a repeat day
    (an older row), the due date's own day is used.
    """
    n = 1
    while True:
        if repeat == "monthly":
            months = due.month - 1 + n
            candidate = on_day(due.year + months // 12, months % 12 + 1, repeat_day or due.day)
        elif repeat == "yearly":
            candidate = on_day(due.year + n, repeat_month or due.month, repeat_day or due.day)
        else:
            candidate = due + _STEP[repeat] * n
        if candidate >= today:
            return candidate
        n += 1


def ordinal(day: int) -> str:
    """1st, 2nd, 3rd, 4th … 31st."""
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def month_end_rule(repeat: str, day: int | None, month: int | None) -> str:
    """MR-8: the line under Repeats when the repeat day doesn't exist in every month, or "" when it always does."""
    if repeat == "monthly" and day and day >= 29:
        return f"Every month on the {ordinal(day)}, or the last day in shorter months."
    if repeat == "yearly" and day == 29 and month == 2:
        return "Every year on February 29, or February 28 when there's no 29th."
    return ""
