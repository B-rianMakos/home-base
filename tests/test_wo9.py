"""WO-FS-009: ongoing tasks (OT-1 to OT-11), month colours (MC-1 to MC-8), the Setup guide (SG-1 to SG-8) and
"Family & Settings" (FS-1)."""

from __future__ import annotations

import math
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SECOND, FakeClock, task
from test_today import open_app, page_text

from family_scheduler import db
from family_scheduler.app import guide, style
from family_scheduler.app.badges import badge_counts
from family_scheduler.app.today import due_text, todo_groups
from family_scheduler.digest import build_digest
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import Organizer, OrganizerError, TaskInput

TODAY = "2026-10-27"


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def ongoing(title: str = "Keep the garage tidy", priority: str = "medium") -> TaskInput:
    return TaskInput(title, "", priority, "none")


# --- ongoing tasks ----------------------------------------------------------------------------------------------

def test_an_ongoing_task_is_never_overdue_or_due_today(org: Organizer, clock: FakeClock) -> None:  # OT-3
    t = org.add_task(ALEX, ongoing())
    assert t["due_date"] == "" and t["ongoing"] and not t["overdue"]
    clock.advance(days=400)
    lists = org.list_tasks()
    assert lists["overdue"] == [] and lists["open"] == []
    assert [x["title"] for x in lists["ongoing"]] == ["Keep the garage tidy"]
    assert [name for name, _ in todo_groups(lists["ongoing"], TODAY)] == ["Ongoing"]


def test_the_ongoing_group_is_last_high_priority_first_then_title(org: Organizer) -> None:  # OT-4, OT-5
    org.add_task(ALEX, ongoing("Water the plants"))
    org.add_task(ALEX, ongoing("Walk the dog", "high"))
    org.add_task(ALEX, ongoing("Answer the mail", "low"))
    org.add_task(ALEX, ongoing("Check the smoke alarms", "high"))
    org.add_task(ALEX, task("Recycling", due=TODAY))
    org.add_task(ALEX, task("Passport", due="2026-10-20"))
    org.add_task(ALEX, task("Tires", due="2026-11-03"))
    lists = org.list_tasks()
    assert [t["title"] for t in lists["ongoing"]] == ["Check the smoke alarms", "Walk the dog", "Answer the mail",
                                                     "Water the plants"]
    groups = todo_groups(lists["overdue"] + lists["open"] + lists["ongoing"], TODAY)
    assert [(n, [t["title"] for t in g]) for n, g in groups] == [
        ("Overdue", ["Passport"]), ("Due today", ["Recycling"]), ("Coming up", ["Tires"]),
        ("Ongoing", ["Check the smoke alarms", "Walk the dog", "Answer the mail", "Water the plants"])]
    assert due_text("", TODAY) == "No due date"  # OT-6


def test_ongoing_tasks_arent_on_the_calendar(org: Organizer) -> None:  # OT-8
    org.add_task(ALEX, ongoing())
    done = org.add_task(ALEX, ongoing("Done one"))
    org.complete_task(ALEX, done["id"])
    org.add_task(ALEX, task("Recycling", due=TODAY))
    tasks = org.week_view(date(2026, 10, 26), 35)["week_tasks"]
    assert [t["title"] for t in tasks] == ["Recycling"]


def test_only_high_priority_ongoing_tasks_are_in_the_digest(org: Organizer) -> None:  # OT-9
    org.add_task(ALEX, ongoing("Walk the dog", "high"))
    org.add_task(ALEX, ongoing("Water the plants"))
    org.add_task(ALEX, task("Tires", due="2026-11-03", priority="high"))
    view = org.today_view()
    assert [t["title"] for t in view["high_priority"]] == ["Tires", "Walk the dog"]
    _, text, _ = build_digest(view, ALEX)
    assert "Walk the dog, no due date" in text and "Water the plants" not in text


