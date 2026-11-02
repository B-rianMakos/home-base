"""Draws Segoe UI and the bundled Inter side by side, at the app's sizes, the way Qt renders them (WO-FS-004 Part A 3).

    python tools/font_sample.py OUTPUT.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication

FONTS = Path(__file__).resolve().parent.parent / "src" / "family_scheduler" / "app" / "assets" / "fonts"

SAMPLES = [  # (pt, weight, colour, text)
    (22, QFont.Weight.DemiBold, "#FFFFFF", "Events"),
    (18, QFont.Weight.Bold, "#4CD964", "Today's events (2)"),
    (16, QFont.Weight.DemiBold, "#FFFFFF", "November 2026 (5)"),
    (13, QFont.Weight.DemiBold, "#FFFFFF", "Parent-teacher interview"),
    (13, QFont.Weight.Normal, "#BFBFBF", "Tue Nov 3 · 4:30 pm–5:00 pm · Riverdale Public School"),
    (13, QFont.Weight.Normal, "#FFFFFF", "Sam is allergic to tree nuts. Tell any new babysitter."),
    (13, QFont.Weight.Normal, "#BFBFBF", "0123456789  ✓ 2  ✗ 0  ? 1  Il1 O0 rn m"),
]


def main() -> None:
    app = QApplication(sys.argv)
    for f in sorted(FONTS.glob("Inter-*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    width, col = 1400, 700
    height = 70 + sum(QFontMetrics(QFont("Segoe UI", pt)).height() + 22 for pt, *_ in SAMPLES)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#121212"))
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    for i, family in enumerate(("Segoe UI", "Inter")):
        x = 32 + i * col
        label = QFont("Segoe UI", 11)
        p.setFont(label)
        p.setPen(QColor("#BFBFBF"))
        p.drawText(x, 36, f"{family} {'(Windows)' if i == 0 else '(bundled, SIL OFL)'}")
        y = 64
        for pt, weight, colour, text in SAMPLES:
            font = QFont(family, pt)
            font.setWeight(weight)
            p.setFont(font)
            p.setPen(QColor(colour))
            h = QFontMetrics(font).height()
            p.drawText(QRect(x, y, col - 48, h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            y += h + 22
    p.end()
    image.save(sys.argv[1] if len(sys.argv) > 1 else "font-sample.png")
    _ = app


if __name__ == "__main__":
    main()
