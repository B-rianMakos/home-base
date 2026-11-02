"""Events, the Google adapter and repeating series (EV-1 to EV-13, WO-FS-001 4.3-4.4)."""

from __future__ import annotations

import pytest
from conftest import ADMIN, ALEX, SAM, event

from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.organizer import ExternalFailure, NeedsConfirmation, Organizer, OrganizerError


def test_add_event_creates_it_in_google_with_attendees(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(ALEX, event())
    assert e["sync_status"] == "synced"
    body = next(iter(gcal.events.values()))
    assert body["summary"] == "Dentist"
    assert body["start"] == {"dateTime": "2026-11-03T16:00:00", "timeZone": "America/Toronto"}
    # The admin organizes the event, so Google doesn't invite them (WO-FS-001 assumption 3).
    assert sorted(a["email"] for a in body["attendees"]) == [ALEX, SAM]
    assert body["recurrence"] == []
    rsvps = {i["email"]: i["rsvp"] for i in e["invitees"]}
    assert rsvps == {ADMIN: "organizer", ALEX: "no_reply", SAM: "no_reply"}
    assert org.list_history()[0]["action"] == "added"
    assert org.list_history()[0]["who"] == ALEX


def test_validation_messages(org: Organizer) -> None:
    with pytest.raises(OrganizerError, match="end time must be after"):
        org.add_event(ALEX, event(start="17:00", end="16:00"))
    with pytest.raises(OrganizerError, match="at least one invitee"):
        org.add_event(ALEX, event(invitees=[" "]))
    with pytest.raises(OrganizerError, match="isn't on the family list"):
        org.add_event(ALEX, event(invitees=["stranger@example.com"]))


def test_overlap_needs_confirmation_then_save_anyway(org: Organizer) -> None:
    org.add_event(ALEX, event())
    with pytest.raises(NeedsConfirmation) as caught:
        org.add_event(ALEX, event("Parent-teacher interview", start="16:30", end="17:30"))
    assert caught.value.message == "Overlaps with Dentist."
    assert caught.value.extra["conflicts"][0]["title"] == "Dentist"
    saved = org.add_event(ALEX, event("Parent-teacher interview", start="16:30", end="17:30"),
                          acknowledge_conflicts=True)
    assert saved["conflict_acknowledged"] is True
    upcoming = {e["title"]: e for e in org.list_events()["upcoming"]}
    assert upcoming["Dentist"]["conflicts_with"] == ["Parent-teacher interview"]


def test_back_to_back_events_do_not_conflict(org: Organizer) -> None:
    org.add_event(ALEX, event())
    org.add_event(ALEX, event("Swim", start="17:00", end="18:00"))


def test_weekly_series_conflicts_with_a_later_one_off(org: Organizer) -> None:
    org.add_event(ALEX, event("Swim practice", day="2026-11-04", start="17:30", end="18:30", repeat="weekly"))
    with pytest.raises(NeedsConfirmation, match="Swim practice"):
        org.add_event(ALEX, event("Concert", day="2027-02-17", start="18:00", end="20:00"))


def test_editing_a_series_patches_google_once_and_logs_once(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(ALEX, event("Sunday dinner", day="2026-11-01", start="18:00", end="20:00", repeat="weekly"))
    assert e["series_note"].startswith("This changes all 52 weekly occurrences of Sunday dinner")
    gcal.calls.clear()
    org.edit_event(SAM, e["id"], event("Sunday dinner", day="2026-11-01", start="18:30", end="20:00",
                                       repeat="weekly"))
    assert [c[0] for c in gcal.calls] == ["patch"]  # EV-8a: one update for the series
    body = gcal.events[next(iter(gcal.events))]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY"]
    assert body["start"]["dateTime"] == "2026-11-01T18:30:00"
    edits = [h for h in org.list_history() if h["action"] == "edited"]
    assert len(edits) == 1  # EV-12
    assert edits[0]["before"] == {"start_time": "18:00"}
    assert edits[0]["after"] == {"start_time": "18:30", "invites_from": ADMIN}  # WO-FS-005 AS-5


def test_edit_keeps_existing_rsvps(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(ALEX, event())
    gcal.respond(next(iter(gcal.events)), SAM, "accepted")
    org.refresh_rsvps(ALEX)
    edited = org.edit_event(ALEX, e["id"], event(location="Smile Dental, Queen St"))
    assert {i["email"]: i["rsvp"] for i in edited["invitees"]}[SAM] == "accepted"


def test_failed_edit_changes_nothing(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(ALEX, event())
    gcal.fail_next = "patch"
    with pytest.raises(ExternalFailure, match="Nothing was changed"):
        org.edit_event(ALEX, e["id"], event(start="09:00", end="10:00"))
    assert org.get_event(e["id"])["start_time"] == "16:00"


def test_removing_a_series_cancels_it_in_google(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(ALEX, event("Swim", repeat="weekly"))
    org.remove_event(SAM, e["id"])
    assert gcal.events == {}
    assert org.list_events()["upcoming"] == []
    assert org.list_history()[0]["action"] == "removed"


def test_failed_remove_keeps_the_event(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(ALEX, event())
    gcal.fail_next = "delete"
    with pytest.raises(ExternalFailure):
        org.remove_event(ALEX, e["id"])
    assert org.get_event(e["id"])["sync_status"] == "synced"


def test_failed_create_shows_error_then_retry_succeeds(org: Organizer, gcal: FakeCalendarGateway) -> None:
    gcal.fail_next = "insert"
    e = org.add_event(ALEX, event())
    assert e["sync_status"] == "error" and "didn't respond" in e["sync_error"]
    assert org.list_events()["upcoming"][0]["id"] == e["id"]  # error badges sort to the top
    retried = org.retry_sync(ALEX, e["id"])
    assert retried is not None and retried["sync_status"] == "synced"
    assert len(gcal.events) == 1


def test_retry_after_lost_response_does_not_duplicate(org: Organizer, gcal: FakeCalendarGateway) -> None:
    """Google created the event but the reply was lost: the retry reuses the same id (409 = success)."""
    e = org.add_event(ALEX, event())
    gid = next(iter(gcal.events))
    org.conn.execute("UPDATE event SET sync_status = 'error', sync_op = 'create' WHERE id = ?", (e["id"],))
    org.retry_sync(ALEX, e["id"])
    assert list(gcal.events) == [gid]


def test_discard_only_for_events_that_never_reached_google(org: Organizer, gcal: FakeCalendarGateway) -> None:
    ok = org.add_event(ALEX, event())
    with pytest.raises(OrganizerError, match="Use Remove"):
        org.discard_event(ALEX, ok["id"])
    gcal.fail_next = "insert"
    bad = org.add_event(ALEX, event("Swim", start="18:00", end="19:00"))
    org.discard_event(ALEX, bad["id"])
    assert [e["title"] for e in org.list_events()["upcoming"]] == ["Dentist"]


def test_without_any_calendar_account_the_event_isnt_saved(tmp_path: object) -> None:
    """WO-FS-005 GS-2 (was: the event waited with an error)."""
    from family_scheduler import db

    conn = db.connect(":memory:")
    db.migrate(conn)
    org = Organizer(conn)
    org.set_up_family(ADMIN)  # WO-FS-010 AC-4: set up, but its account never connected
    with pytest.raises(OrganizerError, match="No calendar account is connected"):
        org.add_event(ADMIN, event(invitees=[ADMIN]))
    assert org.list_events()["upcoming"] == []


def test_monthly_and_yearly_rules(org: Organizer, gcal: FakeCalendarGateway) -> None:
    org.add_event(ALEX, event("Rent", day="2026-11-30", start="09:00", end="09:15", repeat="monthly"))
    org.add_event(ALEX, event("Birthday", day="2027-03-02", start="18:00", end="21:00", repeat="yearly"))
    rules = sorted(b["recurrence"][0] for b in gcal.events.values())
    assert rules == ["RRULE:FREQ=MONTHLY", "RRULE:FREQ=YEARLY"]