def test_the_badge_never_counts_ongoing_tasks(org: Organizer, clock: FakeClock) -> None:  # OT-10
    org.add_task(ALEX, ongoing("Walk the dog", "high"))
    org.add_task(ALEX, task("Passport", due="2026-10-20"))
    clock.advance(days=30)
    assert badge_counts(org.today_view())["Tasks"] == 1


def test_ongoing_tasks_cant_repeat(org: Organizer, ctx: HubContext) -> None:  # OT-2
    with pytest.raises(OrganizerError, match="can't repeat"):
        org.add_task(ALEX, TaskInput("Walk the dog", "", "medium", "weekly"))
    with pytest.raises(sqlite3.IntegrityError):  # and the database won't hold one either (OT-11)
        ctx.conn.execute("INSERT INTO task (id, title, due_date, repeat, created_by, created_at, updated_at) "
                         "VALUES ('t1', 'x', '', 'daily', ?, 'n', 'n')", (ALEX,))


def test_edit_to_and_from_a_date(org: Organizer) -> None:  # OT-1
    t = org.add_task(ALEX, task("Recycling", due=TODAY, repeat="weekly"))
    org.edit_task(ALEX, t["id"], ongoing("Recycling"))
    assert org.list_tasks()["ongoing"][0]["id"] == t["id"]
    org.edit_task(ALEX, t["id"], task("Recycling", due="2026-11-03"))
    after = org.list_tasks()
    assert after["ongoing"] == [] and after["open"][0]["due_date"] == "2026-11-03"
    edits = [h for h in org.list_history() if h["action"] == "edited"]
    assert {"due_date", "repeat"} <= set(edits[-1]["after"]) and edits[-1]["after"]["due_date"] == ""


def test_ongoing_tasks_tick_off_trash_and_restore_like_any_other(org: Organizer) -> None:  # OT-7
    t = org.add_task(ALEX, ongoing())
    org.complete_task(ALEX, t["id"])
    assert [x["id"] for x in org.list_tasks()["done"]] == [t["id"]]
    org.reopen_task(ALEX, t["id"])
    org.remove_task(ALEX, t["id"])
    assert [x["id"] for x in org.list_trash()] == [t["id"]]
    org.restore(ALEX, [("task", t["id"])])
    assert [x["id"] for x in org.list_tasks()["ongoing"]] == [t["id"]]


def test_the_migration_keeps_every_task_and_its_date() -> None:  # OT-11
    conn = db.connect(":memory:")
    conn.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY)")
    for number, sql in db._migration_files()[:3]:  # the database as 1.6.0 left it
        for statement in db._split_sql(sql):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migration (version) VALUES (?)", (number,))
    conn.execute("INSERT INTO family_member (email, created_at) VALUES (?, 'n')", (ALEX,))
    # an existing hub already has its admin (WO-FS-010: the migrations no longer write one)
    conn.execute("INSERT INTO setting (key, value) VALUES ('admin_email', ?)", (ADMIN,))
    conn.execute("INSERT INTO family_member (email, created_at) VALUES (?, 'n')", (ADMIN,))
    conn.execute("INSERT INTO task (id, title, due_date, repeat, created_by, created_at, updated_at) "
                 "VALUES ('a', 'Recycling', '2026-10-29', 'weekly', ?, 'n', 'n')", (ALEX,))
    conn.execute("INSERT INTO task (id, title, due_date, created_by, created_at, updated_at, previous_task_id) "
                 "VALUES ('b', 'Recycling', '2026-11-05', ?, 'n', 'n', 'a')", (ALEX,))
    conn.commit()
    assert db.migrate(conn) == 10
    rows = [tuple(r) for r in conn.execute("SELECT id, due_date, repeat, previous_task_id FROM task ORDER BY id")]
    assert rows == [("a", "2026-10-29", "weekly", None), ("b", "2026-11-05", "none", "a")]
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# --- month colours ----------------------------------------------------------------------------------------------

