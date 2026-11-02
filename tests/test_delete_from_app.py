"""1.9.1: an event that came from Google Calendar can be deleted from Home Base without touching the Google event."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SAM, FakeClock, event
from test_today import open_app

from family_scheduler.app.views import describe_change, event_extras
from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import NotFound, Organizer, OrganizerError

OUTSIDER = "coach@example.org"


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def timed(title: str, day: str = "2026-10-27", start: str = "18:00", end: str = "19:00", **more: Any) -> dict[str, Any]:
    return {"summary": title, "start": {"dateTime": f"{day}T{start}:00", "timeZone": "America/Toronto"},
            "end": {"dateTime": f"{day}T{end}:00", "timeZone": "America/Toronto"}, **more}


def titles(org: Organizer) -> list[str]:
    events = org.list_events()
    return sorted(e["title"] for e in events["upcoming"] + events["past"])


def find(org: Organizer, title: str) -> dict[str, Any]:
    events = org.list_events()
    return next(e for e in events["upcoming"] + events["past"] if e["title"] == title)


def test_a_google_event_is_deleted_from_home_base_only(org: Organizer, gcal: FakeCalendarGateway) -> None:
    gid = gcal.google_add(ADMIN, timed("Parent council", attendees=[{"email": SAM}]))
    org.sync_google(ALEX)
    e = find(org, "Parent council")
    sent = len(gcal.calls)
    org.delete_from_app(ALEX, e["id"])
    assert titles(org) == [] and gid in gcal.events  # the real Google event is untouched
    assert gcal.calls[sent:] == []  # nothing sent to Google, so no email
    assert org.today_view()["events"] == []  # gone from Today, the badge and the digest too
    assert not org.week_view(date(2026, 10, 26), 7)["week_events"]
    assert org.list_hidden() == [] and org.list_events()["hidden_count"] == 0  # not a hidden event
    entry = next(h for h in org.list_history() if h["label"] == "Parent council")
    assert describe_change(entry) == "Deleted from Home Base (still in Google Calendar)"


def test_sync_doesnt_bring_it_back(org: Organizer, gcal: FakeCalendarGateway) -> None:
    gid = gcal.google_add(ADMIN, timed("Book club"))
    org.sync_google(ALEX)
    org.delete_from_app(ALEX, find(org, "Book club")["id"])
    org.sync_google(ALEX)
    gcal.google_change(gid, summary="Book club (moved)")  # changed in Google: still out of Home Base
    org.sync_google(ALEX)
    assert titles(org) == []
    gcal.google_remove(gid)  # removed in Google: the kept row goes too
    org.sync_google(ALEX)
    assert org.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 0


def test_read_only_and_private_google_events_too(org: Organizer, gcal: FakeCalendarGateway) -> None:
    gcal.google_add(OUTSIDER, timed("Team party", attendees=[{"email": ADMIN}]))
    gcal.google_add(ADMIN, timed("Secret", visibility="private"))
    org.sync_google(ALEX)
    for title in ("Team party", "Busy"):
        org.delete_from_app(ALEX, find(org, title)["id"])
    assert titles(org) == []


def test_undo_brings_it_back(org: Organizer, gcal: FakeCalendarGateway) -> None:
    gcal.google_add(ADMIN, timed("Swim"))
    org.sync_google(ALEX)
    e = find(org, "Swim")
    org.delete_from_app(ALEX, e["id"])
    org.undo_delete_from_app(ALEX, e["id"])
    assert titles(org) == ["Swim"]
    entry = org.list_history()[0]
    assert entry["action"] == "restored" and describe_change(entry) == "Back in Home Base"


def test_events_made_in_the_app_are_removed_or_hidden_instead(org: Organizer) -> None:
    e = org.add_event(ALEX, event("Dentist"))
    with pytest.raises(OrganizerError, match="Use Remove to cancel it, or Hide from app"):
        org.delete_from_app(ALEX, e["id"])
    with pytest.raises(NotFound):
        org.delete_from_app(ALEX, "no-such-event")


def test_its_car_no_longer_counts(org: Organizer, gcal: FakeCalendarGateway) -> None:
    gcal.google_add(ADMIN, timed("Hockey", start="16:00", end="17:00"))
    org.sync_google(ALEX)
    e = find(org, "Hockey")
    org.set_event_car(ALEX, e["id"], org.list_cars()[0]["id"])
    org.delete_from_app(ALEX, e["id"])
    assert org.today_view()["cars_today"] == []


def test_the_rows_offer_delete_from_home_base(qt_app: Any, ctx: HubContext, org: Organizer,
                                              gcal: FakeCalendarGateway, clock: FakeClock, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from family_scheduler.app.widgets import ACTION_TIPS

    gcal.google_add(ADMIN, timed("From Google"))
    gcal.google_add(OUTSIDER, timed("Invitation", attendees=[{"email": ADMIN}],
                                    htmlLink="https://calendar.google.com/event?eid=x"))
    org.sync_google(ALEX)
    mine = org.add_event(ALEX, event("Made here"))
    assert event_extras(find(org, "From Google"))[1] == ["car", "edit", "delete_app"]
    assert event_extras(find(org, "Invitation"))[1] == ["open", "delete_app"]
    assert event_extras(org.get_event(mine["id"]))[1] is None  # app events keep Hide and Remove
    assert ACTION_TIPS["delete_app"] == "Delete from Home Base. It stays in Google Calendar."
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("Events")
    view = window.current()
    view.remove(find(org, "From Google"))  # the Delete key on a Google event: from Home Base only
    assert "From Google" not in titles(org) and len(gcal.events) == 3
