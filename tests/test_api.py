"""The client–hub API: member header, home-network guard, hub-only secrets, error mapping."""

from __future__ import annotations

from conftest import ADMIN, ALEX, SAM, SECOND, FakeClock
from fastapi.testclient import TestClient

from family_scheduler.hub.api import create_app, is_home_network
from family_scheduler.hub.context import HubContext

EVENT = {"title": "Dentist", "date": "2026-11-03", "start_time": "16:00", "end_time": "17:00",
         "location": "Smile Dental", "repeat": "none", "invitees": [ADMIN, ALEX, SAM]}


def client(ctx: HubContext, host: str = "192.168.1.20") -> TestClient:
    return TestClient(create_app(ctx), client=(host, 50000))


def test_home_network_check() -> None:
    assert is_home_network("192.168.1.20") and is_home_network("10.0.0.5") and is_home_network("127.0.0.1")
    assert not is_home_network("8.8.8.8") and not is_home_network("testclient")


def test_internet_addresses_are_refused(ctx: HubContext) -> None:
    r = client(ctx, "203.0.113.9").get("/api/health")
    assert r.status_code == 403 and "home network" in r.json()["detail"]


def test_who_is_using_the_app_needs_no_password(ctx: HubContext) -> None:
    c = client(ctx)
    assert {m["email"] for m in c.get("/api/members").json()} == {ADMIN, ALEX, SAM, SECOND}
    assert c.get("/api/tasks").status_code == 403  # no member chosen
    assert c.get("/api/tasks", headers={"X-Member": "stranger@example.com"}).status_code == 403
    r = c.post("/api/tasks", json={"title": "Recycling", "due_date": "2026-10-29"}, headers={"X-Member": SAM})
    assert r.status_code == 200
    history = c.get("/api/history", headers={"X-Member": ALEX}).json()
    assert history[0]["who"] == SAM  # WU-4


def test_changes_counter_moves_after_a_change(ctx: HubContext) -> None:
    c = client(ctx)
    before = c.get("/api/changes").json()["seq"]
    c.post("/api/reminders", json={"message": "Hello"}, headers={"X-Member": ALEX})
    assert c.get("/api/changes").json()["seq"] > before


def test_conflict_is_a_409_with_the_overlapping_events(ctx: HubContext) -> None:
    c = client(ctx)
    h = {"X-Member": ALEX}
    assert c.post("/api/events", json=EVENT, headers=h).status_code == 200
    r = c.post("/api/events", json=EVENT | {"title": "Interview", "start_time": "16:30"}, headers=h)
    assert r.status_code == 409 and r.json()["conflicts"][0]["title"] == "Dentist"
    r = c.post("/api/events", json=EVENT | {"title": "Interview", "start_time": "16:30",
                                            "acknowledge_conflicts": True}, headers=h)
    assert r.status_code == 200


def test_secrets_can_only_be_set_on_the_hub_pc(ctx: HubContext) -> None:
    h = {"X-Member": ADMIN}
    remote = client(ctx).put("/api/mail/app-password", json={"password": "abcd efgh ijkl mnop"}, headers=h)
    assert remote.status_code == 403
    local = client(ctx, "127.0.0.1")
    assert local.put("/api/mail/app-password", json={"password": "short"}, headers=h).status_code == 400
    assert local.put("/api/mail/app-password", json={"password": "abcd efgh ijkl mnop"}, headers=h).status_code == 200
    stored = ctx.conn.execute("SELECT value_enc FROM secret WHERE name = 'gmail_app_password'").fetchone()[0]
    assert b"abcdefghijklmnop" not in stored  # encrypted at rest
    assert ctx.secrets.get("gmail_app_password") == "abcdefghijklmnop"
    settings = client(ctx).get("/api/settings", headers=h).json()
    assert "abcd" not in str(settings) and settings["on_hub_pc"] is False


def test_google_connect_requires_the_admin_account(ctx: HubContext) -> None:
    local = client(ctx, "127.0.0.1")
    h = {"X-Member": ADMIN}
    r = local.post("/api/google/connect", json={"token_json": "{}", "account_email": ALEX}, headers=h)
    assert r.status_code == 400 and ADMIN in r.json()["detail"]
    r = local.post("/api/google/connect", json={"token_json": "{\"x\": 1}", "account_email": ADMIN}, headers=h)
    assert r.status_code == 200


def test_clear_undo_and_trash_over_the_api(ctx: HubContext, clock: FakeClock) -> None:
    c = client(ctx)
    h = {"X-Member": ALEX}
    c.post("/api/tasks", json={"title": "A", "due_date": "2026-10-29"}, headers=h)
    pending = c.post("/api/clear/tasks", headers=h).json()
    assert c.post(f"/api/clear/{pending['id']}/undo", headers=h).status_code == 200
    pending = c.post("/api/clear/tasks", headers=h).json()
    clock.advance(seconds=10)
    ctx.organizer.commit_due_clears()
    r = c.post(f"/api/clear/{pending['id']}/undo", headers=h)
    assert r.status_code == 409 and r.json()["detail"].startswith("Too late")
    trash = c.get("/api/trash", headers=h).json()
    r = c.post("/api/trash/restore", json={"items": [{"type": "task", "id": trash[0]["id"]}]}, headers=h)
    assert r.json() == {"restored": 1}


def test_digest_endpoint_and_cap_status(ctx: HubContext) -> None:
    c = client(ctx)
    r = c.post("/api/digest/send", headers={"X-Member": SAM})
    assert r.json()["sent"] is True
    assert c.get("/api/today", headers={"X-Member": SAM}).json()["digest"]["sent_today"] == 1


def test_no_pdf_routes_anywhere(ctx: HubContext) -> None:
    app = create_app(ctx)
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not [p for p in paths if "pdf" in p.lower()]  # NP-1


def test_update_check(ctx: HubContext, tmp_path: object) -> None:
    updates = ctx.data_dir / "updates"
    updates.mkdir()
    (updates / "FamilySchedulerSetup-1.0.0.exe").write_bytes(b"old")
    (updates / "FamilySchedulerSetup-1.2.0.exe").write_bytes(b"new")
    c = client(ctx)
    assert c.get("/api/update/latest").json() == {"version": "1.2.0", "available": True,
                                                  "file": "FamilySchedulerSetup-1.2.0.exe"}
    assert c.get("/api/update/download").content == b"new"
