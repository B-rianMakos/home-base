"""WO-FS-006 section 3: the Google connection fix (OA-2, OA-3, CB-2 to CB-8)."""

from __future__ import annotations

import json

import pytest
from conftest import ADMIN, SECOND
from fastapi.testclient import TestClient

from family_scheduler.app.accounts import HUB_ONLY, NEED_CLIENT, SELECT, TEST_MODE, account_buttons
from family_scheduler.google_calendar import NOT_A_CLIENT, WEB_CLIENT, CalendarError, check_client_file
from family_scheduler.hub import api
from family_scheduler.hub.api import create_app, is_this_pc
from family_scheduler.hub.context import HubContext

DESKTOP = json.dumps({"installed": {"client_id": "123.apps.googleusercontent.com", "client_secret": "abc",
                                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                    "token_uri": "https://oauth2.googleapis.com/token",
                                    "redirect_uris": ["http://localhost"]}})
WEB = json.dumps({"web": {"client_id": "123.apps.googleusercontent.com", "client_secret": "abc"}})
H = {"X-Member": ADMIN}


def client(ctx: HubContext, host: str) -> TestClient:
    return TestClient(create_app(ctx), client=(host, 50000))


# --- OA-2: the client file ---------------------------------------------------------------------------------

def test_a_desktop_app_client_file_is_accepted() -> None:
    assert check_client_file(DESKTOP)["installed"]["client_id"].endswith("googleusercontent.com")


def test_a_web_client_file_is_refused_with_a_clear_message() -> None:
    with pytest.raises(CalendarError) as caught:
        check_client_file(WEB)
    assert str(caught.value) == WEB_CLIENT
    assert "create an OAuth client of type Desktop app" in WEB_CLIENT


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"installed": {}}', '{"other": 1}'])
def test_any_other_file_gets_a_clear_error(text: str) -> None:
    with pytest.raises(CalendarError) as caught:
        check_client_file(text)
    assert str(caught.value) == NOT_A_CLIENT


# --- OA-3: chosen once, kept on the hub, only the hub PC can set or read it -----------------------------------

def test_the_client_file_is_kept_encrypted_on_the_hub(ctx: HubContext) -> None:
    local, remote = client(ctx, "127.0.0.1"), client(ctx, "192.168.1.20")
    assert local.get("/api/settings", headers=H).json()["google_client_set"] is False
    assert local.get("/api/google/client", headers=H).status_code == 404
    r = local.put("/api/google/client", json={"client_json": WEB}, headers=H)
    assert r.status_code == 400 and r.json()["detail"] == WEB_CLIENT
    assert remote.put("/api/google/client", json={"client_json": DESKTOP}, headers=H).status_code == 403
    assert local.put("/api/google/client", json={"client_json": DESKTOP}, headers=H).status_code == 200
    assert local.get("/api/settings", headers=H).json()["google_client_set"] is True
    assert local.get("/api/google/client", headers=H).json()["client"]["installed"]["client_secret"] == "abc"
    assert remote.get("/api/google/client", headers=H).status_code == 403  # never leaves the hub PC
    stored = ctx.conn.execute("SELECT value_enc FROM secret WHERE name = 'google_client'").fetchone()[0]
    assert b"googleusercontent" not in stored  # encrypted at rest


# --- CB-2: the hub PC by any of its own addresses -------------------------------------------------------------

def test_the_hub_pc_is_recognised_by_its_network_address(ctx: HubContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "own_addresses", lambda: frozenset({"192.168.1.20", "fe80::1"}))
    assert is_this_pc("127.0.0.1") and is_this_pc("::1") and is_this_pc("192.168.1.20")
    assert is_this_pc("::ffff:192.168.1.20")
    assert not is_this_pc("10.0.0.5") and not is_this_pc("testclient")
    by_address = client(ctx, "192.168.1.20")
    assert by_address.get("/api/settings", headers=H).json()["on_hub_pc"] is True
    assert by_address.put("/api/google/client", json={"client_json": DESKTOP}, headers=H).status_code == 200
    assert client(ctx, "10.0.0.5").get("/api/settings", headers=H).json()["on_hub_pc"] is False


