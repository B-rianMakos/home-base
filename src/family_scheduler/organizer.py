"""The Organizer: every change to the family's lists goes through here (WO-FS-001 section 3.3).

Each command checks the member, validates, writes the change and its activity entry in one
transaction, then calls Google where needed. One lock serialises database access, so the
Clear all commit and Undo can never both win.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from bisect import bisect_left
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any

from family_scheduler import recurrence
from family_scheduler.digest import build_digest
from family_scheduler.digest import fmt_time as _fmt_time
from family_scheduler.google_calendar import CalendarAuthError, CalendarError, CalendarGateway, new_event_id
from family_scheduler.mailer import Mailer, MailError
from family_scheduler.timeutil import (
    HOME_TZ_NAME,
    Clock,
    iso,
    local_to_utc,
    local_today,
    parse_hhmm,
    parse_iso,
    system_clock,
    to_local,
)

UNDO_SECONDS = 10
RETENTION_DAYS = 30
DIGEST_DAILY_CAP = 20
DIGEST_REPEAT_SECONDS = 60
CONFLICT_HORIZON_DAYS = 365
MAX_CARS, CAR_NAME_MAX = 10, 30  # WO-FS-010 CR-1
GROCERY_MAX = 100                # GR-2
DUE_SOON_DAYS = 7                # WO-FS-012 CD-2: a task due within this many days counts as high priority
GROCERY_GROUPS = ("food", "item")  # WO-FS-011 GG-1: Food first, then Items
MINUTES_A_DAY = 24 * 60
RETRY_MINUTES = (1, 5, 15)     # AS-2: then every hour
RETRY_FOR_HOURS = 24           # AS-3: then the hub stops and Today shows a notice

PRIORITIES = ("high", "medium", "low")
LISTS = ("events", "tasks", "reminders")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RSVP_FROM_GOOGLE = {"accepted": "accepted", "declined": "declined", "tentative": "tentative",
                     "needsAction": "no_reply"}


# --- Errors the hub turns into HTTP responses --------------------------------------------------

class OrganizerError(Exception):
    status = 400

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra


class NotFound(OrganizerError):
    status = 404


class Forbidden(OrganizerError):
    status = 403


class NeedsConfirmation(OrganizerError):
    """The event overlaps others; the member must choose Save anyway or Change time (EV-4)."""
    status = 409


class TooLate(OrganizerError):
    status = 409


class ExternalFailure(OrganizerError):
    """Google or Gmail refused. Nothing local was left out of sync."""
    status = 502


class LimitReached(OrganizerError):
    status = 429


# --- Inputs ------------------------------------------------------------------------------------

@dataclass
class EventInput:
    title: str
    date: str
    start_time: str
    end_time: str
    location: str = ""
    repeat: str = "none"
    invitees: list[str] = field(default_factory=list)
    all_day: bool = False  # WO-FS-008 AD-1: one whole day; start_time and end_time are then empty


@dataclass
class TaskInput:
    title: str
    due_date: str
    priority: str = "medium"
    repeat: str = "none"


CalendarProvider = Callable[[str], CalendarGateway]  # one gateway per calendar account
MailerProvider = Callable[[], Mailer]


def _not_connected_calendar(account: str) -> CalendarGateway:
    raise CalendarError(f"{account} isn't connected. Connect it on the hub PC under Family & Settings.")


def _statuses(gcal_id: str, gateway: CalendarGateway) -> dict[str, str] | None:
    return gateway.attendee_statuses(gcal_id)


def _delete(gcal_id: str, gateway: CalendarGateway) -> None:
    gateway.delete(gcal_id)


def effective_priority(priority: str, due_date: str, completed_at: str | None, today: date) -> tuple[str, bool]:
    """WO-FS-012 CD-1, DS-1 to DS-8: the priority a task is shown and sorted with, and whether it was bumped. A task
    not yet done and due today or within DUE_SOON_DAYS counts as high; the saved priority never changes (DS-2).
    Overdue tasks (DS-7), ongoing ones (no date, PL-3), done ones and tasks already high (DS-6) aren't bumped."""
    if completed_at or not due_date or priority == "high":
        return priority, False
    if today.isoformat() <= due_date <= (today + timedelta(days=DUE_SOON_DAYS)).isoformat():
        return "high", True
    return priority, False


def next_retry_at(first_failed: datetime, attempts: int) -> datetime | None:
    """AS-2: 1, 5 and 15 minutes after the first failure, then every hour; None after 24 hours (AS-3)."""
    if attempts <= len(RETRY_MINUTES):
        due = first_failed + timedelta(minutes=sum(RETRY_MINUTES[:attempts]))
    else:
        due = first_failed + timedelta(minutes=sum(RETRY_MINUTES), hours=attempts - len(RETRY_MINUTES))
    return due if due <= first_failed + timedelta(hours=RETRY_FOR_HOURS) else None


def _not_connected_mailer() -> Mailer:
    raise MailError("Digest email isn't set up. On the hub PC, add a Gmail app password in Family & Settings.")


