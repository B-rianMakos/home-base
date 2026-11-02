"""Encryption for the Google token and Gmail app password (NF-1, WU-6, FR-4).

The Fernet key lives in Windows Credential Manager; only ciphertext is stored in the database.
"""

from __future__ import annotations

import ctypes
import sqlite3
import sys
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet

from family_scheduler.timeutil import iso, system_clock

KEYRING_SERVICE = "FamilyScheduler"
KEYRING_USER = "hub-secret-key"


class KeyProvider(Protocol):
    def get_key(self) -> bytes: ...


class KeyringKeyProvider:
    """Creates the key on first use and keeps it in the OS credential store."""

    def existing_key(self) -> bytes | None:
        """The key, if this account's credential store has one; never creates one."""
        try:
            import keyring

            stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:  # noqa: BLE001 - no usable credential store (e.g. some service accounts)
            return None
        return stored.encode("ascii") if stored else None

    def get_key(self) -> bytes:
        import keyring

        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if stored is None:
            stored = Fernet.generate_key().decode("ascii")
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, stored)
        return stored.encode("ascii")


class KeyFileProvider:
    """WO-FS-008: the key in a file in the hub's data folder (keys/hub.key), sealed with Windows DPAPI for this
    machine. The installed hub (running as SYSTEM) and an administrator running the import can both open it, so data
    can be moved into the hub and survives restarts; copied to another PC, the file is useless.

    `adopt` is an older key store (the Windows Credential Manager, used up to 1.5.0): if it already holds a key, that
    key is sealed into the file, so data encrypted with it stays readable.
    """

    def __init__(self, path: Path, adopt: KeyringKeyProvider | None = None) -> None:
        self.path = path
        self._adopt = adopt
        self._key: bytes | None = None

    def get_key(self) -> bytes:
        if self._key is None:
            if self.path.exists():
                self._key = unseal(self.path.read_bytes())
            else:
                key = self._adopt.existing_key() if self._adopt else None
                self._key = key or Fernet.generate_key()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_bytes(seal(self._key))
        return self._key


class StaticKeyProvider:
    """For tests and the throwaway test hub."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or Fernet.generate_key()

    def get_key(self) -> bytes:
        return self._key


class SecretStore:
    def __init__(self, conn: sqlite3.Connection, keys: KeyProvider) -> None:
        self._conn = conn
        self._keys = keys

    def _fernet(self) -> Fernet:
        return Fernet(self._keys.get_key())

    def put(self, name: str, value: str) -> None:
        token = self._fernet().encrypt(value.encode("utf-8"))
        self._conn.execute(
            "INSERT INTO secret (name, value_enc, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value_enc = excluded.value_enc, updated_at = excluded.updated_at",
            (name, token, iso(system_clock())),
        )

    def get(self, name: str) -> str | None:
        row = self._conn.execute("SELECT value_enc FROM secret WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return self._fernet().decrypt(row[0]).decode("utf-8")

    def has(self, name: str) -> bool:
        return self._conn.execute("SELECT 1 FROM secret WHERE name = ?", (name,)).fetchone() is not None

    def delete(self, name: str) -> None:
        self._conn.execute("DELETE FROM secret WHERE name = ?", (name,))


# --- Windows DPAPI, machine scope (CryptProtectData with CRYPTPROTECT_LOCAL_MACHINE) ------------------------------

_LOCAL_MACHINE = 0x4
_UI_FORBIDDEN = 0x1
_SEALED = b"DPAPI1:"  # marks a sealed file; anything else is a plain key (non-Windows test machines)


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _call(name: str, data: bytes) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    source, out = _blob(data), _Blob()
    fn = getattr(crypt32, name)
    if not fn(ctypes.byref(source), None, None, None, None, _LOCAL_MACHINE | _UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(f"{name} failed ({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def seal(key: bytes) -> bytes:
    if sys.platform != "win32":
        return key
    return _SEALED + _call("CryptProtectData", key)


def unseal(data: bytes) -> bytes:
    if not data.startswith(_SEALED):
        return data
    return _call("CryptUnprotectData", data[len(_SEALED):])
