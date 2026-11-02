"""Sidebar badge counts (WO-FS-004 NV-2): overdue tasks next to Tasks, today's events next to Events."""

from __future__ import annotations

from conftest import ALEX, FakeClock, event, task

from family_scheduler.app.badges import badge_counts
from family_scheduler.organizer import Organizer


def test_counts_from_the_today_data() -> None:
    today = {"overdue": [{"id": "a"}, {"id": "b"}], "events": [{"id": "e"}], "high_priority": [{"id": "h"}]}
    assert badge_counts(today) == {"Tasks": 2, "Events": 1}


def test_no_badges_when_nothing_is_due() -> None:
    assert badge_counts({"overdue": [], "events": []}) == {"Tasks": 0, "Events": 0}  # hidden at zero
    assert badge_counts({}) == {"Tasks": 0, "Events": 0}


def test_counts_follow_the_hub(org: Organizer, clock: FakeClock) -> None:
    """Tuesday Oct 27, 2026, 8:00 am."""
    assert badge_counts(org.today_view()) == {"Tasks": 0, "Events": 0}
    org.add_event(ALEX, event("Soccer pickup", day="2026-10-27", start="16:00", end="16:30"))
    org.add_event(ALEX, event("Dentist", day="2026-11-03"))
    org.add_task(ALEX, task("Return library books", due="2026-10-25"))
    org.add_task(ALEX, task("Renew passport photos", due="2026-10-26", priority="high"))
    org.add_task(ALEX, task("Book tire swap", due="2026-10-30", priority="high"))
    assert badge_counts(org.today_view()) == {"Tasks": 2, "Events": 1}

    done = org.list_tasks()["overdue"][0]
    org.complete_task(ALEX, done["id"])
    assert badge_counts(org.today_view())["Tasks"] == 1

    clock.advance(days=1)  # Wednesday: Soccer pickup was yesterday; the tire swap isn't due yet
    assert badge_counts(org.today_view()) == {"Tasks": 1, "Events": 0}
