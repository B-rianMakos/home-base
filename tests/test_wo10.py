"""WO-FS-010 sections 3 to 7: the family's details out of the code (AC-1 to AC-6, TX-1, TX-2), the personal-information
check (PC-a, PC-b), the upgrade from 1.7.0 keeping everything (KW-1) and a fresh hub (KW-3); plus the new screens."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SAM, SECOND, FakeClock, event
from fastapi.testclient import TestClient
from test_today import open_app

from family_scheduler import db
from family_scheduler.app import guide
from family_scheduler.hub.api import create_app
from family_scheduler.hub.context import HubContext, build_context
from family_scheduler.organizer import Organizer, OrganizerError
from family_scheduler.secrets_store import StaticKeyProvider

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_public  # noqa: E402

NEW = "new.person@example.com"


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def fresh(tmp_path: Path, clock: FakeClock) -> HubContext:
    """KW-3: a hub with an empty data folder."""
    (tmp_path / "fresh").mkdir()
    return build_context(tmp_path / "fresh", keys=StaticKeyProvider(), clock=clock, fake_calendar=True,
                         fake_mail=True)


# --- AC-1, AC-2: no address in the code ------------------------------------------------------------------------

def test_the_migrations_write_no_address() -> None:  # AC-2
    for _number, sql in db._migration_files():
        assert "@" not in sql.replace("'%_@_%'", "")


def test_a_fresh_hub_has_no_family_and_asks_for_one(fresh: HubContext) -> None:  # AC-1, AC-4, KW-3
    org = fresh.organizer
    assert org.needs_setup() and org.admin_email() == ""
    assert org.list_members() == [] and org.calendar_accounts() == []
    web = TestClient(create_app(fresh), client=("127.0.0.1", 50000))
    assert web.get("/api/setup").json() == {"needs_setup": True}
    with pytest.raises(OrganizerError):
        org.add_event(ADMIN, event())  # nothing can be saved until an admin is set


def test_set_up_your_family(fresh: HubContext) -> None:  # AC-4
    web = TestClient(create_app(fresh), client=("127.0.0.1", 50000))
    r = web.post("/api/setup", json={"admin": "Admin@Example.com ", "second": SECOND, "members": [ALEX, " ", SAM]})
    assert r.status_code == 200
    org = fresh.organizer
    assert org.admin_email() == ADMIN and not org.needs_setup()
    assert [a["email"] for a in org.calendar_accounts()] == [ADMIN, SECOND]  # the admin is always first (AC-5)
    assert {m["email"] for m in org.list_members()} == {ADMIN, SECOND, ALEX, SAM}
    assert web.post("/api/setup", json={"admin": NEW}).status_code == 400  # only once


def test_setup_needs_a_valid_admin(fresh: HubContext) -> None:
    org = fresh.organizer
    with pytest.raises(OrganizerError, match="full email address"):
        org.set_up_family("not an address")
    with pytest.raises(OrganizerError, match="different address"):
        org.set_up_family(ADMIN, ADMIN)
    org.set_up_family(ADMIN)
    assert [a["email"] for a in org.calendar_accounts()] == [ADMIN]  # the second is optional


def test_the_second_calendar_account_is_a_setting(org: Organizer) -> None:  # AC-5
    with pytest.raises(OrganizerError, match="Disconnect"):
        org.set_second_account(ALEX, NEW)  # connected: disconnect first
    org.set_account_status(SECOND, "not_connected")
    e = org.add_event(SECOND, event("Hockey"))  # organized by the second account (it fell back to the admin)
    assert e["organizer_email"] == ADMIN
    org.set_second_account(ALEX, NEW)
    assert [a["email"] for a in org.calendar_accounts()] == [ADMIN, NEW]
    assert NEW in [m["email"] for m in org.list_members()]
    with pytest.raises(OrganizerError, match="already the first"):
        org.set_second_account(ALEX, ADMIN)
    org.set_second_account(ALEX, "")
    assert [a["email"] for a in org.calendar_accounts()] == [ADMIN]


def test_an_account_that_organizes_upcoming_events_cant_be_removed(org: Organizer) -> None:  # AC-5
    org.add_event(SECOND, event("Swim"))
    org.set_account_status(SECOND, "not_connected")
    with pytest.raises(OrganizerError, match="organizes 1 upcoming event"):
        org.set_second_account(ALEX, "")


def test_the_second_account_is_set_on_the_hub_pc(ctx: HubContext) -> None:  # AC-5
    remote = TestClient(create_app(ctx), client=("192.168.1.20", 50000))
    assert remote.put("/api/settings/second-account", json={"email": NEW},
                      headers={"X-Member": ALEX}).status_code == 403


def test_who_sends_without_names(org: Organizer) -> None:  # AC-6
    assert org.sender_for(SECOND)["email"] == SECOND  # their own calendar account
    assert org.sender_for(ADMIN)["email"] == ADMIN
    assert org.sender_for(ALEX)["email"] == ADMIN  # everyone else: the admin's
    org.set_account_status(SECOND, "not_connected")
    assert org.sender_for(SECOND) == {"email": ADMIN, "preferred": SECOND, "fallback": True}  # GS-2 unchanged


def test_changing_the_admin_moves_the_first_account(org: Organizer) -> None:  # AC-5
    org.set_admin_email(ALEX, NEW, google_connected=False)
    assert [a["email"] for a in org.calendar_accounts()] == [NEW, SECOND]


# --- TX-1, TX-2: text from the hub's settings -------------------------------------------------------------------

def test_the_guide_fills_in_addresses_from_settings() -> None:  # TX-1
    settings = {"admin_email": ADMIN, "calendar_accounts": [{"email": ADMIN}, {"email": SECOND}]}
    texts = [step for section in guide.SECTIONS for _, steps in section.steps for step in steps]
    texts += [section.intro for section in guide.SECTIONS]
    assert any("{admin}" in t for t in texts) and any("{second}" in t for t in texts)
    filled = [guide.fill(t, settings) for t in texts]
    assert "Sign in to Google as admin@example.com." in filled
    assert not any("{admin}" in t or "{second}" in t for t in filled)
    assert guide.fill("Sign in to Google as {admin}.", {}) == "Sign in to Google as the admin account."
    assert "the second account" in guide.fill("select {second}", {"calendar_accounts": [{"email": ADMIN}]})


def test_the_calendar_accounts_card_names_no_one(qt_app: Any, ctx: HubContext, clock: FakeClock, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:  # TX-2
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("Family & Settings")
    status = window.current().google_status.text()
    assert status in ("Test mode: nobody is invited.",
                      f"Each calendar account's events go out from that account; everyone else's go out from "
                      f"{ADMIN}. Connect on the hub PC.")


# --- KW-1: the family's hub upgrades with everything kept ------------------------------------------------------

def test_upgrading_from_1_7_0_keeps_everything(tmp_path: Path, clock: FakeClock) -> None:  # KW-1, AC-3
    path = tmp_path / "family.db"
    conn = db.connect(path)
    conn.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY)")
    for number, sql in db._migration_files()[:4]:  # the database as 1.7.0 left it
        for statement in db._split_sql(sql):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migration (version) VALUES (?)", (number,))
    # the family's hub already holds its admin, both accounts and their tokens (in reality written by 1.0 to 1.7)
    conn.execute("INSERT INTO setting (key, value) VALUES ('admin_email', ?), ('digest_cap', '5')", (ADMIN,))
    for email in (ADMIN, SECOND, ALEX):
        conn.execute("INSERT INTO family_member (email, created_at) VALUES (?, 'n')", (email,))
    conn.execute("INSERT INTO calendar_account (email, position, status) VALUES (?, 1, 'connected'), "
                 "(?, 2, 'connected')", (ADMIN, SECOND))
    for name in (f"google_token:{ADMIN}", f"google_token:{SECOND}", "google_client", "gmail_app_password"):
        conn.execute("INSERT INTO secret (name, value_enc, updated_at) VALUES (?, x'01', 'n')", (name,))
    conn.execute("INSERT INTO event (id, title, date, start_time, end_time, gcal_event_id, sync_status, created_by, "
                 "created_at, updated_at, starts_at, ends_at, organizer_email) VALUES ('e1', 'Dentist', "
                 "'2026-11-03', '16:00', '17:00', 'abcdef12345', 'synced', ?, 'n', 'n', 'n', 'n', ?)", (ALEX, SECOND))
    conn.execute("INSERT INTO event_invitee (event_id, email, rsvp) VALUES ('e1', ?, 'accepted')", (ALEX,))
    conn.execute("INSERT INTO task (id, title, due_date, created_by, created_at, updated_at) "
                 "VALUES ('t1', 'Walk the dog', '', ?, 'n', 'n')", (ALEX,))
    conn.execute("INSERT INTO task (id, title, due_date, created_by, created_at, updated_at, deleted_at) "
                 "VALUES ('t2', 'Old hose', '2026-10-01', ?, 'n', 'n', '2026-10-26T00:00:00+00:00')", (ALEX,))
    conn.execute("INSERT INTO reminder (id, seq, message, created_by) "
                 "VALUES ('r1', 1, 'Wi-Fi password is on the fridge.', ?)", (ALEX,))
    conn.execute("INSERT INTO activity_entry (who, action, item_type, item_label, at) "
                 "VALUES (?, 'added', 'event', 'Dentist', 'n')", (ALEX,))
    conn.commit()
    assert db.migrate(conn) == 10
    conn.close()

    ctx = build_context(tmp_path, keys=StaticKeyProvider(), clock=clock, fake_calendar=False, fake_mail=True)
    org = ctx.organizer
    assert org.admin_email() == ADMIN and not org.needs_setup()
    assert {a["email"]: a["status"] for a in org.calendar_accounts()} == {ADMIN: "connected", SECOND: "connected"}
    names = {r[0] for r in ctx.conn.execute("SELECT name FROM secret")}
    assert names == {f"google_token:{ADMIN}", f"google_token:{SECOND}", "google_client", "gmail_app_password"}
    dentist = org.get_event("e1")
    assert (dentist["organizer_email"], dentist["source"], dentist["car"]) == (SECOND, "app", None)
    assert {i["email"]: i["rsvp"] for i in dentist["invitees"]} == {ALEX: "accepted"}
    assert [t["title"] for t in org.list_tasks()["ongoing"]] == ["Walk the dog"]
    assert [t["label"] for t in org.list_trash()] == ["Old hose"]
    assert [r["message"] for r in org.list_reminders()] == ["Wi-Fi password is on the fridge."]
    assert [h["label"] for h in org.list_history()] == ["Dentist"]
    assert org.setting("digest_cap") == "5"
    assert [c["name"] for c in org.list_cars()] == ["Subaru Crosstrek", "Chevrolet Cruze"]  # CR-13
    assert org.list_groceries()["to_buy"] == [] and org.list_groceries()["bought"] == []
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA foreign_key_check").fetchall() == []
    check.close()
    ctx.conn.close()


def test_the_runner_never_reruns_edited_migrations(tmp_path: Path) -> None:  # AC-3
    conn = db.connect(tmp_path / "x.db")
    db.migrate(conn)
    conn.execute("INSERT INTO setting (key, value) VALUES ('admin_email', ?)", (ADMIN,))
    conn.commit()
    assert db.migrate(conn) == 10  # nothing re-run, nothing rejected
    assert conn.execute("SELECT value FROM setting WHERE key = 'admin_email'").fetchone()[0] == ADMIN


# --- PC-a, PC-b: the personal-information check -----------------------------------------------------------------

def test_the_check_finds_each_kind_of_detail() -> None:  # PC-a
    at = "@"
    samples = {
        f"write to someone{at}gmail.com": "email address that isn't a placeholder",
        "the hub is at 192.168." + "68.10": "home network address",
        "C:" + "\\Users\\someone\\Documents": "Windows user folder path",
        "One" + "Drive\\Desktop": "OneDrive path",
        "1234567-" + "a" * 32 + ".apps.googleusercontent.com": "Google client ID",
        "GOCSPX-" + "x" * 20: "Google client secret",
        "ya29." + "y" * 30: "OAuth token",
        "-----BEGIN " + "PRIVATE KEY-----": "Private key",
    }
    for text, kind in samples.items():
        assert [f.kind for f in check_public.scan_text(text, "sample", None)] == [kind], text
    fine = f"admin{at}example.com, name{at}gmail.com, 192.168.1.20, 10.0.0.5, 127.0.0.1, C:\\ProgramData"
    assert check_public.scan_text(fine, "sample", None) == []


def test_the_private_list_is_whole_word_and_never_quoted(tmp_path: Path) -> None:  # PC-a
    words = tmp_path / "list.txt"
    words.write_text("# comment\nZebrafish\nsecret-place\n", encoding="utf-8")
    private = check_public.load_blocklist(words)
    assert private == ["Zebrafish", "secret-place"]
    found = check_public.scan_text("We live in Secret-Place.\nzebrafishes are fine", "f", private)
    assert [(f.line, f.kind) for f in found] == [(1, "a word from the private list")]
    assert "Secret" not in str(found[0])  # reported by file, line and kind only
    assert check_public.load_blocklist(tmp_path / "missing.txt") is None


def test_no_tracked_file_holds_personal_information() -> None:  # PC-a: the check passes with zero findings
    count, found = check_public.scan_files(check_public.load_blocklist())
    assert count > 100 and [str(f) for f in found] == []


# --- the new screens ------------------------------------------------------------------------------------------

def test_set_up_your_family_screen(qt_app: Any) -> None:  # AC-4
    from family_scheduler.app.dialogs import SetupFamilyDialog

    saved: list[dict[str, Any]] = []
    dlg = SetupFamilyDialog(lambda body: saved.append(body))
    assert not dlg.save_button.isEnabled()  # nothing until an admin is set
    dlg.admin.setText(ADMIN)
    dlg.second.setText(SECOND)
    dlg.members.setPlainText(f"{ALEX}\n{SAM}, {NEW}")
    assert dlg.save_button.isEnabled()
    dlg._try_save()
    assert saved == [{"admin": ADMIN, "second": SECOND, "members": [ALEX, SAM, NEW]}]


def test_groceries_page_and_badge(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock, tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:  # GR-1, GR-7, GR-11
    from test_today import page_text

    window = open_app(ctx, tmp_path, monkeypatch, clock)
    titles = [window.sidebar.nav.item(i).text() for i in range(window.sidebar.nav.count())]
    assert titles.index("Groceries") == titles.index("Reminders") + 1
    window.show_view("Groceries")
    view = window.current()
    view.entry.setText("Milk (2 L)")
    view.add_new()
    view.entry.setText("Bread")
    view.add_new()
    bread = next(i for i in org.list_groceries()["to_buy"] if i["text"] == "Bread")
    view.on_action(bread["id"], "tick")
    assert page_text(view) == ["# Food", "Milk (2 L)", "Bread (faded)", "# Items", "- No items on the list."]
    window.update_badges()
    assert window.sidebar.badges["Groceries"][0] == 1  # GR-7: items still to buy


def test_the_cars_card_and_sync_buttons(qt_app: Any, ctx: HubContext, clock: FakeClock, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:  # CR-1, GY-2a
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("Family & Settings")
    settings = window.current()
    assert settings.car_count.value() == 2
    assert [n.text() for n in settings.car_names[:2]] == ["Subaru Crosstrek", "Chevrolet Cruze"]
    settings.car_count.setValue(3)
    assert settings.car_names[2].text() == "Car 3"
    settings.save_cars()
    assert [c["name"] for c in ctx.organizer.list_cars()][2] == "Car 3"
    from PySide6.QtWidgets import QPushButton

    for page in ("Events", "Calendar"):
        window.show_view(page)
        labels = [b.text() for b in window.current().findChildren(QPushButton)]
        assert "Refresh" in labels  # labelled, not an icon only
    window.show_view("Calendar")
    assert [b.text() for b in window.current().findChildren(QPushButton)].count("Refresh") == 2  # header and panel
