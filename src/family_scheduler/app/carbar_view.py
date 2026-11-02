"""The Cars today bar on Today (WO-FS-010 CB-a to CB-i): one bar from midnight to midnight, green, yellow or red by how
many cars are in use. Its rules are in carbar.py."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QHelpEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolTip, QVBoxLayout, QWidget

from family_scheduler.app import carbar
from family_scheduler.app.style import (
    BASE,
    CAR_ALL,
    CAR_BAR,
    CAR_BAR_LABELS,
    CAR_FREE,
    CAR_INK,
    CAR_SOME,
    DIVIDER,
    HALF,
    MUTED,
    OUTLINE,
    R_CONTROL,
    S1,
    S2,
    S3,
    SMALL_PT,
    SURFACE,
    TEXT,
    blend,
    font,
)

COLOURS = {carbar.FREE: CAR_FREE, carbar.SOME: CAR_SOME, carbar.ALL: CAR_ALL}
FADE = 0.45  # CB-f: how much the parts that are over fade towards the panel


class _Bar(QWidget):
    open_event = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[carbar.Part] = []
        self.now = 0  # minutes from midnight
        self.setMouseTracking(True)
        self.setMinimumHeight(CAR_BAR + S3)  # the bar, then the hour labels under it
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _x(self, minutes: int) -> float:
        return self.width() * minutes / carbar.DAY

    def _part_at(self, pos: QPoint) -> carbar.Part | None:
        if not 0 <= pos.y() <= CAR_BAR:
            return None
        minute = int(pos.x() / max(self.width(), 1) * carbar.DAY)
        return next((part for part in self.parts if part.start <= minute < part.end), None)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0, 0, self.width(), CAR_BAR)
        p.setClipRect(bar)
        for part in self.parts:
            colour = COLOURS[part.level]
            if part.end <= self.now:  # CB-f: over, slightly faded
                colour = blend(colour, SURFACE, FADE)
            p.fillRect(QRectF(self._x(part.start), 0, self._x(part.end) - self._x(part.start) + 1, CAR_BAR),
                       QColor(colour))
        p.setPen(QPen(QColor(BASE), 1))  # light hour marks
        for hour in range(1, 24):
            x = self._x(hour * 60)
            p.drawLine(QRectF(x, CAR_BAR - HALF, 0, HALF).topLeft(), QRectF(x, CAR_BAR, 0, 0).topLeft())
        p.setPen(QPen(QColor(TEXT), OUTLINE))  # CB-f: the current time
        x = self._x(self.now)
        p.drawLine(int(x), 0, int(x), CAR_BAR)
        p.setClipping(False)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(DIVIDER), 1))
        p.drawRoundedRect(bar.adjusted(0.5, 0.5, -0.5, -0.5), R_CONTROL, R_CONTROL)
        p.setFont(font(SMALL_PT))
        p.setPen(QColor(MUTED))
        fm = QFontMetrics(font(SMALL_PT))
        for hour in range(0, 25, CAR_BAR_LABELS):  # CB-b: 12 am, 6 am, 12 pm, 6 pm, 12 am
            text = carbar.hour_label(hour * 60)
            w = fm.horizontalAdvance(text)
            x = min(max(int(self._x(hour * 60)) - w // 2, 0), self.width() - w)
            p.drawText(QRect(x, CAR_BAR + 2, w, fm.height()), Qt.AlignmentFlag.AlignCenter, text)
        p.end()

    def event(self, e: QEvent) -> bool:
        if isinstance(e, QHelpEvent):  # CB-e
            part = self._part_at(e.pos())
            if part:
                QToolTip.showText(e.globalPos(), carbar.describe(part), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """CB-e: a yellow or red part lists its events; picking one opens it."""
        part = self._part_at(event.position().toPoint())
        if part is None or not part.uses:
            return
        menu = QMenu(self)
        for use in part.uses:
            action = menu.addAction(f"{use['car']}: {use['title']}")
            action.triggered.connect(lambda _checked=False, event_id=use["event_id"]: self.open_event.emit(event_id))
        menu.exec(event.globalPosition().toPoint())


class CarsToday(QWidget):
    """CB-a: the Cars today section above Today's events: the bar, its legend (CB-g) and a summary line (CB-h)."""

    open_event = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(HALF, 0, S1, 0)
        box.setSpacing(HALF)
        head = QHBoxLayout()
        title = QLabel("Cars today")
        title.setObjectName("TodaySection")
        head.addWidget(title)
        head.addStretch()
        self.summary = QLabel()
        self.summary.setProperty("muted", True)
        head.addWidget(self.summary)
        box.addLayout(head)
        self.bar = _Bar()
        self.bar.open_event.connect(self.open_event)
        box.addWidget(self.bar)
        self.legend = QHBoxLayout()
        self.legend.setSpacing(S2)
        box.addLayout(self.legend)

    def show_day(self, uses: list[dict[str, Any]], car_count: int, now_minutes: int) -> None:
        self.bar.parts = carbar.parts(uses, car_count)
        self.bar.now = now_minutes
        self.bar.update()
        self.summary.setText(carbar.summary(uses, car_count))
        while self.legend.count():
            item = self.legend.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for level, text in carbar.legend(car_count):
            swatch = QLabel()
            swatch.setObjectName("CarSwatch")
            swatch.setProperty("level", level)
            swatch.setFixedSize(S2, S2 - HALF)
            self.legend.addWidget(swatch)
            label = QLabel(text)
            label.setProperty("muted", True)
            self.legend.addWidget(label)
        self.legend.addStretch()

    def ink(self, level: str) -> str:
        """CB-i: black or white text on a bar colour."""
        return CAR_INK[COLOURS[level]]
