"""WO-FS-008 section 6: moving a hub's data in (PS-2, MH-2), the hub's own key, Hub status (PS-9), the Reconnect
notice (PS-7) and staying set up between uses (PS-5)."""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SAM, SECOND, FakeClock, event, set_up, task
from cryptography.fernet import Fernet

from family_scheduler.app.accounts import hub_status_line, reconnect_notice
from family_scheduler.hub.context import HubContext, build_context
from family_scheduler.hub.move import ImportFailed, import_data, source_key
from family_scheduler.organizer import Organizer
from family_scheduler.secrets_store import KeyFileProvider, StaticKeyProvider, seal, unseal


def test_the_key_file_is_sealed_to_this_pc_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "hub.key"
    key = KeyFileProvider(path).get_key()
    Fernet(key)  # a usable key
    assert path.read_bytes() != key  # never stored as plain text on Windows
    assert unseal(path.read_bytes()) == key
    assert KeyFileProvider(path).get_key() == key  # the same key after a restart (PS-5, PS-6)


def test_an_older_key_is_adopted_so_old_secrets_stay_readable(tmp_path: Path) -> None:
    old = Fernet.generate_key()

    class Keyring:
        def existing_key(self) -> bytes | None:
            return old

    assert KeyFileProvider(tmp_path / "hub.key", adopt=Keyring()).get_key() == old  # type: ignore[arg-type]
    assert unseal(seal(old)) == old


@pytest.fixture
def test_hub(tmp_path: Path, clock: FakeClock) -> tuple[HubContext, bytes]:
    """A test hub with everything PS-2 lists, its secrets encrypted with its own key."""
    key = Fernet.generate_key()
    (tmp_path / "test-hub").mkdir()
    ctx = build_context(tmp_path / "test-hub", keys=StaticKeyProvider(key), clock=clock, fake_calendar=True,
                        fake_mail=True)
    set_up(ctx)
    org = ctx.organizer
    org.add_member(ADMIN, ALEX)
    org.add_member(ADMIN, SAM)
    e = org.add_event(SECOND, event("Dentist", invitees=[ADMIN, SECOND, ALEX]))
    org.add_event(ALEX, event("Swim", day="2026-11-04", repeat="weekly"))
    org.add_task(ALEX, task("Recycling"))
    org.remove_task(ALEX, org.add_task(ALEX, task("Old hose"))["id"])  # into Trash
    org.add_reminder(ALEX, "Wi-Fi password is on the fridge.")
    ctx.secrets.put("google_client", '{"installed": {}}')
    ctx.secrets.put(f"google_token:{ADMIN}", "admin-token")
    ctx.secrets.put(f"google_token:{SECOND}", "second-token")
    ctx.secrets.put("gmail_app_password", "app-password")
    ctx.conn.execute("UPDATE event_invitee SET rsvp = 'accepted' WHERE event_id = ? AND email = ?", (e["id"], ALEX))
    ctx.conn.execute("INSERT INTO setting (key, value) VALUES ('digest_cap', '5')")
    ctx.conn.close()
    return ctx, key


def opened(folder: Path, key: bytes, clock: FakeClock) -> HubContext:
    folder.mkdir(exist_ok=True)
    return build_context(folder, keys=StaticKeyProvider(key), clock=clock, fake_calendar=True, fake_mail=True)


def test_everything_is_moved_into_the_installed_hub(tmp_path: Path, test_hub: tuple[HubContext, bytes],
                                                    clock: FakeClock) -> None:  # PS-2
    source, old_key = test_hub
    target, new_key = tmp_path / "installed", Fernet.generate_key()
    before = opened(target, new_key, clock)  # the freshly installed hub, already running once
    before.conn.close()
    report = import_data(source.data_dir, target, old_key, StaticKeyProvider(new_key))
    assert report.counts == {"family members": 4, "events": 2, "tasks": 1, "reminders": 1, "items in Trash": 1,
                             "History entries": report.counts["History entries"]}
    assert report.counts["History entries"] > 0
    assert sorted(report.secrets_moved) == sorted(["google_client", f"google_token:{ADMIN}",
                                                   f"google_token:{SECOND}", "gmail_app_password"])
    assert report.secrets_lost == [] and report.backup is not None and report.backup.exists()

    hub = opened(target, new_key, clock)  # re-encrypted with the installed hub's own key
    org = hub.organizer
    assert hub.secrets.get(f"google_token:{SECOND}") == "second-token"
    assert hub.secrets.get("gmail_app_password") == "app-password"
    assert hub.secrets.get("google_client") == '{"installed": {}}'
    dentist = next(e for e in org.list_events()["upcoming"] if e["title"] == "Dentist")
    assert dentist["organizer_email"] == SECOND  # the organizer
    google_id = hub.conn.execute("SELECT gcal_event_id FROM event WHERE id = ?", (dentist["id"],)).fetchone()[0]
    # the Google event id, so later edits reach the same event in Google
    assert source.fake_calendar is not None and google_id in source.fake_calendar.events
    assert {i["email"]: i["rsvp"] for i in dentist["invitees"]}[ALEX] == "accepted"  # replies
    assert [t["label"] for t in org.list_trash()] == ["Old hose"]
    assert org.setting("digest_cap") == "5"
    assert {a["email"]: a["status"] for a in org.calendar_accounts()} == {ADMIN: "connected", SECOND: "connected"}
    assert "Kept: the Google client file" in report.summary()
    hub.conn.close()