def test_own_addresses_include_this_pc() -> None:
    assert api.own_addresses()  # at least the address used on the network, or the host's own


# --- CB-3: never greyed out without a reason ------------------------------------------------------------------

LIVE = {"google_mode": "google", "on_hub_pc": True, "google_client_set": True}
ADMIN_ACCOUNT = {"email": ADMIN, "status": "not_connected"}


@pytest.mark.parametrize(("settings", "selected", "reason"), [
    (LIVE | {"google_mode": "fake"}, ADMIN_ACCOUNT, TEST_MODE),
    (LIVE | {"on_hub_pc": False}, ADMIN_ACCOUNT, HUB_ONLY),
    (LIVE | {"google_client_set": False}, ADMIN_ACCOUNT, NEED_CLIENT),
    (LIVE, None, SELECT),
])
def test_each_reason_the_buttons_cant_be_used(settings: dict[str, object], selected: dict[str, str] | None,
                                              reason: str) -> None:
    state = account_buttons(settings, selected)
    assert not state.connect and state.reason == reason


def test_buttons_when_they_can_be_used() -> None:
    assert account_buttons(LIVE, ADMIN_ACCOUNT) == account_buttons(LIVE, ADMIN_ACCOUNT)
    ready = account_buttons(LIVE, ADMIN_ACCOUNT)
    assert ready.connect and not ready.disconnect and ready.reason == "" and ready.connect_label == "Connect"
    broken = account_buttons(LIVE, {"email": SECOND, "status": "needs_reconnect"})
    assert broken.connect and broken.disconnect and broken.connect_label == "Reconnect"
    connected = account_buttons(LIVE, {"email": SECOND, "status": "connected"})
    assert not connected.connect and connected.disconnect and connected.reason == f"{SECOND} is connected."
    no_file = account_buttons(LIVE | {"google_client_set": False}, {"email": SECOND, "status": "connected"})
    assert no_file.disconnect  # disconnecting never needs the client file


# --- CB-4, CB-5: Connect and Disconnect against the fake Google -------------------------------------------------

def test_connect_and_disconnect_over_the_api(ctx: HubContext) -> None:
    local = client(ctx, "127.0.0.1")
    ctx.disconnect_google(SECOND)
    ctx.revoked.clear()
    r = local.post("/api/google/connect", json={"token_json": '{"refresh_token": "r"}', "account_email": ADMIN,
                                                "calendar_account": SECOND}, headers=H)
    assert r.status_code == 400 and f"choose {SECOND}" in r.json()["detail"]  # wrong account refused
    r = local.post("/api/google/connect", json={"token_json": '{"refresh_token": "r"}', "account_email": SECOND,
                                                "calendar_account": SECOND}, headers=H)
    assert r.status_code == 200
    accounts = {a["email"]: a["status"] for a in local.get("/api/settings", headers=H).json()["calendar_accounts"]}
    assert accounts[SECOND] == "connected"
    r = local.post("/api/google/disconnect", json={"email": SECOND}, headers=H)
    assert r.json() == {"ok": True, "note": None}
    accounts = {a["email"]: a["status"] for a in local.get("/api/settings", headers=H).json()["calendar_accounts"]}
    assert accounts[SECOND] == "not_connected"
    assert ctx.secrets.get(f"google_token:{SECOND}") is None and ctx.revoked == [SECOND]  # removed and revoked
    remote = client(ctx, "192.168.1.20")
    assert remote.post("/api/google/disconnect", json={"email": ADMIN}, headers=H).status_code == 403


def test_disconnect_still_removes_the_token_when_google_cant_be_told(ctx: HubContext,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from family_scheduler.hub import context as context_module

    def refuse(token_json: str) -> None:
        raise CalendarError("Couldn't reach Google to revoke the access. It was still removed from the hub.")

    monkeypatch.setattr(context_module, "revoke_token", refuse)
    ctx.fake_calendar = None  # as on a real hub
    ctx.secrets.put(f"google_token:{SECOND}", '{"refresh_token": "r"}')
    note = ctx.disconnect_google(SECOND)
    assert note is not None and "still removed" in note
    assert ctx.secrets.get(f"google_token:{SECOND}") is None
