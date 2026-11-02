"""WO-FS-008 section 5: all-day events (AD-1 to AD-10)."""

from __future__ import annotations

from datetime import date
from typing import Any

from conftest import ADMIN, ALEX, SAM, FakeClock, event

from family_scheduler import db
from family_scheduler.app.months import group_by_month
from family_scheduler.app.style import time_range
from family_scheduler.app.today import todays_events
from family_scheduler.app.week import day_items, short_time
from family_scheduler.digest import build_digest
from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.organizer import EventInput, Organizer

TODAY = "2026-10-27"  # the test clock: Tuesday Oct 27, 2026, 8:00 am Toronto time


def all_day(title: str = "Field trip", day: str = "2026-11-03", repeat: str = "none") -> EventInput:
    return EventInput(title, day, "", "", "Science Centre", repeat, [ADMIN, ALEX, SAM], all_day=True)


def google(gcal: FakeCalendarGateway, title: str) -> dict[str, Any]:
    return next(body for body in gcal.events.values() if body["summary"] == title)


def test_sent_to_google_as_a_real_all_day_event(org: Organizer, gcal: FakeCalendarGateway) -> None:  # AD-3
    e = org.add_event(ALEX, all_day())
    body = google(gcal, "Field trip")
    assert body["start"] == {"date": "2026-11-03"}
    assert body["end"] == {"date": "2026-11-04"}  # Google's end is the next day
    assert sorted(a["email"] for a in body["attendees"]) == [ALEX, SAM]  # invites work as for timed events
    assert e["all_day"] is True and (e["start_time"], e["end_time"]) == ("", "")  # AD-10
    assert e["sync_status"] == "synced"


def test_the_last_day_of_a_month_ends_on_the_first(org: Organizer, gcal: FakeCalendarGateway) -> None:
    org.add_event(ALEX, all_day("Halloween", "2026-10-31"))
    assert google(gcal, "Halloween")["end"] == {"date": "2026-11-01"}
    org.add_event(ALEX, all_day("New Year's Eve", "2026-12-31"))
    assert google(gcal, "New Year's Eve")["end"] == {"date": "2027-01-01"}


def test_all_day_events_repeat(org: Organizer, gcal: FakeCalendarGateway) -> None:  # AD-4
    org.add_event(ALEX, all_day("Garbage day", "2026-10-28", repeat="weekly"))
    assert google(gcal, "Garbage day")["recurrence"] == ["RRULE:FREQ=WEEKLY"]
    days = [e["day"] for e in org.week_view(date(2026, 10, 26), 35)["week_events"] if e["title"] == "Garbage day"]
    assert days == ["2026-10-28", "2026-11-04", "2026-11-11", "2026-11-18", "2026-11-25"]


def test_listed_before_timed_events(org: Organizer) -> None:  # AD-6, AD-7, AD-8
    org.add_event(ALEX, event("Breakfast", day=TODAY, start="07:00", end="07:30"))
    org.add_event(ALEX, all_day("Pyjama day", TODAY))
    week = org.week_view(date(2026, 10, 26), 7)["week_events"]
    assert [e["title"] for e in todays_events(week, TODAY)] == ["Pyjama day", "Breakfast"]  # Today
    on_day = [e for e in week if e["day"] == TODAY]
    assert [item["title"] for _, item in day_items(on_day, [])] == ["Pyjama day", "Breakfast"]  # week stack
    assert [e["title"] for e in org.today_view()["events"]] == ["Pyjama day", "Breakfast"]  # digest
    events = org.list_events()
    month = group_by_month(events["upcoming"], events["past"])[0]
    assert [r.event["title"] for r in month.rows] == ["Pyjama day", "Breakfast"]  # Events


def test_never_faded_until_the_day_is_over(org: Organizer, clock: FakeClock) -> None:  # AD-7
    org.add_event(ALEX, all_day("Pyjama day", TODAY))

    def past() -> bool:
        week = org.week_view(date(2026, 10, 26), 7)["week_events"]
        return next(e for e in week if e["title"] == "Pyjama day")["past"]

    clock.advance(hours=15, minutes=59)  # 11:59 pm Toronto time
    assert not past()
    clock.advance(minutes=1)  # midnight
    assert past()


