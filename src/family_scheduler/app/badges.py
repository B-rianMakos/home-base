"""Sidebar badge counts (WO-FS-004 NV-2). Plain data, no Qt, so it can be tested directly."""

from __future__ import annotations

from typing import Any


def badge_counts(today: dict[str, Any]) -> dict[str, int]:
    """Overdue tasks next to Tasks, today's events next to Events. Zero means no badge."""
    return {"Tasks": len(today.get("overdue", [])), "Events": len(today.get("events", []))}
