"""WO-FS-013 section 8: hidden events can be deleted from Home Base, one at a time or all at once (DH-1 to DH-10)."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SAM, FakeClock, event
from test_delete_from_app import OUTSIDER, timed

from family_scheduler.app.views import describe_change
from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import NotFound, Organizer, OrganizerError


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def hidden_titles(org: Organizer) -> list[str]:
    return sorted(e["title"] for e in org.list_hidden())


def test_an_app_made_hidden_event_is_deleted_from_home_base_only(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(ALEX, event("Dentist", invitees=[SAM]))
    gid = org.conn.execute("SELECT gcal_event_id FROM event WHERE id = ?", (e["id"],)).fetchone()[0]
    org.hide_event(ALEX, e["id"])
    sent = len(gcal.calls)
    org.delete_from_app(SAM, e["id"])  # DH-2: made in the app, and hidden
    assert gid in gcal.events and gcal.calls[sent:] == []  # DH-3: still in Google, nothing sent, no email
    assert hidden_titles(org) == [] and org.list_events()["hidden_count"] == 0  # DH-4: out of the Hidden list
    entry = org.list_history()[0]
    assert describe_change(entry) == "Deleted from Home Base (still in Google Calendar)"  # DH-7


def test_visible_app_events_keep_hide_and_remove(org: Organizer) -> None:  # DH-9
    e = org.add_event(ALEX, event("Dentist"))
    with pytest.raises(OrganizerError, match="Use Remove to cancel it, or Hide from app"):
        org.delete_from_app(ALEX, e["id"])


def test_every_kind_of_hidden_event(org: Organizer, gcal: FakeCalendarGateway, clock: FakeClock) -> None:  # DH-2
    gcal.google_add(ADMIN, timed("From Google"))
    gcal.google_add(OUTSIDER, timed("Outside invitation", attendees=[{"email": ADMIN}]))
    gcal.google_add(ADMIN, timed("Secret", visibility="private"))
    org.sync_google(ALEX)
    gcal.offline = True
    org.add_event(ALEX, event("Never sent", day="2026-11-06"))
    gcal.offline = False
    org.add_event(ALEX, event("Weekly swim", repeat="weekly"))
    for e in org.list_events()["upcoming"]:
        org.hide_event(ALEX, e["id"])
    assert hidden_titles(org) == ["Busy", "From Google", "Never sent", "Outside invitation", "Weekly swim"]
    for e in org.list_hidden():
        org.delete_from_app(ALEX, e["id"])
    assert hidden_titles(org) == [] and org.list_events()["upcoming"] == []
    clock.advance(minutes=10)
    assert org.retry_due() == 0  # DH-3: the never-sent one is never sent
    assert not any(e.get("summary") == "Never sent" for e in gcal.events.values())
    assert org.week_view(date(2026, 10, 26), 14)["week_events"] == []  # DH-7: the whole series


def test_delete_all_hidden_then_undo(org: Organizer, gcal: FakeCalendarGateway) -> None:  # DH-5, DH-6
    for title in ("Swim", "Hockey", "Bank"):
        org.hide_event(ALEX, org.add_event(ALEX, event(title))["id"])
    shown = org.add_event(ALEX, event("Piano"))
    result = org.delete_all_hidden(SAM)
    assert result["count"] == 3 and hidden_titles(org) == []
    assert [e["title"] for e in org.list_events()["upcoming"]] == ["Piano"]  # only hidden ones go
    assert len(gcal.events) == 4  # Google untouched
    for event_id in result["ids"]:
        org.undo_delete_from_app(ALEX, event_id)  # Undo puts them back in the Hidden list, still hidden
    assert hidden_titles(org) == ["Bank", "Hockey", "Swim"]
    assert [e["id"] for e in org.list_events()["upcoming"]] == [shown["id"]]
    org.delete_all_hidden(SAM)
    with pytest.raises(NotFound, match="Nothing is hidden"):
        org.delete_all_hidden(SAM)


def test_sync_doesnt_bring_them_back(org: Organizer, gcal: FakeCalendarGateway) -> None:  # DH-4
    gid = gcal.google_add(ADMIN, timed("Book club"))
    org.sync_google(ALEX)
    mine = org.add_event(ALEX, event("Dentist"))
    for e in org.list_events()["upcoming"]:
        org.hide_event(ALEX, e["id"])
    org.delete_all_hidden(ALEX)
    gcal.google_change(gid, summary="Book club (moved)")
    org.sync_google(ALEX)
    assert org.list_events()["upcoming"] == [] and hidden_titles(org) == []
    mine_gid = org.conn.execute("SELECT gcal_event_id FROM event WHERE id = ?", (mine["id"],)).fetchone()[0]
    gcal.google_remove(gid)
    gcal.google_remove(mine_gid)  # removed in Google: the kept records go
    org.sync_google(ALEX)
    assert org.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 0


def test_its_car_no_longer_counts(org: Organizer) -> None:  # DH-8
    e = org.add_event(ALEX, event("Hockey", day="2026-10-27", start="16:00", end="17:00"))
    org.set_event_car(ALEX, e["id"], org.list_cars()[0]["id"])
    org.hide_event(ALEX, e["id"])
    org.delete_from_app(ALEX, e["id"])
    assert org.today_view()["cars_today"] == []


def test_over_the_api(ctx: HubContext) -> None:
    from fastapi.testclient import TestClient

    from family_scheduler.hub.api import create_app

    web = TestClient(create_app(ctx), client=("127.0.0.1", 50000))
    h = {"X-Member": ALEX}
    for title in ("Swim", "Bank"):
        ctx.organizer.hide_event(ALEX, ctx.organizer.add_event(ALEX, event(title))["id"])
    body = web.post("/api/events/hidden/delete-all", headers=h).json()
    assert body["count"] == 2 and web.get("/api/events/hidden", headers=h).json() == []


def test_the_hidden_list(qt_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # DH-1, DH-5, DH-6
    from PySide6.QtWidgets import QPushButton

    from family_scheduler.app import dialogs

    events = [{"id": f"e{n}", "title": t, "next_date": "2026-11-03", "start_time": "16:00", "end_time": "17:00"}
              for n, t in enumerate(("Swim", "Bank", "Hockey"))]
    calls: list[tuple[str, str]] = []
    dlg = dialogs.HiddenDialog(events, lambda i: calls.append(("show", i)),
                               delete=lambda i: calls.append(("delete", i)),
                               delete_all=lambda: calls.append(("delete all", "")),
                               undo_delete=lambda i: calls.append(("undo", i)))

    def trash_buttons() -> list[QPushButton]:
        return [b for b in dlg.findChildren(QPushButton)
                if not b.isHidden() and b.toolTip() == "Delete from Home Base. It stays in Google Calendar."]

    assert len(trash_buttons()) == 3 and dlg.delete_all_b.isEnabled()
    trash_buttons()[1].click()  # Bank: no confirmation
    assert calls == [("delete", "e1")] and [e["title"] for e in dlg.events] == ["Swim", "Hockey"]
    assert not dlg.undo_bar.isHidden()
    assert dlg.undo_text.text() == "Deleted from Home Base. It's still in Google Calendar."
    dlg.undo_b.click()
    assert calls[-1] == ("undo", "e1") and [e["title"] for e in dlg.events] == ["Swim", "Bank", "Hockey"]
    assert dlg.undo_bar.isHidden() and dlg.deleted == []
    asked: list[str] = []
    monkeypatch.setattr(dialogs, "confirm", lambda _p, _t, text, *a, **k: asked.append(text) or True)
    dlg.delete_every()
    assert asked == ["Delete all 3 hidden events from Home Base? They stay in Google Calendar."]
    assert calls[-1] == ("delete all", "") and dlg.events == [] and not dlg.delete_all_b.isEnabled()
    assert dlg.undo_text.text() == "3 hidden events deleted from Home Base. They're still in Google Calendar."
    dlg.undo()
    assert [c for c in calls if c[0] == "undo"][-3:] == [("undo", "e0"), ("undo", "e1"), ("undo", "e2")]
    assert len(dlg.events) == 3
