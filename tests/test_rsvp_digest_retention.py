"""RSVP refresh (RV-1 to RV-5), digest on request (DG-4 to DG-11, FR-8) and the 30-day purge (RT-1 to RT-6)."""

from __future__ import annotations

import pytest
from conftest import ALEX, SAM, FakeClock, event, task

from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.hub.context import HubContext
from family_scheduler.hub.worker import prune_backups, run_maintenance
from family_scheduler.mailer import RecordingMailer
from family_scheduler.organizer import DIGEST_DAILY_CAP, ExternalFailure, LimitReached, Organizer

# --- RSVPs ------------------------------------------------------------------------------------


def test_refresh_reads_replies_and_records_the_time(org: Organizer, gcal: FakeCalendarGateway,
                                                    clock: FakeClock) -> None:
    e = org.add_event(ALEX, event())
    gid = next(iter(gcal.events))
    gcal.respond(gid, ALEX, "accepted")
    gcal.respond(gid, SAM, "declined")
    assert org.get_event(e["id"])["rsvp_counts"]["accepted"] == 0  # nothing refreshes on its own
    result = org.refresh_rsvps(SAM)
    assert result["refreshed"] == 1 and result["rsvp_refreshed_at"] == "2026-10-27T12:00:00+00:00"
    counts = org.get_event(e["id"])["rsvp_counts"]
    assert counts["accepted"] == 1 and counts["declined"] == 1
    assert all(h["action"] != "edited" for h in org.list_history())  # RV-5: not logged


def test_refresh_failure_says_so(org: Organizer, gcal: FakeCalendarGateway) -> None:
    org.add_event(ALEX, event())
    gcal.fail_next = "get"
    with pytest.raises(ExternalFailure, match="partly updated"):
        org.refresh_rsvps(ALEX)
    assert org.setting("rsvp_refreshed_at") is None


def test_worker_never_asks_google_for_replies(ctx: HubContext, gcal: FakeCalendarGateway) -> None:
    ctx.organizer.add_event(ALEX, event())
    gcal.calls.clear()
    ctx.organizer.commit_due_clears()
    run_maintenance(ctx)
    assert gcal.calls == []


# --- digest -----------------------------------------------------------------------------------


def test_digest_goes_to_the_member_who_asked(org: Organizer, mail: RecordingMailer) -> None:
    org.add_event(ALEX, event("School run", day="2026-10-27", start="15:00", end="15:30"))
    org.add_task(ALEX, task("Renew passport", due="2026-10-20", priority="high"))
    org.add_task(ALEX, task("Book tires", due="2026-10-30", priority="high"))
    result = org.send_digest(SAM)
    assert result == {"sent": True, "message": f"Digest sent to {SAM}."}
    assert len(mail.sent) == 1 and mail.sent[0]["to"] == SAM
    text = mail.sent[0]["text"]
    assert "Today's events" in text and "School run, 3:00 pm" in text
    assert "Overdue tasks" in text and "High-priority tasks" in text and "Book tires" in text
    assert "Waiting on RSVPs" in text and "haven't been refreshed" in text
    assert mail.sent[0]["subject"] == "Home Base: your digest for Tuesday, October 27 · 1 event, 1 overdue task"
    assert org.list_history()[0]["action"] == "digest sent" and org.list_history()[0]["who"] == SAM


def test_empty_digest_says_nothing_scheduled(org: Organizer, mail: RecordingMailer) -> None:
    org.send_digest(ALEX)
    assert "Nothing scheduled today." in mail.sent[0]["text"]


def test_repeat_clicks_within_a_minute_send_one_email(org: Organizer, mail: RecordingMailer,
                                                      clock: FakeClock) -> None:
    org.send_digest(ALEX)
    second = org.send_digest(ALEX)
    assert second["sent"] is False and len(mail.sent) == 1
    clock.advance(seconds=61)
    org.send_digest(ALEX)
    assert len(mail.sent) == 2


