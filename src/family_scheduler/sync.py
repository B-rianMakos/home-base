"""Two-way Google Calendar sync (WO-FS-010 section 8).

Sync with Google asks each connected calendar account's primary calendar for what changed since the last sync
(Google's sync tokens, GY-3) and brings it into the app: new events are added with a From Google tag (GY-4), changes
are applied (GY-5) and removals removed (GY-6), each recorded in History. Changes made in the app already go to Google
straight away through the event's organizer (GY-7), so nothing is pushed here.

It's free: the same calendar.events permission, no billing account, no website and no push notifications (GY-2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from family_scheduler import recurrence
from family_scheduler.google_calendar import CalendarError, CalendarGateway, SyncTokenExpired
from family_scheduler.timeutil import HOME_TZ_NAME, iso, local_to_utc, parse_hhmm, to_local

if TYPE_CHECKING:
    from family_scheduler.organizer import Organizer

FIRST_SYNC_DAYS = 30  # GY-3: the first sync brings in events from 30 days ago on (the retention period, RT-2)
TITLE_MAX, PLACE_MAX = 120, 200
RSVP = {"accepted": "accepted", "declined": "declined", "tentative": "tentative", "needsAction": "no_reply"}
SOURCE = "Google Calendar"
REPLACED = "Your change was replaced by the Google Calendar version"
# What a sync compares, to decide whether an event changed in Google (GY-5).
TRACKED = ("title", "date", "start_time", "end_time", "location", "repeat", "all_day", "end_date")


@dataclass
class SyncResult:
    added: int = 0
    changed: int = 0
    removed: int = 0
    replaced: list[str] = field(default_factory=list)  # GY-10: titles whose app change Google's version replaced
    problems: list[str] = field(default_factory=list)  # per account, e.g. one that needs reconnecting (GY-15)

    def message(self) -> str:
        parts = [f"{n} {what}" for n, what in ((self.added, "added"), (self.changed, "changed"),
                                               (self.removed, "removed")) if n]
        text = "Synced with Google" + (f": {', '.join(parts)}." if parts else ". Nothing new.")
        if self.replaced:
            text += f" {REPLACED} for {', '.join(self.replaced)}."
        if self.problems:
            text += " " + " ".join(self.problems)
        return text


def _local(part: dict[str, Any]) -> tuple[date, datetime | None]:
    """A Google start or end: (local date, local time or None for a date-only, all-day value)."""
    if part.get("date"):
        return date.fromisoformat(part["date"]), None
    moment = datetime.fromisoformat(str(part["dateTime"]).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(part.get("timeZone") or HOME_TZ_NAME))
    local = to_local(moment)
    return local.date(), local


def _repeat(item: dict[str, Any]) -> tuple[str, bool]:
    """GY-11: (repeat, supported). Plain daily, weekly, monthly and yearly repeats are the app's own; anything else
    (every 2 weeks, chosen weekdays, exceptions) is shown read-only."""
    rules = item.get("recurrence") or []
    if not rules:
        return "none", True
    for repeat in ("daily", "weekly", "monthly", "yearly"):
        if rules == [recurrence.rrule_line(repeat)]:
            return repeat, True
    return "none", False


def read_item(item: dict[str, Any], accounts: list[str]) -> dict[str, Any]:
    """One Google event as the app's event fields, with whether (and why) it's read-only here."""
    start_day, start = _local(item["start"])
    end_day, end = _local(item["end"])
    all_day = start is None
    repeat, supported = _repeat(item)
    organizer = str((item.get("organizer") or {}).get("email", "")).lower()
    private = item.get("visibility") in ("private", "confidential")
    notes = []
    if all_day:
        last_day = end_day - timedelta(days=1)  # Google's all-day end is the next day
        start_time = end_time = ""
    else:
        assert start is not None and end is not None
        start_time, end_time = start.strftime("%H:%M"), end.strftime("%H:%M")
        last_day = end_day
        if end_day == start_day + timedelta(days=1) and end.hour == end.minute == 0:
            last_day, end_time = start_day, "23:59"  # ends at midnight: still one day
    end_date = last_day.isoformat() if last_day > start_day else ""
    if private:
        notes.append("Private in Google Calendar")
    if organizer and organizer not in accounts:
        notes.append(f"Organized by {organizer}")  # GY-8: Google doesn't let a guest change it
    if not supported:
        notes.append("Repeats in a way the app can't show")
    if end_date:
        notes.append("Spans several days")
    title = "Busy" if private else (str(item.get("summary") or "").strip() or "(No title)")[:TITLE_MAX]
    attendees = [] if private else [
        (str(a["email"]).lower(), RSVP.get(a.get("responseStatus", "needsAction"), "no_reply"))
        for a in item.get("attendees", []) if a.get("email") and not a.get("resource")]
    day_start = local_to_utc(start_day, parse_hhmm(start_time) if start_time else parse_hhmm("00:00"))
    if all_day:
        day_end = local_to_utc(last_day + timedelta(days=1), parse_hhmm("00:00"))
    else:
        day_end = local_to_utc(last_day, parse_hhmm(end_time))
    return {
        "title": title, "date": start_day.isoformat(), "start_time": start_time, "end_time": end_time,
        "location": "" if private else str(item.get("location") or "")[:PLACE_MAX],
        "repeat": repeat, "all_day": int(all_day), "end_date": end_date, "organizer": organizer,
        "read_only": int(bool(notes)), "google_note": " · ".join(notes), "private": int(private),
        "html_link": str(item.get("htmlLink") or ""), "updated": item.get("updated"),
        "attendees": attendees, "starts_at": iso(day_start), "ends_at": iso(day_end),
    }


def sync_google(org: Organizer, who: str) -> SyncResult:
    """GY-2: Sync with Google, for every connected calendar account, in one go (events and replies)."""
    who = org.require_member(who)
    result = SyncResult()
    accounts = [a["email"] for a in org.calendar_accounts()]
    connected = org.connected_accounts()
    for account in accounts:
        if account not in connected:
            result.problems.append(f"{account} isn't connected, so its events weren't synced: reconnect it on the "
                                   "hub PC in Family & Settings.")
            continue
        token = org.conn.execute("SELECT sync_token FROM calendar_account WHERE email = ?",
                                 (account,)).fetchone()[0]
        since = iso(org.now() - timedelta(days=FIRST_SYNC_DAYS))
        try:
            try:
                items, new_token = org._google(account, partial(_changes, token, since))
            except SyncTokenExpired:
                items, new_token = org._google(account, partial(_changes, None, since))
        except CalendarError as err:
            result.problems.append(str(err))
            continue
        with org._write() as c:
            for item in items:
                _apply(org, c, account, accounts, item, result)
            c.execute("UPDATE calendar_account SET sync_token = ? WHERE email = ?", (new_token or None, account))
    with org._write():
        stamp = iso(org.now())
        org._set_setting("google_synced_at", stamp)
        if not result.problems:
            org._set_setting("rsvp_refreshed_at", stamp)
    return result


def _changes(token: str | None, since: str, gateway: CalendarGateway) -> tuple[list[dict[str, Any]], str]:
    return gateway.list_changes(token, since)


def _apply(org: Organizer, c: Any, account: str, accounts: list[str], item: dict[str, Any],
           result: SyncResult) -> None:
    gid = str(item.get("id", ""))
    if not gid:
        return
    parent_id = item.get("recurringEventId")
    if parent_id:  # GY-11: one changed or removed date of a series: the series becomes read-only
        c.execute("UPDATE event SET read_only = 1, google_note = ? WHERE gcal_event_id = ? AND read_only = 0",
                  ("Some dates were changed in Google Calendar", parent_id))
        return
    row = c.execute("SELECT * FROM event WHERE gcal_event_id = ?", (gid,)).fetchone()
    if row is not None and row["sync_op"] is not None:
        return  # still on its way to Google, or being cancelled: the app's own action finishes first
    if item.get("status") == "cancelled":
        if row is not None:  # GY-6: removed in Google, so no email from here
            c.execute("DELETE FROM event WHERE id = ?", (row["id"],))
            org._log(account, "removed", "event", row["title"], item_id=row["id"], after={"source": SOURCE})
            result.removed += 1
        return
    try:
        fields = read_item(item, accounts)
    except (KeyError, ValueError):
        return  # something Google sent that the app can't read: leave it in Google
    now = iso(org.now())
    if row is None:  # GY-4: new in Google
        event_id = uuid.uuid4().hex
        c.execute(
            "INSERT INTO event (id, title, date, start_time, end_time, location, repeat, gcal_event_id, sync_status, "
            "created_by, created_at, updated_at, starts_at, ends_at, organizer_email, all_day, source, "
            "calendar_email, read_only, google_note, html_link, private, end_date, google_updated, last_synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'synced', ?, ?, ?, ?, ?, ?, ?, 'google', ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, fields["title"], fields["date"], fields["start_time"], fields["end_time"], fields["location"],
             fields["repeat"], gid, account, now, now, fields["starts_at"], fields["ends_at"],
             fields["organizer"] or account, fields["all_day"], account, fields["read_only"], fields["google_note"],
             fields["html_link"], fields["private"], fields["end_date"], fields["updated"], now))
        _write_invitees(c, event_id, fields)
        org._log(account, "added", "event", fields["title"], item_id=event_id,
                 after={"source": SOURCE, "calendar": account})
        result.added += 1
        return
    before = {k: (bool(row[k]) if k == "all_day" else row[k]) for k in TRACKED}
    after = {k: (bool(fields[k]) if k == "all_day" else fields[k]) for k in TRACKED}
    changed = [k for k in TRACKED if before[k] != after[k]]
    # GY-10: changed in the app since the last sync, and Google's version differs: Google's wins, and it's said
    replaced = bool(changed) and row["last_synced_at"] is not None and row["updated_at"] > row["last_synced_at"]
    c.execute(
        "UPDATE event SET title = ?, date = ?, start_time = ?, end_time = ?, location = ?, repeat = ?, all_day = ?, "
        "end_date = ?, starts_at = ?, ends_at = ?, read_only = ?, google_note = ?, html_link = ?, private = ?, "
        "google_updated = ?, last_synced_at = ?, updated_at = ?, sync_status = 'synced' WHERE id = ?",
        (fields["title"], fields["date"], fields["start_time"], fields["end_time"], fields["location"],
         fields["repeat"], fields["all_day"], fields["end_date"], fields["starts_at"], fields["ends_at"],
         fields["read_only"] if row["source"] == "google" or fields["read_only"] else row["read_only"],
         fields["google_note"], fields["html_link"] or row["html_link"], fields["private"], fields["updated"], now,
         now if changed else row["updated_at"], row["id"]))
    _write_invitees(c, row["id"], fields, keep_organizer=True)
    if changed:  # GY-5
        note = {"source": SOURCE} | ({"note": REPLACED} if replaced else {})
        org._log(account, "edited", "event", fields["title"], item_id=row["id"],
                 before={k: before[k] for k in changed}, after={k: after[k] for k in changed} | note)
        result.changed += 1
        if replaced:
            result.replaced.append(fields["title"])


def _write_invitees(c: Any, event_id: str, fields: dict[str, Any], keep_organizer: bool = False) -> None:
    """GY-13: every invitee by address, on the family list or not; replies as Google has them."""
    organizer = fields["organizer"]
    if keep_organizer:
        c.execute("DELETE FROM event_invitee WHERE event_id = ? AND rsvp <> 'organizer'", (event_id,))
    else:
        c.execute("DELETE FROM event_invitee WHERE event_id = ?", (event_id,))
        if organizer:
            c.execute("INSERT OR IGNORE INTO event_invitee (event_id, email, rsvp) VALUES (?, ?, 'organizer')",
                      (event_id, organizer))
    for email, rsvp in fields["attendees"]:
        if email == organizer:
            continue
        c.execute("INSERT OR REPLACE INTO event_invitee (event_id, email, rsvp) VALUES (?, ?, ?) ",
                  (event_id, email, rsvp))
