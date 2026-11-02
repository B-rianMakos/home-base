"""Wires the Organizer to the database, the stored secrets, Google and Gmail."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from family_scheduler import db
from family_scheduler.google_calendar import (
    CalendarError,
    CalendarGateway,
    FakeCalendarGateway,
    GoogleCalendarGateway,
    check_client_file,
    revoke_token,
)
from family_scheduler.mailer import GmailMailer, Mailer, MailError, RecordingMailer
from family_scheduler.organizer import Organizer
from family_scheduler.secrets_store import KeyFileProvider, KeyProvider, KeyringKeyProvider, SecretStore
from family_scheduler.timeutil import Clock, system_clock

GOOGLE_CLIENT = "google_client"  # the OAuth Desktop app client file (OA-3)
GOOGLE_TOKEN = "google_token:"  # + the calendar account's email
GMAIL_APP_PASSWORD = "gmail_app_password"
log = logging.getLogger(__name__)


@dataclass
class HubContext:
    conn: sqlite3.Connection
    organizer: Organizer
    secrets: SecretStore
    data_dir: Path
    fake_calendar: FakeCalendarGateway | None = None
    fake_mailer: RecordingMailer | None = None
    _gateways: dict[str, CalendarGateway] = field(default_factory=dict, repr=False)
    revoked: list[str] = field(default_factory=list, repr=False)  # test mode: accounts "revoked" (CB-5)
    _gateway_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- Google: one token and one gateway per calendar account (GA-1, GA-2) -------------------

    def calendar(self, account: str) -> CalendarGateway:
        if self.fake_calendar is not None:
            if account not in self.organizer.connected_accounts():
                raise CalendarError(f"{account} isn't connected. Connect it on the hub PC under Family & Settings.")
            return self.fake_calendar.as_account(account)
        with self._gateway_lock:
            if account not in self._gateways:
                with self.organizer._read():
                    token = self.secrets.get(GOOGLE_TOKEN + account)
                if token is None:
                    raise CalendarError(
                        f"{account} isn't connected. Connect it on the hub PC under Family & Settings.")
                self._gateways[account] = GoogleCalendarGateway(token)
            return self._gateways[account]

    def google_connected(self) -> bool:
        return bool(self.organizer.connected_accounts())

    def connect_google(self, account: str, token_json: str) -> None:
        account = self.organizer.require_calendar_account(account)
        with self.organizer._write():
            self.secrets.put(GOOGLE_TOKEN + account, token_json)
        with self._gateway_lock:
            self._gateways.pop(account, None)
        self.organizer.set_account_status(account, "connected")

    def disconnect_google(self, account: str) -> str | None:
        """CB-5: remove the account's token from the hub, then revoke it at Google. Returns a note if Google
        couldn't be told; the token is gone from the hub either way."""
        account = self.organizer.require_calendar_account(account)
        with self.organizer._write():
            token = self.secrets.get(GOOGLE_TOKEN + account)
            self.secrets.delete(GOOGLE_TOKEN + account)
        with self._gateway_lock:
            self._gateways.pop(account, None)
        self.organizer.set_account_status(account, "not_connected")
        if self.fake_calendar is not None:
            self.revoked.append(account)
            return None
        if token is None:
            return None
        try:
            revoke_token(token)
        except CalendarError as err:
            log.warning("Revoking %s at Google failed: %s", account, err)
            return str(err)
        return None

    # --- the Google client file (OA-2, OA-3): chosen once, kept encrypted on the hub ------------

    def set_client_file(self, text: str) -> None:
        data = check_client_file(text)
        with self.organizer._write():
            self.secrets.put(GOOGLE_CLIENT, json.dumps(data))

    def client_file(self) -> dict[str, Any] | None:
        with self.organizer._read():
            text = self.secrets.get(GOOGLE_CLIENT)
        return json.loads(text) if text else None

    def client_file_set(self) -> bool:
        with self.organizer._read():
            return self.secrets.has(GOOGLE_CLIENT)

    # --- Gmail --------------------------------------------------------------------------------

    def mailer(self) -> Mailer:
        if self.fake_mailer is not None:
            return self.fake_mailer
        with self.organizer._read():
            password = self.secrets.get(GMAIL_APP_PASSWORD)
        if password is None:
            raise MailError("Digest email isn't set up. On the hub PC, add a Gmail app password in "
                            "Family & Settings.")
        return GmailMailer(self.organizer.admin_email(), password)

    def mail_configured(self) -> bool:
        if self.fake_mailer is not None:
            return True
        with self.organizer._read():
            return self.secrets.has(GMAIL_APP_PASSWORD)

    def set_app_password(self, password: str) -> None:
        with self.organizer._write():
            self.secrets.put(GMAIL_APP_PASSWORD, password)


def build_context(
    data_dir: Path,
    *,
    db_path: Path | str | None = None,
    keys: KeyProvider | None = None,
    clock: Clock = system_clock,
    fake_calendar: bool | None = None,
    fake_mail: bool | None = None,
) -> HubContext:
    """FS_CALENDAR=fake / FS_MAIL=fake run the hub without touching Google or Gmail (test setup)."""
    if fake_calendar is None:
        fake_calendar = os.environ.get("FS_CALENDAR") == "fake"
    if fake_mail is None:
        fake_mail = os.environ.get("FS_MAIL") == "fake"
    conn = db.connect(db_path if db_path is not None else data_dir / "family.db")
    db.migrate(conn)
    secrets = SecretStore(conn, keys or KeyFileProvider(data_dir / "keys" / "hub.key", adopt=KeyringKeyProvider()))
    ctx = HubContext(conn=conn, organizer=Organizer(conn, clock=clock), secrets=secrets, data_dir=data_dir,
                     fake_calendar=FakeCalendarGateway() if fake_calendar else None,
                     fake_mailer=RecordingMailer() if fake_mail else None)
    ctx.organizer = Organizer(conn, calendar=ctx.calendar, mailer=ctx.mailer, clock=clock)
    restored = ctx.organizer.restore_repeat_days()  # WO-FS-013 ET-2 to ET-4: once, on the first start on schema 9
    if restored is not None:
        log.info("Repeat days filled in for monthly and yearly tasks: %s", restored)
    test_cap = os.environ.get("FS_TEST_DIGEST_CAP")
    if test_cap and os.environ.get("FS_DATA_DIR"):  # EL-11: a test hub only; the real hub keeps 20 a day
        ctx.organizer.digest_cap = max(int(test_cap), 0)
    if fake_calendar:  # test setup: both calendar accounts count as connected
        for account in ctx.organizer.calendar_accounts():
            if account["status"] != "connected":
                ctx.organizer.set_account_status(account["email"], "connected")
    return ctx
