"""Clear all with the 10-second undo window (CA-1 to CA-5)."""

from __future__ import annotations

import pytest
from conftest import ALEX, SAM, FakeClock, event, task

from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.hub.context import HubContext
from family_scheduler.hub.worker import Worker
from family_scheduler.organizer import Organizer, TooLate


def test_clear_hides_items_then_undo_brings_them_back(org: Organizer) -> None:
    for i in range(3):
        org.add_task(ALEX, task(f"Task {i}"))
    preview = org.clear_preview("tasks")
    assert preview["count"] == 3
    pending = org.clear_all(SAM, "tasks")
    assert pending["count"] == 3
    assert org.list_tasks()["open"] == []  # hidden for everyone, nothing committed
    assert org.conn.execute("SELECT COUNT(*) FROM task WHERE deleted_at IS NULL").fetchone()[0] == 3
    org.undo_clear(SAM, pending["id"])
    assert len(org.list_tasks()["open"]) == 3
    assert [h["action"] for h in org.list_history()[:2]] == ["undone", "cleared"]


def test_items_added_during_the_window_are_kept(org: Organizer, clock: FakeClock) -> None:
    org.add_reminder(ALEX, "Old")
    org.clear_all(ALEX, "reminders")
    org.add_reminder(ALEX, "New")
    clock.advance(seconds=11)
    org.commit_due_clears()
    assert [r["message"] for r in org.list_reminders()] == ["New"]


def test_commit_after_ten_seconds_moves_tasks_to_trash(org: Organizer, clock: FakeClock) -> None:
    org.add_task(ALEX, task())
    pending = org.clear_all(ALEX, "tasks")
    clock.advance(seconds=9)
    assert org.commit_due_clears() == 0
    clock.advance(seconds=2)
    assert org.commit_due_clears() == 1
    assert org.list_trash()[0]["deleted_by"] == ALEX
    with pytest.raises(TooLate):
        org.undo_clear(ALEX, pending["id"])


def test_clearing_events_cancels_each_in_google(org: Organizer, gcal: FakeCalendarGateway, clock: FakeClock) -> None:
    org.add_event(ALEX, event())
    org.add_event(ALEX, event("Swim", start="18:00", end="19:00", repeat="weekly"))
    assert org.clear_preview("events")["invitees"] == [ALEX, SAM]
    org.clear_all(ALEX, "events")
    assert gcal.events and org.list_events()["upcoming"] == []
    clock.advance(seconds=10)
    org.commit_due_clears()
    assert gcal.events == {}
    assert [h["action"] for h in org.list_history()[:3]] == ["removed", "removed", "cleared"]


def test_event_cancel_failure_leaves_a_retry_badge(org: Organizer, gcal: FakeCalendarGateway,
                                                   clock: FakeClock) -> None:
    e = org.add_event(ALEX, event())
    org.clear_all(ALEX, "events")
    clock.advance(seconds=10)
    gcal.fail_next = "delete"
    org.commit_due_clears()
    left = org.list_events()["upcoming"]
    assert left[0]["id"] == e["id"] and left[0]["sync_op"] == "delete" and left[0]["sync_status"] == "error"
    assert org.retry_sync(ALEX, e["id"]) is None
    assert org.list_events()["upcoming"] == [] and gcal.events == {}


def test_undo_during_event_commit_is_too_late(org: Organizer, clock: FakeClock) -> None:
    org.add_event(ALEX, event())
    pending = org.clear_all(ALEX, "events")
    clock.advance(seconds=10)
    original = org._cancel_events
    seen: list[str] = []

    def cancel_and_try_undo(who: str, ids: list[str]) -> list[str]:
        try:
            org.undo_clear(SAM, pending["id"])
        except TooLate as err:
            seen.append(err.message)
        return original(who, ids)

    org._cancel_events = cancel_and_try_undo  # type: ignore[method-assign]
    org.commit_due_clears()
    assert seen and seen[0].startswith("Too late to undo")


def test_window_open_during_a_hub_restart_commits_on_start(ctx: HubContext, clock: FakeClock) -> None:
    ctx.organizer.add_task(ALEX, task())
    ctx.organizer.clear_all(ALEX, "tasks")
    clock.advance(minutes=5)  # the hub was off when the window ended
    worker = Worker(ctx, tick=0.01)
    worker.start()
    try:
        import time

        for _ in range(200):
            if ctx.organizer.list_trash():
                break
            time.sleep(0.01)
    finally:
        worker.stop()
        worker.join(2)
    assert len(ctx.organizer.list_trash()) == 1
