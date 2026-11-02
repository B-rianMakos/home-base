"""Daylight saving (WO-FS-002 correction 2), time zone display (PC-10) and the SQLite schema."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time

import pytest
from conftest import ADMIN, ALEX, event

from family_scheduler import db, recurrence
from family_scheduler.organizer import Organizer
from family_scheduler.timeutil import local_to_utc


@pytest.mark.parametrize(
    ("day", "expected_utc_hour"),
    [
        (date(2026, 10, 31), 20),  # EDT, day before the change
        (date(2026, 11, 1), 21),  # EST from Nov 1, 2026
        (date(2027, 3, 13), 21),  # EST, day before the spring change
        (date(2027, 3, 14), 20),  # EDT from Mar 14, 2027
        (date(2027, 11, 6), 20),  # EDT
        (date(2027, 11, 7), 21),  # EST from Nov 7, 2027
    ],
)
def test_4pm_toronto_across_dst_changes(day: date, expected_utc_hour: int) -> None:
    assert local_to_utc(day, time(16, 0)).hour == expected_utc_hour


def test_weekly_series_stays_at_the_same_local_time_across_dst() -> None:
    window = (datetime(2027, 3, 1, tzinfo=UTC), datetime(2027, 3, 31, tzinfo=UTC))
    occ = recurrence.occurrences(date(2027, 3, 7), time(16, 0), time(17, 0), "weekly", "America/Toronto", *window)
    assert [s.hour for s, _ in occ] == [21, 20, 20, 20]  # Mar 7 EST, then EDT from Mar 14


def test_event_stored_in_utc(org: Organizer) -> None:
    e = org.add_event(ALEX, event(day="2027-03-14", start="01:30", end="03:30"))
    row = org.conn.execute("SELECT starts_at, ends_at FROM event WHERE id = ?", (e["id"],)).fetchone()
    # 2:00-3:00 am doesn't exist on Mar 14, 2027; 3:30 am EDT is 07:30 UTC.
    assert row["starts_at"] == "2027-03-14T06:30:00+00:00" and row["ends_at"] == "2027-03-14T07:30:00+00:00"


def test_monthly_on_the_31st_skips_short_months() -> None:
    window = (datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 5, 31, tzinfo=UTC))
    occ = recurrence.occurrences(date(2027, 1, 31), time(9), time(10), "monthly", "America/Toronto", *window)
    assert [s.month for s, _ in occ] == [1, 3]


def test_migrations_are_idempotent_and_constraints_hold() -> None:
    conn = db.connect(":memory:")
    assert db.migrate(conn) == 10
    assert db.migrate(conn) == 10
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO event (id, title, date, start_time, end_time, gcal_event_id, created_by, "
                     "created_at, updated_at, starts_at, ends_at) VALUES ('x', 'T', '2026-11-01', '10:00', '11:00', "
                     "'NOT_BASE32', ?, 'n', 'n', 'n', 'n')", (ADMIN,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO event (id, title, date, start_time, end_time, gcal_event_id, created_by, "
                     "created_at, updated_at, starts_at, ends_at) VALUES ('x', 'T', '2026-11-01', '11:00', '10:00', "
                     "'abcdef123', ?, 'n', 'n', 'n', 'n')", (ADMIN,))
    columns = {r[1] for r in conn.execute("PRAGMA table_info(family_member)")}
    assert "gets_digest" not in columns  # DG-9
    reminder_columns = {r[1] for r in conn.execute("PRAGMA table_info(reminder)")}
    assert "created_at" not in reminder_columns and "seq" in reminder_columns


def test_admin_account_can_be_changed_after_disconnecting(org: Organizer) -> None:
    from family_scheduler.organizer import OrganizerError

    with pytest.raises(OrganizerError, match="Disconnect Google Calendar first"):
        org.set_admin_email(ADMIN, "new.admin@example.com", google_connected=True)
    org.set_admin_email(ADMIN, "new.admin@example.com", google_connected=False)
    assert org.admin_email() == "new.admin@example.com"
    with pytest.raises(OrganizerError, match="admin account can't be removed"):
        org.remove_member(ALEX, "new.admin@example.com")
