"""WO-FS-005 section 2: invites from two calendar accounts, organizer routing and automatic retry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SAM, SECOND, FakeClock, event

from family_scheduler import db
from family_scheduler.google_calendar import CalendarAuthError, FakeCalendarGateway
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import ExternalFailure, Organizer, OrganizerError, next_retry_at
from family_scheduler.timeutil import parse_iso


def senders(gcal: FakeCalendarGateway, op: str) -> list[str]:
    return [account for call, _, account in gcal.senders if call == op]


# --- GS-1, GS-2: who sends ---------------------------------------------------------------------------

def test_each_account_sends_its_own_and_everyone_else_sends_as_the_admin(org: Organizer) -> None:
    assert org.sender_for(ADMIN)["email"] == ADMIN
    assert org.sender_for(SECOND)["email"] == SECOND
    assert org.sender_for(ALEX) == {"email": ADMIN, "preferred": ADMIN, "fallback": False}


def test_events_are_sent_from_the_chosen_account(org: Organizer, gcal: FakeCalendarGateway) -> None:
    by_second = org.add_event(SECOND, event("TEST – second's", invitees=[ADMIN, SECOND]))
    by_alex = org.add_event(ALEX, event("TEST – Alex's", day="2026-11-05", invitees=[ADMIN, SECOND]))
    assert senders(gcal, "insert") == [SECOND, ADMIN]
    assert by_second["organizer_email"] == SECOND and by_alex["organizer_email"] == ADMIN
    # GS-8: the organizer isn't invited to their own event; the other account is.
    attendees = {b["summary"]: [a["email"] for a in b["attendees"]] for b in gcal.events.values()}
    assert attendees == {"TEST – second's": [ADMIN], "TEST – Alex's": [SECOND]}
    rsvps = {i["email"]: i["rsvp"] for i in by_second["invitees"]}
    assert rsvps == {ADMIN: "no_reply", SECOND: "organizer"}


def test_falls_back_to_the_other_connected_account(org: Organizer, gcal: FakeCalendarGateway) -> None:
    org.set_account_status(SECOND, "not_connected")
    assert org.sender_for(SECOND) == {"email": ADMIN, "preferred": SECOND, "fallback": True}
    org.add_event(SECOND, event())
    assert senders(gcal, "insert") == [ADMIN]
    org.set_account_status(SECOND, "connected")
    org.set_account_status(ADMIN, "needs_reconnect")
    assert org.sender_for(ALEX)["email"] == SECOND


def test_no_account_connected_means_the_event_isnt_saved(org: Organizer) -> None:
    org.set_account_status(ADMIN, "not_connected")
    org.set_account_status(SECOND, "not_connected")
    with pytest.raises(OrganizerError, match=f"connect {ADMIN} or {SECOND}"):
        org.add_event(ALEX, event())
    assert org.list_events()["upcoming"] == []


def test_only_the_two_calendar_accounts_can_connect(org: Organizer) -> None:
    with pytest.raises(OrganizerError, match="can't be connected"):
        org.require_calendar_account(ALEX)
    assert [a["email"] for a in org.calendar_accounts()] == [ADMIN, SECOND]


# --- GS-4, GS-6, AS-4: the organizer's account does everything for its event ---------------------------

def test_edits_removals_and_replies_go_through_the_organizer(org: Organizer, gcal: FakeCalendarGateway) -> None:
    weekly = org.add_event(SECOND, event("TEST – weekly", repeat="weekly", invitees=[ADMIN, SECOND, SAM]))
    org.add_event(ADMIN, event("TEST – one time", day="2026-11-06", invitees=[ADMIN, SECOND]))
    org.edit_event(ALEX, weekly["id"], event("TEST – weekly", start="16:30", end="17:30", repeat="weekly",
                                             invitees=[ADMIN, SECOND, SAM]))
    assert senders(gcal, "patch") == [SECOND]  # Alex edits; the second account sends the one update
    for gid, body in gcal.events.items():
        for a in body["attendees"]:
            gcal.respond(gid, a["email"], "accepted" if a["email"] == ADMIN else "declined")
    result = org.refresh_rsvps(ALEX)
    assert result["refreshed"] == 2 and sorted(senders(gcal, "get")) == [ADMIN, SECOND]
    replies = {e["title"]: {i["email"]: i["rsvp"] for i in e["invitees"]} for e in org.list_events()["upcoming"]}
    assert replies["TEST – weekly"] == {ADMIN: "accepted", SAM: "declined", SECOND: "organizer"}
    assert replies["TEST – one time"] == {ADMIN: "organizer", SECOND: "declined"}
    org.remove_event(SAM, weekly["id"])
    assert senders(gcal, "delete") == [SECOND]  # one cancellation, from the organizer


def test_disconnected_organizer_blocks_edit_and_remove(org: Organizer, gcal: FakeCalendarGateway) -> None:
    e = org.add_event(SECOND, event())
    org.set_account_status(SECOND, "not_connected")
    assert org.get_event(e["id"])["organizer_connected"] is False
    with pytest.raises(OrganizerError, match=f"Reconnect {SECOND} to change this event"):
        org.edit_event(SECOND, e["id"], event(start="17:00", end="18:00"))
    with pytest.raises(OrganizerError, match=f"Reconnect {SECOND}"):
        org.remove_event(SECOND, e["id"])
    with pytest.raises(ExternalFailure, match=f"Reconnect {SECOND}"):
        org.refresh_rsvps(ALEX)
    assert senders(gcal, "patch") == senders(gcal, "delete") == []


def test_a_refused_token_marks_the_account_needs_reconnecting(ctx: HubContext) -> None:
    class Refusing:
        def insert(self, body: dict[str, Any]) -> None:
            raise CalendarAuthError("Google no longer accepts this account's connection.")

    org = ctx.organizer
    org._calendar = lambda account: Refusing()  # type: ignore[assignment,return-value]
    e = org.add_event(SECOND, event())
    assert e["sync_status"] == "error"
    status = {a["email"]: a["status"] for a in org.calendar_accounts()}
    assert status == {ADMIN: "connected", SECOND: "needs_reconnect"}


def test_history_says_which_account_sent_it(org: Organizer) -> None:
    e = org.add_event(SECOND, event())
    org.remove_event(ALEX, e["id"])
    added, removed = (next(h for h in org.list_history() if h["action"] == a) for a in ("added", "removed"))
    assert added["after"]["invites_from"] == SECOND and removed["after"] == {"invites_from": SECOND}


# --- AS-2, AS-3: automatic retry ------------------------------------------------------------------------

def test_retry_schedule() -> None:
    first = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    minutes = [int((next_retry_at(first, n) - first).total_seconds() // 60) for n in range(1, 6)]  # type: ignore[operator]
    assert minutes == [1, 6, 21, 81, 141]  # 1, 5 and 15 minutes apart, then hourly
    assert next_retry_at(first, 3 + 23) == first + timedelta(minutes=21, hours=23)
    assert next_retry_at(first, 3 + 24) is None  # past 24 hours: stop


def test_an_event_saved_offline_is_sent_once_when_google_is_back(org: Organizer, gcal: FakeCalendarGateway,
                                                                 clock: FakeClock) -> None:
    gcal.offline = True
    e = org.add_event(ALEX, event())
    assert e["sync_status"] == "error" and e["next_retry_at"] is not None
    assert org.retry_due() == 0  # not due yet
    clock.advance(minutes=1)
    assert org.retry_due() == 0  # due, still offline
    assert parse_iso(org.get_event(e["id"])["next_retry_at"]) == clock.value + timedelta(minutes=5)
    gcal.offline = False
    clock.advance(minutes=5)
    assert org.retry_due() == 1
    assert org.get_event(e["id"])["sync_status"] == "synced"
    assert [c for c in gcal.calls if c[0] == "insert"] == [("insert", next(iter(gcal.events)))]  # once
    clock.advance(hours=2)
    assert org.retry_due() == 0


def test_after_24_hours_retries_stop_and_today_says_so(org: Organizer, gcal: FakeCalendarGateway,
                                                        clock: FakeClock) -> None:
    gcal.offline = True
    e = org.add_event(ALEX, event(day="2026-11-20"))
    for _ in range(30):
        clock.advance(hours=1)
        org.retry_due()
    stuck = org.get_event(e["id"])
    assert stuck["sync_status"] == "error" and stuck["next_retry_at"] is None and stuck["retry_gave_up"]
    assert org.today_view()["unsent"] == [{"id": e["id"], "title": "Dentist"}]
    gcal.offline = False
    org.retry_sync(ALEX, e["id"])  # Retry still works by hand
    assert org.get_event(e["id"])["sync_status"] == "synced"
    assert org.today_view()["unsent"] == []


def test_a_hub_restart_retries_at_once(org: Organizer, gcal: FakeCalendarGateway, clock: FakeClock) -> None:
    gcal.offline = True
    e = org.add_event(ALEX, event())
    gcal.offline = False
    org.retry_on_start()
    assert org.retry_due() == 1  # no need to wait for the 1-minute retry
    assert org.get_event(e["id"])["sync_status"] == "synced"


# --- DB-1, DB-4: upgrading a 1.2.0 database --------------------------------------------------------------

def test_migration_from_1_2_0_keeps_everything() -> None:
    conn = db.connect(":memory:")
    number, sql = db._migration_files()[0]
    conn.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY)")
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
    conn.execute("INSERT INTO secret (name, value_enc, updated_at) VALUES ('google_token', x'00', '2026-10-27')")
    conn.commit()

    assert db.migrate(conn) == 10
    r = conn.execute("SELECT * FROM event WHERE id = 'e1'").fetchone()
    assert (r["title"], r["organizer_email"], r["retry_attempts"], r["next_retry_at"]) == \
        ("Dentist", ADMIN, 0, None)  # GS-5
    assert [x[0] for x in conn.execute("SELECT name FROM secret")] == [f"google_token:{ADMIN}"]
    accounts = {x["email"]: x["status"] for x in conn.execute("SELECT * FROM calendar_account")}
    assert accounts == {ADMIN: "connected"}  # WO-FS-010: a second account is chosen in Family & Settings
    members = {x[0] for x in conn.execute("SELECT email FROM family_member WHERE active = 1")}
    assert members == {ADMIN, ALEX}
