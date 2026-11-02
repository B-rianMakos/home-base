"""Digest email content (DG-2, DG-6, RV-4). HTML is one of the allowed non-Python exceptions (PY-2)."""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from family_scheduler.palette import PRIORITY_COLOURS
from family_scheduler.timeutil import parse_iso, to_local


def fmt_time(hhmm: str) -> str:
    """"4:15 pm"; an all-day event has no time and reads "All day" (WO-FS-008 AD-6)."""
    if not hhmm:
        return "All day"
    hours, minutes = (int(x) for x in hhmm.split(":"))
    suffix = "am" if hours < 12 else "pm"
    return f"{(hours % 12) or 12}:{minutes:02d} {suffix}"


def _minutes(total: int) -> str:
    """Minutes from midnight as "1:00 pm"; 24 * 60 is the next midnight, "12:00 am"."""
    return fmt_time(f"{(total // 60) % 24:02d}:{total % 60:02d}")


def fmt_day(iso_date: str) -> str:
    """"Fri Oct 30"; an ongoing task has no date (WO-FS-009 OT-6)."""
    if not iso_date:
        return "No due date"
    d = date.fromisoformat(iso_date)
    return f"{d.strftime('%a %b')} {d.day}"


def fmt_refreshed(stamp: str | None) -> str:
    if not stamp:
        return "Replies haven't been refreshed yet."
    local = to_local(parse_iso(stamp))
    return f"Replies as of {local.strftime('%a %b')} {local.day}, {fmt_time(local.strftime('%H:%M'))}."


def _sections(view: dict[str, Any]) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    if view["events"]:
        lines = []
        for e in view["events"]:
            c = e["rsvp_counts"]
            where = f", {e['location']}" if e["location"] else ""
            waiting = c["no_reply"] + c["tentative"]
            when = f"{fmt_time(e['start_time'])}–{fmt_time(e['end_time'])}" if e["start_time"] else "all day"
            lines.append(f"{e['title']}, {when}{where}. "
                         f"{c['accepted']} accepted, {c['declined']} declined, {waiting} no reply")
        sections.append(("Today's events", lines))
    if view.get("cars_today"):  # WO-FS-010 CB-j: one line per car in use today
        lines = []
        for use in view["cars_today"]:
            when = "all day" if use["all_day"] else f"{_minutes(use['start'])}–{_minutes(use['end'])}"
            lines.append(f"{use['car']}: {when} {use['title']}")
        sections.append(("Cars today", lines))
    if view["overdue"]:
        sections.append(("Overdue tasks", [f"{t['title']} ({t['priority'].title()}), due {fmt_day(t['due_date'])}"
                                           for t in view["overdue"]]))
    if view["high_priority"]:
        sections.append(("High-priority tasks", [f"{t['title']}, " + (f"due {fmt_day(t['due_date'])}"
                                                                         if t["due_date"] else "no due date")
                                                 for t in view["high_priority"]]))
    if view["waiting_on_rsvps"]:
        lines = [f"{w['title']}, {fmt_day(w['next_date'])}: {', '.join(w['waiting_on'])}"
                 for w in view["waiting_on_rsvps"]]
        lines.append(fmt_refreshed(view["rsvp_refreshed_at"]))
        sections.append(("Waiting on RSVPs", lines))
    return sections


def _pill(priority: str) -> str:
    """A filled pill with black text, readable on a white or a dark mail background (WO-FS-013 TP-d)."""
    return (f'<span style="background:{PRIORITY_COLOURS[priority]};color:#000000;border-radius:8px;'
            f'padding:0 6px;font-size:85%;font-weight:600">{priority.title()}</span>')


def _with_pill(item: str) -> str:
    for priority in PRIORITY_COLOURS:
        item = item.replace(f"({priority.title()})", _pill(priority), 1)
    return item


def build_digest(view: dict[str, Any], recipient: str) -> tuple[str, str, str]:
    """Returns (subject, plain text, HTML). Empty sections are left out (DG-6)."""
    day = date.fromisoformat(view["date"])
    long_day = f"{day.strftime('%A, %B')} {day.day}"
    n_events, n_overdue = len(view["events"]), len(view["overdue"])
    parts = []
    if n_events:
        parts.append(f"{n_events} event{'s' if n_events != 1 else ''}")
    if n_overdue:
        parts.append(f"{n_overdue} overdue task{'s' if n_overdue != 1 else ''}")
    # WO-FS-011 HB-1: "Home Base: your digest for Saturday, October 31 · 2 events, 1 overdue task"
    subject = f"Home Base: your digest for {day.strftime('%A, %B')} {day.day} · " + \
        (", ".join(parts) if parts else "nothing urgent")

    sections = _sections(view)
    text_lines = [f"Here's {long_day}.", ""]
    html_parts = [f"<h2 style=\"font-family:Segoe UI,Arial,sans-serif\">Here's {escape(long_day)}.</h2>"]
    if not sections:
        text_lines.append("Nothing scheduled today.")
        html_parts.append("<p>Nothing scheduled today.</p>")
    for heading, lines in sections:
        text_lines.append(heading)
        text_lines.extend(f"  - {line}" for line in lines)
        text_lines.append("")
        items = [escape(line) for line in lines]
        if heading == "Overdue tasks":  # WO-FS-013 TP-d: the priority as a pill in its colour
            items = [_with_pill(item) for item in items]
        title = escape(heading) + (" " + _pill("high") if heading == "High-priority tasks" else "")
        html_parts.append(f"<h3 style=\"font-family:Segoe UI,Arial,sans-serif\">{title}</h3><ul>"
                          + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
    footer = f"Sent to {recipient} because you clicked Send me my digest in Home Base."
    text_lines.append(footer)
    html_parts.append(f"<p style=\"color:#5b6878;font-size:12px\">{escape(footer)}</p>")
    html = ("<div style=\"font-family:Segoe UI,Arial,sans-serif;color:#1d2733;max-width:560px\">"
            + "".join(html_parts) + "</div>")
    return subject, "\n".join(text_lines), html
