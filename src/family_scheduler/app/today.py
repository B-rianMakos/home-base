"""What the Today page shows (WO-FS-007). Plain data, no Qt, so it can be tested."""

from __future__ import annotations

from datetime import date
from typing import Any

from family_scheduler.app.week import PRIORITY_ORDER

GROUPS = ("Overdue", "Due today", "Coming up", "Ongoing")  # TD-b, in this order; Ongoing: WO-FS-009 OT-4


def todays_events(occurrences: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    """TE-1, TE-2: only the occurrences on `today` (a repeating event's included), earliest first."""
    return sorted((e for e in occurrences if e["day"] == today),
                  key=lambda e: (e["start_time"], e["end_time"], e["title"].lower()))


def todo_groups(open_tasks: list[dict[str, Any]], today: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """TD-a to TD-c: every open task, grouped Overdue, Due today, Coming up (empty groups left out); in each, high
    priority first, then by due date, then by title. Done tasks are never shown (TD-f). Tasks with no due date are
    in a fourth group, Ongoing: high priority first, then by title (OT-4); they're never overdue (OT-3)."""
    groups: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUPS}
    for t in open_tasks:
        if t.get("completed_at"):
            continue
        due = t["due_date"]
        if not due:
            groups["Ongoing"].append(t)
            continue
        groups["Overdue" if due < today else "Due today" if due == today else "Coming up"].append(t)
    out = []
    for name in GROUPS:
        if name == "Ongoing":  # OT-4: high priority first, then by title
            tasks = sorted(groups[name], key=lambda t: (_priority(t) != "high", t["title"].lower()))
        else:
            tasks = sorted(groups[name], key=lambda t: (_priority(t) != "high", t["due_date"],
                                                        PRIORITY_ORDER.get(_priority(t), 3), t["title"].lower()))
        if tasks:
            out.append((name, tasks))
    return out


def _priority(t: dict[str, Any]) -> str:
    """WO-FS-012 DS-9: a due-soon task sorts with the high-priority ones."""
    return str(t.get("effective_priority", t["priority"]))


def due_text(due: str, today: str) -> str:
    """TD-d: "Due today", "Due Fri Oct 30"; "No due date" for an ongoing task (OT-6)."""
    if not due:
        return "No due date"
    if due == today:
        return "Due today"
    d = date.fromisoformat(due)
    return f"Due {d.strftime('%a %b')} {d.day}"


def long_date(iso_date: str) -> str:
    """TP-3: "Thursday, October 29"."""
    d = date.fromisoformat(iso_date)
    return f"{d.strftime('%A, %B')} {d.day}"


def day_changed(shown: str | None, today: str) -> bool:
    """TP-5: the page moves to the new day at midnight (Toronto time)."""
    return shown is not None and shown != today
