"""The Calendar page's dates and order (WO-FS-005 section 3, WO-FS-006 section 2). Plain data, no Qt, so it can be
tested: which days a week or month shows, the order blocks stack in, and how the Week · Month switch keeps your
place."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

WEEK, MONTH = "week", "month"
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_days(start: date) -> list[date]:
    """Monday to Sunday (WV-1)."""
    first = monday(start)
    return [first + timedelta(days=i) for i in range(7)]


def week_label(start: date) -> str:
    """SV-5: "Oct 26 – Nov 1, 2026"."""
    end = start + timedelta(days=6)
    first = f"{start.strftime('%b')} {start.day}" + (f", {start.year}" if start.year != end.year else "")
    return f"{first} – {end.strftime('%b')} {end.day}, {end.year}"


def month_label(any_day: date) -> str:
    """SV-5: "November 2026"."""
    return f"{MONTH_NAMES[any_day.month - 1]} {any_day.year}"


def first_of_month(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, months: int) -> date:
    """The first of the month `months` away from d's month."""
    index = d.year * 12 + d.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_grid(any_day: date) -> list[list[date]]:
    """MV-1: the weeks, Monday to Sunday, that show a month: five or six rows (four-week months get the next
    week too, so the grid keeps its size). Days outside the month are shown dimmed by the view."""
    first = first_of_month(any_day)
    last = add_months(first, 1) - timedelta(days=1)
    start = monday(first)
    rows = ((last - start).days // 7) + 1
    return [[start + timedelta(days=7 * r + c) for c in range(7)] for r in range(max(rows, 5))]


def short_time(hhmm: str) -> str:
    """MV-3: "6 pm", "4:15 pm"; "All day" for an all-day event (WO-FS-008 AD-8)."""
    if not hhmm:
        return "All day"
    hours, minutes = (int(x) for x in hhmm.split(":"))
    suffix = "am" if hours < 12 else "pm"
    return f"{(hours % 12) or 12}{f':{minutes:02d}' if minutes else ''} {suffix}"


def day_items(events: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """SB-2, SB-4, SB-10: a day's events, earliest first (then by end time, then title), then its tasks: overdue
    first, then open by priority, then done."""
    ordered_events = sorted(events, key=lambda e: (e["start_time"], e["end_time"], e["title"].lower()))
    ordered_tasks = sorted(tasks, key=lambda t: (t.get("done", False), not t.get("overdue", False),
                                                 PRIORITY_ORDER.get(t.get("effective_priority", t.get("priority", "")),
                                                                    3), t["title"].lower()))
    return [("event", e) for e in ordered_events] + [("task", t) for t in ordered_tasks]


def split_items(items: list[Any], room: int) -> tuple[list[Any], int]:
    """MV-4: what fits in a month cell, and how many go behind "+N more" (which takes one of the places)."""
    if len(items) <= room:
        return items, 0
    shown = items[:max(room - 1, 0)]
    return shown, len(items) - len(shown)


# --- the Week · Month switch and ‹ › Today (SV-2 to SV-4) ------------------------------------------------------

def anchor_for_today(view: str, today: date) -> date:
    """SV-3 Today: the period that contains today. A week is anchored on its Monday, a month on its first day."""
    return monday(today) if view == WEEK else first_of_month(today)


def step(view: str, anchor: date, direction: int) -> date:
    """SV-3: ‹ and › move by one week, or one month."""
    return anchor + timedelta(days=7 * direction) if view == WEEK else add_months(anchor, direction)


def switch(to_view: str, anchor: date, today: date) -> date:
    """SV-4: Week → Month shows the month of the week you were looking at (the month its Thursday is in, so a week
    across two months goes to the one it mostly belongs to). Month → Week shows the week containing today if today
    is in that month, otherwise the month's first week."""
    if to_view == MONTH:
        return first_of_month(monday(anchor) + timedelta(days=3))
    month = first_of_month(anchor)
    if first_of_month(today) == month:
        return monday(today)
    return monday(month)


def period_label(view: str, anchor: date) -> str:
    return week_label(monday(anchor)) if view == WEEK else month_label(anchor)


def fetch_range(view: str, anchor: date) -> tuple[date, int]:
    """The days the hub is asked for: one week, or the whole month grid (35 or 42 days)."""
    if view == WEEK:
        return monday(anchor), 7
    grid = month_grid(anchor)
    return grid[0][0], len(grid) * 7
