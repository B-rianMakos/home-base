"""The time wheels' rules (WO-FS-008 TM-1 to TM-7). Plain data, no Qt, so it can be tested.

Times are "HH:MM" on the 24-hour clock, as the hub stores them; the wheels show 1 to 12, 00 to 55 in fives, and
am or pm.
"""

from __future__ import annotations

from datetime import datetime

HOURS = list(range(1, 13))
MINUTES = list(range(0, 60, 5))
LATEST_START = 22 * 60 + 55  # a new event starting later than this couldn't end an hour later the same day
LAST_MINUTE = 23 * 60 + 55   # the last time the wheels can show
LENGTH = 60                  # TM-4: a new event lasts an hour


def to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def to_hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def to_parts(hhmm: str) -> tuple[int, int, bool]:
    """"16:15" -> (4, 15, True): hour 1 to 12, minute, pm."""
    total = to_minutes(hhmm)
    hour24, minute = divmod(total, 60)
    return (hour24 % 12) or 12, minute, hour24 >= 12


def from_parts(hour: int, minute: int, pm: bool) -> str:
    """(12, 0, False) is midnight, 00:00; (12, 0, True) is noon, 12:00."""
    hour24 = (hour % 12) + (12 if pm else 0)
    return f"{hour24:02d}:{minute:02d}"


def minute_values(current: int) -> list[int]:
    """TM-6: 00 to 55 in fives, plus an existing event's odd minute (e.g. 12) for that event only."""
    return MINUTES if current in MINUTES else sorted([*MINUTES, current])


def step(values: list[int], current: int, delta: int) -> int:
    """TM-2: a wheel moves by `delta` places and wraps (12 → 1, 55 → 00)."""
    index = values.index(current) if current in values else 0
    return values[(index + delta) % len(values)]


def default_times(now: datetime) -> tuple[str, str]:
    """TM-4: a new event starts at the next 5-minute step after now and ends an hour later."""
    total = now.hour * 60 + now.minute
    start = min((total // 5 + 1) * 5, LATEST_START)
    return to_hhmm(start), to_hhmm(start + LENGTH)


def follow_start(old_start: str, new_start: str, end: str) -> str:
    """TM-4: moving the start moves the end by the same amount, so the length stays the same, as far as the day
    allows."""
    moved = to_minutes(end) + to_minutes(new_start) - to_minutes(old_start)
    return to_hhmm(max(0, min(moved, LAST_MINUTE)))


def problem(start: str, end: str) -> str:
    """TM-5: what's wrong, or "" if the times can be saved."""
    if to_minutes(end) <= to_minutes(start):
        return "The end must be after the start, on the same day."
    return ""


def fmt(hhmm: str) -> str:
    """TM-7: lowercase, as everywhere in the app: "4:15 pm", "12:00 am"."""
    hour, minute, pm = to_parts(hhmm)
    return f"{hour}:{minute:02d} {'pm' if pm else 'am'}"


def summary(start: str, end: str) -> str:
    """TM-3: "4:15 pm to 5:00 pm (45 min)"."""
    if problem(start, end):
        return f"{fmt(start)} to {fmt(end)}"
    length = to_minutes(end) - to_minutes(start)
    hours, minutes = divmod(length, 60)
    parts = ([f"{hours} h"] if hours else []) + ([f"{minutes} min"] if minutes else [])
    return f"{fmt(start)} to {fmt(end)} ({' '.join(parts)})"
