"""WO-FS-007: the Today page (TP, TL, TE, TD, TR, TU) and the test email (EL-5, EL-11)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SECOND, FakeClock, event, task
from fastapi.testclient import TestClient

from family_scheduler.app.today import day_changed, due_text, long_date, todays_events, todo_groups
from family_scheduler.hub.api import create_app
from family_scheduler.hub.context import HubContext
from family_scheduler.mailer import RecordingMailer
from family_scheduler.organizer import LimitReached, Organizer

TODAY = "2026-10-27"  # the test clock: Tuesday Oct 27, 2026, 8:00 am Toronto time


def occ(title: str, day: str, start: str, end: str = "23:00") -> dict[str, Any]:
    return {"title": title, "day": day, "start_time": start, "end_time": end}


def tk(title: str, due: str, priority: str = "medium", done: bool = False) -> dict[str, Any]:
    return {"title": title, "due_date": due, "priority": priority, "completed_at": "x" if done else None}


# --- the rules (pure) ----------------------------------------------------------------------------------------

def test_only_todays_events_earliest_first() -> None:
    week = [occ("Yesterday", "2026-10-26", "09:00"), occ("Dinner", TODAY, "18:00"), occ("Walk", TODAY, "07:00"),
            occ("Tomorrow", "2026-10-28", "08:00"), occ("Dentist", TODAY, "16:00")]
    assert [e["title"] for e in todays_events(week, TODAY)] == ["Walk", "Dentist", "Dinner"]  # TE-1, TE-2


def test_todo_groups_and_order() -> None:
    tasks = [tk("Coming low", "2026-10-30", "low"), tk("Coming high", "2026-11-05", "high"),
             tk("Late medium", "2026-10-20"), tk("Late high", "2026-10-25", "high"), tk("Today b", TODAY),
             tk("Today a", TODAY), tk("Done", TODAY, done=True)]
    groups = todo_groups(tasks, TODAY)
    assert [(name, [t["title"] for t in group]) for name, group in groups] == [
        ("Overdue", ["Late high", "Late medium"]),     # high priority first, then due date
        ("Due today", ["Today a", "Today b"]),          # then title
        ("Coming up", ["Coming high", "Coming low"]),
    ]  # TD-b, TD-c; checked-off tasks never shown (TD-f)
    assert [name for name, _ in todo_groups([tk("Only later", "2026-11-01")], TODAY)] == ["Coming up"]  # empty hidden


def test_due_text_and_date() -> None:
    assert due_text(TODAY, TODAY) == "Due today"
    assert due_text("2026-10-30", TODAY) == "Due Fri Oct 30" and due_text("2026-10-25", TODAY) == "Due Sun Oct 25"
    assert long_date("2026-10-29") == "Thursday, October 29"  # TP-3


def test_midnight_rollover_rule() -> None:
    assert not day_changed(None, TODAY) and not day_changed(TODAY, TODAY)
    assert day_changed(TODAY, "2026-10-28")  # TP-5


# --- the page, on a test hub ----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def open_app(ctx: HubContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> Any:
    from family_scheduler.app import style, views
    from family_scheduler.app.main_window import MainWindow
    from family_scheduler.client.hub_client import HubClient
    from family_scheduler.client.local_settings import LocalSettings
    from family_scheduler.timeutil import local_today

    monkeypatch.setattr(views, "today_iso", lambda: local_today(clock()).isoformat())
    monkeypatch.setattr(style, "today_iso", lambda: local_today(clock()).isoformat())
    client = HubClient("testserver", ALEX)
    client._http = TestClient(create_app(ctx), client=("127.0.0.1", 50000))
    window = MainWindow(client, LocalSettings(tmp_path / "client.json"), client.members(), lambda: None)
    window.poll.stop()
    return window


def page_text(view: Any) -> list[str]:
    from family_scheduler.app.widgets import Header, Note, Row

    out = []
    for i in range(view.list.count()):
        e = view.list.row_delegate.entry(view.list.model().index(i, 0))
        if isinstance(e, Header):
            out.append(f"# {e.text}")
        elif isinstance(e, Note):
            out.append(f"- {e.text}")
        elif isinstance(e, Row):
            out.append(e.title + (" (faded)" if e.dim else ""))
    return out


def test_the_app_opens_on_today_with_three_sections(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock,
                                                    tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    org.add_event(ALEX, event("Walk", day="2026-10-20", start="07:00", end="07:30", repeat="weekly",
                              invitees=[ADMIN, ALEX]))
    org.add_event(ALEX, event("Dentist", day=TODAY, start="16:00", end="17:00"))
    org.add_event(ALEX, event("Yesterday's", day="2026-10-26"))
    org.add_event(ALEX, event("Tomorrow's", day="2026-10-28"))
    org.add_task(ALEX, task("Passport photos", due="2026-10-20", priority="high"))
    org.add_task(ALEX, task("Recycling", due=TODAY))
    org.add_task(ALEX, task("Tires", due="2026-11-03", priority="high"))
    org.add_reminder(SECOND, "Guest Wi-Fi password is on the fridge.")
    org.add_reminder(ALEX, "Sam is allergic to tree nuts.")
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    view = window.current()
    assert view.title == "Today" and window.sidebar.nav.item(0).text() == "Today"  # TP-1, TP-2
    assert window.sidebar.nav.item(1).text() == "Calendar"
    assert view.subtitle.text() == "Tuesday, October 27"
    assert page_text(view) == [
        "# Today's events", "Walk (faded)", "Dentist",  # the repeat's occurrence today; the walk is over
        "# To-do list", "# Overdue", "Passport photos", "# Due today", "Recycling",
        "# Coming up", "Tires",
        "# Reminders", "Guest Wi-Fi password is on the fridge.", "Sam is allergic to tree nuts.",
    ]


def test_an_empty_today(qt_app: Any, ctx: HubContext, clock: FakeClock, tmp_path: Path,
                        monkeypatch: pytest.MonkeyPatch) -> None:
    view = open_app(ctx, tmp_path, monkeypatch, clock).current()
    assert page_text(view) == ["# Today's events", "- No events today.", "# To-do list", "- Nothing to do.",
                               "# Reminders", "- No reminders."]  # TL-4


def test_checking_a_task_off_from_today(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock,
                                        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    t = org.add_task(ALEX, task("Recycling", due=TODAY, repeat="weekly"))
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    view = window.current()
    view.on_action(f"task:{t['id']}", "tick")  # TD-e
    assert org.list_tasks()["done"][0]["title"] == "Recycling"
    assert "Recycling" in page_text(view)  # the next one, due in a week (TK-2), now Coming up
    assert page_text(view)[page_text(view).index("Recycling") - 1] == "# Coming up"


def test_the_page_moves_to_the_new_day_at_midnight(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock,
                                                   tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    org.add_event(ALEX, event("Tomorrow's", day="2026-10-28", start="09:00", end="10:00"))
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    view = window.current()
    assert "Tomorrow's" not in page_text(view)
    clock.advance(hours=16, minutes=5)  # 12:05 am Wednesday in Toronto
    view.check_day()  # the minute timer's job (TP-5)
    assert view.subtitle.text() == "Wednesday, October 28" and "Tomorrow's" in page_text(view)


def test_the_digest_button_is_on_today_not_calendar(qt_app: Any, ctx: HubContext, clock: FakeClock,
                                                    tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    assert window.views["Today"].digest_button.text() == "Send me my digest"  # TP-4
    assert not hasattr(window.views["Calendar"], "digest_button")


# --- the test email (EL-5) and the cap (FR-8, EL-11) --------------------------------------------------------------

def test_the_test_email_goes_to_the_admin_and_counts(org: Organizer, mail: RecordingMailer) -> None:
    result = org.send_test_email(ALEX)
    assert result["sent"] and mail.sent[-1]["to"] == ADMIN
    assert mail.sent[-1]["subject"] == "Home Base test email"
    assert org.today_view()["digest"]["sent_today"] == 1
    assert org.list_history()[0]["label"] == f"Test email to {ADMIN}"
    assert org.send_digest(ADMIN)["sent"]  # the test email doesn't trip the admin's one-minute rule
    assert len(mail.sent) == 2 and org.today_view()["digest"]["sent_today"] == 2


def test_the_cap_counts_test_emails(org: Organizer, mail: RecordingMailer, clock: FakeClock) -> None:
    org.digest_cap = 2
    org.send_test_email(ALEX)
    org.send_digest(ALEX)
    with pytest.raises(LimitReached, match="Daily digest limit reached"):
        org.send_digest(SECOND)
    with pytest.raises(LimitReached):
        org.send_test_email(ALEX)
    clock.advance(days=1)
    assert org.send_test_email(ALEX)["sent"]  # a new day


def test_a_test_hub_can_lower_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from family_scheduler.hub.context import build_context
    from family_scheduler.secrets_store import StaticKeyProvider

    monkeypatch.setenv("FS_TEST_DIGEST_CAP", "2")
    monkeypatch.delenv("FS_DATA_DIR", raising=False)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    real = build_context(tmp_path / "a", keys=StaticKeyProvider(), fake_calendar=True, fake_mail=True)
    assert real.organizer.digest_cap == 20  # only a test hub (with its own data folder) lowers it
    monkeypatch.setenv("FS_DATA_DIR", str(tmp_path / "b"))
    test_hub = build_context(tmp_path / "b", keys=StaticKeyProvider(), fake_calendar=True, fake_mail=True)
    assert test_hub.organizer.digest_cap == 2


def test_a_wrong_app_password_sends_nothing(org: Organizer, mail: RecordingMailer) -> None:
    from family_scheduler.organizer import ExternalFailure

    mail.fail_with = "The Gmail sign-in failed: Gmail didn't accept the app password."
    with pytest.raises(ExternalFailure, match="Gmail sign-in failed"):
        org.send_test_email(ALEX)
    assert mail.sent == [] and org.today_view()["digest"]["sent_today"] == 0  # EL-10


def test_the_test_email_over_the_api(ctx: HubContext, mail: RecordingMailer) -> None:
    c = TestClient(create_app(ctx), client=("192.168.1.20", 50000))
    r = c.post("/api/digest/test", headers={"X-Member": ALEX})
    assert r.status_code == 200 and mail.sent[-1]["to"] == ADMIN

