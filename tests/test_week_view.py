"""The Calendar page (WO-FS-005 section 3, WO-FS-006 section 2): stacked week order, the month grid, the Week · Month
switch, and the hub's data for a week or a month."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pytest
from conftest import ALEX, FakeClock, event, task

from family_scheduler.app.week import (
    MONTH,
    WEEK,
    anchor_for_today,
    day_items,
    fetch_range,
    month_grid,
    period_label,
    short_time,
    split_items,
    step,
    switch,
    week_days,
    week_label,
)
from family_scheduler.organizer import Organizer

TODAY = date(2026, 10, 28)  # a Wednesday


def ev(title: str, start: str, end: str) -> dict[str, Any]:
    return {"title": title, "start_time": start, "end_time": end}


def tk(title: str, priority: str = "medium", overdue: bool = False, done: bool = False) -> dict[str, Any]:
    return {"title": title, "priority": priority, "overdue": overdue, "done": done}


def titles(items: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [i["title"] for _, i in items]


# --- SB-2, SB-4, SB-5: the stacked week's order --------------------------------------------------------------

def test_events_stack_earliest_first() -> None:
    items = day_items([ev("Dinner", "18:00", "20:00"), ev("Walk", "07:00", "07:30"), ev("Dentist", "16:00", "17:00")],
                      [])
    assert titles(items) == ["Walk", "Dentist", "Dinner"]


def test_same_start_ties_go_by_end_then_title() -> None:
    items = day_items([ev("B long", "16:00", "18:00"), ev("C short", "16:00", "16:30"),
                       ev("A short", "16:00", "16:30")], [])
    assert titles(items) == ["A short", "C short", "B long"]


def test_tasks_stack_below_events() -> None:
    items = day_items([ev("Dinner", "18:00", "20:00")],
                      [tk("Low", "low"), tk("Done", "high", done=True), tk("High", "high"),
                       tk("Late", "low", overdue=True)])
    assert [k for k, _ in items] == ["event", "task", "task", "task", "task"]
    assert titles(items) == ["Dinner", "Late", "High", "Low", "Done"]  # overdue, then priority, then done


def test_overlapping_events_are_just_stacked_in_time_order() -> None:
    items = day_items([ev("Piano lesson", "16:30", "17:15"), ev("Soccer pickup", "16:15", "16:45")], [])
    assert titles(items) == ["Soccer pickup", "Piano lesson"]


# --- MV-1, MV-4: the month grid ------------------------------------------------------------------------------

def test_month_grid_starts_on_monday_with_dimmed_neighbours() -> None:
    grid = month_grid(date(2026, 10, 15))  # October 2026 starts on a Thursday
    assert len(grid) == 5 and all(len(week) == 7 for week in grid)
    assert grid[0][0] == date(2026, 9, 28) and grid[0][0].weekday() == 0
    assert grid[-1][-1] == date(2026, 11, 1)
    outside = [d for week in grid for d in week if d.month != 10]
    assert outside == [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 11, 1)]


def test_six_week_months() -> None:
    grid = month_grid(date(2026, 11, 1))  # starts on a Sunday, 30 days
    assert len(grid) == 6 and grid[0][0] == date(2026, 10, 26) and grid[-1][-1] == date(2026, 12, 6)


def test_a_four_week_month_still_shows_five_rows() -> None:
    grid = month_grid(date(2027, 2, 10))  # Feb 2027: Monday 1st to Sunday 28th
    assert len(grid) == 5 and grid[0][0] == date(2027, 2, 1) and grid[-1][-1] == date(2027, 3, 7)


def test_plus_n_more() -> None:
    items = list(range(5))
    assert split_items(items[:2], 2) == ([0, 1], 0)
    assert split_items(items, 2) == ([0], 4)  # one item, then "+4 more" (MV-8)
    assert split_items(items, 3) == ([0, 1], 3)


def test_labels_and_short_times() -> None:
    assert week_label(date(2026, 10, 26)) == "Oct 26 – Nov 1, 2026"
    assert week_label(date(2026, 12, 28)) == "Dec 28, 2026 – Jan 3, 2027"
    assert period_label(MONTH, date(2026, 11, 1)) == "November 2026"
    assert [short_time(t) for t in ("18:00", "16:15", "00:30", "12:00")] == ["6 pm", "4:15 pm", "12:30 am", "12 pm"]
    assert week_days(TODAY)[0] == date(2026, 10, 26) and week_days(TODAY)[-1] == date(2026, 11, 1)


# --- SV-2 to SV-4: the Week · Month switch -------------------------------------------------------------------

def test_today_is_the_period_containing_today() -> None:
    assert anchor_for_today(WEEK, TODAY) == date(2026, 10, 26)
    assert anchor_for_today(MONTH, TODAY) == date(2026, 10, 1)


def test_previous_and_next_move_a_week_or_a_month() -> None:
    assert step(WEEK, date(2026, 10, 26), 1) == date(2026, 11, 2)
    assert step(WEEK, date(2026, 10, 26), -1) == date(2026, 10, 19)
    assert step(MONTH, date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert step(MONTH, date(2026, 1, 1), -1) == date(2025, 12, 1)


def test_week_to_month_shows_the_month_of_that_week() -> None:
    assert switch(MONTH, date(2026, 11, 16), TODAY) == date(2026, 11, 1)
    assert switch(MONTH, date(2026, 10, 26), TODAY) == date(2026, 10, 1)  # Oct 26 – Nov 1: mostly October
    assert switch(MONTH, date(2026, 11, 30), TODAY) == date(2026, 12, 1)  # Nov 30 – Dec 6: mostly December


def test_month_to_week_shows_today_or_the_first_week() -> None:
    assert switch(WEEK, date(2026, 10, 1), TODAY) == date(2026, 10, 26)  # today's week
    assert switch(WEEK, date(2026, 12, 1), TODAY) == date(2026, 11, 30)  # December's first week


def test_the_hub_is_asked_for_a_week_or_the_whole_grid() -> None:
    assert fetch_range(WEEK, date(2026, 10, 26)) == (date(2026, 10, 26), 7)
    assert fetch_range(MONTH, date(2026, 10, 1)) == (date(2026, 9, 28), 35)
    assert fetch_range(MONTH, date(2026, 11, 1)) == (date(2026, 10, 26), 42)


# --- the hub's data (WB-5, WB-6, WB-8, MV-5) -----------------------------------------------------------------

def test_repeating_events_show_on_every_day_they_occur(org: Organizer) -> None:
    """Tuesday Oct 27, 2026: the week is Mon Oct 26 to Sun Nov 1."""
    org.add_event(ALEX, event("Walk", day="2026-10-20", start="07:00", end="07:30", repeat="daily"))
    org.add_event(ALEX, event("Swim", day="2026-10-01", start="17:30", end="18:30", repeat="weekly"))
    org.add_event(ALEX, event("Dentist", day="2026-11-03"))  # next week
    week = org.week_view(date(2026, 10, 28))
    assert week["week_start"] == "2026-10-26" and week["week_end"] == "2026-11-01"
    days = [e["day"] for e in week["week_events"] if e["title"] == "Walk"]
    assert days == [f"2026-10-{d}" for d in range(26, 32)] + ["2026-11-01"]
    assert [e["day"] for e in week["week_events"] if e["title"] == "Swim"] == ["2026-10-29"]
    assert "Dentist" not in {e["title"] for e in week["week_events"]}
    # The clock is Tuesday 8:00 am: Monday's and Tuesday's 7:00 walks are over (WB-8).
    assert [e["past"] for e in week["week_events"] if e["title"] == "Walk"][:3] == [True, True, False]


def test_a_month_grid_has_the_repeats_on_each_day(org: Organizer) -> None:
    org.add_event(ALEX, event("Swim", day="2026-10-01", start="17:30", end="18:30", repeat="weekly"))
    org.add_event(ALEX, event("Rent", day="2026-09-01", start="09:00", end="09:15", repeat="monthly"))
    start, days = fetch_range(MONTH, date(2026, 11, 1))
    month = org.week_view(start, days)
    assert month["week_start"] == "2026-10-26" and month["week_end"] == "2026-12-06"
    assert [e["day"] for e in month["week_events"] if e["title"] == "Swim"] == \
        ["2026-10-29", "2026-11-05", "2026-11-12", "2026-11-19", "2026-11-26", "2026-12-03"]
    assert [e["day"] for e in month["week_events"] if e["title"] == "Rent"] == ["2026-11-01", "2026-12-01"]


def test_overdue_tasks_show_on_today_and_done_tasks_are_faded(org: Organizer, clock: FakeClock) -> None:
    org.add_task(ALEX, task("Passport photos", due="2026-10-14", priority="high"))  # due two weeks ago
    org.add_task(ALEX, task("Recycling", due="2026-10-29"))
    done = org.add_task(ALEX, task("Hydro bill", due="2026-10-27"))
    org.complete_task(ALEX, done["id"])
    org.add_task(ALEX, task("Tires", due="2026-11-10"))  # another week
    tasks = {t["title"]: t for t in org.week_view()["week_tasks"]}
    assert set(tasks) == {"Passport photos", "Recycling", "Hydro bill"}
    assert tasks["Passport photos"]["day"] == "2026-10-27" and tasks["Passport photos"]["overdue"]  # WB-6
    assert tasks["Recycling"]["day"] == "2026-10-29" and not tasks["Recycling"]["overdue"]
    assert tasks["Hydro bill"]["done"]  # WB-8
    clock.advance(days=7)  # next week: the overdue task moves to the new today
    assert {t["title"]: t["day"] for t in org.week_view()["week_tasks"]}["Passport photos"] == "2026-11-03"


def test_last_week_keeps_its_own_overdue_tasks_off(org: Organizer) -> None:
    org.add_task(ALEX, task("Passport photos", due="2026-10-14"))
    assert org.week_view(date(2026, 10, 19))["week_tasks"] == []  # overdue tasks only sit on today


def test_overlaps_and_failed_sends_are_marked(org: Organizer, gcal: Any) -> None:
    org.add_event(ALEX, event("Dentist", day="2026-10-29"))
    org.add_event(ALEX, event("Interview", day="2026-10-29", start="16:30", end="17:00"), acknowledge_conflicts=True)
    gcal.offline = True
    org.add_event(ALEX, event("Recital", day="2026-10-30", start="19:00", end="20:00"))
    week = {e["title"]: e for e in org.week_view()["week_events"]}
    assert week["Dentist"]["conflicts_with"] == ["Interview"]
    assert week["Recital"]["sync_status"] == "error"


# --- SB-3, SB-7: same-height blocks, and a busy day makes the whole week scroll ---------------------------------

@pytest.fixture(scope="module")
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_a_busy_day_makes_the_whole_week_scroll(qt_app: Any) -> None:
    from family_scheduler.app.calview import WeekStack
    from family_scheduler.app.style import HALF, STACK_BLOCK

    def occurrence(n: int) -> dict[str, Any]:
        return {"id": f"e{n}", "day": "2026-10-28", "title": f"Event {n}", "start_time": f"{8 + n:02d}:00",
                "end_time": f"{9 + n:02d}:00", "past": False, "sync_status": "synced", "conflicts_with": [],
                "location": "", "organizer_connected": True}

    week = WeekStack()
    week.resize(800, 400)
    data = {"date": "2026-10-28", "week_events": [occurrence(n) for n in range(12)], "week_tasks": []}
    week.load(data, [d.isoformat() for d in week_days(TODAY)])
    assert week.canvas.minimumHeight() >= 12 * (STACK_BLOCK + HALF)  # taller than the window: it scrolls
    week.canvas.resize(780, week.canvas.minimumHeight())
    blocks = week.canvas.layout_blocks()
    assert [b.item["title"] for b in blocks] == [f"Event {n}" for n in range(12)]
    assert len({b.rect.height() for b in blocks}) == 1  # every block the same height
    assert len({(b.rect.left(), b.rect.width()) for b in blocks}) == 1  # one column, the full width
