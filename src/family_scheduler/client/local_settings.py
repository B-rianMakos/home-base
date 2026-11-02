"""Per-user settings on this PC: last member picked and the hub address (WU-3)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from family_scheduler.config import client_settings_path


class LocalSettings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or client_settings_path()
        self.data: dict[str, Any] = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> str | None:
        value = self.data.get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str | None) -> None:
        self.data[key] = value
        with contextlib.suppress(OSError):  # remembering is a convenience; the app still works
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
