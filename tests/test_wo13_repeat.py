"""WO-FS-013 sections 1 to 4: monthly and yearly task repeats keep their day (MR-1 to MR-10, ET-1 to ET-4, CO-1,
CO-2)."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from conftest import ALEX, FakeClock, task

from family_scheduler import recurrence
from family_scheduler.organizer import Organizer, TaskInput

D = date(2026, 10, 27)  # the test clock's day


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def open_task(org: Organizer, title: str) -> dict[str, Any]:
    lists = org.list_tasks()
    return next(t for key in ("overdue", "high", "medium", "low") for t in lists[key] if t["title"] == title)


def ticks(org: Organizer, title: str, times: int) -> list[str]:
    """Tick the open occurrence off `times` times; the due dates of the occurrences that follow."""
    dates = []
    for _ in range(times):
        org.complete_task(ALEX, open_task(org, title)["id"])
        dates.append(open_task(org, title)["due_date"])
    return dates


# --- the rule (MR-2 to MR-5, MR-7, CO-1) --------------------------------------------------------------------

def test_monthly_on_the_31st_through_a_year(org: Organizer) -> None:
    org.add_task(ALEX, task("Pay rent", due="2027-01-31", repeat="monthly"))
    assert ticks(org, "Pay rent", 12) == ["2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31", "2027-06-30",
                                          "2027-07-31", "2027-08-31", "2027-09-30", "2027-10-31", "2027-11-30",
                                          "2027-12-31", "2028-01-31"]
    assert ticks(org, "Pay rent", 2) == ["2028-02-29", "2028-03-31"]  # a leap year


@pytest.mark.parametrize(("first", "expected"), [
    ("2027-01-30", ["2027-02-28", "2027-03-30", "2027-04-30", "2027-05-30"]),
    ("2027-01-29", ["2027-02-28", "2027-03-29", "2027-04-29", "2027-05-29"]),
    ("2028-01-29", ["2028-02-29", "2028-03-29", "2028-04-29", "2028-05-29"]),
    ("2027-01-28", ["2027-02-28", "2027-03-28", "2027-04-28", "2027-05-28"]),  # unchanged
    ("2027-01-01", ["2027-02-01", "2027-03-01", "2027-04-01", "2027-05-01"]),  # unchanged
])
def test_monthly_on_other_days(org: Organizer, first: str, expected: list[str]) -> None:
    org.add_task(ALEX, task("Bins", due=first, repeat="monthly"))
    assert ticks(org, "Bins", 4) == expected


def test_yearly_on_february_29(org: Organizer) -> None:
    org.add_task(ALEX, task("Birthday card", due="2028-02-29", repeat="yearly"))
    assert ticks(org, "Birthday card", 4) == ["2029-02-28", "2030-02-28", "2031-02-28", "2032-02-29"]


def test_daily_and_weekly_unchanged() -> None:
    assert recurrence.next_due_date(date(2026, 10, 31), "daily", D) == date(2026, 11, 1)
    assert recurrence.next_due_date(date(2026, 10, 27), "weekly", D) == date(2026, 11, 3)
    assert recurrence.repeat_anchor(date(2026, 10, 31), "weekly") == (None, None)


def test_ticked_off_months_late(org: Organizer, clock: FakeClock) -> None:  # MR-5
    org.add_task(ALEX, task("Pay rent", due="2026-05-31", repeat="monthly"))
    # today is Oct 27: May 31 -> ... the first one on or after today, on the 31st (or the month's last day)
    assert ticks(org, "Pay rent", 1) == ["2026-10-31"]
    assert recurrence.next_due_date(date(2026, 1, 31), "monthly", date(2026, 4, 5), 31) == date(2026, 4, 30)


def test_editing_the_due_date_sets_a_new_repeat_day(org: Organizer) -> None:  # MR-1, MR-6
    t = org.add_task(ALEX, task("Pay rent", due="2027-01-31", repeat="monthly"))
    assert t["repeat_day"] == 31 and t["repeat_month"] is None  # CO-2
    ticks(org, "Pay rent", 1)  # Feb 28, still on the 31st
    feb = open_task(org, "Pay rent")
    assert feb["repeat_day"] == 31
    org.edit_task(ALEX, feb["id"], task("Pay the rent", due="2027-02-28", repeat="monthly"))  # title only
    assert open_task(org, "Pay the rent")["repeat_day"] == 31
    org.edit_task(ALEX, feb["id"], task("Pay the rent", due="2027-02-15", repeat="monthly"))
    assert open_task(org, "Pay the rent")["repeat_day"] == 15
    assert ticks(org, "Pay the rent", 1) == ["2027-03-15"]


def test_reopening_a_ticked_off_task(org: Organizer) -> None:
    t = org.add_task(ALEX, task("Pay rent", due="2027-01-31", repeat="monthly"))
    org.complete_task(ALEX, t["id"])
    org.reopen_task(ALEX, t["id"])
    assert open_task(org, "Pay rent")["due_date"] == "2027-01-31"
    assert ticks(org, "Pay rent", 2) == ["2027-02-28", "2027-03-31"]


def test_ongoing_tasks_still_cant_repeat_and_the_bump_uses_the_right_date(org: Organizer) -> None:  # MR-10
    t = org.add_task(ALEX, TaskInput("Tidy garage", "", "low"))
    assert t["repeat_day"] is None
    org.add_task(ALEX, task("Pay rent", due="2026-09-30", priority="low", repeat="monthly"))
    ticks(org, "Pay rent", 1)  # Sep 30 -> Oct 30, within 7 days of Oct 27
    assert open_task(org, "Pay rent")["due_soon"] is True


def test_events_monthly_repeats_unchanged() -> None:  # Google's rule: months without the 31st are skipped
    window = (datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 8, 1, tzinfo=UTC))
    occ = recurrence.occurrences(date(2027, 1, 31), time(9), time(10), "monthly", "America/Toronto", *window)
    assert [s.month for s, _ in occ] == [1, 3, 5, 7]


# --- existing tasks (ET-1 to ET-4) --------------------------------------------------------------------------

def _chain(org: Organizer, title: str, dates: list[str], repeat: str = "monthly") -> str:
    """Tasks linked by previous_task_id, all done but the last, as 1.10.1 would have left them."""
    prev = None
    for n, due in enumerate(dates):
        tid = f"{title}-{n}"
        done = "2026-01-01T00:00:00+00:00" if n < len(dates) - 1 else None
        org.conn.execute(
            "INSERT INTO task (id, title, priority, due_date, repeat, completed_at, previous_task_id, created_by, "
            "created_at, updated_at) VALUES (?, ?, 'medium', ?, ?, ?, ?, ?, 'n', 'n')",
            (tid, title, due, repeat, done, prev, ALEX))
        prev = tid
    return str(prev)


def test_the_migration_puts_back_drifted_tasks(org: Organizer) -> None:
    drifted = _chain(org, "Rent", ["2026-01-31", "2026-02-28", "2026-03-28", "2026-11-28"])  # the old drift
    past = _chain(org, "Water", ["2026-08-31", "2026-09-28"])  # Sep 30 would be in the past: kept
    short = _chain(org, "Gas", ["2026-11-28"])  # nothing earlier to tell: its own day
    weekly = _chain(org, "Bins", ["2026-10-01", "2026-10-29"], repeat="weekly")
    leap = _chain(org, "Card", ["2024-02-29", "2025-02-28", "2026-02-28", "2027-02-28"], repeat="yearly")
    org.conn.execute("DELETE FROM setting WHERE key = 'repeat_days_restored'")
    counts = org.restore_repeat_days()
    assert counts == {"checked": 4, "put_back": 1, "restored_for_later": 1, "kept_current_day": 1,
                      "already_right": 1}
    row = {r["id"]: r for r in org.conn.execute("SELECT * FROM task")}
    assert (row[drifted]["due_date"], row[drifted]["repeat_day"]) == ("2026-11-30", 31)
    assert (row[past]["due_date"], row[past]["repeat_day"]) == ("2026-09-28", 31)
    assert (row[short]["due_date"], row[short]["repeat_day"]) == ("2026-11-28", 28)
    assert row[weekly]["repeat_day"] is None and row[weekly]["due_date"] == "2026-10-29"
    assert (row[leap]["repeat_day"], row[leap]["repeat_month"]) == (29, 2)  # Feb 28, 2027: right for 2027
    assert recurrence.next_due_date(date(2026, 9, 28), "monthly", date(2026, 10, 1), 31) == date(2026, 10, 31)
    notes = [h for h in org.list_history() if h["after"] and "note" in h["after"]]
    assert [(h["label"], h["after"]["note"]) for h in notes] == [("Rent", "Repeat day restored to the 31st")]
    assert org.restore_repeat_days() is None  # once only: History written once
    assert len([h for h in org.list_history() if h["after"] and "note" in h["after"]]) == 1


def test_history_reads_the_restored_day(org: Organizer) -> None:
    from family_scheduler.app.views import describe_change

    entry = {"action": "edited", "before": {"due_date": "2026-11-28"},
             "after": {"due_date": "2026-11-30", "note": "Repeat day restored to the 31st"}}
    assert describe_change(entry) == "Repeat day restored to the 31st  ·  Due Sat Nov 28 → Mon Nov 30"


# --- the form and tooltips (MR-8, MR-9) ---------------------------------------------------------------------

def test_the_form_explains_the_month_end_rule(qt_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from family_scheduler.app import dialogs

    monkeypatch.setattr(dialogs, "today_iso", lambda: D.isoformat())
    dlg = dialogs.TaskDialog(lambda body: body, due="2027-01-31")
    assert dlg.repeat_rule.isHidden()
    dlg.repeat.setCurrentIndex(dlg.repeat.findData("monthly"))
    assert dlg.repeat_rule.text() == "Every month on the 31st, or the last day in shorter months."
    dlg.repeat.setCurrentIndex(dlg.repeat.findData("weekly"))
    assert dlg.repeat_rule.isHidden()
    leap = dialogs.TaskDialog(lambda body: body, due="2028-02-29")
    leap.repeat.setCurrentIndex(leap.repeat.findData("yearly"))
    assert leap.repeat_rule.text() == "Every year on February 29, or February 28 when there's no 29th."
    feb = {"title": "Rent", "priority": "medium", "due_date": "2027-02-28", "repeat": "monthly", "repeat_day": 31,
           "repeat_month": None}
    edit = dialogs.TaskDialog(lambda body: body, existing=feb)
    assert edit.repeat_rule.text() == "Every month on the 31st, or the last day in shorter months."
    edit.due.setDate(edit.due.date().addDays(-13))  # Feb 15
    assert edit.repeat_rule.text() == "Later ones will repeat on the 15th."


def test_the_tooltip_adds_the_repeat_day() -> None:
    from family_scheduler.app.style import repeat_note
    from family_scheduler.app.views import TasksView

    t = {"id": "t", "title": "Rent", "priority": "medium", "due_date": "2027-02-28", "repeat": "monthly",
         "repeat_day": 31, "repeat_month": None, "completed_at": None, "effective_priority": "medium",
         "due_soon": False}
    assert repeat_note(t) == "Repeats on the 31st"
    assert TasksView.task_row(t, "medium").tooltip.endswith("Repeats on the 31st")
    assert repeat_note(t | {"repeat_day": 15}) == ""
    assert repeat_note(t | {"repeat": "yearly", "repeat_day": 29, "repeat_month": 2}) == "Repeats on February 29"
    assert TasksView.task_row(t | {"repeat_day": 15}, "medium").tooltip == ""
