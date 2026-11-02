"""Where things live, and which role this PC has (PC-2)."""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path

DEFAULT_PORT = 8765
SERVICE_TYPE = "_familysched._tcp.local."


def hub_port() -> int:
    return int(os.environ.get("FS_PORT", DEFAULT_PORT))


def data_dir() -> Path:
    """Hub data: C:\\ProgramData\\FamilyScheduler, or FS_DATA_DIR for a separate test hub."""
    override = os.environ.get("FS_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "FamilyScheduler"
    else:
        path = Path.home() / ".family-scheduler"
    path.mkdir(parents=True, exist_ok=True)
    return path


def machine_role() -> str:
    """'hub' or 'client', written by the installer to config.ini."""
    ini = data_dir() / "config.ini"
    parser = configparser.ConfigParser()
    if ini.exists():
        parser.read(ini, encoding="utf-8")
    return parser.get("setup", "role", fallback="client")


def client_settings_path() -> Path:
    """Per Windows user: last member picked and hub address (WU-3)."""
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / "FamilyScheduler"
    path.mkdir(parents=True, exist_ok=True)
    return path / "client.json"
