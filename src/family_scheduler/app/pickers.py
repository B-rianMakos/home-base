"""The date picker and the time wheels (WO-FS-008 sections 2 and 3).

The date picker is the app's own calendar pop-up: dark, Monday first, Saturday and Sunday light blue, today circled
and the selected day light green. A time is picked with an hour wheel, a minute wheel and an am / pm switch; the
rules behind them are in timepick.py.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QDate, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QTextCharFormat,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCalendarWidget,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)

from family_scheduler.app import icons, timepick
from family_scheduler.app.style import (
    ACCENT,
    ICON,
    OUTLINE,
    PAST,
    PICKER_CELL,
    R_CONTROL,
    RAISED,
    RAISED_HOVER,
    S1,
    SELECT,
    SELECT_TEXT,
    TEXT,
    WEEKEND,
    WHEEL_ROW,
    WHEEL_ROWS,
    WHEEL_WIDTH,
    font,
    show_month,
)

# --- the date picker (DP-1 to DP-4) -------------------------------------------------------------------------

WEEKEND_DAYS = (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday)


def day_colour(day: QDate, month: int, today: QDate, selected: QDate) -> tuple[str | None, str, bool]:
    """How a day in the picker is drawn: (fill, text colour, circled). The selected day is light green with black
    text (SL-1); days of another month are dimmed; Saturday and Sunday are light blue, never red (DP-1); today is
    circled, as on the Calendar page (DP-3)."""
    if day == selected:
        return SELECT, SELECT_TEXT, False
    if day.month() != month:
        return None, PAST, day == today
    if Qt.DayOfWeek(day.dayOfWeek()) in WEEKEND_DAYS:
        return None, WEEKEND, day == today
    return None, TEXT, day == today


class DarkCalendar(QCalendarWidget):
    """The pop-up behind every date field (Add/Edit event, Add/Edit task)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)  # DP-4
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.setGridVisible(False)
        self.setMinimumSize(PICKER_CELL * 7 + S1 * 2, PICKER_CELL * 7 + S1 * 2)
        header = QTextCharFormat()
        header.setForeground(QColor(TEXT))
        header.setBackground(QColor(RAISED))
        header.setFont(font(bold=True))
        self.setHeaderTextFormat(header)
        weekend = QTextCharFormat(header)
        weekend.setForeground(QColor(WEEKEND))  # DP-1: the Sat and Sun headings too
        for day in WEEKEND_DAYS:
            self.setWeekdayTextFormat(day, weekend)
        self.currentPageChanged.connect(self._month_shown)  # WO-FS-009 MC-7
        self._month_shown(self.yearShown(), self.monthShown())
        for name, icon in (("qt_calendar_prevmonth", "chevron-left"), ("qt_calendar_nextmonth", "chevron-right")):
            arrow = self.findChild(QToolButton, name)
            if arrow is not None:  # DP-3: arrows readable on the dark header
                arrow.setIcon(icons.icon(icon, TEXT))
                arrow.setIconSize(QSize(ICON, ICON))

    def _month_shown(self, _year: int, month: int) -> None:
        """MC-7: the month name at the top is underlined in the month's colour; weekends stay light blue."""
        name = self.findChild(QToolButton, "qt_calendar_monthbutton")
        if name is not None:
            show_month(name, month)

    def paintCell(self, painter: QPainter, rect: QRect, day: QDate | object) -> None:  # noqa: N802 - Qt's name
        assert isinstance(day, QDate)
        fill, text, circled = day_colour(day, self.monthShown(), QDate.currentDate(), self.selectedDate())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, QColor(RAISED))
        side = min(rect.width(), rect.height()) - OUTLINE * 2
        spot = QRectF(rect.center().x() - side / 2 + 1, rect.center().y() - side / 2 + 1, side, side)
        if fill:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(spot, R_CONTROL, R_CONTROL)
        if circled:
            painter.setPen(QPen(QColor(TEXT), OUTLINE))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(spot.adjusted(OUTLINE, OUTLINE, -OUTLINE, -OUTLINE))
        painter.setPen(QColor(text))
        painter.setFont(font(bold=fill is not None or circled))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(day.day()))
        painter.restore()


def date_field(iso_date: str) -> QDateEdit:
    """A date field with the dark pop-up: "Fri Oct 30, 2026"."""
    edit = QDateEdit(QDate.fromString(iso_date, "yyyy-MM-dd"))
    edit.setCalendarPopup(True)
    edit.setCalendarWidget(DarkCalendar(edit))
    edit.setDisplayFormat("ddd MMM d, yyyy")
    return edit


# --- the time wheels (TM-1 to TM-3) --------------------------------------------------------------------------

