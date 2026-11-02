"""WO-FS-010 section 8: two-way Google Calendar sync, against the fake Google (GY-1 to GY-16)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ADMIN, ALEX, SECOND, FakeClock, event

from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.organizer import Organizer, OrganizerError

OUTSIDER = "coach@example.org"


def timed(title: str, day: str = "2026-11-05", start: str = "18:00", end: str = "19:00", **more: Any) -> dict[str, Any]:
    return {"summary": title, "start": {"dateTime": f"{day}T{start}:00", "timeZone": "America/Toronto"},
            "end": {"dateTime": f"{day}T{end}:00", "timeZone": "America/Toronto"}, **more}


def find(org: Organizer, title: str) -> dict[str, Any]:
    events = org.list_events()
    return next(e for e in events["upcoming"] + events["past"] if e["title"] == title)


def titles(org: Organizer) -> list[str]:
    events = org.list_events()
    return sorted(e["title"] for e in events["upcoming"] + events["past"])


def history(org: Organizer) -> list[tuple[str, str, Any]]:
    return [(h["action"], h["label"], h["after"]) for h in org.list_history()]


def test_an_event_added_in_google_appears(org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-4
    gcal.google_add(ADMIN, timed("TEST – Parent night", location="School gym",
                                 attendees=[{"email": SECOND}, {"email": OUTSIDER, "responseStatus": "accepted"}]))
    result = org.sync_google(ALEX)
    assert result["added"] == 1 and "1 added" in result["message"]
    e = find(org, "TEST – Parent night")
    assert e["from_google"] and e["calendar_email"] == ADMIN and not e["read_only"]
    assert (e["date"], e["start_time"], e["end_time"], e["location"]) == ("2026-11-05", "18:00", "19:00", "School gym")
    assert {i["email"]: i["rsvp"] for i in e["invitees"]} == {ADMIN: "organizer", SECOND: "no_reply",
                                                              OUTSIDER: "accepted"}  # GY-13: by address
    assert ("added", "TEST – Parent night", {"source": "Google Calendar", "calendar": ADMIN}) in history(org)
    assert org.list_members() == org.list_members()  # the family list doesn't change
    assert OUTSIDER not in [m["email"] for m in org.list_members()]


def test_changed_and_removed_in_google(org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-5, GY-6
    gid = gcal.google_add(ADMIN, timed("TEST – Dinner"))
    org.sync_google(ALEX)
    gcal.google_change(gid, summary="TEST – Late dinner", end={"dateTime": "2026-11-05T20:00:00",
                                                                "timeZone": "America/Toronto"})
    assert org.sync_google(ALEX)["changed"] == 1
    e = find(org, "TEST – Late dinner")
    assert e["end_time"] == "20:00"
    edit = next(h for h in org.list_history() if h["action"] == "edited")
    assert edit["before"] == {"title": "TEST – Dinner", "end_time": "19:00"}
    assert edit["after"]["source"] == "Google Calendar"
    sent = len(gcal.calls)
    gcal.google_remove(gid)
    assert org.sync_google(ALEX)["removed"] == 1
    assert "TEST – Late dinner" not in titles(org)
    assert [c for c in gcal.calls[sent:] if c[0] != "list"] == []  # GY-6: nothing sent to Google, no email


def test_a_shared_event_shows_once(org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-9
    gcal.google_add(ADMIN, timed("TEST – Shared", attendees=[{"email": SECOND}]))  # on both calendars
    mine = org.add_event(ALEX, event("App event", invitees=[ADMIN, SECOND]))  # made in the app, also in Google
    result = org.sync_google(ALEX)
    assert result["added"] == 1 and titles(org).count("TEST – Shared") == 1
    assert titles(org).count("App event") == 1 and find(org, "App event")["id"] == mine["id"]
    assert org.sync_google(ALEX)["added"] == 0  # the next sync brings nothing new


def test_editing_a_synced_event_goes_to_google(org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-7
    gid = gcal.google_add(SECOND, timed("TEST – Swim", attendees=[{"email": ADMIN}]))
    org.sync_google(ALEX)
    e = find(org, "TEST – Swim")
    org.edit_event(ALEX, e["id"], event("TEST – Swim", day="2026-11-05", start="17:00", end="18:00",
                                        invitees=[ADMIN, SECOND]))
    assert gcal.events[gid]["start"]["dateTime"] == "2026-11-05T17:00:00"
    assert ("patch", gid, SECOND) in gcal.senders  # through its organizer's account (GS-4)
    org.remove_event(ALEX, e["id"])
    assert gid not in gcal.events and ("delete", gid, SECOND) in gcal.senders


def test_changed_in_both_places_google_wins(org: Organizer, gcal: FakeCalendarGateway,
                                            clock: FakeClock) -> None:  # GY-10
    gid = gcal.google_add(ADMIN, timed("TEST – Picnic"))
    org.sync_google(ALEX)
    e = find(org, "TEST – Picnic")
    clock.advance(minutes=5)
    org.edit_event(ALEX, e["id"], event("TEST – Picnic", day="2026-11-05", start="18:00", end="19:00",
                                        location="Park", invitees=[ADMIN]))
    gcal.google_change(gid, location="Beach")  # someone else, in Google, after the app's change
    clock.advance(minutes=5)
    result = org.sync_google(ALEX)
    assert find(org, "TEST – Picnic")["location"] == "Beach"
    assert result["replaced"] == ["TEST – Picnic"] and "replaced by the Google Calendar version" in result["message"]
    edit = next(h for h in org.list_history() if h["action"] == "edited" and h["after"].get("source"))
    assert edit["after"]["note"] == "Your change was replaced by the Google Calendar version"


def test_outside_invitations_are_read_only(org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-8
    gcal.google_add(OUTSIDER, timed("TEST – Team party", attendees=[{"email": ADMIN}],
                                    htmlLink="https://calendar.google.com/event?eid=abc"))
    org.sync_google(ALEX)
    e = find(org, "TEST – Team party")
    assert e["read_only"] and e["google_note"] == f"Organized by {OUTSIDER}"
    assert e["html_link"] == "https://calendar.google.com/event?eid=abc"
    with pytest.raises(OrganizerError, match="can only be changed in Google Calendar"):
        org.edit_event(ALEX, e["id"], event("Mine now"))
    with pytest.raises(OrganizerError, match="can only be changed in Google Calendar"):
        org.remove_event(ALEX, e["id"])


def test_private_events_show_only_as_busy(org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-12
    gcal.google_add(SECOND, timed("TEST – Secret plans", visibility="private", location="Somewhere",
                                  attendees=[{"email": OUTSIDER}]))
    org.sync_google(ALEX)
    e = find(org, "Busy")
    assert e["private"] and e["read_only"] and (e["start_time"], e["end_time"]) == ("18:00", "19:00")
    assert e["location"] == "" and [i["email"] for i in e["invitees"]] == [SECOND]  # no guests shown
    assert "TEST – Secret plans" not in str(org.list_history())


def test_repeats_the_app_cant_show_are_read_only(org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-11
    gcal.google_add(ADMIN, timed("TEST – Weekly", recurrence=["RRULE:FREQ=WEEKLY"]))
    gcal.google_add(ADMIN, timed("TEST – Fortnightly", recurrence=["RRULE:FREQ=WEEKLY;INTERVAL=2"]))
    gid = gcal.google_add(ADMIN, timed("TEST – Daily", recurrence=["RRULE:FREQ=DAILY"]))
    org.sync_google(ALEX)
    weekly, fortnightly = find(org, "TEST – Weekly"), find(org, "TEST – Fortnightly")
    assert weekly["repeat"] == "weekly" and not weekly["read_only"]
    assert fortnightly["read_only"] and "can't show" in fortnightly["google_note"]
    gcal.google_add(ADMIN, timed("TEST – Daily", day="2026-11-07", start="09:00", end="10:00",
                                 recurringEventId=gid, id=f"{gid}_20261107T130000Z"))  # one date changed
    org.sync_google(ALEX)
    daily = find(org, "TEST – Daily")
    assert daily["read_only"] and daily["google_note"] == "Some dates were changed in Google Calendar"
    assert titles(org).count("TEST – Daily") == 1


def test_multi_day_events_show_on_each_day(org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-11
    from datetime import date

    gcal.google_add(ADMIN, {"summary": "TEST – Cottage", "start": {"date": "2026-11-06"},
                            "end": {"date": "2026-11-09"}})
    org.sync_google(ALEX)
    e = find(org, "TEST – Cottage")
    assert e["end_date"] == "2026-11-08" and e["read_only"]
    days = [o["day"] for o in org.week_view(date(2026, 11, 2), 7)["week_events"] if o["title"] == "TEST – Cottage"]
    assert days == ["2026-11-06", "2026-11-07", "2026-11-08"]
    assert e["conflicts_with"] == []


def test_all_day_and_plain_repeats_are_fully_editable(org: Organizer, gcal: FakeCalendarGateway) -> None:
    gcal.google_add(ADMIN, {"summary": "TEST – Holiday", "start": {"date": "2026-11-11"},
                            "end": {"date": "2026-11-12"}, "recurrence": ["RRULE:FREQ=YEARLY"]})
    org.sync_google(ALEX)
    e = find(org, "TEST – Holiday")
    assert e["all_day"] and e["repeat"] == "yearly" and not e["read_only"] and e["end_date"] == ""


def test_the_first_sync_starts_30_days_back_and_later_ones_only_fetch_changes(
        org: Organizer, gcal: FakeCalendarGateway) -> None:  # GY-3
    org.sync_google(ALEX)
    lists = [c for c in gcal.calls if c[0] == "list"]
    assert len(lists) == 2  # one per connected account
    gcal.google_add(ADMIN, timed("TEST – Later"))
    assert org.sync_google(ALEX)["added"] == 1
    assert org.sync_google(ALEX)["added"] == 0


def test_an_expired_sync_token_syncs_everything_again(org: Organizer, gcal: FakeCalendarGateway,
                                                     ctx: Any) -> None:  # GY-3
    gcal.google_add(ADMIN, timed("TEST – Kept"))
    org.sync_google(ALEX)
    ctx.conn.execute("UPDATE calendar_account SET sync_token = 'expired'")
    result = org.sync_google(ALEX)
    assert result["added"] == 0 and titles(org).count("TEST – Kept") == 1


def test_a_disconnected_account_keeps_its_events_marked_not_synced(org: Organizer,
                                                                   gcal: FakeCalendarGateway) -> None:  # GY-15
    gcal.google_add(SECOND, timed("TEST – Hockey"))
    org.sync_google(ALEX)
    org.set_account_status(SECOND, "needs_reconnect")
    result = org.sync_google(ALEX)
    e = find(org, "TEST – Hockey")
    assert e["not_synced"]
    assert any(SECOND in p and "reconnect" in p for p in result["problems"])


def test_synced_events_take_part_in_overlaps_and_today(org: Organizer, gcal: FakeCalendarGateway,
                                                       clock: FakeClock) -> None:  # GY-14
    gcal.google_add(ADMIN, timed("TEST – Google one", day="2026-10-27", start="16:00", end="17:00"))
    org.sync_google(ALEX)
    assert [e["title"] for e in org.today_view()["events"]] == ["TEST – Google one"]
    conflicts = org.check_conflicts(ALEX, event("Dentist", day="2026-10-27", start="16:30", end="17:30"))
    assert [c["title"] for c in conflicts] == ["TEST – Google one"]


def test_the_sync_api_and_last_synced_time(ctx: Any, gcal: FakeCalendarGateway) -> None:  # GY-2
    from fastapi.testclient import TestClient

    from family_scheduler.hub.api import create_app

    web = TestClient(create_app(ctx), client=("127.0.0.1", 50000))
    gcal.google_add(ADMIN, timed("TEST – Over the API"))
    r = web.post("/api/sync", headers={"X-Member": ALEX})
    assert r.status_code == 200 and r.json()["added"] == 1 and r.json()["synced_at"]
    assert web.get("/api/events", headers={"X-Member": ALEX}).json()["synced_at"] == r.json()["synced_at"]
