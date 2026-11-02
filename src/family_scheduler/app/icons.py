"""Icons (IF-1): the bundled Lucide set (ISC licence), drawn in any colour, and the app's own icon (IF-2)."""

from __future__ import annotations

import struct
from functools import cache
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

from family_scheduler.app.style import ACCENT, ASSETS, ICON, SURFACE, TEXT

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

# What each icon stands for in the app.
# CP-2, WO-FS-007 TP-1: Today, Calendar and Events each have their own icon.
VIEW_ICONS = {"Today": "sun", "Calendar": "calendar-days", "Events": "calendar-clock", "Tasks": "list-checks",
              "Reminders": "sticky-note", "Groceries": "shopping-basket",
              "History": "history", "Trash": "trash-2", "Family & Settings": "settings"}


@cache
def _svg(name: str) -> str:
    return (ASSETS / "icons" / f"{name}.svg").read_text(encoding="utf-8")


@cache
def pixmap(name: str, colour: str = TEXT, size: int = ICON, ratio: float = 2.0) -> QPixmap:
    """A Lucide icon in one colour, sharp on high-DPI screens."""
    renderer = QSvgRenderer(QByteArray(_svg(name).replace("currentColor", colour).encode("utf-8")))
    px = int(size * ratio)
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(ratio)
    return result


def icon(name: str, colour: str = TEXT, size: int = ICON) -> QIcon:
    return QIcon(pixmap(name, colour, size))


def draw_app_icon(size: int) -> QImage:
    """The Home Base icon (HB-5, unchanged): a house whose windows are the days of a calendar, on the app's amber."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    p.drawRoundedRect(QRectF(0, 0, size, size), 14 * s, 14 * s)
    house = QPainterPath()
    house.moveTo(QPointF(32 * s, 11 * s))
    house.lineTo(QPointF(53 * s, 28 * s))
    house.lineTo(QPointF(53 * s, 50 * s))
    house.quadTo(QPointF(53 * s, 53 * s), QPointF(50 * s, 53 * s))
    house.lineTo(QPointF(14 * s, 53 * s))
    house.quadTo(QPointF(11 * s, 53 * s), QPointF(11 * s, 50 * s))
    house.lineTo(QPointF(11 * s, 28 * s))
    house.closeSubpath()
    p.setBrush(QColor(SURFACE))
    p.drawPath(house)
    p.setBrush(QColor(ACCENT))
    for row in range(2):
        for col in range(3):
            p.drawRoundedRect(QRectF((17 + col * 11) * s, (31 + row * 10) * s, 8 * s, 7 * s), 1.5 * s, 1.5 * s)
    p.end()
    return image


def app_icon() -> QIcon:
    result = QIcon()
    for size in ICO_SIZES:
        result.addPixmap(QPixmap.fromImage(draw_app_icon(size)))
    return result



def write_ico(path: Path) -> None:
    """The app icon as a Windows .ico for the exe, the taskbar and the installer (IF-2): PNG images in an ICO."""
    images = []
    for size in ICO_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        draw_app_icon(size).save(buffer, "PNG")  # type: ignore[call-overload]
        images.append((size, bytes(buffer.data().data())))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 means 256 in an ICO directory
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    path.write_bytes(header + entries + blobs)
