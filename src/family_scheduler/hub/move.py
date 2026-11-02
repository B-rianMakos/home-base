"""Moving a hub's data into this hub (WO-FS-008 PS-2, MH-2): the test hub into the installed one, or one PC's hub
into another's. Everything comes across, and every secret is re-encrypted with this hub's own key."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from family_scheduler import db
from family_scheduler.secrets_store import KeyFileProvider, KeyProvider, KeyringKeyProvider, unseal


class ImportFailed(Exception):
    """The import couldn't run. The message says why; nothing was changed."""


@dataclass
class ImportReport:
    counts: dict[str, int] = field(default_factory=dict)
    secrets_moved: list[str] = field(default_factory=list)
    secrets_lost: list[str] = field(default_factory=list)  # couldn't be decrypted: to be entered again
    backup: Path | None = None

    def summary(self) -> str:
        lines = [f"{n} {what}" for what, n in self.counts.items()]
        text = "Moved: " + ", ".join(lines) + "."
        if self.secrets_moved:
            text += "\nKept: " + ", ".join(_secret_label(s) for s in self.secrets_moved) + "."
        if self.secrets_lost:
            text += ("\nEnter again in Family & Settings: " + ", ".join(_secret_label(s) for s in self.secrets_lost)
                     + ".")
        if self.backup:
            text += f"\nThe hub's earlier data was saved to {self.backup}."
        return text


def _secret_label(name: str) -> str:
    if name.startswith("google_token:"):
        return f"the {name.split(':', 1)[1]} calendar connection"
    return {"google_client": "the Google client file", "gmail_app_password": "the Gmail app password"}.get(name, name)


def source_key(source_dir: Path) -> bytes | None:
    """The key the source hub's secrets were encrypted with: its key file, or (up to 1.5.0) the Windows Credential
    Manager of the person running the import. None if neither exists."""
    key_file = source_dir / "keys" / "hub.key"
    if key_file.exists():
        try:
            return unseal(key_file.read_bytes())
        except OSError:  # sealed to another PC (MH-1): the data still moves; its secrets are listed to enter again
            return None
    return KeyringKeyProvider().existing_key()


def import_data(source_dir: Path, target_dir: Path, source: bytes | None, target_keys: KeyProvider | None = None,
                ) -> ImportReport:
    """Copy the source hub's database over this hub's (keeping a backup of this hub's), bring it up to date, and
    re-encrypt each secret with this hub's key. A secret that can't be decrypted is removed and listed, so it can be
    entered again; the calendar account it belonged to then says Not connected."""
    source_db = source_dir / "family.db"
    if not source_db.exists():
        raise ImportFailed(f"There's no hub data at {source_dir} (family.db is missing).")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_db = target_dir / "family.db"
    report = ImportReport()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if target_db.exists():
        report.backup = target_dir / "backups" / f"family-before-import-{stamp}.db"
        report.backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target_db)) as current:  # closed, so the file can be replaced on Windows
            db.backup(current, report.backup)
    temp = target_dir / f"family.import-{stamp}.db"
    src = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(temp)
        src.backup(dst)  # a consistent copy, even if the source hub is still running
        dst.close()
    finally:
        src.close()
    try:
        for leftover in (target_dir / "family.db-wal", target_dir / "family.db-shm"):
            leftover.unlink(missing_ok=True)
        os.replace(temp, target_db)
    except OSError as err:  # the hub still has its data open: nothing was changed
        temp.unlink(missing_ok=True)
        raise ImportFailed("The hub's data is still in use. Run the import as an administrator, so it can stop "
                           "the hub first.") from err

    conn = db.connect(target_db)
    try:
        db.migrate(conn)
        old = Fernet(source) if source else None
        new = Fernet((target_keys or KeyFileProvider(target_dir / "keys" / "hub.key")).get_key())
        for name, value in conn.execute("SELECT name, value_enc FROM secret").fetchall():
            try:
                if old is None:
                    raise InvalidToken
                plain = old.decrypt(value)
            except InvalidToken:
                conn.execute("DELETE FROM secret WHERE name = ?", (name,))
                if name.startswith("google_token:"):
                    conn.execute("UPDATE calendar_account SET status = 'not_connected' WHERE email = ?",
                                 (name.split(":", 1)[1],))
                report.secrets_lost.append(name)
                continue
            conn.execute("UPDATE secret SET value_enc = ? WHERE name = ?", (new.encrypt(plain), name))
            report.secrets_moved.append(name)
        queries = {
            "family members": "SELECT COUNT(*) FROM family_member WHERE active = 1",
            "events": "SELECT COUNT(*) FROM event",
            "tasks": "SELECT COUNT(*) FROM task WHERE deleted_at IS NULL",
            "reminders": "SELECT COUNT(*) FROM reminder WHERE deleted_at IS NULL",
            "items in Trash": "SELECT (SELECT COUNT(*) FROM task WHERE deleted_at IS NOT NULL) + "
                              "(SELECT COUNT(*) FROM reminder WHERE deleted_at IS NOT NULL)",
            "History entries": "SELECT COUNT(*) FROM activity_entry",
        }
        report.counts = {what: int(conn.execute(sql).fetchone()[0]) for what, sql in queries.items()}
        conn.commit()
    finally:
        conn.close()
    return report
