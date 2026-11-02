"""The Cars today bar's rules (WO-FS-010 CB-a to CB-j). Plain data, no Qt, so it can be tested.

The day runs from midnight to midnight, in minutes 0 to 1440. Each part of the bar is coloured by how many cars are in
use then: none (free), some but not all (some), or all of them, or a car double-booked (all).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DAY = 24 * 60
FREE, SOME, ALL = "free", "some", "all"


@dataclass
class Part:
    start: int                 # minutes from midnight
    end: int
    level: str                 # FREE, SOME or ALL
    uses: list[dict[str, Any]] = field(default_factory=list)  # the car uses in this part

    def cars(self) -> list[str]:
        return sorted({u["car"] for u in self.uses})


def parts(uses: list[dict[str, Any]], car_count: int) -> list[Part]:
    """CB-c, CB-d: the bar's parts, left to right, joined where nothing changes."""
    edges = sorted({0, DAY} | {max(0, min(DAY, u[k])) for u in uses for k in ("start", "end")})
    out: list[Part] = []
    for start, end in zip(edges, edges[1:], strict=False):
        if start >= end:
            continue
        here = [u for u in uses if u["start"] < end and start < u["end"]]
        cars = [u["car_id"] for u in here]
        double = len(cars) != len(set(cars))  # one car needed by two events at once (CR-7)
        in_use = len(set(cars))
        level = FREE if not in_use else ALL if in_use >= car_count or double else SOME
        if out and out[-1].level == level and _same(out[-1].uses, here):
            out[-1].end = end
        else:
            out.append(Part(start, end, level, here))
    return out


def _same(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    return sorted(u["event_id"] for u in a) == sorted(u["event_id"] for u in b)


def clock(minutes: int) -> str:
    """"1:00 pm"; 0 and 1440 are both "12:00 am" (CB-b)."""
    hours, mins = divmod(minutes % DAY, 60)
    return f"{(hours % 12) or 12}:{mins:02d} {'am' if hours < 12 else 'pm'}"


def hour_label(minutes: int) -> str:
    """CB-b: "12 am", "6 am", "12 pm", "6 pm"."""
    hours = (minutes // 60) % 24
    return f"{(hours % 12) or 12} {'am' if hours < 12 else 'pm'}"


def describe(part: Part) -> str:
    """CB-e: "2:00–3:00 pm · Subaru Crosstrek: Dentist · Chevrolet Cruze: Hockey"."""
    span = f"{clock(part.start)}–{clock(part.end)}"
    if not part.uses:
        return f"{span} · no car in use"
    return " · ".join([span, *(f"{u['car']}: {u['title']}" for u in sorted(part.uses, key=lambda u: u["car"]))])


def legend(car_count: int) -> list[tuple[str, str]]:
    """CB-g: the legend's wording follows the number of cars."""
    if car_count == 1:
        return [(FREE, "The car is free"), (ALL, "The car is in use")]
    if car_count == 2:
        return [(FREE, "Both cars free"), (SOME, "One car in use"), (ALL, "Both cars in use")]
    return [(FREE, "All cars free"), (SOME, "Some cars in use"), (ALL, "All cars in use")]


def summary(uses: list[dict[str, Any]], car_count: int) -> str:
    """CB-h: the line under the bar."""
    if uses:
        busy = len({u["car_id"] for u in uses})
        return f"{busy} of {car_count} car{'s' if car_count != 1 else ''} needed today"
    if car_count == 1:
        return "The car is free today"
    return "Both cars are free today" if car_count == 2 else "All cars are free today"
