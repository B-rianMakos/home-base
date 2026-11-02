"""WO-FS-012: the Tasks page's High, Medium and Low priority lists (PL-1 to PL-9) and the due-soon bump (DS-1 to
DS-13, CD-1 to CD-4)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import ALEX, SAM, FakeClock, task
from test_today import TODAY, open_app, page_text

from family_scheduler.app.badges import badge_counts
from family_scheduler.app.today import todo_groups
from family_scheduler.digest import build_digest
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import DUE_SOON_DAYS, Organizer, TaskInput, effective_priority

D = date(2026, 10, 27)  # the test clock's day (a Tuesday)


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def titles(tasks: list[dict[str, Any]]) -> list[str]:
    return [t["title"] for t in tasks]


# --- the bump at its edges (DS-1, DS-6, DS-7, PL-3, CD-1, CD-2) -------------------------------------------------

@pytest.mark.parametrize(("priority", "due", "done", "expected"), [
    ("low", "2026-10-27", None, ("high", True)),      # due today
    ("medium", "2026-11-03", None, ("high", True)),   # today + 7: bumped
    ("low", "2026-11-04", None, ("low", False)),      # today + 8: not
    ("low", "2026-11-03", "2026-10-27T10:00:00+00:00", ("low", False)),  # done
    ("low", "2026-10-26", None, ("low", False)),      # overdue: its own group, no bump
    ("medium", "", None, ("medium", False)),          # ongoing: no date
    ("high", "2026-10-28", None, ("high", False)),    # already high: no Due soon
])
def test_the_bump_at_its_edges(priority: str, due: str, done: str | None, expected: tuple[str, bool]) -> None:
    assert DUE_SOON_DAYS == 7
    assert effective_priority(priority, due, done, D) == expected


# --- grouping (PL-1 to PL-7) -------------------------------------------------------------------------------------

def test_three_priority_lists_in_place_of_open(org: Organizer) -> None:
    org.add_task(ALEX, task("Passport", due="2026-10-20", priority="low"))         # overdue
    org.add_task(ALEX, task("Tires", due="2026-11-20", priority="high"))
    org.add_task(ALEX, task("Recycling", due="2026-10-29", priority="low"))        # bumped
    org.add_task(ALEX, task("Paint", due="2026-11-20", priority="medium"))
    org.add_task(ALEX, task("Attic", due="2026-11-10", priority="medium"))
    org.add_task(ALEX, task("Shed", due="2026-12-01", priority="low"))
    org.add_task(ALEX, TaskInput("Tidy garage", "", "high"))                        # ongoing
    done = org.add_task(ALEX, task("Old", due="2026-11-01", priority="low"))
    org.complete_task(ALEX, done["id"])
    lists = org.list_tasks()
    assert titles(lists["overdue"]) == ["Passport"]
    assert titles(lists["high"]) == ["Recycling", "Tires"]  # PL-5: earliest due date first
    assert titles(lists["medium"]) == ["Attic", "Paint"]
    assert titles(lists["low"]) == ["Shed"]
    assert titles(lists["ongoing"]) == ["Tidy garage"] and titles(lists["done"]) == ["Old"]
    recycling = lists["high"][0]
    assert (recycling["priority"], recycling["effective_priority"], recycling["due_soon"]) == ("low", "high", True)
    assert titles(lists["open"]) == ["Recycling", "Tires", "Attic", "Paint", "Shed"]  # CD-3: older apps


def test_same_due_date_sorts_by_title(org: Organizer) -> None:  # PL-5
    for t in ("Zinnias", "Apples", "Mangoes"):
        org.add_task(ALEX, task(t, due="2026-11-20", priority="low"))
    assert titles(org.list_tasks()["low"]) == ["Apples", "Mangoes", "Zinnias"]


def test_moving_the_due_date_out_returns_its_own_priority(org: Organizer) -> None:  # DS-2, PL-9
    t = org.add_task(ALEX, task("Recycling", due="2026-10-29", priority="low"))
    assert titles(org.list_tasks()["high"]) == ["Recycling"]
    org.edit_task(ALEX, t["id"], task("Recycling", due="2026-11-15", priority="low"))
    lists = org.list_tasks()
    assert lists["high"] == [] and titles(lists["low"]) == ["Recycling"]
    assert org.conn.execute("SELECT priority FROM task WHERE id = ?", (t["id"],)).fetchone()[0] == "low"
    assert not any(h["action"] == "edited" and "priority" in (h["after"] or {}) for h in org.list_history())  # DS-13


def test_the_lists_move_over_at_midnight(org: Organizer, clock: FakeClock) -> None:  # DS-3
    org.add_task(ALEX, task("Shed", due="2026-11-04", priority="low"))  # today + 8
    assert titles(org.list_tasks()["low"]) == ["Shed"]
    clock.advance(days=1)  # midnight passes: now today + 7
    assert titles(org.list_tasks()["high"]) == ["Shed"]


def test_repeating_tasks_check_the_current_occurrence(org: Organizer) -> None:  # DS-8
    t = org.add_task(ALEX, task("Recycling", due="2026-10-27", priority="low", repeat="monthly"))
    org.complete_task(ALEX, t["id"])  # the next one is due Nov 27: not due soon
    lists = org.list_tasks()
    assert titles(lists["low"]) == ["Recycling"] and lists["low"][0]["due_date"] == "2026-11-27"
    assert lists["done"][0]["effective_priority"] == "low"


# --- everywhere else (DS-9 to DS-12) ----------------------------------------------------------------------------

def test_today_digest_calendar_and_badge(org: Organizer) -> None:
    org.add_task(ALEX, task("Recycling", due="2026-10-29", priority="low"))
    org.add_task(ALEX, task("Paint", due="2026-10-29", priority="medium"))
    org.add_task(SAM, task("Passport", due="2026-10-20", priority="low"))
    lists = org.list_tasks()
    groups = dict(todo_groups(lists["overdue"] + lists["open"] + lists["ongoing"], TODAY))
    assert [(t["title"], t["effective_priority"]) for t in groups["Coming up"]] == [("Paint", "high"),
                                                                                   ("Recycling", "high")]
    view = org.today_view()
    assert sorted(titles(view["high_priority"])) == ["Paint", "Recycling"]  # DS-11
    _, text, _ = build_digest(view, ALEX)
    assert "Recycling, due Thu Oct 29" in text
    week = org.week_view(D, 7)["week_tasks"]
    assert {t["title"]: t["effective_priority"] for t in week}["Recycling"] == "high"  # DS-10
    assert badge_counts(view)["Tasks"] == 1  # DS-12: overdue only


def test_an_older_app_still_gets_open(ctx: HubContext) -> None:  # CD-3
    from fastapi.testclient import TestClient

    from family_scheduler.hub.api import create_app

    web = TestClient(create_app(ctx), client=("127.0.0.1", 50000))
    ctx.organizer.add_task(ALEX, task("Recycling", due="2026-10-29", priority="low"))
    body = web.get("/api/tasks", headers={"X-Member": ALEX}).json()
    assert titles(body["open"]) == ["Recycling"] and titles(body["high"]) == ["Recycling"]
    assert body["open"][0]["priority"] == "low" and body["open"][0]["due_soon"] is True


# --- in the app (PL-6, PL-7, DS-4, DS-5, DS-9) ------------------------------------------------------------------

def test_the_tasks_page(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock, tmp_path: Path,
                        monkeypatch: pytest.MonkeyPatch) -> None:
    from family_scheduler.app import style

    org.add_task(ALEX, task("Passport", due="2026-10-20", priority="low"))
    org.add_task(ALEX, task("Recycling", due="2026-10-29", priority="low"))
    org.add_task(ALEX, task("Shed", due="2026-12-01", priority="low"))
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("Tasks")
    view = window.current()
    assert page_text(view) == ["# Overdue", "Passport", "# High priority · 1", "Recycling", "# Low priority · 1",
                               "Shed"]  # PL-7: no Medium list when it's empty
    assert view.subtitle.text() == "3 open · 1 overdue"  # PL-8
    recycling = view.task_row(org.list_tasks()["high"][0], "high")
    assert recycling.tags[:2] == [("High", style.HIGH), ("Due soon", style.HIGH)]  # DS-4
    assert recycling.tag_tips["Due soon"] == "Due within 7 days, so it's treated as high priority. Set as Low."
    passport = view.task_row(org.list_tasks()["overdue"][0], "overdue")
    assert ("Due soon", style.HIGH) not in passport.tags  # DS-7


def test_the_task_form_says_it_will_show_as_high(qt_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # DS-5
    from family_scheduler.app import dialogs

    monkeypatch.setattr(dialogs, "today_iso", lambda: TODAY)
    existing = {"title": "Recycling", "priority": "low", "due_date": "2026-10-29", "repeat": "none"}
    dlg = dialogs.TaskDialog(lambda body: body, existing=existing)
    assert dlg.due_soon_note.text() == "Due within 7 days: it will show as high priority."
    assert dlg.body_data()["priority"] == "low"  # what's picked is saved
    next(b for b in dlg.priority.buttons() if b.property("key") == "high").click()
    assert dlg.due_soon_note.text() == ""
    dlg.no_due.setChecked(True)
    assert dlg.due_soon_note.isHidden()


def test_month_bars_paint_events_and_bumped_tasks(qt_app: Any, org: Organizer) -> None:  # DS-10
    from conftest import event
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    from family_scheduler.app.calview import MonthGrid

    ev = org.add_event(ALEX, event())
    org.add_task(ALEX, task("Recycling", due="2026-10-29", priority="low"))
    week = org.week_view(D, 14)
    image = QImage(400, 40, QImage.Format.Format_ARGB32)
    p = QPainter(image)
    try:  # an event has no priority: painting its bar mustn't look one up
        MonthGrid._paint_bar(p, QRect(0, 0, 400, 20), "event", next(e for e in week["week_events"]
                                                                    if e["id"] == ev["id"]), False)
        MonthGrid._paint_bar(p, QRect(0, 20, 400, 20), "task", week["week_tasks"][0], False)
    finally:
        p.end()
