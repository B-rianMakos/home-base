"""When the Calendar accounts buttons can be used, and why not (WO-FS-006 CB-3). Plain data, no Qt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

TEST_MODE = "Test mode: Google isn't used."
HUB_ONLY = "Connect on the hub PC."
NEED_CLIENT = "Choose the Google client file first."
SELECT = "Select an account."


@dataclass
class AccountButtons:
    connect: bool
    connect_label: str
    disconnect: bool
    reason: str  # never empty when a button is greyed out (CB-3)


def account_buttons(settings: dict[str, Any], selected: dict[str, Any] | None) -> AccountButtons:
    status = selected["status"] if selected else "not_connected"
    label = "Reconnect" if status == "needs_reconnect" else "Connect"
    if not settings:
        return AccountButtons(False, label, False, "")
    if settings.get("google_mode") == "fake":
        return AccountButtons(False, label, False, TEST_MODE)
    if not settings.get("on_hub_pc"):
        return AccountButtons(False, label, False, HUB_ONLY)
    if selected is None:
        return AccountButtons(False, label, False, SELECT)
    can_disconnect = status != "not_connected"
    if not settings.get("google_client_set"):
        return AccountButtons(False, label, can_disconnect, NEED_CLIENT)
    if status == "connected":
        return AccountButtons(False, label, True, f"{selected['email']} is connected.")
    return AccountButtons(True, label, can_disconnect, "")


def hub_status_line(status: dict[str, Any] | None, when: Callable[[str], str]) -> str:
    r"""WO-FS-008 PS-9: "Home hub running · started with Windows · data in C:\ProgramData\FamilyScheduler · last
    backup Fri Oct 30, 2:00 am". `when` formats the backup time."""
    if not status:
        return ""
    return " · ".join([
        "Test hub running" if status["test_hub"] else "Home hub running",
        "started with Windows" if status["started_with_windows"] else "not started with Windows",
        f"data in {status['data_dir']}",
        f"last backup {when(status['last_backup'])}" if status["last_backup"] else "no backup yet",
    ])


def reconnect_notice(emails: list[str]) -> str:
    """WO-FS-008 PS-7: what Today says when Google stops accepting an account's sign-in."""
    if not emails:
        return ""
    names = " and ".join(emails)
    return (f"Google no longer accepts the sign-in for {names}. Events from "
            f"{'these accounts' if len(emails) > 1 else 'this account'} can't be sent or changed until it's "
            "reconnected in Family & Settings, on the hub PC.")
