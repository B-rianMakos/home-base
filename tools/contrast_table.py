"""Writes the WCAG contrast table for every text colour in the app (WO-FS-003 BK-9) as Markdown.

    python tools/contrast_table.py OUTPUT.md
"""

from __future__ import annotations

import sys
from pathlib import Path

from family_scheduler.app.style import CONTRAST_PAIRS, contrast_ratio


def main() -> None:
    lines = ["| Text | Text colour | Background | Contrast | WCAG AA (4.5 : 1) |", "|---|---|---|---|---|"]
    for name, fg, bg in CONTRAST_PAIRS:
        ratio = contrast_ratio(fg, bg)
        lines.append(f"| {name} | `{fg}` | `{bg}` | {ratio:.1f} : 1 | {'Pass' if ratio >= 4.5 else 'FAIL'} |")
    Path(sys.argv[1]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