def test_never_warns_about_overlaps(org: Organizer) -> None:  # AD-9
    org.add_event(ALEX, all_day("Pyjama day"))
    assert org.check_conflicts(ALEX, event("Dentist")) == []  # a timed event isn't warned about it
    assert org.check_conflicts(ALEX, all_day("Book fair")) == []  # two all-day events don't warn either
    org.add_event(ALEX, event("Dentist"))
    org.add_event(ALEX, all_day("Book fair"))
    assert all(not e["conflicts_with"] for e in org.list_events()["upcoming"])


def test_switching_between_timed_and_all_day_is_one_update(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(ALEX, event("Dentist"))  # AD-5
    org.edit_event(ALEX, e["id"], all_day("Dentist", "2026-11-03"))
    assert [c[0] for c in gcal.calls].count("patch") == 1
    body = google(gcal, "Dentist")
    assert body["start"]["date"] == "2026-11-03" and body["start"].get("dateTime") is None
    assert body["end"]["date"] == "2026-11-04" and body["end"].get("dateTime") is None
    assert org.get_event(e["id"])["all_day"] is True
    org.edit_event(ALEX, e["id"], event("Dentist", start="16:00", end="17:00"))
    assert [c[0] for c in gcal.calls].count("patch") == 2
    body = google(gcal, "Dentist")
    assert body["start"]["dateTime"] == "2026-11-03T16:00:00" and body["start"].get("date") is None
    after = org.get_event(e["id"])
    assert after["all_day"] is False and (after["start_time"], after["end_time"]) == ("16:00", "17:00")
    edits = [h for h in org.list_history() if h["action"] == "edited"]
    assert edits[0]["after"]["all_day"] is False and edits[1]["after"]["all_day"] is True


def test_shown_as_all_day_everywhere(org: Organizer) -> None:  # AD-6
    org.add_event(ALEX, all_day("Pyjama day", TODAY))
    assert time_range("", "") == "All day" and short_time("") == "All day"
    subject, text, html = build_digest(org.today_view(), ALEX)
    assert "Pyjama day, all day, Science Centre." in text
    assert "all day" in html


def test_existing_events_stay_timed_after_the_upgrade() -> None:  # AD-10
    conn = db.connect(":memory:")
    conn.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY)")
    for number, sql in db._migration_files()[:2]:  # the database as 1.5.0 left it
        for statement in db._split_sql(sql):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migration (version) VALUES (?)", (number,))
    conn.execute("INSERT INTO family_member (email, created_at) VALUES (?, 'n')", (ALEX,))
    # an existing hub already has its admin (WO-FS-010: the migrations no longer write one)
    conn.execute("INSERT INTO setting (key, value) VALUES ('admin_email', ?)", (ADMIN,))
    conn.execute("INSERT INTO family_member (email, created_at) VALUES (?, 'n')", (ADMIN,))
    conn.execute("INSERT INTO event (id, title, date, start_time, end_time, gcal_event_id, sync_status, created_by, "
                 "created_at, updated_at, starts_at, ends_at) VALUES ('e1', 'Dentist', '2026-11-03', '16:00', "
                 "'17:00', 'abcdef12345', 'synced', ?, 'n', 'n', 'n', 'n')", (ALEX,))
    conn.execute("UPDATE event SET organizer_email = ?", (ADMIN,))  # as 1.5.0 kept it
    conn.execute("INSERT INTO event_invitee (event_id, email, rsvp) VALUES ('e1', ?, 'accepted')", (ALEX,))
    conn.commit()
    assert db.migrate(conn) == 10
    r = conn.execute("SELECT * FROM event WHERE id = 'e1'").fetchone()
    assert (r["title"], r["start_time"], r["end_time"], r["all_day"]) == ("Dentist", "16:00", "17:00", 0)
    assert (r["organizer_email"], r["gcal_event_id"]) == (ADMIN, "abcdef12345")
    # the table was rebuilt: its invitees and replies are kept, and foreign keys are back on
    assert [tuple(x) for x in conn.execute("SELECT email, rsvp FROM event_invitee")] == [(ALEX, "accepted")]
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    indexes = {x[0] for x in conn.execute(
        "SELECT name FROM sqlite_master WHERE tbl_name = 'event' AND sql IS NOT NULL")}
    assert {"event_starts_at_idx", "event_unsynced_idx", "event_retry_idx"} <= indexes
