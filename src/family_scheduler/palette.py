"""Colours shared by the app and the digest email, so a priority looks the same everywhere (WO-FS-013 TP-d).

Each is at least 12 apart (CIEDE2000) from the colours that already mean something: overdue red, today's-events
green, the light green selection, the reminder lavender, the grocery teal and the amber accent (TP-c).
"""

from __future__ import annotations

PRIORITY_COLOURS = {
    "high": "#5AA9FF",    # blue, as before (TD-5)
    "medium": "#E6CF4A",  # yellow: the suggested #E8C547 moved yellower, away from the amber accent
    "low": "#8FB89A",     # calm grey-green, darker and greyer than the selection
}