def test_a_secret_that_cant_be_read_is_listed_to_enter_again(tmp_path: Path, test_hub: tuple[HubContext, bytes],
                                                              clock: FakeClock) -> None:
    source, _ = test_hub
    new_key = Fernet.generate_key()
    report = import_data(source.data_dir, tmp_path / "installed", Fernet.generate_key(), StaticKeyProvider(new_key))
    assert len(report.secrets_lost) == 4 and report.secrets_moved == []
    assert "Enter again in Family & Settings: " in report.summary()
    # read as stored: a test-mode hub would mark both accounts connected again when it starts
    with closing(sqlite3.connect(tmp_path / "installed" / "family.db")) as conn:
        status = dict(conn.execute("SELECT email, status FROM calendar_account").fetchall())
        assert status == {ADMIN: "not_connected", SECOND: "not_connected"}  # said plainly, never dropped (PS-7)
        assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 2  # the data itself still came across


@pytest.mark.skipif(sys.platform != "win32", reason="the key file is sealed with Windows DPAPI")
def test_a_key_sealed_to_another_pc_still_moves_the_data(tmp_path: Path, test_hub: tuple[HubContext, bytes]) -> None:
    source, _ = test_hub  # MH-1: a hub folder copied from another PC, with that PC's key file
    (source.data_dir / "keys").mkdir()
    (source.data_dir / "keys" / "hub.key").write_bytes(b"DPAPI1:" + b"\x01" * 200)
    assert source_key(source.data_dir) is None
    report = import_data(source.data_dir, tmp_path / "installed", None, StaticKeyProvider())
    assert report.counts["events"] == 2 and len(report.secrets_lost) == 4


def test_nothing_changes_without_a_database(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ImportFailed, match="family.db is missing"):
        import_data(tmp_path / "empty", tmp_path / "installed", None)
    assert not (tmp_path / "installed" / "family.db").exists()


def test_a_running_source_hub_is_copied_consistently(tmp_path: Path, test_hub: tuple[HubContext, bytes]) -> None:
    source, key = test_hub
    holder = sqlite3.connect(source.data_dir / "family.db")  # the test hub still has it open
    try:
        report = import_data(source.data_dir, tmp_path / "installed", key, StaticKeyProvider())
    finally:
        holder.close()
    assert report.counts["events"] == 2


def test_hub_status_line() -> None:  # PS-9
    status: dict[str, Any] = {"test_hub": False, "started_with_windows": True,
                              "data_dir": r"C:\ProgramData\FamilyScheduler", "last_backup": "2026-10-30T06:00:00+00:00"}
    assert hub_status_line(status, lambda s: "Fri Oct 30, 2:00 am") == (
        r"Home hub running · started with Windows · data in C:\ProgramData\FamilyScheduler · "
        "last backup Fri Oct 30, 2:00 am")
    status |= {"test_hub": True, "started_with_windows": False, "last_backup": None}
    assert hub_status_line(status, str).startswith("Test hub running · not started with Windows")
    assert hub_status_line(status, str).endswith("no backup yet")
    assert hub_status_line(None, str) == ""


def test_today_says_when_google_stops_accepting_an_account(org: Organizer) -> None:  # PS-7
    assert org.today_view()["accounts_needing_reconnect"] == [] and reconnect_notice([]) == ""
    org.set_account_status(SECOND, "needs_reconnect")
    needing = org.today_view()["accounts_needing_reconnect"]
    assert needing == [SECOND]
    assert reconnect_notice(needing).startswith(f"Google no longer accepts the sign-in for {SECOND}.")
