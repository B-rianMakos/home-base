"""Events grouped under month headers (WO-FS-003 EM-1 to EM-5). Plain data, no Qt, so it can be tested directly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")


@dataclass
class EventRow:
    event: dict[str, Any]
    day: str    # the date shown: the event's date, or a repeating event's next occurrence (EM-3)
    past: bool  # kept for 30 days after it ended, shown dimmer (EM-4)


@dataclass
class MonthGroup:
    month: str  # "2026-11"
    rows: list[EventRow] = field(default_factory=list)

    @property
    def label(self) -> str:
        """EM-6: "November 2026 (4)"."""
        year, month = self.month.split("-")
        return f"{MONTH_NAMES[int(month) - 1]} {year}"  # WO-FS-008 ST-1: no count


def group_by_month(upcoming: list[dict[str, Any]], past: list[dict[str, Any]]) -> list[MonthGroup]:
    """Months in date order, each sorted by date and start time (EM-1, EM-2). Past events sort before upcoming
    ones, so their months come above the current month (EM-4). Months with no events are left out (EM-5)."""
    rows = [EventRow(e, e["date"], True) for e in past] + [EventRow(e, e["next_date"], False) for e in upcoming]
    rows.sort(key=lambda r: (r.day, r.event["start_time"], not r.past, r.event["title"]))
    groups: dict[str, MonthGroup] = {}
    for row in rows:
        groups.setdefault(row.day[:7], MonthGroup(row.day[:7])).rows.append(row)
    return list(groups.values())
