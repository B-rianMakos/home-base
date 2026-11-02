"""SQLite access and the migration runner. Only the hub service opens the database file."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _migration_files() -> list[tuple[int, str]]:
    folder = resources.files("family_scheduler") / "migrations"
    found: list[tuple[int, str]] = []
    for entry in folder.iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.read_text(encoding="utf-8")))
    return sorted(found)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply numbered migrations that haven't run yet. Returns the schema version."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migration (version INTEGER PRIMARY KEY)")
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migration")}
    version = max(done, default=0)
    # A migration may rebuild a table (003). With foreign keys on, dropping the old table would delete the rows that
    # point at it, so they are off while migrating (this only works outside a transaction) and checked before each
    # commit instead.
    keys_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for number, sql in _migration_files():
            if number in done:
                continue
            conn.execute("BEGIN")
            try:
                # executescript would commit implicitly; run statements one by one inside our transaction.
                for statement in _split_sql(sql):
                    conn.execute(statement)
                broken = conn.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise sqlite3.IntegrityError(f"Migration {number} would leave {len(broken)} broken references.")
                conn.execute("INSERT INTO schema_migration (version) VALUES (?)", (number,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            version = number
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if keys_on else 'OFF'}")
    return version


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines():
        stripped = line.split("--", 1)[0] if not line.lstrip().startswith("--") else ""
        buffer += stripped + "\n"
        if sqlite3.complete_statement(buffer):
            if buffer.strip() and not buffer.strip().upper().startswith("PRAGMA"):
                statements.append(buffer.strip())
            buffer = ""
    return statements


def backup(conn: sqlite3.Connection, target: Path) -> None:
    """Online backup (PC-11): safe while the hub is running, unlike a file copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    dest = sqlite3.connect(str(target))
    try:
        conn.backup(dest)
    finally:
        dest.close()