class Wheel(QWidget):
    """One scrolling wheel: the chosen value in the middle, the ones above and below dimmed (TM-2). It moves with
    the mouse wheel, by dragging and with the arrow keys, and wraps at the ends."""

    moved = Signal()

    def __init__(self, values: list[int], text: Callable[[int], str], name: str) -> None:
        super().__init__()
        self.values = values
        self.value = values[0]
        self._text = text
        self._drag_from: float | None = None
        self._dragged = False
        self._scrolled = 0
        self.setFixedSize(WHEEL_WIDTH, WHEEL_ROW * WHEEL_ROWS)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setAccessibleName(name)
        self.setToolTip(f"{name}: scroll, drag or use the arrow keys")

    def set_value(self, value: int, values: list[int] | None = None) -> None:
        if values is not None:
            self.values = values
        self.value = value
        self.update()

    def move_by(self, places: int) -> None:
        self.value = timepick.step(self.values, self.value, places)
        self.update()
        self.moved.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        self._scrolled += event.angleDelta().y()
        while abs(self._scrolled) >= 120:  # one notch
            self.move_by(-1 if self._scrolled > 0 else 1)  # up shows the earlier value
            self._scrolled -= 120 if self._scrolled > 0 else -120
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus()
        self._drag_from = event.position().y()
        self._dragged = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_from is None:
            return
        moved = event.position().y() - self._drag_from
        while abs(moved) >= WHEEL_ROW:  # dragging up brings the later values into the middle
            self.move_by(1 if moved < 0 else -1)
            self._drag_from += WHEEL_ROW if moved > 0 else -WHEEL_ROW
            moved = event.position().y() - self._drag_from
            self._dragged = True

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._dragged:  # a click on the dimmed value above or below picks it
            row = int(event.position().y() // WHEEL_ROW)
            if row != WHEEL_ROWS // 2:
                self.move_by(row - WHEEL_ROWS // 2)
        self._drag_from = None

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.move_by(-1 if event.key() == Qt.Key.Key_Up else 1)
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.update()

    def shown_values(self) -> list[int]:
        """The values on the wheel from top to bottom, the chosen one in the middle."""
        middle = WHEEL_ROWS // 2
        return [timepick.step(self.values, self.value, n - middle) for n in range(WHEEL_ROWS)]

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        whole = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(ACCENT), OUTLINE) if self.hasFocus() else Qt.PenStyle.NoPen)
        p.setBrush(QColor(RAISED))
        p.drawRoundedRect(whole, R_CONTROL, R_CONTROL)
        middle = WHEEL_ROWS // 2
        band = QRectF(OUTLINE, middle * WHEEL_ROW, self.width() - OUTLINE * 2, WHEEL_ROW)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(RAISED_HOVER))
        p.drawRoundedRect(band, R_CONTROL, R_CONTROL)
        for n, value in enumerate(self.shown_values()):
            chosen = n == middle
            p.setPen(QColor(TEXT if chosen else PAST))
            p.setFont(font(bold=chosen))
            p.drawText(QRectF(0, n * WHEEL_ROW, self.width(), WHEEL_ROW), Qt.AlignmentFlag.AlignCenter,
                       self._text(value))
        p.end()


class TimePicker(QWidget):
    """TM-1: hour wheel (1 to 12), minute wheel (00 to 55 in fives) and an am / pm switch. `value()` is "HH:MM" on
    the 24-hour clock, as the hub stores it."""

    changed = Signal()

    def __init__(self, hhmm: str, name: str) -> None:
        super().__init__()
        hour, minute, pm = timepick.to_parts(hhmm)
        self.hour = Wheel(timepick.HOURS, str, f"{name} hour")
        self.minute = Wheel(timepick.minute_values(minute), lambda m: f"{m:02d}", f"{name} minutes")  # TM-3, TM-6
        self.hour.set_value(hour)
        self.minute.set_value(minute)
        colon = QLabel(":")
        colon.setObjectName("Section")
        switch = QFrame()
        switch.setObjectName("AmPm")  # WO-FS-013 EC-3: am and pm each in their own colour
        box = QHBoxLayout(switch)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(2)
        self.am = QPushButton("am")  # TM-7: lowercase
        self.pm = QPushButton("pm")
        self.am.setProperty("side", "am")
        self.pm.setProperty("side", "pm")
        self.ampm = QButtonGroup(self)
        for b in (self.am, self.pm):
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.ampm.addButton(b)
            box.addWidget(b)
        (self.pm if pm else self.am).setChecked(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(S1)
        row.addWidget(self.hour)
        row.addWidget(colon)
        row.addWidget(self.minute)
        row.addWidget(switch)
        row.addStretch()
        self.hour.moved.connect(self.changed)
        self.minute.moved.connect(self.changed)
        self.ampm.buttonClicked.connect(lambda _b: self.changed.emit())

    def value(self) -> str:
        return timepick.from_parts(self.hour.value, self.minute.value, self.pm.isChecked())

    def set_value(self, hhmm: str) -> None:
        """Set without telling anyone (the end following the start, TM-4). An odd minute is added to the wheel."""
        hour, minute, pm = timepick.to_parts(hhmm)
        self.hour.set_value(hour)
        values = self.minute.values if minute in self.minute.values else sorted([*self.minute.values, minute])
        self.minute.set_value(minute, values)
        (self.pm if pm else self.am).setChecked(True)