def test_daily_cap_blocks_sends(org: Organizer, mail: RecordingMailer, clock: FakeClock) -> None:
    for _ in range(DIGEST_DAILY_CAP):
        org.send_digest(ALEX)
        clock.advance(seconds=61)
    assert org.today_view()["digest"]["limit_reached"] is True
    with pytest.raises(LimitReached, match="Daily digest limit reached"):
        org.send_digest(SAM)
    clock.advance(hours=17)  # next Toronto day
    org.send_digest(SAM)
    assert len(mail.sent) == DIGEST_DAILY_CAP + 1


def test_gmail_refusal_is_reported_and_not_counted(org: Organizer, mail: RecordingMailer) -> None:
    mail.fail_with = "Gmail didn't accept the app password."
    with pytest.raises(ExternalFailure, match="app password"):
        org.send_digest(ALEX)
    assert org.today_view()["digest"]["sent_today"] == 0
    org.send_digest(ALEX)
    assert len(mail.sent) == 1


# --- 30-day retention -------------------------------------------------------------------------


def test_purge_removes_only_what_is_older_than_30_days(org: Organizer, clock: FakeClock) -> None:
    done = org.add_task(ALEX, task("Done long ago"))
    org.complete_task(ALEX, done["id"])
    trashed = org.add_task(ALEX, task("Trashed"))
    org.remove_task(ALEX, trashed["id"])
    note = org.add_reminder(ALEX, "Trashed note")
    org.remove_reminder(ALEX, note["id"])
    org.add_event(ALEX, event("Past one-off", day="2026-10-28"))
    org.add_event(ALEX, event("Weekly", day="2026-10-28", start="09:00", end="10:00", repeat="weekly"))
    org.add_task(ALEX, task("Still open", due="2026-10-01"))
    org.add_reminder(ALEX, "Kept forever")
    clock.advance(days=29)
    assert sum(org.purge().values()) == 0
    clock.advance(days=3)  # the one-off ended Oct 28 at 5 pm; 30 days later it goes
    counts = org.purge()
    assert counts["done_tasks"] == 1 and counts["trash_tasks"] == 1 and counts["trash_reminders"] == 1
    assert counts["past_events"] == 1 and counts["activity"] > 0
    assert [t["title"] for t in org.list_tasks()["overdue"]] == ["Still open"]
    assert [e["title"] for e in org.list_events()["upcoming"]] == ["Weekly"]
    assert [r["message"] for r in org.list_reminders()] == ["Kept forever"]


def test_maintenance_runs_nightly_and_after_a_missed_night(ctx: HubContext, clock: FakeClock) -> None:
    org = ctx.organizer
    assert org.maintenance_due()  # first start
    run_maintenance(ctx)
    assert not org.maintenance_due()
    assert len(list((ctx.data_dir / "backups").glob("family-*.db"))) == 1
    clock.advance(hours=16)  # 0:00 Toronto next day: wait for 3:00 am
    assert not org.maintenance_due()
    clock.advance(hours=3)
    assert org.maintenance_due()
    run_maintenance(ctx)
    clock.advance(days=3)  # hub off for days, back on at 11 am
    assert org.maintenance_due()


def test_backups_keep_the_newest_seven(tmp_path: object) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path)) / "b"
    folder.mkdir()
    for day in range(1, 11):
        (folder / f"family-2026-11-{day:02d}.db").write_bytes(b"x")
    prune_backups(folder)
    assert sorted(p.name for p in folder.iterdir())[0] == "family-2026-11-04.db"
    assert len(list(folder.iterdir())) == 7


def test_today_includes_events_that_already_finished_today(org: Organizer, clock: FakeClock) -> None:
    org.add_event(ALEX, event("Breakfast", day="2026-10-27", start="07:00", end="07:30"))
    org.add_event(ALEX, event("School run", day="2026-10-27", start="15:00", end="15:30"))
    clock.advance(hours=10)  # 6:00 pm Toronto: both are over
    assert [e["title"] for e in org.today_view()["events"]] == ["Breakfast", "School run"]
