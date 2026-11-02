"""PC app entry point: find the hub, check for updates, pick the member, open the window."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog

from family_scheduler import __version__
from family_scheduler.app import dialogs
from family_scheduler.app.main_window import MainWindow
from family_scheduler.app.style import apply_theme
from family_scheduler.client.discovery import find_hub, is_hub, normalise_address
from family_scheduler.client.hub_client import UNREACHABLE, HubClient, HubError, HubUnreachable
from family_scheduler.client.local_settings import LocalSettings
from family_scheduler.config import hub_port, machine_role


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


def locate_hub(settings: LocalSettings) -> str | None:
    """Hub PC: itself. Other PCs: last address, then discovery, then ask (PC-4, PC-8)."""
    if os.environ.get("FS_HUB"):
        return normalise_address(os.environ["FS_HUB"])
    if machine_role() == "hub":
        return f"127.0.0.1:{hub_port()}"
    saved = settings.get("hub_address")
    if saved and is_hub(saved):
        return saved
    message = UNREACHABLE
    while True:
        found = find_hub()
        if found and is_hub(found):
            settings.set("hub_address", found)
            return found
        dlg = dialogs.HubAddressDialog(
            f"{message}\n\nIf it's on, type the hub PC's name or IP address. On the hub PC, it's shown in "
            "Family & Settings under This PC.")
        if not dlg.exec():
            return None
        if dlg.search_again:
            continue
        typed = dlg.address.text()
        if typed:
            address = normalise_address(typed)
            if is_hub(address):
                settings.set("hub_address", address)
                return address
            message = f"No hub answered at {address}."


def offer_update(client: HubClient) -> bool:
    """PC-9: keep every PC on the hub's version. Returns True if the installer was started."""
    try:
        latest = client.update_latest()
    except (HubUnreachable, HubError):
        return False
    if not latest.get("available") or _version(latest["version"]) <= _version(__version__):
        return False
    if not dialogs.confirm(None, "Update available", f"Home Base {latest['version']} is on the hub.",
                           "Install update", danger=False,
                           detail=f"This PC has {__version__}. The app closes while the update installs. "
                                  "If Windows shows “Windows protected your PC”, click More info, "
                                  "then Run anyway."):
        return False
    target = Path(tempfile.gettempdir()) / latest["file"]
    try:
        client.download_update(target)
    except (HubUnreachable, Exception) as err:  # noqa: BLE001 - any failure: carry on with this version
        dialogs.error(None, f"The update couldn't be downloaded: {err}")
        return False
    os.startfile(target)  # Windows only
    return True


def set_up_family(client: HubClient) -> bool:
    """WO-FS-010 AC-4: a new hub has no admin yet, so the family's details come first. False if cancelled."""
    if not client.setup_state().get("needs_setup"):
        return True
    dlg = dialogs.SetupFamilyDialog(lambda body: client.set_up(body["admin"], body["second"], body["members"]))
    return dlg.exec() == QDialog.DialogCode.Accepted


def pick_member(client: HubClient, settings: LocalSettings, force: bool) -> str | None:
    members = client.members()
    emails = {m["email"] for m in members}
    last = settings.get("member")
    if last in emails and not force:
        return last
    dlg = dialogs.WhoDialog(members, last)
    if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen:
        return None
    settings.set("member", dlg.chosen)
    return dlg.chosen


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Home Base")  # WO-FS-011 HB-1
    apply_theme(app)
    settings = LocalSettings()
    address = locate_hub(settings)
    if address is None:
        return
    client = HubClient(address)
    if offer_update(client):
        return
    try:
        if not set_up_family(client):
            return
        member = pick_member(client, settings, force=False)
        if member is None:
            return
        client.member = member
        members = client.members()
    except (HubUnreachable, HubError) as err:
        dialogs.error(None, str(err))
        return

    def switch() -> str | None:
        try:
            return pick_member(client, settings, force=True)
        except (HubUnreachable, HubError) as err:
            dialogs.error(None, str(err))
            return None

    window = MainWindow(client, settings, members, switch)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