def _lab(colour: str) -> tuple[float, float, float]:
    rgb = [int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (x, y, z)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def _distance(a: str, b: str) -> float:
    """CIE76 distance in Lab: a simple, stricter-than-needed stand-in for "clearly different"."""
    return math.dist(_lab(a), _lab(b))


def test_twelve_months_each_with_its_own_colour() -> None:  # MC-1
    assert sorted(style.MONTH_COLOURS) == list(range(1, 13))
    assert len(set(style.MONTH_COLOURS.values())) == 12
    assert [style.month_colour(m) for m in (1, 4, 7, 10, 11)] == ["#3D63D1", "#6FA83A", "#FFE433", "#C9471A",
                                                                 "#8C1C3A"]


@pytest.mark.parametrize("season", [(12, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11)])
def test_each_season_goes_light_to_dark(season: tuple[int, int, int]) -> None:  # MC-2
    lightness = [_lab(style.month_colour(m))[0] for m in season]
    assert lightness == sorted(lightness, reverse=True)


@pytest.mark.parametrize("month", range(1, 13))
def test_months_are_clearly_different(month: int) -> None:  # MC-2, MC-3
    colour = style.month_colour(month)
    assert _distance(colour, style.month_colour(month % 12 + 1)) > 15  # the next month
    for meaning in (style.SELECT, style.TODAY_EVENT, style.HIGH, style.OVERDUE, style.REM, style.ACCENT,
                    style.WEEKEND):
        assert _distance(colour, meaning) > 15, meaning


@pytest.mark.parametrize("month", range(1, 13))
def test_text_on_a_month_colour_is_readable(month: int) -> None:  # MC-8
    ink = style.month_ink(month)
    assert ink in (style.BASE, style.TEXT)
    assert style.contrast_ratio(ink, style.month_colour(month)) >= 4.5
    assert style.month_ink(2) == style.month_ink(11) == style.TEXT  # dark shades take white text


# --- the Setup guide ---------------------------------------------------------------------------------------------

def test_setup_guide_statuses() -> None:  # SG-5
    accounts = [{"email": ADMIN, "status": "connected"}, {"email": SECOND, "status": "not_connected"}]
    settings: dict[str, Any] = {"mail_configured": True, "google_mode": "google", "calendar_accounts": accounts}
    assert guide.status("digest", settings, "") == ("Ready", True)
    assert guide.status("digest", settings | {"mail_configured": False}, "") == ("Not set up", False)
    assert guide.status("calendar", settings, "") == ("1 of 2 connected", False)
    both = [a | {"status": "connected"} for a in accounts]
    assert guide.status("calendar", settings | {"calendar_accounts": both}, "") == ("Both accounts connected", True)
    none = [a | {"status": "not_connected"} for a in accounts]
    assert guide.status("calendar", settings | {"calendar_accounts": none}, "") == ("Not set up", False)
    broken = [accounts[0], {"email": SECOND, "status": "needs_reconnect"}]
    assert guide.status("calendar", settings | {"calendar_accounts": broken}, "")[0] == \
        f"1 of 2 connected · {SECOND} needs reconnecting"
    assert guide.status("pc", settings, "DESKTOP-ABC:8765") == ("This PC's hub address: DESKTOP-ABC:8765", None)


def test_the_guide_text_and_links() -> None:  # SG-4, SG-6, SG-8
    assert [s.key for s in guide.SECTIONS] == ["digest", "calendar", "pc"]
    digest = guide.SECTIONS[0]
    steps = [step for _, group in digest.steps for step in group]
    assert any("https://myaccount.google.com/apppasswords" in s for s in steps)
    assert guide.links("Go to https://myaccount.google.com/apppasswords (you may") == \
        ["https://myaccount.google.com/apppasswords"]
    assert guide.to_html("Click **Save** at https://myaccount.google.com/security.") == \
        'Click <b>Save</b> at <a href="https://myaccount.google.com/security">myaccount.google.com/security</a>.'
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for phrase in ("https://myaccount.google.com/apppasswords", "Go to Home Base (unsafe)",
                   "Connect to the home hub", "More info → Run anyway"):  # the README says the same
        assert phrase in readme


# --- in the app ----------------------------------------------------------------------------------------------------

def test_no_due_date_in_the_task_form(qt_app: Any) -> None:  # OT-1, OT-2
    from family_scheduler.app.dialogs import TaskDialog

    dlg = TaskDialog(lambda body: body)
    dlg.repeat.setCurrentIndex(2)
    dlg.no_due.setChecked(True)
    assert dlg.due.isHidden() and dlg.repeats.isHidden()
    assert dlg.body_data()["due_date"] == "" and dlg.body_data()["repeat"] == "none"
    dlg.no_due.setChecked(False)
    assert not dlg.due.isHidden() and len(dlg.body_data()["due_date"]) == 10
    existing = {"title": "Walk", "priority": "high", "due_date": "", "repeat": "none"}
    assert TaskDialog(lambda body: body, existing=existing).no_due.isChecked()


def test_ongoing_on_today_and_tasks(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch) -> None:  # OT-4 to OT-6
    org.add_task(ALEX, ongoing("Walk the dog", "high"))
    org.add_task(ALEX, task("Recycling", due=TODAY))
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    text = page_text(window.current())
    assert text[text.index("# To-do list"):text.index("# Reminders")] == [
        "# To-do list", "# Due today", "Recycling", "# Ongoing", "Walk the dog"]
    window.show_view("Tasks")
    # WO-FS-012: Recycling is due today, so it shows under High priority
    assert page_text(window.current()) == ["# High priority · 1", "Recycling", "# Ongoing", "Walk the dog"]
    row = window.current().task_row(org.list_tasks()["ongoing"][0], "ongoing")
    assert row.subtitle == "No due date" and ("Ongoing", style.TAG_TEXT) in row.tags
    assert window.current().subtitle.text() == "2 open · 0 overdue"


def test_the_setup_guide_page(qt_app: Any, ctx: HubContext, clock: FakeClock, tmp_path: Path,
                              monkeypatch: pytest.MonkeyPatch) -> None:  # SG-1 to SG-3, SG-7, FS-1
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    titles = [window.sidebar.nav.item(i).text() for i in range(window.sidebar.nav.count())]
    assert "Family & Settings" in titles and "Setup guide" not in titles  # FS-1, SG-2
    window.show_view("Family & Settings")
    window.show_guide()
    assert window.current() is window.guide
    assert window.sidebar.nav.currentItem().text() == "Family & Settings"  # stays selected
    assert window.guide.statuses["digest"].text() == "Ready"  # read live from the hub (SG-5; test mail)
    assert window.guide.statuses["calendar"].text() == "Test mode: Google isn't used"
    window.guide.back()  # SG-3
    assert window.current().title == "Family & Settings"
    window.show_guide()
    window.guide.go_to("pc")  # SG-7
    assert window.current().title == "Family & Settings"
    window.show_guide()
    window.close()
    assert window.settings.get("view") == "Family & Settings"  # the guide isn't remembered as a page


def test_events_month_headers_are_coloured(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock,
                                           tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # MC-4
    from conftest import event

    from family_scheduler.app.widgets import Header

    org.add_event(ALEX, event("Dentist", day="2026-11-03"))
    org.add_event(ALEX, event("Concert", day="2026-12-14"))
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("Events")
    rows = window.current().list
    headers = [e for e in (rows.row_delegate.entry(rows.model().index(i, 0)) for i in range(rows.count()))
               if isinstance(e, Header)]
    assert [(h.text, h.band, h.colour) for h in headers] == [
        ("November 2026", style.month_colour(11), style.TEXT), ("December 2026", style.month_colour(12), style.BASE)]
