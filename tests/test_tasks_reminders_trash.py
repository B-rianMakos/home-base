"""Tasks, reminders and Trash (TK-1 to TK-5a, RM-1 to RM-3a, TR-1 to TR-4)."""

from __future__ import annotations

from datetime import date

import pytest
from conftest import ALEX, SAM, FakeClock, task

from family_scheduler.organizer import NotFound, Organizer
from family_scheduler.recurrence import next_due_date


def test_task_report_sections(org: Organizer) -> None:
    org.add_task(ALEX, task("Renew passport photos", due="2026-10-24", priority="high"))
    org.add_task(ALEX, task("Return library books", due="2026-10-26", priority="low"))
    org.add_task(ALEX, task("Order school photos", due="2026-11-06", priority="low"))
    org.add_task(ALEX, task("Book winter tires", due="2026-10-30", priority="high"))
    org.add_task(ALEX, task("Recycling", due="2026-10-29", priority="medium"))
    report = org.list_tasks()
    assert [t["title"] for t in report["overdue"]] == ["Renew passport photos", "Return library books"]
    # WO-FS-012: Recycling (medium, due in 2 days) counts as high, so it sorts with the high tasks by due date
    assert [t["title"] for t in report["open"]] == ["Recycling", "Book winter tires", "Order school photos"]
    assert [t["title"] for t in report["high"]] == ["Recycling", "Book winter tires"]
    assert [t["title"] for t in report["low"]] == ["Order school photos"] and report["medium"] == []


def test_completing_a_weekly_task_creates_the_next_one(org: Organizer) -> None:
    t = org.add_task(ALEX, task(due="2026-10-26", repeat="weekly"))  # a Monday
    org.complete_task(SAM, t["id"])
    report = org.list_tasks()
    assert report["done"][0]["completed_by"] == SAM
    nxt = report["open"][0]
    assert nxt["due_date"] == "2026-11-02"  # still a Monday, counted from the old due date
    assert org.conn.execute("SELECT previous_task_id FROM task WHERE id = ?", (nxt["id"],)).fetchone()[0] == t["id"]


def test_next_due_date_rules() -> None:
    assert next_due_date(date(2027, 1, 31), "monthly", date(2027, 1, 1)) == date(2027, 2, 28)
    assert next_due_date(date(2026, 10, 1), "weekly", date(2026, 10, 27)) == date(2026, 10, 29)
    assert next_due_date(date(2024, 2, 29), "yearly", date(2024, 3, 1)) == date(2025, 2, 28)


def test_reopen_removes_the_untouched_next_occurrence(org: Organizer) -> None:
    t = org.add_task(ALEX, task(repeat="daily"))
    org.complete_task(ALEX, t["id"])
    org.reopen_task(ALEX, t["id"])
    report = org.list_tasks()
    assert [x["id"] for x in report["open"]] == [t["id"]]
    assert report["done"] == []


def test_single_task_removal_goes_to_trash_and_restores(org: Organizer, clock: FakeClock) -> None:
    first = org.add_task(ALEX, task(repeat="weekly"))
    org.complete_task(ALEX, first["id"])
    nxt = org.list_tasks()["open"][0]
    org.remove_task(SAM, nxt["id"])
    assert org.list_tasks()["open"] == []
    trash = org.list_trash()
    assert trash[0]["type"] == "task" and trash[0]["deleted_by"] == SAM and trash[0]["days_left"] == 30
    clock.advance(days=3)
    assert org.list_trash()[0]["days_left"] == 27
    assert org.restore(ALEX, [("task", nxt["id"])]) == 1
    restored = org.conn.execute("SELECT repeat, previous_task_id FROM task WHERE id = ?", (nxt["id"],)).fetchone()
    assert restored["repeat"] == "weekly" and restored["previous_task_id"] == first["id"]  # TR-4
    assert org.list_history()[0]["action"] == "restored"


def test_reminders_keep_the_order_added_and_have_no_date(org: Organizer) -> None:
    a = org.add_reminder(ALEX, "Wi-Fi password is on the fridge.")
    org.add_reminder(SAM, "Sam is allergic to tree nuts.")
    org.edit_reminder(SAM, a["id"], "Guest Wi-Fi password is on the fridge.")
    listed = org.list_reminders()
    assert [r["message"] for r in listed] == ["Guest Wi-Fi password is on the fridge.", "Sam is allergic to tree nuts."]
    assert set(listed[0]) == {"id", "message", "created_by"}


def test_single_reminder_removal_goes_to_trash(org: Organizer) -> None:
    r = org.add_reminder(ALEX, "Bring reusable bags.")
    org.remove_reminder(ALEX, r["id"])
    assert org.list_reminders() == []
    assert org.list_trash()[0]["label"] == "Bring reusable bags."
    org.restore(ALEX, [("reminder", r["id"])])
    assert org.list_reminders()[0]["id"] == r["id"]


def test_removed_items_cannot_be_edited(org: Organizer) -> None:
    t = org.add_task(ALEX, task())
    org.remove_task(ALEX, t["id"])
    with pytest.raises(NotFound):
        org.edit_task(ALEX, t["id"], task("New title"))