class Organizer:
    def __init__(
        self,
        conn: sqlite3.Connection,
        calendar: CalendarProvider = _not_connected_calendar,
        mailer: MailerProvider = _not_connected_mailer,
        clock: Clock = system_clock,
    ) -> None:
        self.conn = conn
        self._calendar = calendar
        self._mailer = mailer
        self._clock = clock
        self._lock = threading.RLock()
        self._digest_lock = threading.Lock()
        self.digest_cap = DIGEST_DAILY_CAP  # FR-8; lowered only on a test hub (WO-FS-007 EL-11)

    # --- plumbing ------------------------------------------------------------------------------

    def now(self) -> datetime:
        return self._clock()

    def today(self) -> date:
        return local_today(self.now())

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("UPDATE setting SET value = CAST(value AS INTEGER) + 1 WHERE key = 'change_seq'")
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self.conn

    def _log(self, who: str, action: str, item_type: str, label: str, *, item_id: str | None = None,
             list_name: str | None = None, before: Any = None, after: Any = None) -> None:
        self.conn.execute(
            "INSERT INTO activity_entry (who, action, item_type, item_id, list, item_label, at, before, after) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (who, action, item_type, item_id, list_name, label, iso(self.now()),
             None if before is None else json.dumps(before), None if after is None else json.dumps(after)),
        )

    def setting(self, key: str) -> str | None:
        with self._read() as c:
            row = c.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def _set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def change_seq(self) -> int:
        return int(self.setting("change_seq") or 0)

    def admin_email(self) -> str:
        return self.setting("admin_email") or ""

    def require_member(self, who: str) -> str:
        who = (who or "").strip().lower()
        with self._read() as c:
            row = c.execute("SELECT 1 FROM family_member WHERE email = ? AND active = 1", (who,)).fetchone()
        if row is None:
            raise Forbidden(f"{who or 'That person'} isn't on the family list. Pick someone else.")
        return who

    def _hidden(self, list_name: str) -> set[str]:
        """Items inside an open Clear all window are hidden from everyone (CA-3), and so are events hidden from the
        app (WO-FS-011 HE-4): every list, view, overlap check, the badge and the digest leave them out."""
        hidden: set[str] = set()
        if list_name == "events":  # and (1.9.1) Google events deleted from Home Base
            hidden.update(r[0] for r in self.conn.execute("SELECT id FROM event WHERE hidden = 1 OR app_deleted = 1"))
        for row in self.conn.execute(
            "SELECT item_ids FROM pending_clear WHERE list = ? AND state IN ('pending', 'committing')", (list_name,)
        ):
            hidden.update(json.loads(row[0]))
        return hidden

    # --- family members ------------------------------------------------------------------------

    def list_members(self) -> list[dict[str, Any]]:
        admin = self.admin_email()
        with self._read() as c:
            rows = c.execute("SELECT email FROM family_member WHERE active = 1 ORDER BY email").fetchall()
        return [{"email": r["email"], "is_admin": r["email"] == admin} for r in rows]

    def add_member(self, who: str, email: str) -> dict[str, Any]:
        self.require_member(who)
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise OrganizerError("Enter a full email address, like name@gmail.com.")
        with self._write() as c:
            c.execute(
                "INSERT INTO family_member (email, created_at) VALUES (?, ?) "
                "ON CONFLICT(email) DO UPDATE SET active = 1",
                (email, iso(self.now())),
            )
        return {"email": email, "is_admin": email == self.admin_email()}

    def remove_member(self, who: str, email: str) -> None:
        self.require_member(who)
        email = email.strip().lower()
        if email == self.admin_email():
            raise OrganizerError("The admin account can't be removed. Change the admin account first.")
        with self._write() as c:
            if c.execute("UPDATE family_member SET active = 0 WHERE email = ? AND active = 1", (email,)).rowcount == 0:
                raise NotFound(f"{email} isn't on the family list.")

    def set_admin_email(self, who: str, email: str, *, google_connected: bool) -> None:
        """Correction 4: the admin account can be changed after disconnecting Google. The admin is always the first
        calendar account (WO-FS-010 AC-5)."""
        self.require_member(who)
        email = self._valid_email(email)
        if google_connected:
            raise OrganizerError("Disconnect Google Calendar first, then change the admin account.")
        if email == self.second_account():
            raise OrganizerError(f"{email} is the second calendar account. Remove it there first.")
        with self._write() as c:
            c.execute("INSERT INTO family_member (email, created_at) VALUES (?, ?) "
                      "ON CONFLICT(email) DO UPDATE SET active = 1", (email, iso(self.now())))
            self._set_setting("admin_email", email)
            c.execute("DELETE FROM calendar_account WHERE position = 1")
            c.execute("INSERT INTO calendar_account (email, position) VALUES (?, 1)", (email,))

    @staticmethod
    def _valid_email(email: str) -> str:
        email = (email or "").strip().lower()
        if not _EMAIL.match(email):
            raise OrganizerError("Enter a full email address, like name@gmail.com.")
        return email

    # --- first-time setup (WO-FS-010 AC-4) -------------------------------------------------------

    def needs_setup(self) -> bool:
        """A new hub has no admin: the app asks for the family's details before anything else."""
        return not self.admin_email()

    def set_up_family(self, admin: str, second: str = "", members: list[str] | None = None) -> None:
        """AC-4: the admin (required), a second calendar account (optional) and family members. Only once: after
        that, Family & Settings changes them."""
        if not self.needs_setup():
            raise OrganizerError("This hub is already set up. Change it in Family & Settings.")
        admin = self._valid_email(admin)
        second = self._valid_email(second) if second.strip() else ""
        if second == admin:
            raise OrganizerError("The second calendar account must be a different address from the admin.")
        others = [self._valid_email(m) for m in members or [] if m.strip()]
        now = iso(self.now())
        with self._write() as c:
            for email in dict.fromkeys([admin, *([second] if second else []), *others]):
                c.execute("INSERT INTO family_member (email, created_at) VALUES (?, ?) "
                          "ON CONFLICT(email) DO UPDATE SET active = 1", (email, now))
            self._set_setting("admin_email", admin)
            c.execute("DELETE FROM calendar_account")
            c.execute("INSERT INTO calendar_account (email, position) VALUES (?, 1)", (admin,))
            if second:
                c.execute("INSERT INTO calendar_account (email, position) VALUES (?, 2)", (second,))

    # --- calendar accounts (GA-1, GA-5, GS-1, GS-2) ----------------------------------------------

    def calendar_accounts(self) -> list[dict[str, Any]]:
        with self._read() as c:
            rows = c.execute("SELECT * FROM calendar_account ORDER BY position").fetchall()
        return [{"email": r["email"], "status": r["status"], "last_error": r["last_error"],
                 "connected_at": r["connected_at"]} for r in rows]

    def second_account(self) -> str:
        with self._read() as c:
            row = c.execute("SELECT email FROM calendar_account WHERE position = 2").fetchone()
        return str(row[0]) if row else ""

    def set_second_account(self, who: str, email: str) -> None:
        """AC-5: up to two calendar accounts; the admin is always the first. The second can be added, changed or
        removed (email ""), but not while it's connected or organizes upcoming events."""
        self.require_member(who)
        email = self._valid_email(email) if email.strip() else ""
        current = self.second_account()
        if email == current:
            return
        if email and email == self.admin_email():
            raise OrganizerError("The admin is already the first calendar account.")
        if current:
            status = next(a["status"] for a in self.calendar_accounts() if a["email"] == current)
            if status != "not_connected":
                raise OrganizerError(f"Disconnect {current} first, then change the second calendar account.")
            upcoming = self._upcoming_organized_by(current)
            if upcoming:
                raise OrganizerError(f"{current} organizes {upcoming} upcoming event{'s' if upcoming != 1 else ''}, "
                                     "so it can't be removed. Remove those events first.")
        with self._write() as c:
            c.execute("DELETE FROM calendar_account WHERE position = 2")
            if email:
                c.execute("INSERT INTO family_member (email, created_at) VALUES (?, ?) "
                          "ON CONFLICT(email) DO UPDATE SET active = 1", (email, iso(self.now())))
                c.execute("INSERT INTO calendar_account (email, position) VALUES (?, 2)", (email,))

    def _upcoming_organized_by(self, email: str) -> int:
        with self._read() as c:
            return int(c.execute("SELECT COUNT(*) FROM event WHERE organizer_email = ? AND sync_op IS NOT 'create' "
                                 "AND (ends_at >= ? OR repeat <> 'none')", (email, iso(self.now()))).fetchone()[0])

    def connected_accounts(self) -> list[str]:
        return [a["email"] for a in self.calendar_accounts() if a["status"] == "connected"]

    def require_calendar_account(self, email: str) -> str:
        email = email.strip().lower()
        if email not in [a["email"] for a in self.calendar_accounts()]:
            names = " or ".join(a["email"] for a in self.calendar_accounts())
            raise OrganizerError(f"Only {names} can send invites. {email} can't be connected.")
        return email

    def set_account_status(self, email: str, status: str, error: str | None = None) -> None:
        """connected, not_connected or needs_reconnect (GA-5). Called by the hub, which holds the tokens."""
        email = self.require_calendar_account(email)
        with self._write() as c:
            c.execute("UPDATE calendar_account SET status = ?, last_error = ?, "
                      "connected_at = CASE WHEN ? = 'connected' THEN ? ELSE connected_at END WHERE email = ?",
                      (status, error, status, iso(self.now()), email))

    def sender_for(self, who: str) -> dict[str, Any]:
        """GS-1, GS-2: whose account sends a new event's invites when `who` is using the app."""
        who = (who or "").strip().lower()
        accounts = [a["email"] for a in self.calendar_accounts()]
        connected = self.connected_accounts()
        admin = self.admin_email()
        if not accounts:
            raise OrganizerError("No calendar account is set up yet. Open Family & Settings and set the admin.")
        # AC-6: the user's own calendar account if they have one, otherwise the admin's
        preferred = who if who in accounts else (admin if admin in accounts else accounts[0])
        if preferred in connected:
            return {"email": preferred, "preferred": preferred, "fallback": False}
        if connected:
            return {"email": connected[0], "preferred": preferred, "fallback": True}
        raise OrganizerError(
            "No calendar account is connected, so invites can't be sent and the event can't be saved. On the hub "
            f"PC, open Family & Settings and connect {' or '.join(accounts)}.")

    def _gateway(self, account: str) -> CalendarGateway:
        return self._calendar(account)

    def _google(self, account: str, call: Callable[[CalendarGateway], Any]) -> Any:
        """Run one Google call as `account`. A refused token marks the account Needs reconnecting (GA-5)."""
        try:
            return call(self._gateway(account))
        except CalendarAuthError as err:
            with self._write() as c:
                c.execute("UPDATE calendar_account SET status = 'needs_reconnect', last_error = ? WHERE email = ?",
                          (str(err), account))
            raise CalendarError(f"{account}: {err}") from err

    # --- hiding events from the app (WO-FS-011 section 3) -------------------------------------------------

    def hide_event(self, who: str, event_id: str) -> None:
        """HE-1 to HE-6: takes the event out of the app for the whole family, a repeating one's whole series. Nothing
        changes in Google Calendar and no email is sent; Sync with Google leaves it hidden."""
        who = self.require_member(who)
        with self._write() as c:
            r = c.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
            if r is None:
                raise NotFound("That event no longer exists.")
            if r["hidden"]:
                return
            c.execute("UPDATE event SET hidden = 1, hidden_by = ?, hidden_at = ? WHERE id = ?",
                      (who, iso(self.now()), event_id))
            self._log(who, "removed", "event", r["title"], item_id=event_id, after={"to": "hidden"})  # HE-8

    def show_event(self, who: str, event_id: str) -> dict[str, Any]:
        """HE-3 Undo, HE-7 Show again: back everywhere, with its car (whose clashes are worked out again)."""
        who = self.require_member(who)
        with self._write() as c:
            r = c.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
            if r is None:
                raise NotFound("That event no longer exists. It may have been removed in Google Calendar.")
            if r["hidden"]:
                c.execute("UPDATE event SET hidden = 0, hidden_by = NULL, hidden_at = NULL WHERE id = ?",
                          (event_id,))
                self._log(who, "restored", "event", r["title"], item_id=event_id, after={"from": "hidden"})
        return self.get_event(event_id)

    def delete_from_app(self, who: str, event_id: str) -> None:
        """1.9.1: an event that came from Google Calendar is deleted from Home Base only, for the whole family. The
        Google event isn't touched and no email is sent; Sync with Google won't bring it back. WO-FS-013 DH-1 to DH-3:
        so is any hidden event, including one made in the app (it stays in each person's Google Calendar; one that
        never reached Google is never sent). Visible events made in the app keep Hide and Remove (DH-9)."""
        who = self.require_member(who)
        with self._write() as c:
            r = c.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
            if r is None or r["app_deleted"]:
                raise NotFound("That event no longer exists.")
            if r["source"] != "google" and not r["hidden"]:
                raise OrganizerError(f"{r['title']} was made in Home Base. Use Remove to cancel it, or Hide from app.")
            self._mark_deleted_from_app(c, who, r)

    def _mark_deleted_from_app(self, c: Any, who: str, r: Any) -> None:
        """DH-4, DH-7, DH-8: a marked record stays so Sync recognises it; a repeating event goes as a whole series;
        its car no longer counts; History says so."""
        c.execute("UPDATE event SET app_deleted = 1, app_deleted_by = ?, app_deleted_at = ? WHERE id = ?",
                  (who, iso(self.now()), r["id"]))
        self._log(who, "removed", "event", r["title"], item_id=r["id"], after={"to": "deleted from app"})

    def delete_all_hidden(self, who: str) -> dict[str, Any]:
        """WO-FS-013 DH-6: Delete all hidden. Returns the ids, so Undo can put each back in the Hidden list."""
        who = self.require_member(who)
        with self._write() as c:
            rows = c.execute("SELECT * FROM event WHERE hidden = 1 AND app_deleted = 0 ORDER BY hidden_at DESC"
                             ).fetchall()
            if not rows:
                raise NotFound("Nothing is hidden.")
            for r in rows:
                self._mark_deleted_from_app(c, who, r)
        return {"ids": [r["id"] for r in rows], "count": len(rows)}

    def undo_delete_from_app(self, who: str, event_id: str) -> dict[str, Any]:
        """Undo, straight after Delete from Home Base."""
        who = self.require_member(who)
        with self._write() as c:
            r = c.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
            if r is None:
                raise NotFound("That event no longer exists. It may have been removed in Google Calendar.")
            if r["app_deleted"]:
                c.execute("UPDATE event SET app_deleted = 0, app_deleted_by = NULL, app_deleted_at = NULL "
                          "WHERE id = ?", (event_id,))
                self._log(who, "restored", "event", r["title"], item_id=event_id, after={"from": "deleted from app"})
        return self.get_event(event_id)

    def list_hidden(self) -> list[dict[str, Any]]:
        """HE-7: the hidden events, most recently hidden first, for the Hidden list."""
        with self._read() as c:
            rows = c.execute("SELECT * FROM event WHERE hidden = 1 AND app_deleted = 0 "
                             "ORDER BY hidden_at DESC").fetchall()
            details = {d["id"]: d for d in self._event_dicts(rows, with_conflicts=False)}
        return [details[r["id"]] | {"hidden_by": r["hidden_by"], "hidden_at": r["hidden_at"]} for r in rows]

    @staticmethod
    def _require_editable(r: Any) -> None:
        """WO-FS-010 GY-8, GY-11, GY-12: some events from Google can only be changed in Google Calendar."""
        if r["read_only"]:
            raise OrganizerError(f"{r['title']} can only be changed in Google Calendar ({r['google_note']}). Open it "
                                 "there.")

    def sync_google(self, who: str) -> dict[str, Any]:
        """WO-FS-010 GY-2: Sync with Google: new, changed and removed events, and replies, in one go."""
        from family_scheduler.sync import sync_google

        result = sync_google(self, who)
        return {"message": result.message(), "added": result.added, "changed": result.changed,
                "removed": result.removed, "replaced": result.replaced, "problems": result.problems,
                "synced_at": self.setting("google_synced_at")}

    def _require_organizer_connected(self, r: Any) -> None:
        """GS-6: an event can only be changed through the account that created it."""
        if r["sync_op"] == "create":
            return  # never reached Google: it can still be changed here
        if r["organizer_email"] not in self.connected_accounts():
            raise OrganizerError(f"Reconnect {r['organizer_email']} to change this event. It was sent from that "
                                 "account, and Google only lets the organizer change it.")

    # --- events: validation, conflicts, Google body ---------------------------------------------

    def _validate_event(self, inp: EventInput) -> EventInput:
        title = inp.title.strip()
        if not 1 <= len(title) <= 120:
            raise OrganizerError("Give the event a title (up to 120 characters).")
        try:
            day = date.fromisoformat(inp.date)
            if inp.all_day:
                start_text = end_text = ""
            else:
                start, end = parse_hhmm(inp.start_time), parse_hhmm(inp.end_time)
                start_text, end_text = start.strftime("%H:%M"), end.strftime("%H:%M")
        except ValueError as err:
            raise OrganizerError("Enter a valid date and times.") from err
        if not inp.all_day and end <= start:
            raise OrganizerError("The end time must be after the start time on the same day.")
        location = inp.location.strip()
        if len(location) > 200:
            raise OrganizerError("Keep the location under 200 characters.")
        if inp.repeat not in recurrence.REPEATS:
            raise OrganizerError("Choose how often the event repeats.")
        invitees = sorted({e.strip().lower() for e in inp.invitees if e.strip()})
        if not invitees:
            raise OrganizerError("Pick at least one invitee.")
        active = {m["email"] for m in self.list_members()}
        unknown = [e for e in invitees if e not in active]
        if unknown:
            raise OrganizerError(f"{', '.join(unknown)} isn't on the family list.")
        return EventInput(title, day.isoformat(), start_text, end_text, location, inp.repeat, invitees,
                          all_day=inp.all_day)

    def _window(self, first: datetime) -> tuple[datetime, datetime]:
        now = self.now()
        return min(now, first), now + timedelta(days=CONFLICT_HORIZON_DAYS)

    def _occurrences(self, r: Any, window: tuple[datetime, datetime]) -> list[recurrence.Interval]:
        """Every occurrence in the window; an all-day event's run from midnight to the last minute of its day. A
        multi-day event from Google is shown on each day it covers (WO-FS-010 GY-11)."""
        if r["end_date"]:
            first, last = date.fromisoformat(r["date"]), date.fromisoformat(r["end_date"])
            days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
            spans = [(local_to_utc(d, time(0, 0)), local_to_utc(d, time(23, 59))) for d in days]
            return [(a, b) for a, b in spans if b >= window[0] and a < window[1]]
        if r["all_day"]:
            start, end = time(0, 0), time(23, 59)
        else:
            start, end = parse_hhmm(r["start_time"]), parse_hhmm(r["end_time"])
        return recurrence.occurrences(date.fromisoformat(r["date"]), start, end, r["repeat"], r["time_zone"],
                                      *window)

    def _find_conflicts(self, inp: EventInput, exclude_id: str | None) -> list[dict[str, Any]]:
        if inp.all_day:  # AD-9: an all-day event never overlaps anything
            return []
        first = local_to_utc(date.fromisoformat(inp.date), parse_hhmm(inp.start_time))
        window = self._window(first)
        candidate = {"date": inp.date, "start_time": inp.start_time, "end_time": inp.end_time,
                     "repeat": inp.repeat, "time_zone": HOME_TZ_NAME, "all_day": 0, "end_date": ""}
        mine = self._occurrences(candidate, window)
        hidden = self._hidden("events")
        others: list[tuple[datetime, datetime, Any]] = []
        for r in self.conn.execute("SELECT * FROM event"):
            if r["id"] == exclude_id or r["id"] in hidden or r["all_day"] or r["end_date"]:  # AD-9
                continue
            others.extend((s, e, r) for s, e in self._occurrences(r, window))
        others.sort(key=lambda o: o[0])
        starts = [o[0] for o in others]
        found: dict[str, dict[str, Any]] = {}
        for s, e in mine:
            i = bisect_left(starts, s - timedelta(days=1))
            while i < len(others) and others[i][0] < e:
                os_, oe, r = others[i]
                if oe > s and r["id"] not in found:
                    found[r["id"]] = {"id": r["id"], "title": r["title"], "date": to_local(os_).date().isoformat(),
                                      "start_time": r["start_time"], "end_time": r["end_time"],
                                      "location": r["location"]}
                i += 1
        return sorted(found.values(), key=lambda c: (c["date"], c["start_time"]))

    def check_conflicts(self, who: str, inp: EventInput, exclude_id: str | None = None) -> list[dict[str, Any]]:
        self.require_member(who)
        inp = self._validate_event(inp)
        with self._read():
            return self._find_conflicts(inp, exclude_id)

    def _google_body(self, r: Any, invitees: list[str], *, with_id: bool) -> dict[str, Any]:
        organizer = r["organizer_email"]
        if r["all_day"]:  # AD-3: Google's date-only start and end, the end being the next day
            next_day = (date.fromisoformat(r["date"]) + timedelta(days=1)).isoformat()
            start: dict[str, Any] = {"date": r["date"]}
            end: dict[str, Any] = {"date": next_day}
            if not with_id:  # a patch from timed to all-day must clear the old times (AD-5)
                start |= {"dateTime": None, "timeZone": None}
                end |= {"dateTime": None, "timeZone": None}
        else:
            start = {"dateTime": f"{r['date']}T{r['start_time']}:00", "timeZone": r["time_zone"]}
            end = {"dateTime": f"{r['date']}T{r['end_time']}:00", "timeZone": r["time_zone"]}
            if not with_id:  # and from all-day back to timed
                start["date"] = None
                end["date"] = None
        body: dict[str, Any] = {
            "summary": r["title"],
            "location": r["location"],
            "start": start,
            "end": end,
            # Google doesn't invite the organizer to their own event (GS-8).
            "attendees": [{"email": e} for e in invitees if e != organizer],
        }
        rule = recurrence.rrule_line(r["repeat"])
        body["recurrence"] = [rule] if rule else []
        if with_id:
            body["id"] = r["gcal_event_id"]
        return body

    def _event_or_404(self, event_id: str) -> Any:
        r = self.conn.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
        if r is None or event_id in self._hidden("events"):
            raise NotFound("That event no longer exists.")
        return r

    def _invitee_emails(self, event_id: str) -> list[str]:
        return [x[0] for x in self.conn.execute(
            "SELECT email FROM event_invitee WHERE event_id = ? ORDER BY email", (event_id,))]

    @staticmethod
    def _event_values(r: Any, invitees: list[str]) -> dict[str, Any]:
        return {"title": r["title"], "date": r["date"], "start_time": r["start_time"], "end_time": r["end_time"],
                "location": r["location"], "repeat": r["repeat"], "invitees": invitees,
                "all_day": bool(r["all_day"])}

    def _times(self, inp: EventInput) -> tuple[str, str]:
        day = date.fromisoformat(inp.date)
        if inp.all_day:  # the local midnights at the start and end of the day
            return (iso(local_to_utc(day, time(0, 0))), iso(local_to_utc(day + timedelta(days=1), time(0, 0))))
        return (iso(local_to_utc(day, parse_hhmm(inp.start_time))),
                iso(local_to_utc(day, parse_hhmm(inp.end_time))))

    def series_note(self, r: Any) -> str:
        """EV-10: say plainly that a change to a repeating event affects the whole series."""
        if r["repeat"] == "none":
            return ""
        n = recurrence.count_in_next_year(date.fromisoformat(r["date"]), r["repeat"], self.today())
        noun = recurrence.describe(r["repeat"])
        return f"This changes all {n} {noun} occurrences of {r['title']} in the next 12 months."

    # --- events: commands ----------------------------------------------------------------------

    def add_event(self, who: str, inp: EventInput, *, acknowledge_conflicts: bool = False) -> dict[str, Any]:
        who = self.require_member(who)
        inp = self._validate_event(inp)
        sender = self.sender_for(who)["email"]  # GS-1, GS-2; raises if no account is connected
        event_id = uuid.uuid4().hex
        with self._write() as c:
            conflicts = self._find_conflicts(inp, None)
            if conflicts and not acknowledge_conflicts:
                raise NeedsConfirmation(_overlap_message(conflicts), conflicts=conflicts)
            starts_at, ends_at = self._times(inp)
            now = iso(self.now())
            c.execute(
                "INSERT INTO event (id, title, date, start_time, end_time, location, repeat, gcal_event_id, "
                "sync_status, sync_op, conflict_acknowledged, created_by, created_at, updated_at, starts_at, ends_at, "
                "organizer_email, all_day) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'create', ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, inp.title, inp.date, inp.start_time, inp.end_time, inp.location, inp.repeat,
                 new_event_id(), int(bool(conflicts)), who, now, now, starts_at, ends_at, sender, int(inp.all_day)),
            )
            self._write_invitees(event_id, inp.invitees, keep={}, organizer=sender)
            self._log(who, "added", "event", inp.title, item_id=event_id,
                      after=self._event_values(c.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone(),
                                               inp.invitees) | {"invites_from": sender})  # AS-5
        self._push_create(event_id)
        return self.get_event(event_id)

    def _write_invitees(self, event_id: str, emails: list[str], keep: dict[str, str], organizer: str) -> None:
        self.conn.execute("DELETE FROM event_invitee WHERE event_id = ?", (event_id,))
        for e in emails:
            rsvp = "organizer" if e == organizer else keep.get(e, "no_reply")
            self.conn.execute("INSERT INTO event_invitee (event_id, email, rsvp) VALUES (?, ?, ?)",
                              (event_id, e, rsvp))

    def _push_create(self, event_id: str) -> bool:
        """Send a saved event to Google, from its organizer's account. On failure it keeps a visible error badge
        (WO-FS-001 4.4) and the next automatic retry is scheduled (AS-2). Returns True once it's in Google."""
        with self._read():
            r = self.conn.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
            if r is None:
                return False
            body = self._google_body(r, self._invitee_emails(event_id), with_id=True)
        try:
            self._google(r["organizer_email"], lambda g: g.insert(body))
        except CalendarError as err:
            now = self.now()
            first = parse_iso(r["first_failed_at"]) if r["first_failed_at"] else now
            attempts = r["retry_attempts"] + 1
            due = next_retry_at(first, attempts)
            with self._write() as c:
                c.execute("UPDATE event SET sync_status = 'error', sync_error = ?, retry_attempts = ?, "
                          "first_failed_at = ?, next_retry_at = ? WHERE id = ?",
                          (str(err), attempts, iso(first), iso(due) if due else None, event_id))
            return False
        with self._write() as c:
            c.execute("UPDATE event SET sync_status = 'synced', sync_op = NULL, sync_error = NULL, retry_attempts = 0, "
                      "first_failed_at = NULL, next_retry_at = NULL WHERE id = ?", (event_id,))
        return True

    def retry_due(self) -> int:
        """AS-2: the hub's worker sends events whose next automatic retry is due. Returns how many were sent."""
        now = iso(self.now())
        with self._read() as c:
            due = [r["id"] for r in c.execute(
                "SELECT id FROM event WHERE sync_status = 'error' AND sync_op = 'create' AND hidden = 0 "
                "AND app_deleted = 0 "
                "AND next_retry_at IS NOT NULL AND next_retry_at <= ? ORDER BY next_retry_at", (now,))]
        return sum(self._push_create(event_id) for event_id in due)

    def retry_on_start(self) -> None:
        """AS-2: when the hub starts, every unsent event still inside its 24 hours is tried again at once."""
        now = self.now()
        with self._write() as c:
            c.execute("UPDATE event SET next_retry_at = ? WHERE sync_op = 'create' "
                      "AND sync_status IN ('error', 'pending') AND (first_failed_at IS NULL OR first_failed_at > ?)",
                      (iso(now), iso(now - timedelta(hours=RETRY_FOR_HOURS))))

    def edit_event(self, who: str, event_id: str, inp: EventInput, *,
                   acknowledge_conflicts: bool = False) -> dict[str, Any]:
        """EV-8, EV-8a: Google is updated first; locally nothing changes unless Google accepted it."""
        who = self.require_member(who)
        inp = self._validate_event(inp)
        with self._read() as c:
            old = self._event_or_404(event_id)
            self._require_editable(old)
            self._require_organizer_connected(old)
            old_invitees = self._invitee_emails(event_id)
            conflicts = self._find_conflicts(inp, event_id)
            if conflicts and not acknowledge_conflicts:
                raise NeedsConfirmation(_overlap_message(conflicts), conflicts=conflicts)
            keep = {x["email"]: x["rsvp"] for x in c.execute(
                "SELECT email, rsvp FROM event_invitee WHERE event_id = ?", (event_id,))}
        starts_at, ends_at = self._times(inp)
        new_row = dict(old) | {"title": inp.title, "date": inp.date, "start_time": inp.start_time,
                               "end_time": inp.end_time, "location": inp.location, "repeat": inp.repeat,
                               "all_day": int(inp.all_day)}
        in_google = old["sync_op"] != "create"
        if in_google:
            try:
                body = self._google_body(new_row, inp.invitees, with_id=False)
                self._google(old["organizer_email"], lambda g: g.patch(old["gcal_event_id"], body))
            except CalendarError as err:
                raise ExternalFailure(f"Nothing was changed. {err}") from err
        before_vals = self._event_values(old, old_invitees)
        after_vals = self._event_values(new_row, inp.invitees)
        changed = [k for k in before_vals if before_vals[k] != after_vals[k]]
        with self._write() as c:
            c.execute(
                "UPDATE event SET title = ?, date = ?, start_time = ?, end_time = ?, location = ?, repeat = ?, "
                "all_day = ?, "
                "starts_at = ?, ends_at = ?, conflict_acknowledged = ?, updated_at = ? WHERE id = ?",
                (inp.title, inp.date, inp.start_time, inp.end_time, inp.location, inp.repeat, int(inp.all_day),
                 starts_at, ends_at,
                 int(bool(conflicts)), iso(self.now()), event_id),
            )
            self._write_invitees(event_id, inp.invitees, keep, organizer=old["organizer_email"])
            if changed:
                self._log(who, "edited", "event", inp.title, item_id=event_id,
                          before={k: before_vals[k] for k in changed},
                          after={k: after_vals[k] for k in changed} | {"invites_from": old["organizer_email"]})
        if not in_google:
            self._push_create(event_id)
        return self.get_event(event_id)

    def remove_event(self, who: str, event_id: str) -> None:
        """EV-9, EV-9a: cancel in Google (whole series), then delete locally. Final."""
        who = self.require_member(who)
        with self._read():
            r = self._event_or_404(event_id)
            self._require_editable(r)
            self._require_organizer_connected(r)
        if r["sync_op"] != "create":
            try:
                self._google(r["organizer_email"], lambda g: g.delete(r["gcal_event_id"]))
            except CalendarError as err:
                raise ExternalFailure(f"The event wasn't removed. {err}") from err
        with self._write() as c:
            self._delete_event_locally(c, who, r)

    def _delete_event_locally(self, c: sqlite3.Connection, who: str, r: Any) -> None:
        c.execute("DELETE FROM event WHERE id = ?", (r["id"],))
        self._log(who, "removed", "event", r["title"], item_id=r["id"],
                  before=self._event_values(r, []) | {"invitees": None},
                  after={"invites_from": r["organizer_email"]} if r["sync_op"] != "create" else None)

    def retry_sync(self, who: str, event_id: str) -> dict[str, Any] | None:
        who = self.require_member(who)
        with self._read():
            r = self._event_or_404(event_id)
        if r["sync_op"] == "create":
            self._push_create(event_id)
            return self.get_event(event_id)
        if r["sync_op"] == "delete":
            try:
                self._google(r["organizer_email"], lambda g: g.delete(r["gcal_event_id"]))
            except CalendarError as err:
                with self._write() as c:
                    c.execute("UPDATE event SET sync_error = ? WHERE id = ?", (str(err), event_id))
                raise ExternalFailure(f"Still couldn't cancel it. {err}") from err
            with self._write() as c:
                self._delete_event_locally(c, who, r)
            return None
        return self.get_event(event_id)

    def discard_event(self, who: str, event_id: str) -> None:
        """Delete an event that never reached Google."""
        who = self.require_member(who)
        with self._write() as c:
            r = self._event_or_404(event_id)
            if r["sync_op"] != "create":
                raise OrganizerError("Only events that never reached Google can be discarded. Use Remove instead.")
            self._delete_event_locally(c, who, r)

    # --- events: views -------------------------------------------------------------------------

    def get_event(self, event_id: str) -> dict[str, Any]:
        with self._read() as c:
            r = c.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
            if r is None:
                raise NotFound("That event no longer exists.")
            return self._event_dicts([r], with_conflicts=False)[0]

    def _event_dicts(self, rows: list[Any], *, with_conflicts: bool) -> list[dict[str, Any]]:
        today = self.today()
        invitees: dict[str, list[dict[str, str]]] = {}
        ids = [r["id"] for r in rows]
        if ids:
            marks = ",".join("?" * len(ids))
            for x in self.conn.execute(
                f"SELECT event_id, email, rsvp FROM event_invitee WHERE event_id IN ({marks}) ORDER BY email", ids
            ):
                invitees.setdefault(x["event_id"], []).append({"email": x["email"], "rsvp": x["rsvp"]})
        clashes = self._all_conflicts(rows) if with_conflicts else {}
        connected = set(self.connected_accounts())
        cars = {car["id"]: car["name"] for car in self.list_cars()}
        double = self._double_booked()
        out = []
        for r in rows:
            nxt = recurrence.next_occurrence_date(date.fromisoformat(r["date"]), r["repeat"], today)
            people = invitees.get(r["id"], [])
            counts = {k: sum(1 for p in people if p["rsvp"] == k) for k in ("accepted", "declined", "tentative",
                                                                          "no_reply")}
            out.append({
                "id": r["id"], "title": r["title"], "date": r["date"], "start_time": r["start_time"],
                "end_time": r["end_time"], "location": r["location"], "repeat": r["repeat"],
                "all_day": bool(r["all_day"]),
                "next_date": (nxt or date.fromisoformat(r["date"])).isoformat(),
                "sync_status": r["sync_status"], "sync_error": r["sync_error"], "sync_op": r["sync_op"],
                "conflict_acknowledged": bool(r["conflict_acknowledged"]),
                "invitees": people, "rsvp_counts": counts,
                "series_note": self.series_note(r),
                "occurrences_next_year": (recurrence.count_in_next_year(date.fromisoformat(r["date"]), r["repeat"],
                                                                        today) if r["repeat"] != "none" else 1),
                "conflicts_with": clashes.get(r["id"], []),
                "organizer_email": r["organizer_email"],  # GS-7
                # GS-6; an event only Google can change (GY-8) never asks to reconnect someone outside the family
                "organizer_connected": r["organizer_email"] in connected or bool(r["read_only"]),
                "retry_gave_up": r["sync_status"] == "error" and r["sync_op"] == "create"
                and r["next_retry_at"] is None,  # AS-3
                "next_retry_at": r["next_retry_at"],
                # WO-FS-010 section 8: where it came from, and whether it can be changed here
                "source": r["source"], "from_google": r["source"] == "google", "calendar_email": r["calendar_email"],
                "read_only": bool(r["read_only"]), "google_note": r["google_note"], "html_link": r["html_link"],
                "private": bool(r["private"]), "end_date": r["end_date"],
                "not_synced": r["source"] == "google" and r["calendar_email"] not in connected,  # GY-15
                # CR-9: the car it needs, who chose it, and whether that car is double-booked (CR-7)
                "car": ({"id": r["car_id"], "name": cars.get(r["car_id"], "")} if r["car_id"] else None),
                "car_chosen_by": r["car_chosen_by"],
                "car_double_booked": bool(r["car_id"]) and r["id"] in double,
            })
        return out

    def _double_booked(self) -> set[str]:
        """CR-7: events whose car another event needs at an overlapping time (all events with a car are compared,
        whichever ones are being shown)."""
        rows = self.conn.execute("SELECT * FROM event WHERE car_id IS NOT NULL AND hidden = 0 "
                                 "AND app_deleted = 0").fetchall()
        window = self._car_window()
        by_car: dict[int, list[tuple[datetime, datetime, str]]] = {}
        for r in rows:
            if r["car_id"]:
                by_car.setdefault(r["car_id"], []).extend((s, e, r["id"]) for s, e in self._occurrences(r, window))
        double: set[str] = set()
        for spans in by_car.values():
            spans.sort()
            for i, (_start, end, event_id) in enumerate(spans):
                for s2, _e2, other in spans[i + 1:]:
                    if s2 >= end:
                        break
                    if other != event_id:
                        double.update((event_id, other))
        return double

    def _all_conflicts(self, rows: list[Any]) -> dict[str, list[str]]:
        """Which events overlap which, over the next 12 months (RP-4). One sweep over all occurrences."""
        window = (self.now() - timedelta(days=1), self.now() + timedelta(days=CONFLICT_HORIZON_DAYS))
        occ: list[tuple[datetime, datetime, Any]] = []
        for r in rows:
            if not r["all_day"] and not r["end_date"]:  # AD-9: all-day (and multi-day) events never overlap
                occ.extend((s, e, r) for s, e in self._occurrences(r, window))
        occ.sort(key=lambda o: o[0])
        found: dict[str, set[str]] = {}
        active: list[tuple[datetime, datetime, Any]] = []
        for s, e, r in occ:
            active = [a for a in active if a[1] > s]
            for _, _, other in active:
                if other["id"] != r["id"]:
                    found.setdefault(r["id"], set()).add(other["title"])
                    found.setdefault(other["id"], set()).add(r["title"])
            active.append((s, e, r))
        return {k: sorted(v) for k, v in found.items()}

    def list_events(self) -> dict[str, Any]:
        with self._read() as c:
            hidden = self._hidden("events")
            rows = [r for r in c.execute("SELECT * FROM event") if r["id"] not in hidden]
            now = iso(self.now())
            upcoming = [r for r in rows if r["repeat"] != "none" or r["ends_at"] >= now]
            past = [r for r in rows if r["repeat"] == "none" and r["ends_at"] < now]
            up = self._event_dicts(upcoming, with_conflicts=True)
            up.sort(key=lambda e: (e["sync_status"] != "error", e["next_date"], e["start_time"]))
            gone = self._event_dicts(past, with_conflicts=False)
            gone.sort(key=lambda e: (e["date"], e["start_time"]), reverse=True)
            return {"upcoming": up, "past": gone, "rsvp_refreshed_at": self.setting("rsvp_refreshed_at"),
                    "synced_at": self.setting("google_synced_at"),  # WO-FS-010 GY-2
                    "hidden_count": int(c.execute("SELECT COUNT(*) FROM event WHERE hidden = 1 AND app_deleted = 0"
                                                  ).fetchone()[0])}

    def refresh_rsvps(self, who: str) -> dict[str, Any]:
        """RV-1: ask Google for the latest replies to every upcoming event. Not logged (RV-5)."""
        self.require_member(who)
        connected = self.connected_accounts()
        with self._read() as c:
            hidden = self._hidden("events")
            now = iso(self.now())
            targets = [(r["id"], r["gcal_event_id"], r["organizer_email"]) for r in c.execute(
                "SELECT id, gcal_event_id, organizer_email FROM event "
                "WHERE sync_status = 'synced' AND (repeat <> 'none' OR ends_at >= ?)",
                (now,)) if r["id"] not in hidden]
        if targets and not connected:
            raise ExternalFailure("No calendar account is connected. Connect one on the hub PC under Family & "
                                  "settings.")
        results: dict[str, dict[str, str] | None] = {}
        failure: CalendarError | None = None
        skipped = sorted({org for _, _, org in targets if org not in connected})
        broken: set[str] = set()
        for event_id, gcal_id, organizer in targets:  # each event through its organizer's account
            if organizer not in connected or organizer in broken:
                continue
            try:
                results[event_id] = self._google(organizer, partial(_statuses, gcal_id))
            except CalendarError as err:
                failure = err
                broken.add(organizer)
        with self._write() as c:
            for event_id, statuses in results.items():
                if statuses is None:
                    continue
                for email, status in statuses.items():
                    c.execute("UPDATE event_invitee SET rsvp = ? "
                              "WHERE event_id = ? AND email = ? AND rsvp <> 'organizer'",
                              (_RSVP_FROM_GOOGLE.get(status, "no_reply"), event_id, email.lower()))
            if failure is None and not skipped:
                self._set_setting("rsvp_refreshed_at", iso(self.now()))
        if failure is not None:
            raise ExternalFailure(f"Replies were only partly updated. {failure}")
        if skipped:
            raise ExternalFailure(f"Replies were only partly updated. Reconnect {' and '.join(skipped)} to see "
                                  "replies to the events sent from it.")
        return {"refreshed": len(results), "rsvp_refreshed_at": self.setting("rsvp_refreshed_at")}

    # --- tasks ---------------------------------------------------------------------------------

    def _validate_task(self, inp: TaskInput) -> TaskInput:
        title = inp.title.strip()
        if not 1 <= len(title) <= 200:
            raise OrganizerError("Give the task a title (up to 200 characters).")
        if inp.priority not in PRIORITIES:
            raise OrganizerError("Choose High, Medium or Low priority.")
        if inp.repeat not in recurrence.REPEATS:
            raise OrganizerError("Choose how often the task repeats.")
        if not inp.due_date:  # WO-FS-009 OT-1, OT-2: an ongoing task has no due date and can't repeat
            if inp.repeat != "none":
                raise OrganizerError("A task with no due date can't repeat. Give it a due date, or choose "
                                     "Doesn't repeat.")
            return TaskInput(title, "", inp.priority, "none")
        try:
            due = date.fromisoformat(inp.due_date)
        except ValueError as err:
            raise OrganizerError("Enter a valid due date.") from err
        return TaskInput(title, due.isoformat(), inp.priority, inp.repeat)

    @staticmethod
    def _anchor(inp: TaskInput) -> tuple[int | None, int | None]:
        if inp.repeat == "none" or not inp.due_date:
            return None, None
        return recurrence.repeat_anchor(date.fromisoformat(inp.due_date), inp.repeat)

    def restore_repeat_days(self) -> dict[str, int] | None:
        """WO-FS-013 ET-2 to ET-4, once, when the hub first starts on schema 9: fill in each monthly and yearly task's
        repeat day from the earliest occurrence its chain still holds, and put back an open task that had drifted
        (e.g. from the 31st to the 28th) unless that would put it in the past. Returns the counts, or None if it
        has already run."""
        if self.setting("repeat_days_restored"):
            return None
        today = self.today()
        counts = {"checked": 0, "put_back": 0, "restored_for_later": 0, "kept_current_day": 0, "already_right": 0}
        with self._write() as c:
            rows = c.execute("SELECT * FROM task WHERE repeat IN ('monthly', 'yearly') AND due_date <> ''").fetchall()
            by_id = {r["id"]: r for r in c.execute("SELECT id, due_date, previous_task_id FROM task")}
            for r in rows:
                earliest, seen, prev = r["due_date"], {r["id"]}, r["previous_task_id"]
                while prev and prev in by_id and prev not in seen:  # ET-2: follow the chain back
                    seen.add(prev)
                    if by_id[prev]["due_date"]:
                        earliest = min(earliest, by_id[prev]["due_date"])
                    prev = by_id[prev]["previous_task_id"]
                day, month = recurrence.repeat_anchor(date.fromisoformat(earliest), r["repeat"])
                c.execute("UPDATE task SET repeat_day = ?, repeat_month = ? WHERE id = ?", (day, month, r["id"]))
                if r["completed_at"] or r["deleted_at"]:
                    continue
                counts["checked"] += 1
                if len(seen) == 1:  # ET-3: no earlier occurrence left to tell
                    counts["kept_current_day"] += 1
                    continue
                due = date.fromisoformat(r["due_date"])
                right = recurrence.on_day(due.year, month or due.month, day or due.day)
                if right <= due or (month and month != due.month):
                    counts["already_right"] += 1
                elif right < today:  # moving it would put it in the past: later occurrences use the day
                    counts["restored_for_later"] += 1
                else:
                    c.execute("UPDATE task SET due_date = ?, updated_at = ? WHERE id = ?",
                              (right.isoformat(), iso(self.now()), r["id"]))
                    self._log(r["created_by"], "edited", "task", r["title"], item_id=r["id"],
                              before={"due_date": r["due_date"]},
                              after={"due_date": right.isoformat(),
                                     "note": f"Repeat day restored to the {recurrence.ordinal(day or due.day)}"})
                    counts["put_back"] += 1  # ET-4: written to History once
            self._set_setting("repeat_days_restored", iso(self.now()))
        return counts

    def _task_or_404(self, task_id: str, *, allow_deleted: bool = False) -> Any:
        r = self.conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        if r is None or task_id in self._hidden("tasks") or (r["deleted_at"] and not allow_deleted):
            raise NotFound("That task no longer exists.")
        return r

    @staticmethod
    def _task_values(r: Any) -> dict[str, Any]:
        return {"title": r["title"], "priority": r["priority"], "due_date": r["due_date"], "repeat": r["repeat"]}

    def add_task(self, who: str, inp: TaskInput) -> dict[str, Any]:
        who = self.require_member(who)
        inp = self._validate_task(inp)
        task_id = uuid.uuid4().hex
        now = iso(self.now())
        day, month = self._anchor(inp)
        with self._write() as c:
            c.execute("INSERT INTO task (id, title, priority, due_date, repeat, repeat_day, repeat_month, created_by, "
                      "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (task_id, inp.title, inp.priority, inp.due_date, inp.repeat, day, month, who, now, now))
            self._log(who, "added", "task", inp.title, item_id=task_id, after=vars(inp))
        return self._task_dict(task_id)

    def edit_task(self, who: str, task_id: str, inp: TaskInput) -> dict[str, Any]:
        who = self.require_member(who)
        inp = self._validate_task(inp)
        with self._write() as c:
            old = self._task_or_404(task_id)
            before, after = self._task_values(old), vars(inp)
            changed = [k for k in before if before[k] != after[k]]
            day, month = old["repeat_day"], old["repeat_month"]
            if "due_date" in changed or "repeat" in changed or (inp.repeat != "none" and day is None):
                day, month = self._anchor(inp)  # MR-1, MR-6: a new date or repeat sets a new repeat day
            c.execute("UPDATE task SET title = ?, priority = ?, due_date = ?, repeat = ?, repeat_day = ?, "
                      "repeat_month = ?, updated_at = ? WHERE id = ?",
                      (inp.title, inp.priority, inp.due_date, inp.repeat, day, month, iso(self.now()), task_id))
            if changed:
                self._log(who, "edited", "task", inp.title, item_id=task_id,
                          before={k: before[k] for k in changed}, after={k: after[k] for k in changed})
        return self._task_dict(task_id)

    def remove_task(self, who: str, task_id: str) -> None:
        """TK-5a: a single removal goes to Trash."""
        who = self.require_member(who)
        with self._write() as c:
            r = self._task_or_404(task_id)
            c.execute("UPDATE task SET deleted_at = ?, deleted_by = ? WHERE id = ?", (iso(self.now()), who, task_id))
            self._log(who, "removed", "task", r["title"], item_id=task_id, after={"to": "Trash"})

    def complete_task(self, who: str, task_id: str) -> dict[str, Any]:
        """TK-2, TK-3: check off; a repeating task gets its next occurrence straight away."""
        who = self.require_member(who)
        now = iso(self.now())
        with self._write() as c:
            r = self._task_or_404(task_id)
            if r["completed_at"]:
                raise OrganizerError("That task is already done.")
            c.execute("UPDATE task SET completed_at = ?, completed_by = ?, updated_at = ? WHERE id = ?",
                      (now, who, now, task_id))
            after: dict[str, Any] = {}
            if r["repeat"] != "none":
                due = date.fromisoformat(r["due_date"])
                day, month = r["repeat_day"], r["repeat_month"]
                if day is None:  # a row from before WO-FS-013 that the start-up pass didn't reach
                    day, month = recurrence.repeat_anchor(due, r["repeat"])
                nxt = recurrence.next_due_date(due, r["repeat"], self.today(), day, month)
                c.execute("INSERT INTO task (id, title, priority, due_date, repeat, repeat_day, repeat_month, "
                          "previous_task_id, created_by, created_at, updated_at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (uuid.uuid4().hex, r["title"], r["priority"], nxt.isoformat(), r["repeat"], day, month,
                           task_id, who, now, now))  # MR-4: the next one inherits the repeat day
                after["next_due_date"] = nxt.isoformat()
            self._log(who, "completed", "task", r["title"], item_id=task_id, after=after or None)
        return self._task_dict(task_id)

    def reopen_task(self, who: str, task_id: str) -> dict[str, Any]:
        """Undo a mistaken check-off. An untouched next occurrence it created is removed."""
        who = self.require_member(who)
        with self._write() as c:
            r = self._task_or_404(task_id)
            if not r["completed_at"]:
                raise OrganizerError("That task isn't done.")
            c.execute("UPDATE task SET completed_at = NULL, completed_by = NULL, updated_at = ? WHERE id = ?",
                      (iso(self.now()), task_id))
            c.execute("DELETE FROM task WHERE previous_task_id = ? AND completed_at IS NULL AND deleted_at IS NULL "
                      "AND created_at = updated_at", (task_id,))
            self._log(who, "reopened", "task", r["title"], item_id=task_id)
        return self._task_dict(task_id)

    def _task_dict(self, task_id: str) -> dict[str, Any]:
        with self._read() as c:
            r = c.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        if r is None:
            raise NotFound("That task no longer exists.")
        return self._task_row(r, self.today())

    @staticmethod
    def _task_row(r: Any, today: date) -> dict[str, Any]:
        overdue = r["completed_at"] is None and bool(r["due_date"]) and r["due_date"] < today.isoformat()
        priority, due_soon = effective_priority(r["priority"], r["due_date"], r["completed_at"], today)
        return {"id": r["id"], "title": r["title"], "priority": r["priority"], "due_date": r["due_date"],
                "repeat": r["repeat"], "completed_at": r["completed_at"], "completed_by": r["completed_by"],
                "ongoing": not r["due_date"],  # WO-FS-009: no due date, so never overdue (OT-3)
                "overdue": overdue,
                "effective_priority": priority, "due_soon": due_soon,  # WO-FS-012 CD-3
                "repeat_day": r["repeat_day"], "repeat_month": r["repeat_month"]}  # WO-FS-013 CO-2

    def list_tasks(self) -> dict[str, list[dict[str, Any]]]:
        """RP-3: Overdue; WO-FS-012 PL-1 to PL-5: High, Medium and Low priority by effective priority (each earliest
        due date first, then title); Ongoing (no due date: high priority first, then title; WO-FS-009 OT-5); Done
        (last 30 days, RT-1). "open" is all three priority lists together, for apps not yet updated (CD-3)."""
        today = self.today()
        rank = {p: i for i, p in enumerate(PRIORITIES)}
        cutoff = iso(self.now() - timedelta(days=RETENTION_DAYS))
        with self._read() as c:
            hidden = self._hidden("tasks")
            rows = [r for r in c.execute("SELECT * FROM task WHERE deleted_at IS NULL") if r["id"] not in hidden]
        items = [self._task_row(r, today) for r in rows]
        overdue = sorted((t for t in items if t["overdue"]), key=lambda t: (t["due_date"], rank[t["priority"]]))
        open_ = sorted((t for t in items if t["completed_at"] is None and not t["overdue"] and not t["ongoing"]),
                       key=lambda t: (rank[t["effective_priority"]], t["due_date"]))
        by_priority = {p: sorted((t for t in open_ if t["effective_priority"] == p),
                                 key=lambda t: (t["due_date"], t["title"].lower())) for p in PRIORITIES}
        ongoing = sorted((t for t in items if t["completed_at"] is None and t["ongoing"]),
                         key=lambda t: (t["priority"] != "high", t["title"].lower()))
        done = sorted((t for t in items if t["completed_at"] and t["completed_at"] >= cutoff),
                      key=lambda t: t["completed_at"], reverse=True)
        return {"overdue": overdue, "high": by_priority["high"], "medium": by_priority["medium"],
                "low": by_priority["low"], "ongoing": ongoing, "done": done, "open": open_}

    # --- reminders -----------------------------------------------------------------------------

    @staticmethod
    def _clean_message(message: str) -> str:
        message = message.strip()
        if not 1 <= len(message) <= 500:
            raise OrganizerError("Write the reminder (up to 500 characters).")
        return message

    def _reminder_or_404(self, reminder_id: str) -> Any:
        r = self.conn.execute("SELECT * FROM reminder WHERE id = ?", (reminder_id,)).fetchone()
        if r is None or r["deleted_at"] or reminder_id in self._hidden("reminders"):
            raise NotFound("That reminder no longer exists.")
        return r

    def add_reminder(self, who: str, message: str) -> dict[str, Any]:
        who = self.require_member(who)
        message = self._clean_message(message)
        reminder_id = uuid.uuid4().hex
        with self._write() as c:
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM reminder").fetchone()[0]
            c.execute("INSERT INTO reminder (id, seq, message, created_by) VALUES (?, ?, ?, ?)",
                      (reminder_id, seq, message, who))
            self._log(who, "added", "reminder", _short(message), item_id=reminder_id, after={"message": message})
        return {"id": reminder_id, "message": message, "created_by": who}

    def edit_reminder(self, who: str, reminder_id: str, message: str) -> dict[str, Any]:
        who = self.require_member(who)
        message = self._clean_message(message)
        with self._write() as c:
            r = self._reminder_or_404(reminder_id)
            c.execute("UPDATE reminder SET message = ? WHERE id = ?", (message, reminder_id))
            if r["message"] != message:
                self._log(who, "edited", "reminder", _short(message), item_id=reminder_id,
                          before={"message": r["message"]}, after={"message": message})
        return {"id": reminder_id, "message": message, "created_by": r["created_by"]}

    def remove_reminder(self, who: str, reminder_id: str) -> None:
        """RM-3a: a single removal goes to Trash."""
        who = self.require_member(who)
        with self._write() as c:
            r = self._reminder_or_404(reminder_id)
            c.execute("UPDATE reminder SET deleted_at = ?, deleted_by = ? WHERE id = ?",
                      (iso(self.now()), who, reminder_id))
            self._log(who, "removed", "reminder", _short(r["message"]), item_id=reminder_id, after={"to": "Trash"})

    def list_reminders(self) -> list[dict[str, Any]]:
        """RM-2: in the order added. No dates (WO-FS-002 correction 1)."""
        with self._read() as c:
            hidden = self._hidden("reminders")
            rows = c.execute("SELECT * FROM reminder WHERE deleted_at IS NULL ORDER BY seq").fetchall()
        return [{"id": r["id"], "message": r["message"], "created_by": r["created_by"]}
                for r in rows if r["id"] not in hidden]

    # --- Clear all and undo (CA-1 to CA-5) -------------------------------------------------------

    def _clearable(self, list_name: str) -> list[tuple[str, str]]:
        hidden = self._hidden(list_name)
        if list_name == "events":
            rows = self.conn.execute("SELECT id, title AS label FROM event ORDER BY starts_at").fetchall()
        elif list_name == "tasks":
            rows = self.conn.execute("SELECT id, title AS label FROM task WHERE deleted_at IS NULL").fetchall()
        else:
            rows = self.conn.execute("SELECT id, message AS label FROM reminder WHERE deleted_at IS NULL "
                                     "ORDER BY seq").fetchall()
        return [(r["id"], r["label"]) for r in rows if r["id"] not in hidden]

    def clear_preview(self, list_name: str) -> dict[str, Any]:
        """Counts for the confirmation (CA-2), and for events who will get cancellations."""
        if list_name not in LISTS:
            raise NotFound("No such list.")
        with self._read() as c:
            items = self._clearable(list_name)
            invitees: list[str] = []
            if list_name == "events" and items:
                marks = ",".join("?" * len(items))
                invitees = [x[0] for x in c.execute(
                    f"SELECT DISTINCT email FROM event_invitee WHERE event_id IN ({marks}) AND rsvp <> 'organizer' "
                    "ORDER BY email", [i for i, _ in items])]
        return {"list": list_name, "count": len(items), "invitees": invitees}

    def clear_all(self, who: str, list_name: str) -> dict[str, Any]:
        who = self.require_member(who)
        if list_name not in LISTS:
            raise NotFound("No such list.")
        with self._write() as c:
            items = self._clearable(list_name)
            if not items:
                raise OrganizerError(f"There are no {list_name} to clear.")
            clear_id = uuid.uuid4().hex
            commit_after = self.now() + timedelta(seconds=UNDO_SECONDS)
            c.execute("INSERT INTO pending_clear (id, list, item_ids, requested_by, requested_at, commit_after) "
                      "VALUES (?, ?, ?, ?, ?, ?)",
                      (clear_id, list_name, json.dumps([i for i, _ in items]), who, iso(self.now()), iso(commit_after)))
            self._log(who, "cleared", "list", f"All {list_name} ({len(items)})", list_name=list_name,
                      before={"items": [{"id": i, "label": _short(lbl)} for i, lbl in items]})
        return {"id": clear_id, "list": list_name, "count": len(items), "commit_after": iso(commit_after)}

    def undo_clear(self, who: str, clear_id: str) -> None:
        who = self.require_member(who)
        with self._write() as c:
            r = c.execute("SELECT * FROM pending_clear WHERE id = ?", (clear_id,)).fetchone()
            if r is None:
                raise NotFound("That Clear all no longer exists.")
            if r["state"] != "pending":
                raise TooLate("Too late to undo. The 10 seconds had already run out.")
            c.execute("UPDATE pending_clear SET state = 'undone', resolved_at = ? WHERE id = ?",
                      (iso(self.now()), clear_id))
            count = len(json.loads(r["item_ids"]))
            self._log(who, "undone", "list", f"Clear all {r['list']} ({count})", list_name=r["list"])

    def commit_due_clears(self) -> int:
        with self._read() as c:
            due = [r[0] for r in c.execute(
                "SELECT id FROM pending_clear WHERE state = 'pending' AND commit_after <= ? ORDER BY commit_after",
                (iso(self.now()),))]
        for clear_id in due:
            self.commit_clear(clear_id)
        return len(due)

    def commit_clear(self, clear_id: str) -> None:
        now = iso(self.now())
        with self._write() as c:
            r = c.execute("SELECT * FROM pending_clear WHERE id = ?", (clear_id,)).fetchone()
            if r is None or r["state"] != "pending":
                return
            ids: list[str] = json.loads(r["item_ids"])
            who = r["requested_by"]
            if r["list"] != "events":
                table = "task" if r["list"] == "tasks" else "reminder"
                marks = ",".join("?" * len(ids))
                c.execute(f"UPDATE {table} SET deleted_at = ?, deleted_by = ? "
                          f"WHERE id IN ({marks}) AND deleted_at IS NULL", [now, who, *ids])
                c.execute("UPDATE pending_clear SET state = 'committed', resolved_at = ? WHERE id = ?", (now, clear_id))
                return
            # Events: mark committing first, so Undo now says "too late", then cancel in Google outside the lock.
            c.execute("UPDATE pending_clear SET state = 'committing' WHERE id = ?", (clear_id,))
        failed = self._cancel_events(who, ids)
        with self._write() as c:
            c.execute("UPDATE pending_clear SET state = ?, resolved_at = ?, failed_ids = ? WHERE id = ?",
                      ("partial" if failed else "committed", iso(self.now()), json.dumps(failed), clear_id))

    def _cancel_events(self, who: str, ids: list[str]) -> list[str]:
        failed: list[str] = []
        for event_id in ids:
            with self._read() as c:
                r = c.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
            if r is None:
                continue
            if r["sync_op"] != "create":
                try:
                    self._google(r["organizer_email"], partial(_delete, r["gcal_event_id"]))
                except CalendarError as err:
                    failed.append(event_id)
                    with self._write() as c:
                        c.execute("UPDATE event SET sync_status = 'error', sync_op = 'delete', sync_error = ? "
                                  "WHERE id = ?", (f"Clear all couldn't cancel this in Google. {err}", event_id))
                    continue
            with self._write() as c:
                self._delete_event_locally(c, who, r)
        return failed

    def pending_clears(self) -> list[dict[str, Any]]:
        with self._read() as c:
            rows = c.execute("SELECT * FROM pending_clear WHERE state = 'pending'").fetchall()
        return [{"id": r["id"], "list": r["list"], "count": len(json.loads(r["item_ids"])),
                 "commit_after": r["commit_after"], "requested_by": r["requested_by"]} for r in rows]

    # --- Trash (TR-1 to TR-4) ------------------------------------------------------------------

    def list_trash(self) -> list[dict[str, Any]]:
        now = self.now()
        out: list[dict[str, Any]] = []
        with self._read() as c:
            for r in c.execute("SELECT id, title, deleted_at, deleted_by, repeat FROM task "
                               "WHERE deleted_at IS NOT NULL AND purge_batch IS NULL"):
                out.append({"type": "task", "id": r["id"], "label": r["title"], "deleted_at": r["deleted_at"],
                            "deleted_by": r["deleted_by"], "repeat": r["repeat"]})
            for r in c.execute("SELECT id, message, deleted_at, deleted_by FROM reminder "
                               "WHERE deleted_at IS NOT NULL AND purge_batch IS NULL"):
                out.append({"type": "reminder", "id": r["id"], "label": r["message"], "deleted_at": r["deleted_at"],
                            "deleted_by": r["deleted_by"], "repeat": "none"})
        for item in out:
            age = now - parse_iso(item["deleted_at"])
            item["days_left"] = max(0, RETENTION_DAYS - age.days)
        return sorted(out, key=lambda i: i["deleted_at"], reverse=True)

    def restore(self, who: str, items: list[tuple[str, str]]) -> int:
        """TR-2, TR-4: clear deleted_at only, so repeat rules and previous_task_id are kept."""
        who = self.require_member(who)
        restored = 0
        with self._write() as c:
            for item_type, item_id in items:
                if item_type == "task":
                    r = c.execute("SELECT title AS label FROM task WHERE id = ? AND deleted_at IS NOT NULL "
                                  "AND purge_batch IS NULL", (item_id,)).fetchone()
                    table = "task"
                elif item_type == "reminder":
                    r = c.execute("SELECT message AS label FROM reminder WHERE id = ? AND deleted_at IS NOT NULL "
                                  "AND purge_batch IS NULL", (item_id,)).fetchone()
                    table = "reminder"
                else:
                    raise OrganizerError("Only tasks and reminders can be restored.")
                if r is None:
                    continue
                c.execute(f"UPDATE {table} SET deleted_at = NULL, deleted_by = NULL WHERE id = ?", (item_id,))
                self._log(who, "restored", item_type, _short(r["label"]), item_id=item_id)
                restored += 1
        return restored

    # --- deleting from Trash for good (WO-FS-014) -------------------------------------------------------

    def delete_from_trash(self, who: str, items: list[tuple[str, str]]) -> dict[str, Any]:
        """DT-1 to DT-3, TK-a: these Trash items, deleted for good once the Undo time has run out. Returns the batch,
        for Undo."""
        who = self.require_member(who)
        with self._write() as c:
            found: list[tuple[str, str, str]] = []
            for item_type, item_id in items:
                if item_type not in ("task", "reminder"):
                    raise OrganizerError("Only tasks and reminders are in Trash.")
                label = "title" if item_type == "task" else "message"
                r = c.execute(f"SELECT {label} AS label FROM {item_type} WHERE id = ? AND deleted_at IS NOT NULL "
                              "AND purge_batch IS NULL", (item_id,)).fetchone()
                if r is not None:
                    found.append((item_type, item_id, r["label"]))
            if not found:
                raise NotFound("That's no longer in Trash.")
            return self._purge_batch(c, who, found, cleared=len(items) > 1)

    def clear_trash(self, who: str) -> dict[str, Any]:
        """CT-1 to CT-4: everything in Trash, for the whole family, with the same Undo."""
        who = self.require_member(who)
        with self._write() as c:
            found = [("task", r["id"], r["title"]) for r in c.execute(
                "SELECT id, title FROM task WHERE deleted_at IS NOT NULL AND purge_batch IS NULL")]
            found += [("reminder", r["id"], r["message"]) for r in c.execute(
                "SELECT id, message FROM reminder WHERE deleted_at IS NOT NULL AND purge_batch IS NULL")]
            if not found:
                raise NotFound("Trash is already empty.")
            return self._purge_batch(c, who, found, cleared=True)

    def _purge_batch(self, c: Any, who: str, found: list[tuple[str, str, str]], *, cleared: bool) -> dict[str, Any]:
        """CD-1: mark them with a batch id; RL-2: one History entry, which Undo takes away again."""
        batch = uuid.uuid4().hex
        now = iso(self.now())
        for item_type, item_id, _label in found:
            c.execute(f"UPDATE {item_type} SET purge_at = ?, purge_batch = ? WHERE id = ?", (now, batch, item_id))
        n = len(found)
        if cleared:
            self._log(who, "cleared", "list", f"Trash ({n} item{'s' if n != 1 else ''})",
                      after={"to": "deleted for good", "batch": batch, "count": n})
        else:
            item_type, item_id, label = found[0]
            self._log(who, "removed", item_type, _short(label), item_id=item_id,
                      after={"to": "deleted for good", "batch": batch})
        commit_after = self.now() + timedelta(seconds=UNDO_SECONDS)
        return {"id": batch, "count": n, "commit_after": iso(commit_after)}

    def undo_trash_delete(self, who: str, batch: str) -> int:
        """DT-3, CT-3: back in Trash exactly as they were. CD-3: under the same lock as the final delete, so only one
        of the two can happen."""
        self.require_member(who)
        with self._write() as c:
            back = sum(c.execute(f"UPDATE {table} SET purge_at = NULL, purge_batch = NULL WHERE purge_batch = ?",
                                 (batch,)).rowcount for table in ("task", "reminder"))
            if not back:
                raise TooLate("Too late to undo. The 10 seconds had already run out.")
            c.execute("DELETE FROM activity_entry WHERE after LIKE ?", (f'%"batch": "{batch}"%',))  # RL-2
        return int(back)

    def commit_trash_deletes(self) -> int:
        """DT-4, CT-3: deleted for good once Undo has run out (a few seconds' grace for a slow PC, as History). A
        repeating task's next occurrence just loses its link to the deleted one (DT-5: ON DELETE SET NULL)."""
        cutoff = iso(self.now() - timedelta(seconds=UNDO_SECONDS + 5))
        with self._write() as c:
            return sum(int(c.execute(f"DELETE FROM {table} WHERE purge_at <= ?", (cutoff,)).rowcount)
                       for table in ("task", "reminder"))

    # --- History (AL-1 to AL-3) ----------------------------------------------------------------

    def list_history(self, *, list_name: str | None = None, member: str | None = None,
                     limit: int = 1000) -> list[dict[str, Any]]:
        sql = "SELECT * FROM activity_entry WHERE deleted_at IS NULL"
        args: list[Any] = []
        if list_name:
            item_type = {"events": "event", "tasks": "task", "reminders": "reminder"}.get(list_name, list_name)
            sql += " AND (item_type = ? OR list = ?)"
            args += [item_type, list_name]
        if member:
            sql += " AND who = ?"
            args.append(member.lower())
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._read() as c:
            rows = c.execute(sql, args).fetchall()
        return [{"id": r["id"], "who": r["who"], "action": r["action"], "item_type": r["item_type"],
                 "item_id": r["item_id"], "list": r["list"], "label": r["item_label"], "at": r["at"],
                 "before": json.loads(r["before"]) if r["before"] else None,
                 "after": json.loads(r["after"]) if r["after"] else None} for r in rows]

    # --- cars (WO-FS-010 section 9) -------------------------------------------------------------

    def list_cars(self) -> list[dict[str, Any]]:
        with self._read() as c:
            return [{"id": r["id"], "name": r["name"], "position": r["position"]}
                    for r in c.execute("SELECT * FROM car ORDER BY position")]

    def set_cars(self, who: str, names: list[str], *, remove_anyway: bool = False) -> list[dict[str, Any]]:
        """CR-1 to CR-3: 1 to 10 cars, each with its own name (up to 30 characters). Renames apply everywhere at once;
        removing a car that upcoming events need asks first (NeedsConfirmation), then clears it from them."""
        who = self.require_member(who)
        names = [" ".join(n.split()) for n in names]
        if not 1 <= len(names) <= MAX_CARS:
            raise OrganizerError(f"Choose between 1 and {MAX_CARS} cars.")
        for n, name in enumerate(names, start=1):
            if not name:
                names[n - 1] = name = f"Car {n}"
            if len(name) > CAR_NAME_MAX:
                raise OrganizerError(f"Keep each car's name to {CAR_NAME_MAX} characters.")
        if len({n.lower() for n in names}) != len(names):
            raise OrganizerError("Give each car a different name.")
        old = self.list_cars()
        removed = old[len(names):]
        needed = [(car, self._upcoming_with_car(car["id"])) for car in removed]
        needed = [(car, n) for car, n in needed if n]
        if needed and not remove_anyway:
            raise NeedsConfirmation(" ".join(f"{car['name']} is needed for {n} upcoming event{'s' if n != 1 else ''}."
                                             for car, n in needed),
                                    cars=[{"name": car["name"], "events": n} for car, n in needed])
        with self._write() as c:
            for car in removed:  # the events that needed it lose it (ON DELETE SET NULL)
                c.execute("UPDATE event SET car_id = NULL, car_chosen_by = NULL WHERE car_id = ?", (car["id"],))
                c.execute("DELETE FROM car WHERE id = ?", (car["id"],))
            kept = old[:len(names)]
            for car in kept:  # two steps, so swapping two names never clashes
                c.execute("UPDATE car SET name = ? WHERE id = ?", (f"#renaming {car['id']}", car["id"]))
            for car, name in zip(kept, names, strict=False):
                c.execute("UPDATE car SET name = ? WHERE id = ?", (name, car["id"]))
            for position in range(len(kept) + 1, len(names) + 1):
                c.execute("INSERT INTO car (name, position) VALUES (?, ?)", (names[position - 1], position))
            before, after = [car["name"] for car in old], names
            if before != after:
                self._log(who, "edited", "car", "Cars", before={"cars": before}, after={"cars": after})
        return self.list_cars()

    def _upcoming_with_car(self, car_id: int) -> int:
        with self._read() as c:
            return int(c.execute("SELECT COUNT(*) FROM event WHERE car_id = ? AND (ends_at >= ? OR repeat <> 'none')",
                                 (car_id, iso(self.now()))).fetchone()[0])

    def _car_window(self) -> tuple[datetime, datetime]:
        return self.now() - timedelta(days=1), self.now() + timedelta(days=CONFLICT_HORIZON_DAYS)

    def _car_uses(self, r: Any, car_id: int, window: tuple[datetime, datetime]) -> list[dict[str, Any]]:
        """CR-5, CR-7, CR-11: other events needing this car at a time that overlaps any occurrence of `r` (an
        all-day event takes its car for the whole day)."""
        mine = self._occurrences(r, window)
        found: list[dict[str, Any]] = []
        for other in self.conn.execute("SELECT * FROM event WHERE car_id = ? AND id <> ? AND hidden = 0 "
                                       "AND app_deleted = 0", (car_id, r["id"])):
            for start, end in self._occurrences(other, window):
                if any(start < e and s < end for s, e in mine):
                    local = to_local(start)
                    found.append({"id": other["id"], "title": other["title"], "date": local.date().isoformat(),
                                  "start_time": "" if other["all_day"] else other["start_time"],
                                  "end_time": "" if other["all_day"] else other["end_time"]})
                    break
        return found

    def event_cars(self, who: str, event_id: str) -> list[dict[str, Any]]:
        """CR-5, CR-6: every car, Free or In use (and what for) at this event's time, the chosen one marked."""
        self.require_member(who)
        with self._read():
            r = self._event_or_404(event_id)
            window = self._car_window()
            return [car | {"selected": r["car_id"] == car["id"], "in_use": self._car_uses(r, car["id"], window)}
                    for car in self.list_cars()]

    def set_event_car(self, who: str, event_id: str, car_id: int | None, *,
                      use_anyway: bool = False) -> dict[str, Any]:
        """CR-4 to CR-12: one car per event, or none. A car already needed at an overlapping time asks first (CR-7).
        Kept in the app only: nothing goes to Google and no email is sent (CR-12)."""
        who = self.require_member(who)
        with self._read():
            r = self._event_or_404(event_id)
            self._require_editable(r)  # CR-10: not on read-only events
            cars = {car["id"]: car["name"] for car in self.list_cars()}
            if car_id is not None and car_id not in cars:
                raise NotFound("That car is no longer in Family & Settings.")
            clashes = self._car_uses(r, car_id, self._car_window()) if car_id is not None else []
        if clashes and not use_anyway:
            first = clashes[0]
            when = "all day" if not first["start_time"] else \
                f"{_fmt_time(first['start_time'])}–{_fmt_time(first['end_time'])}"
            raise NeedsConfirmation(f"The {cars[car_id]} is already needed for {first['title']}, {when}.",
                                    car_clashes=clashes)
        before = cars.get(r["car_id"]) if r["car_id"] else None
        with self._write() as c:
            c.execute("UPDATE event SET car_id = ?, car_chosen_by = ? WHERE id = ?",
                      (car_id, who if car_id is not None else None, event_id))
            after = cars[car_id] if car_id is not None else None
            if before != after:
                self._log(who, "edited", "event", r["title"], item_id=event_id,
                          before={"car": before or "No car"}, after={"car": after or "No car"})
        return self.get_event(event_id)

    def cars_today(self) -> list[dict[str, Any]]:
        """CB-a to CB-j: each use of a car today, repeats included: the car, the event and its time, in minutes from
        midnight (an all-day event takes the whole day)."""
        today = self.today()
        window = (local_to_utc(today, time(0, 0)), local_to_utc(today + timedelta(days=1), time(0, 0)))
        cars = {car["id"]: car["name"] for car in self.list_cars()}
        uses = []
        with self._read() as c:
            hidden = self._hidden("events")
            for r in c.execute("SELECT * FROM event WHERE car_id IS NOT NULL"):
                if r["id"] in hidden or r["car_id"] not in cars:
                    continue
                for start, end in self._occurrences(r, window):
                    if r["all_day"] or r["end_date"]:
                        first, last = 0, MINUTES_A_DAY
                    else:
                        s_, e_ = to_local(max(start, window[0])), to_local(min(end, window[1]))
                        first = s_.hour * 60 + s_.minute
                        last = MINUTES_A_DAY if e_ >= to_local(window[1]) else e_.hour * 60 + e_.minute
                    uses.append({"car_id": r["car_id"], "car": cars[r["car_id"]], "event_id": r["id"],
                                 "title": r["title"], "start": first, "end": last, "all_day": bool(r["all_day"])})
        return sorted(uses, key=lambda u: (u["start"], u["car"]))

    # --- groceries (WO-FS-010 section 10): shared, not in History (GR-8), not in the digest (GR-9) ----------

    def list_groceries(self) -> dict[str, Any]:
        """GR-3; WO-FS-011 GG-1, GG-5, GO-1, GO-6: Food then Items. In each group, the items to buy in their shared
        order, then the bought ones, in the order they were ticked."""
        with self._read() as c:
            rows = [dict(r) | {"bought": bool(r["bought"])}
                    for r in c.execute("SELECT * FROM grocery_item ORDER BY position, added_at, rowid")]
        to_buy = [r for r in rows if not r["bought"]]
        bought = sorted((r for r in rows if r["bought"]), key=lambda r: r["bought_at"] or "")
        groups = {cat: {"to_buy": [r for r in to_buy if r["category"] == cat],
                        "bought": [r for r in bought if r["category"] == cat]} for cat in GROCERY_GROUPS}
        return {"to_buy": to_buy, "bought": bought, "groups": groups}

    def _next_position(self, c: Any, category: str) -> int:
        row = c.execute("SELECT MAX(position) FROM grocery_item WHERE category = ? AND bought = 0",
                        (category,)).fetchone()
        return int(row[0] or 0) + 1

    def place_grocery(self, who: str, item_id: str, category: str, index: int | None = None) -> None:
        """GG-4, GO-1 to GO-5: put an item at `index` (from 0) in a group's to-buy list, or at its bottom (None):
        up and down, a drag within or across groups, and Move to Food / Move to Items. The last change wins."""
        self.require_member(who)
        if category not in GROCERY_GROUPS:
            raise OrganizerError("Choose Food or Items.")
        with self._write() as c:
            if c.execute("SELECT 1 FROM grocery_item WHERE id = ?", (item_id,)).fetchone() is None:
                raise NotFound("That item is no longer on the list.")
            order = [r[0] for r in c.execute(
                "SELECT id FROM grocery_item WHERE category = ? AND bought = 0 AND id <> ? "
                "ORDER BY position, added_at, rowid", (category, item_id))]
            order.insert(len(order) if index is None else max(0, min(index, len(order))), item_id)
            c.execute("UPDATE grocery_item SET category = ? WHERE id = ?", (category, item_id))
            for position, gid in enumerate(order, start=1):
                c.execute("UPDATE grocery_item SET position = ? WHERE id = ?", (position, gid))

    def move_grocery(self, who: str, item_id: str, step: int) -> None:
        """GO-2, GO-4: one place up (-1) or down (+1) within its group; at the top or bottom it stays put."""
        groups = self.list_groceries()["groups"]
        for category, group in groups.items():
            ids = [i["id"] for i in group["to_buy"]]
            if item_id in ids:
                index = ids.index(item_id) + step
                if 0 <= index < len(ids):
                    self.place_grocery(who, item_id, category, index)
                return
        raise NotFound("That item isn't on the to-buy list.")

    def add_grocery(self, who: str, text: str, *, category: str = "food",
                    allow_duplicate: bool = False) -> dict[str, Any]:
        """GR-2, GG-3, GG-7: up to 100 characters, at the bottom of the chosen group. The same item twice in a group
        is allowed, after "Already on the list"."""
        who = self.require_member(who)
        text = " ".join(text.split())
        if not 1 <= len(text) <= GROCERY_MAX:
            raise OrganizerError(f"Type the item (up to {GROCERY_MAX} characters).")
        if category not in GROCERY_GROUPS:
            raise OrganizerError("Choose Food or Items.")
        same_group = self.list_groceries()["groups"][category]["to_buy"]
        if not allow_duplicate and any(i["text"].lower() == text.lower() for i in same_group):
            raise NeedsConfirmation(f"{text} is already on the list.", duplicate=True)
        item: dict[str, Any] = {"id": uuid.uuid4().hex, "text": text, "added_by": who, "added_at": iso(self.now()),
                "category": category}
        with self._write() as c:
            item["position"] = self._next_position(c, category)
            c.execute("INSERT INTO grocery_item (id, text, added_by, added_at, category, position) "
                      "VALUES (:id, :text, :added_by, :added_at, :category, :position)", item)
        return item | {"bought": False, "bought_at": None}

    def set_grocery_bought(self, who: str, item_id: str, bought: bool) -> None:
        """GR-3: ticked off moves it to Bought; unticked moves it back."""
        self.require_member(who)
        with self._write() as c:
            row = c.execute("SELECT category FROM grocery_item WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise NotFound("That item is no longer on the list.")
            # GG-5: unticked, it goes back to the bottom of its group's to-buy list
            position = 0 if bought else self._next_position(c, row["category"])
            c.execute("UPDATE grocery_item SET bought = ?, bought_at = ?, position = CASE WHEN ? THEN position "
                      "ELSE ? END WHERE id = ?",
                      (int(bought), iso(self.now()) if bought else None, int(bought), position, item_id))

    def remove_groceries(self, who: str, item_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """GR-4, GR-5: removed straight away (not to Trash); the removed items come back for Undo. No ids: every
        bought item (Clear bought)."""
        self.require_member(who)
        with self._write() as c:
            if item_ids is None:
                rows = [dict(r) for r in c.execute("SELECT * FROM grocery_item WHERE bought = 1")]
            else:
                marks = ",".join("?" * len(item_ids))
                rows = [dict(r) for r in c.execute(f"SELECT * FROM grocery_item WHERE id IN ({marks})", item_ids)]
            for row in rows:
                c.execute("DELETE FROM grocery_item WHERE id = ?", (row["id"],))
        return rows

    def restore_groceries(self, who: str, items: list[dict[str, Any]]) -> None:
        """Undo (GR-4, GR-5): the removed items return as they were."""
        self.require_member(who)
        with self._write() as c:
            for i in items:
                c.execute("INSERT OR IGNORE INTO grocery_item (id, text, added_by, added_at, bought, bought_at, "
                          "category, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (i["id"], i["text"], i["added_by"], i["added_at"], int(bool(i["bought"])), i["bought_at"],
                           i.get("category", "food"), i.get("position", 0)))

    # --- Today and the digest (DG-4 to DG-11) --------------------------------------------------

    def today_view(self) -> dict[str, Any]:
        today = self.today()
        events = self.list_events()
        tasks = self.list_tasks()
        todays = [e for e in events["upcoming"] + events["past"]
                  if recurrence.occurs_on(date.fromisoformat(e["date"]), e["repeat"], today)
                  or (e["end_date"] and e["date"] <= today.isoformat() <= e["end_date"])]  # GY-11
        todays.sort(key=lambda e: e["start_time"])  # "" (all day) sorts first (AD-6)
        waiting = [{"id": e["id"], "title": e["title"], "next_date": e["next_date"],
                    "waiting_on": [p["email"] for p in e["invitees"] if p["rsvp"] in ("no_reply", "tentative")]}
                   for e in events["upcoming"] if e["sync_status"] == "synced"]
        waiting = [w for w in waiting if w["waiting_on"]]
        sent_today = self.emails_sent_on(today.isoformat())
        return {
            "date": today.isoformat(),
            "events": todays,
            "overdue": tasks["overdue"],
            # OT-9: a high-priority ongoing task is in the digest too, after the dated ones
            # WO-FS-012 DS-11: bumped (due-soon) tasks are high priority here too
            "high_priority": [t for t in tasks["open"] + tasks["ongoing"] if t["effective_priority"] == "high"],
            "waiting_on_rsvps": waiting,
            "rsvp_refreshed_at": events["rsvp_refreshed_at"],
            "accounts_needing_reconnect": [a["email"] for a in self.calendar_accounts()
                                           if a["status"] == "needs_reconnect"],  # WO-FS-008 PS-7
            "unsent": [{"id": e["id"], "title": e["title"]} for e in events["upcoming"] + events["past"]
                       if e["retry_gave_up"]],  # AS-3
            "digest": {"sent_today": sent_today, "daily_cap": self.digest_cap,
                       "limit_reached": sent_today >= self.digest_cap},
            "cars": self.list_cars(), "cars_today": self.cars_today(),  # WO-FS-010 CB-a, CB-j
            "groceries_to_buy": len(self.list_groceries()["to_buy"]),  # GR-7: the sidebar badge
        }

    def week_view(self, start: date | None = None, days: int = 7) -> dict[str, Any]:
        """WO-FS-005 section 3, WO-FS-006: everything on the Calendar for `days` days from `start`, a Monday
        (7 for a week; 35 or 42 for the month grid).

        Repeating events appear on every day they occur (WB-5). Open overdue tasks sit on today, wherever their due
        date is (WB-6); other tasks sit on their due date. Past events and done tasks are marked to be faded (WB-8).
        """
        today = self.today()
        start = start or today - timedelta(days=today.weekday())
        start -= timedelta(days=start.weekday())  # always a Monday (WV-1)
        end = start + timedelta(days=days)
        now = self.now()
        window = (local_to_utc(start, time(0, 0)), local_to_utc(end, time(0, 0)))
        with self._read() as c:
            hidden = self._hidden("events")
            rows = [r for r in c.execute("SELECT * FROM event") if r["id"] not in hidden]
            details = {d["id"]: d for d in self._event_dicts(rows, with_conflicts=True)}
            occurrences = []
            for r in rows:
                for s_, e_ in self._occurrences(r, window):
                    local_start, local_end = to_local(s_), to_local(e_)
                    if not start <= local_start.date() < end:
                        continue
                    d = details[r["id"]]
                    whole_day = bool(r["all_day"] or r["end_date"])  # a multi-day day reads All day (GY-11)
                    occurrences.append({
                        "id": r["id"], "day": local_start.date().isoformat(),
                        "start_time": "" if whole_day else local_start.strftime("%H:%M"),
                        "end_time": "" if whole_day else local_end.strftime("%H:%M"),
                        "all_day": whole_day,
                        # AD-7: an all-day event's occurrence runs to 11:59 pm; it's over at midnight
                        "past": e_ + timedelta(minutes=1 if whole_day else 0) <= now,
                        "title": d["title"], "location": d["location"], "repeat": d["repeat"],
                        "sync_status": d["sync_status"], "sync_op": d["sync_op"], "invitees": d["invitees"],
                        "rsvp_counts": d["rsvp_counts"], "conflicts_with": d["conflicts_with"],
                        "organizer_email": d["organizer_email"], "organizer_connected": d["organizer_connected"],
                        "retry_gave_up": d["retry_gave_up"],
                        **{k: d[k] for k in ("from_google", "calendar_email", "read_only", "google_note",
                                             "html_link", "private", "not_synced", "car", "car_chosen_by",
                                             "car_double_booked")},
                    })
        occurrences.sort(key=lambda o: (o["day"], o["start_time"], o["end_time"], o["title"]))
        tasks = []
        lists = self.list_tasks()
        for t in lists["overdue"] + lists["open"] + lists["done"]:
            if t["ongoing"]:  # OT-8: no day, so not on the Calendar
                continue
            day = today.isoformat() if t["overdue"] else t["due_date"]
            if start.isoformat() <= day < end.isoformat():
                tasks.append(t | {"day": day, "done": t["completed_at"] is not None})
        rank = {p: i for i, p in enumerate(PRIORITIES)}
        tasks.sort(key=lambda t: (t["day"], t["done"], not t["overdue"], rank[t["effective_priority"]], t["title"]))
        return self.today_view() | {
            "week_start": start.isoformat(), "week_end": (end - timedelta(days=1)).isoformat(),
            "week_events": occurrences, "week_tasks": tasks,
        }

    def send_digest(self, who: str) -> dict[str, Any]:
        who = self.require_member(who)
        with self._digest_lock:
            now = self.now()
            today = self.today().isoformat()
            with self._read() as c:
                recent = c.execute("SELECT 1 FROM digest_send WHERE email = ? AND sent_at > ?",
                                   (who, iso(now - timedelta(seconds=DIGEST_REPEAT_SECONDS)))).fetchone()
            sent_today = self.emails_sent_on(today)
            if recent:
                return {"sent": False, "message": f"Digest sent to {who} a moment ago. Check your inbox."}
            if sent_today >= self.digest_cap:
                raise LimitReached("Daily digest limit reached. You can still see it on the Today screen.")
            subject, text, html = build_digest(self.today_view(), who)
            try:
                self._mailer().send(who, subject, text, html)
            except MailError as err:
                raise ExternalFailure(f"The digest wasn't sent. {err}") from err
            with self._write() as c:
                c.execute("INSERT INTO digest_send (email, sent_at, local_date) VALUES (?, ?, ?)",
                          (who, iso(now), today))
                self._log(who, "digest sent", "digest", f"Digest to {who}")
        return {"sent": True, "message": f"Digest sent to {who}."}

    def emails_sent_on(self, local_date: str) -> int:
        """Digests plus test emails sent on a Toronto date: what the daily cap counts (FR-8, WO-FS-007 EL-5)."""
        with self._read() as c:
            digests = c.execute("SELECT COUNT(*) FROM digest_send WHERE local_date = ?", (local_date,)).fetchone()[0]
        return int(digests) + int(self.setting(f"test_emails:{local_date}") or 0)

    def send_test_email(self, who: str) -> dict[str, Any]:
        """EL-5: a short message to the admin address, to check the Gmail setup. Counts towards the daily cap."""
        who = self.require_member(who)
        admin = self.admin_email()
        with self._digest_lock:
            today = self.today().isoformat()
            sent_today = self.emails_sent_on(today)
            if sent_today >= self.digest_cap:
                raise LimitReached("Daily digest limit reached. Sending works again tomorrow.")
            when = to_local(self.now())
            text = (f"This is a test from Home Base, sent by {who} on {when:%A, %B} {when.day} at "
                    f"{when.strftime('%I:%M %p').lstrip('0').lower()}.\n\n"
                    "If you can read this, the digest email is set up. Nothing else to do.")
            html = ("<p style=\"font-family:Segoe UI,Arial,sans-serif;font-size:16px\">"
                    + text.replace("\n\n", "</p><p style=\"font-family:Segoe UI,Arial,sans-serif;font-size:16px\">")
                    + "</p>")
            try:
                self._mailer().send(admin, "Home Base test email", text, html)
            except MailError as err:
                raise ExternalFailure(f"The test email wasn't sent. {err}") from err
            earlier = int(self.setting(f"test_emails:{today}") or 0)
            with self._write():
                self._set_setting(f"test_emails:{today}", str(earlier + 1))
                self._log(who, "digest sent", "digest", f"Test email to {admin}")  # History's own action names
        return {"sent": True, "message": f"Test email sent to {admin}. Check that inbox."}

    def delete_history(self, who: str, entry_id: int) -> dict[str, Any]:
        """One History entry out, for the whole family, with Undo until the Undo time runs out."""
        return self._delete_history(who, "id = ?", [entry_id], "That History entry is already gone.")

    def clear_history(self, who: str) -> dict[str, Any]:
        """Clear all on History: every entry, for the whole family, with Undo until the Undo time runs out."""
        return self._delete_history(who, "1 = 1", [], "History is already empty.")

    def _delete_history(self, who: str, where: str, args: list[Any], empty: str) -> dict[str, Any]:
        self.require_member(who)
        batch = uuid.uuid4().hex
        commit_after = self.now() + timedelta(seconds=UNDO_SECONDS)
        with self._write() as c:
            count = c.execute(f"UPDATE activity_entry SET deleted_at = ?, deleted_batch = ? "
                              f"WHERE deleted_at IS NULL AND {where}", [iso(self.now()), batch, *args]).rowcount
        if not count:
            raise NotFound(empty)
        return {"id": batch, "count": count, "commit_after": iso(commit_after)}

    def undo_history_delete(self, who: str, batch: str) -> int:
        self.require_member(who)
        with self._write() as c:
            count = c.execute("UPDATE activity_entry SET deleted_at = NULL, deleted_batch = NULL "
                              "WHERE deleted_batch = ?", (batch,)).rowcount
        if not count:
            raise TooLate("Too late to undo. The 10 seconds had already run out.")
        return int(count)

    def commit_history_deletes(self) -> int:
        """Deleted History entries go for good once Undo has run out (a few seconds' grace for a slow PC)."""
        cutoff = iso(self.now() - timedelta(seconds=UNDO_SECONDS + 5))
        with self._write() as c:
            return int(c.execute("DELETE FROM activity_entry WHERE deleted_at <= ?", (cutoff,)).rowcount)

    # --- 30-day retention (RT-1 to RT-6) -------------------------------------------------------

    def purge(self) -> dict[str, int]:
        cutoff = iso(self.now() - timedelta(days=RETENTION_DAYS))
        statements = {
            "done_tasks": "DELETE FROM task WHERE completed_at < ? AND deleted_at IS NULL",
            "trash_tasks": "DELETE FROM task WHERE deleted_at < ?",
            "trash_reminders": "DELETE FROM reminder WHERE deleted_at < ?",
            "past_events": "DELETE FROM event WHERE repeat = 'none' AND ends_at < ? AND sync_op IS NULL",
            "activity": "DELETE FROM activity_entry WHERE at < ?",
            "clears": "DELETE FROM pending_clear WHERE resolved_at < ?",
            "digests": "DELETE FROM digest_send WHERE sent_at < ?",
            "bought_groceries": "DELETE FROM grocery_item WHERE bought = 1 AND bought_at < ?",  # GR-8
        }
        counts: dict[str, int] = {}
        with self._write() as c:
            for name, sql in statements.items():
                counts[name] = c.execute(sql, (cutoff,)).rowcount
        return counts

    def maintenance_due(self) -> bool:
        """Nightly after 3:00 am Toronto time, or at start-up if a night was missed (RT-6)."""
        last = self.setting("last_maintenance_date")
        local = to_local(self.now())
        today = local.date().isoformat()
        if last is None:
            return True
        if last >= today:
            return False
        yesterday = (local.date() - timedelta(days=1)).isoformat()
        return local.hour >= 3 or last < yesterday

    def mark_maintenance_done(self) -> None:
        with self._write():
            self._set_setting("last_maintenance_date", self.today().isoformat())


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _overlap_message(conflicts: list[dict[str, Any]]) -> str:
    names = [c["title"] for c in conflicts[:3]]
    more = len(conflicts) - len(names)
    text = "Overlaps with " + ", ".join(names)
    if more:
        text += f" and {more} more"
    return text + "."
