"""The Calendar page's two views (WO-FS-006 section 2): the week, each day a column of stacked blocks, and the month
grid with green event bars and blue task bars. Data comes from the hub's /api/week; order and dates from `week.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetrics,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QFrame, QScrollArea, QToolTip, QVBoxLayout, QWidget

from family_scheduler.app import icons
from family_scheduler.app.style import (
    BASE,
    BASE_PT,
    BLOCK_SELECTED,
    BLOCK_TEXT,
    DAY_HEADER,
    DIVIDER,
    EVENT_BLOCK,
    HALF,
    HIGH,
    MONTH_BAND,
    MONTH_BAR,
    MUTED,
    OUTLINE,
    OVERDUE,
    PAST,
    R_CONTROL,
    REPEAT_LABELS,
    S1,
    S2,
    SCROLLBAR,
    SMALL_PT,
    STACK_BLOCK,
    SURFACE,
    TAG_HEIGHT,
    TASK_BLOCK,
    TEXT,
    TINT,
    TODAY_COLUMN,
    WEEKDAY_ROW,
    font,
    month_colour,
    repeat_note,
    time_range,
)
from family_scheduler.app.week import day_items, short_time, split_items

Selection = tuple[str, str, str]  # ("event" or "task", id, day)
Item = tuple[str, dict[str, Any]]


# --- shared: tooltips, pills, blocks --------------------------------------------------------------------------

def _day_text(iso_date: str) -> str:
    d = date.fromisoformat(iso_date)
    return f"{d.strftime('%a %b')} {d.day}"


def event_tip(e: dict[str, Any]) -> str:
    """WB-9, MV-6: everything about an event, on hover."""
    replies = "\n".join(f"  {i['email']}: {i['rsvp'].replace('_', ' ')}" for i in e["invitees"])
    lines = [e["title"], f"{_day_text(e['day'])} · {time_range(e['start_time'], e['end_time'])}"]
    if e["location"]:
        lines.append(e["location"])
    if e["repeat"] != "none":
        lines.append(REPEAT_LABELS[e["repeat"]])
    lines.append(f"Organizer: {e['organizer_email']}")
    if e.get("car"):  # CR-9
        lines.append(f"{e['car']['name']} · chosen by {e.get('car_chosen_by') or 'someone'}"
                     + ("  (double-booked)" if e.get("car_double_booked") else ""))
    if e.get("from_google"):  # GY-4
        lines.append(f"From Google Calendar ({e['calendar_email']})")
    if e.get("read_only"):  # GY-8, GY-11, GY-12
        lines.append(f"{e['google_note']}: change it in Google Calendar")
    if e.get("not_synced"):  # GY-15
        lines.append(f"Not synced: reconnect {e['calendar_email']} in Family & Settings")
    if not e["organizer_connected"]:
        lines.append(f"Reconnect {e['organizer_email']} to change this event.")
    if e["sync_status"] == "error":
        lines.append("Not sent to Google yet" + (": open Events to retry." if e["retry_gave_up"]
                                                  else ". The hub keeps trying."))
    if e["conflicts_with"]:
        lines.append("Overlaps " + ", ".join(e["conflicts_with"]))
    return "\n".join(lines) + (f"\nInvitees:\n{replies}" if replies else "")


def task_tip(t: dict[str, Any]) -> str:
    lines = [t["title"], f"Due {_day_text(t['due_date'])} · {t.get('effective_priority', t['priority']).title()} "
                         "priority"]
    if t.get("due_soon") and not t["done"]:  # WO-FS-012 DS-4
        lines.append(f"Due soon, so it's treated as high priority. Set as {t['priority'].title()}.")
    if t["overdue"]:
        lines.append("Overdue")
    if t["repeat"] != "none":
        lines.append(REPEAT_LABELS[t["repeat"]])
        if repeat_note(t):  # WO-FS-013 MR-9
            lines.append(repeat_note(t))
    if t["done"]:
        lines.append(f"Done by {t['completed_by']}")
    return "\n".join(lines)


def item_tip(kind: str, item: dict[str, Any]) -> str:
    return event_tip(item) if kind == "event" else task_tip(item)


def pill_width(text: str) -> int:
    return QFontMetrics(font(SMALL_PT, bold=True)).horizontalAdvance(text) + S1 + HALF


def draw_pill(p: QPainter, x: int, y: int, text: str, colour: str, right: int, height: int = TAG_HEIGHT - HALF) -> int:
    """A small dark pill with coloured text (WB-6, WB-7, WB-8), drawn only if it fits before `right`. Returns the
    width it took, 0 if it didn't fit (the tooltip still says it)."""
    w = pill_width(text)
    if x + w > right:
        return 0
    r = QRect(x, y, w, height)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BASE))
    p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
    p.setFont(font(SMALL_PT, bold=True))
    p.setPen(QColor(colour))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    return w


def faded(kind: str, item: dict[str, Any]) -> bool:
    """WB-8: past events and done tasks are drawn faded."""
    return bool(item["past"]) if kind == "event" else bool(item["done"])


def block_colours(kind: str, item: dict[str, Any]) -> tuple[str, str]:
    """(fill, text): green or blue with black text; faded: a dark tint with green or blue text (WB-3, WB-8)."""
    colour = EVENT_BLOCK if kind == "event" else TASK_BLOCK
    return (TINT[colour], colour) if faded(kind, item) else (colour, BLOCK_TEXT)


def draw_frame(p: QPainter, r: QRect, kind: str, item: dict[str, Any], selected: bool) -> str:
    """Fill, and the outlines: white when selected (WU-a), red when overdue or not sent (WB-6, WB-8). Returns the
    text colour."""
    fill, ink = block_colours(kind, item)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(fill))
    p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
    alert = (kind == "task" and item["overdue"]) or (kind == "event" and item["sync_status"] == "error")
    if selected or alert:
        p.setPen(QPen(QColor(BLOCK_SELECTED if selected else OVERDUE), OUTLINE))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), R_CONTROL, R_CONTROL)
    return ink


def draw_tick(p: QPainter, r: QRect, done: bool, ink: str) -> None:
    """WB-2, LC-8: the round tick on a task."""
    if done:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TASK_BLOCK))
        p.drawEllipse(r)
        p.drawPixmap(r.adjusted(2, 2, -2, -2), icons.pixmap("check", BASE, r.width()))
    else:
        p.setPen(QPen(QColor(ink), OUTLINE / 2 + 0.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)


def _pieces(text: str) -> list[tuple[str, bool]]:
    """The words of `text`, each long one also split after its hyphens ("Back-", "to-", "school"), with whether a
    space comes before the piece."""
    out: list[tuple[str, bool]] = []
    for word in text.split():
        parts = [part for part in re.split(r"(?<=-)", word) if part]
        out.extend((part, n == 0) for n, part in enumerate(parts))
    return out


def _joined(pieces: list[tuple[str, bool]]) -> str:
    return "".join((" " if spaced and n else "") + piece for n, (piece, spaced) in enumerate(pieces))


def wrap(text: str, f: QFont, width: int, lines: int) -> list[str]:
    """Up to `lines` lines of `text`, broken at spaces (SB-9), or after a hyphen when a word is too long for the line
    ("Back-to-" / "school night"). The last line takes all the words left, so whenever text is shortened it ends in
    "…" and nothing goes missing silently (WO-FS-008 FX-3)."""
    metrics = QFontMetrics(f)
    pieces, out = _pieces(text), list[str]()
    i = 0
    while i < len(pieces) and len(out) < lines:
        if len(out) == lines - 1 or metrics.horizontalAdvance(pieces[i][0]) > width:
            # the last line, or a piece too long for any line: the rest of the title, shortened with "…"
            out.append(metrics.elidedText(_joined(pieces[i:]), Qt.TextElideMode.ElideRight, width))
            break
        line = [pieces[i]]
        i += 1
        while i < len(pieces) and metrics.horizontalAdvance(_joined([*line, pieces[i]])) <= width:
            line.append(pieces[i])
            i += 1
        out.append(_joined(line))
    return out or [""]


def whole(text: str, lines: list[str]) -> bool:
    """Whether `lines` show all of `text`, with nothing shortened (FX-3). A line ending in a hyphen runs straight on."""
    shown = ""
    for line in lines:
        shown += line if not shown or shown.endswith("-") else f" {line}"
    return shown == " ".join(text.split())


# --- week: each day a column of stacked blocks (SB-1 to SB-11) ---------------------------------------------------

@dataclass
class Placed:
    kind: str
    item: dict[str, Any]
    day: str
    rect: QRect


class _CalendarBase(QWidget):
    """Shared by both views: the data, the selection and the signals."""

    selected = Signal()
    open_item = Signal(str, str)      # (kind, id): double-click (WU-b, MV-6)
    tick = Signal(str)                # task id (WU-c)
    remove_item = Signal(str, str)    # (kind, id): the Delete key
    add_event_on = Signal(str)        # day: double-click empty space (SB-8, MV-7)
    more_items = Signal(str)          # day: "+N more" (MV-4)
    open_week = Signal(str)           # day: a day number in the month (MV-7)
    car_clicked = Signal(str)         # event id: its car spot (WO-FS-010 CR-4)
    context_item = Signal(str, str, QPoint)  # (kind, id, where): a right-click on a block or bar (WO-FS-011 HE-1)

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.today = ""
        self.days: list[str] = []
        self.items: dict[str, list[Item]] = {}
        self.selection: Selection | None = None

    def load(self, data: dict[str, Any], days: list[str]) -> None:
        self.today = data["date"]
        self.days = days
        events: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
        tasks: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
        for e in data["week_events"]:
            events.setdefault(e["day"], []).append(e)
        for t in data["week_tasks"]:
            tasks.setdefault(t["day"], []).append(t)
        self.items = {d: day_items(events[d], tasks[d]) for d in days}
        if self.selection and not any(it["id"] == self.selection[1] and k == self.selection[0]
                                      for k, it in self.items.get(self.selection[2], [])):
            self.selection = None
        self.refresh()

    def refresh(self) -> None:
        self.update()

    def select(self, sel: Selection | None) -> None:
        self.selection = sel
        self.refresh()
        self.selected.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.selection and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_item.emit(self.selection[0], self.selection[1])
            return
        if self.selection and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_item.emit(self.selection[0], self.selection[1])
            return
        super().keyPressEvent(event)


def _columns(width: int) -> tuple[list[int], list[int]]:
    """The x and width of each of the seven day columns, with the scroll bar's room kept free on the right."""
    usable = max(width - SCROLLBAR, 7)
    xs = [round(i * usable / 7) for i in range(8)]
    return xs[:7], [xs[i + 1] - xs[i] for i in range(7)]


class WeekStack(_CalendarBase):
    """SB-1: no hours. Seven columns under day names; each day's events stacked earliest first, then its tasks,
    every block the same height and the full column width; the whole week scrolls together (SB-7)."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = _WeekHeader(self)
        self.canvas = _WeekCanvas(self)
        self.area = QScrollArea()
        self.area.setWidget(self.canvas)
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        layout.addWidget(self.header)
        layout.addWidget(self.area, 1)

    def refresh(self) -> None:
        tallest = max((len(v) for v in self.items.values()), default=0)
        self.canvas.setMinimumHeight(S1 + tallest * (STACK_BLOCK + HALF) + S1)
        self.header.update()
        self.canvas.update()


class _WeekHeader(QWidget):
    """Day names and dates; today's date in a white circle (WV-1)."""

    def __init__(self, cal: WeekStack) -> None:
        super().__init__()
        self.cal = cal
        self.setFixedHeight(DAY_HEADER)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        xs, ws = _columns(self.width())
        for i, day in enumerate(self.cal.days):
            d = date.fromisoformat(day)
            is_today = day == self.cal.today
            if is_today:
                p.fillRect(QRect(xs[i], 0, ws[i], self.height()), QColor(TODAY_COLUMN))
            p.setFont(font(SMALL_PT, bold=True))
            p.setPen(QColor(TEXT if is_today else MUTED))
            p.drawText(QRect(xs[i], HALF, ws[i], TAG_HEIGHT - HALF), Qt.AlignmentFlag.AlignCenter, d.strftime("%a"))
            size = TAG_HEIGHT + HALF
            circle = QRect(xs[i] + ws[i] // 2 - size // 2, TAG_HEIGHT - HALF, size, size)
            if is_today:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(TEXT))
                p.drawEllipse(circle)
            p.setFont(font(BASE_PT, bold=True))
            p.setPen(QColor(BASE if is_today else TEXT))
            p.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(d.day))
        p.setPen(QPen(QColor(DIVIDER), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


class _WeekCanvas(QWidget):
    def __init__(self, cal: WeekStack) -> None:
        super().__init__()
        self.cal = cal
        self.setMouseTracking(True)
        self.placed: list[Placed] = []
        self.hover_id = ""  # the event under the mouse: its faint car icon shows (CR-9)

    def layout_blocks(self) -> list[Placed]:
        xs, ws = _columns(self.width() + SCROLLBAR)
        out = []
        for i, day in enumerate(self.cal.days):
            y = S1
            for kind, item in self.cal.items.get(day, []):
                out.append(Placed(kind, item, day, QRect(xs[i] + HALF, y, ws[i] - S1, STACK_BLOCK)))
                y += STACK_BLOCK + HALF  # SB-6: a small gap
        return out

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(SURFACE))
        xs, ws = _columns(self.width() + SCROLLBAR)
        if self.cal.today in self.cal.days:
            i = self.cal.days.index(self.cal.today)
            p.fillRect(QRect(xs[i], 0, ws[i], self.height()), QColor(TODAY_COLUMN))
        p.setPen(QPen(QColor(DIVIDER), 1))
        for x in xs[1:]:
            p.drawLine(x, 0, x, self.height())
        self.placed = self.layout_blocks()
        for b in self.placed:
            selected = self.cal.selection == (b.kind, b.item["id"], b.day)
            if b.kind == "event":
                paint_event_block(p, b.rect, b.item, selected, hovered=b.item["id"] == self.hover_id)
            else:
                paint_task_block(p, b.rect, b.item, selected)
        p.end()

    def _hit(self, pos: QPoint) -> Placed | None:
        return next((b for b in self.placed if b.rect.contains(pos)), None)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        b = self._hit(event.pos())
        if b:
            self.cal.context_item.emit(b.kind, b.item["id"], event.globalPos())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        b = self._hit(event.position().toPoint())
        hover = b.item["id"] if b and b.kind == "event" else ""
        if hover != self.hover_id:
            self.hover_id = hover
            self.update()

    def leaveEvent(self, event: QEvent) -> None:
        self.hover_id = ""
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        b = self._hit(pos)
        self.cal.setFocus()
        if b and b.kind == "event" and not b.item.get("read_only") and \
                car_rect(b.rect, b.item).adjusted(-HALF, -HALF, HALF, HALF).contains(pos):
            self.cal.car_clicked.emit(b.item["id"])  # CR-4, CR-5
            return
        if b and b.kind == "task" and task_tick_rect(b.rect).adjusted(-HALF, -HALF, HALF, HALF).contains(pos):
            self.cal.tick.emit(b.item["id"])
            return
        self.cal.select((b.kind, b.item["id"], b.day) if b else None)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        b = self._hit(pos)
        if b:
            self.cal.open_item.emit(b.kind, b.item["id"])
            return
        xs, ws = _columns(self.width() + SCROLLBAR)
        for i, day in enumerate(self.cal.days):
            if xs[i] <= pos.x() < xs[i] + ws[i]:
                self.cal.add_event_on.emit(day)  # SB-8: that day, no time filled in
                return

    def event(self, e: QEvent) -> bool:
        if isinstance(e, QHelpEvent):
            b = self._hit(e.pos())
            if b:
                QToolTip.showText(e.globalPos(), item_tip(b.kind, b.item), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)


def car_rect(r: QRect, item: dict[str, Any], bar: bool = False) -> QRect:
    """CR-4, CR-9: where an event's car sits: on a week block's top line, beside the time, so the title keeps its two
    lines; at the right end of a month bar. With a car, the icon and the (shortened) name; without, room for the
    faint icon shown on hover."""
    height = r.height() - HALF if bar else S2 + 2
    car = item.get("car")
    if bar:
        room = r.width() - height - S1 - S2
    else:  # the time comes first: the car gets what's left after the short time ("11:30 am")
        time_w = QFontMetrics(font(SMALL_PT, bold=True)).horizontalAdvance(short_time(item["start_time"]))
        room = r.width() - time_w - height - S1 - HALF * 2
    if room < pill_width("Ab…"):
        room = 0  # too tight for a name: the car icon alone, the name on hover
    width = height + (min(pill_width(car["name"]), room) if car else 0)
    top = r.top() + (r.height() - height) // 2 if bar else r.top() + 2
    return QRect(r.right() - width - 2, top, width, height)


def paint_car(p: QPainter, r: QRect, item: dict[str, Any], bar: bool, hovered: bool) -> int:
    """Draws the car spot; returns how much width it took from the right (0 if nothing was drawn)."""
    if item.get("read_only"):
        return 0
    car = item.get("car")
    if car is None and not hovered:
        return 0
    spot = car_rect(r, item, bar)
    if car is None:  # CR-9: a faint car icon on hover, so the button can be found
        p.setOpacity(0.45)
        p.drawPixmap(QRect(spot.left(), spot.top(), spot.height(), spot.height()),
                     icons.pixmap("car", BLOCK_TEXT, spot.height()))
        p.setOpacity(1.0)
        return spot.width()
    colour = OVERDUE if item.get("car_double_booked") else TEXT
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BASE))
    p.drawRoundedRect(spot, spot.height() / 2, spot.height() / 2)
    icon = QRect(spot.left() + HALF, spot.top() + 2, spot.height() - 4, spot.height() - 4)
    p.drawPixmap(icon, icons.pixmap("car", colour, spot.height()))
    p.setFont(font(SMALL_PT, bold=True))
    p.setPen(QColor(colour))
    text = QRect(icon.right() + 2, spot.top(), spot.right() - icon.right() - HALF, spot.height())
    p.drawText(text, Qt.AlignmentFlag.AlignVCenter,
               QFontMetrics(font(SMALL_PT, bold=True)).elidedText(car["name"], Qt.TextElideMode.ElideRight,
                                                                 text.width()))
    return spot.width()


def task_tick_rect(r: QRect) -> QRect:
    """The round tick sits on the bottom row of a task block, beside its markers, so the title gets the full width
    (SB-9)."""
    size = S2
    return QRect(r.left() + HALF + 2, r.bottom() - size - HALF + 1, size, size)


@dataclass
class BlockText:
    """Exactly what an event block shows, worked out once for drawing it and for the cut-off check (FX-3)."""

    time: str
    lines: list[str]
    place: str
    time_width: int = 0  # how wide the time may be: a car shares its line (CR-9)


def event_block_text(r: QRect, e: dict[str, Any]) -> BlockText:
    """SB-3: the time ("All day" for an all-day event, AD-8), then the title (on two lines if needed, SB-9), then the
    place, only when the whole title fits and there's room (FX-3)."""
    inner = r.adjusted(HALF + 2, HALF, -HALF, -HALF)
    sm, tm = QFontMetrics(font(SMALL_PT, bold=True)), QFontMetrics(font(BASE_PT, bold=True))
    # CR-9: a car shares the top line with the time
    time_room = inner.width() - (car_rect(r, e).width() + HALF if e.get("car") and not e.get("read_only") else 0)
    time_text = time_range(e["start_time"], e["end_time"])
    if sm.horizontalAdvance(time_text) > time_room:
        time_text = short_time(e["start_time"])
    y = inner.top() + sm.height()
    room = max((inner.bottom() - y) // tm.height(), 1)
    lines = wrap(e["title"], font(BASE_PT, bold=True), inner.width(), min(room, 2))
    y += tm.height() * len(lines)
    place = ""
    if e["location"] and whole(e["title"], lines) and y + sm.height() <= inner.bottom() + 2:
        place = QFontMetrics(font(SMALL_PT)).elidedText(e["location"], Qt.TextElideMode.ElideRight, inner.width())
    return BlockText(time_text, lines, place, time_room)


def task_block_lines(r: QRect, t: dict[str, Any]) -> list[str]:
    """SB-4: a task's title on up to two lines across the whole block."""
    inner = r.adjusted(HALF + 2, HALF, -HALF, -HALF)
    return wrap(t["title"], font(BASE_PT, bold=True), inner.width(), 2)


def paint_event_block(p: QPainter, r: QRect, e: dict[str, Any], selected: bool, hovered: bool = False) -> None:
    ink = draw_frame(p, r, "event", e, selected)
    inner = r.adjusted(HALF + 2, HALF, -HALF, -HALF)
    text = event_block_text(r, e)
    small, title = font(SMALL_PT, bold=True), font(BASE_PT, bold=True)
    sm, tm = QFontMetrics(small), QFontMetrics(title)
    p.setFont(small)
    p.setPen(QColor(ink))
    p.drawText(QRect(inner.left(), inner.top(), text.time_width or inner.width(), sm.height()),
               Qt.AlignmentFlag.AlignVCenter, sm.elidedText(text.time, Qt.TextElideMode.ElideRight,
                                                          text.time_width or inner.width()))
    y = inner.top() + sm.height()
    p.setFont(title)
    for line in text.lines:
        p.drawText(QRect(inner.left(), y, inner.width(), tm.height()), Qt.AlignmentFlag.AlignVCenter, line)
        y += tm.height()
    if text.place:
        p.setFont(font(SMALL_PT))
        p.drawText(QRect(inner.left(), y, inner.width(), sm.height()), Qt.AlignmentFlag.AlignVCenter, text.place)
    if e["conflicts_with"] and not selected:  # SB-5: the overlap marker stays (at the bottom when a car is up top)
        marker_top = r.bottom() - S2 - 2 if e.get("car") else r.top() + 3
        p.drawPixmap(QRect(r.right() - S2 - 2, marker_top, S2 - 2, S2 - 2),
                     icons.pixmap("triangle-alert", ink, S2))
    if e["sync_status"] == "error":
        draw_pill(p, r.right() - pill_width("Not sent") - 2, r.bottom() - TAG_HEIGHT + 2, "Not sent", OVERDUE,
                  r.right())
    if not e["organizer_connected"] and not e.get("car"):
        p.drawPixmap(QRect(r.right() - S2 - 2, r.bottom() - S2 - 2, S2 - 2, S2 - 2),
                     icons.pixmap("triangle-alert", ink, S2))
    if e["sync_status"] != "error":
        paint_car(p, r, e, bar=False, hovered=hovered)


def paint_task_block(p: QPainter, r: QRect, t: dict[str, Any], selected: bool) -> None:
    """SB-4: the title on up to two lines across the whole block, then the tick and the Overdue and High markers."""
    ink = draw_frame(p, r, "task", t, selected)
    inner = r.adjusted(HALF + 2, HALF, -HALF, -HALF)
    title = font(BASE_PT, bold=True)
    tm = QFontMetrics(title)
    p.setFont(title)
    p.setPen(QColor(ink))
    y = inner.top()
    for line in task_block_lines(r, t):
        p.drawText(QRect(inner.left(), y, inner.width(), tm.height()), Qt.AlignmentFlag.AlignVCenter, line)
        y += tm.height()
    tick = task_tick_rect(r)
    draw_tick(p, tick, t["done"], ink)
    x, py = tick.right() + HALF, tick.top() - 1
    if t["overdue"]:
        x += draw_pill(p, x, py, "Overdue", OVERDUE, r.right() - HALF, tick.height() + 2) + 2
    if t.get("effective_priority", t["priority"]) == "high":  # WO-FS-012 DS-10: bumped tasks too
        draw_pill(p, x, py, "High", HIGH, r.right() - HALF, tick.height() + 2)


# --- month: a grid of days with event and task bars (MV-1 to MV-8) ----------------------------------------------

@dataclass
class _Cell:
    day: str
    rect: QRect
    number: QRect
    bars: list[tuple[str, dict[str, Any], QRect]]
    more: int
    more_rect: QRect | None


class MonthGrid(_CalendarBase):
    def __init__(self) -> None:
        super().__init__()
        self.month = ""  # "2026-11": days outside it are dimmed (MV-1)
        self.hover_id = ""  # CR-9
        self.setMouseTracking(True)
        self.cells: list[_Cell] = []

    def load_month(self, data: dict[str, Any], weeks: list[list[date]], month: date) -> None:
        self.month = month.isoformat()[:7]
        self.load(data, [d.isoformat() for w in weeks for d in w])

    def _layout(self) -> list[_Cell]:
        xs, ws = _columns(self.width() + SCROLLBAR)
        rows = max(len(self.days) // 7, 1)
        height = (self.height() - WEEKDAY_ROW) / rows
        cells = []
        for n, day in enumerate(self.days):
            r, c = divmod(n, 7)
            rect = QRect(xs[c], int(WEEKDAY_ROW + r * height), ws[c], int(height))
            number = QRect(rect.left() + HALF, rect.top() + HALF, TAG_HEIGHT, TAG_HEIGHT)
            top = number.bottom() + HALF
            room = max((rect.bottom() - HALF - top) // (MONTH_BAR + 2), 1)
            shown, more = split_items(self.items.get(day, []), room)
            bars = []
            y = top
            for kind, item in shown:
                bars.append((kind, item, QRect(rect.left() + HALF, y, rect.width() - S1, MONTH_BAR)))
                y += MONTH_BAR + 2
            more_rect = QRect(rect.left() + HALF, y, rect.width() - S1, MONTH_BAR) if more else None
            cells.append(_Cell(day, rect, number, bars, more, more_rect))
        return cells

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(SURFACE))
        if self.month:  # WO-FS-009 MC-5: a thin band in the month's colour across the top of the grid
            p.fillRect(QRect(0, 0, self.width(), MONTH_BAND), QColor(month_colour(int(self.month[5:]))))
        xs, ws = _columns(self.width() + SCROLLBAR)
        p.setFont(font(SMALL_PT, bold=True))
        p.setPen(QColor(MUTED))
        for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            p.drawText(QRect(xs[i], 0, ws[i], WEEKDAY_ROW), Qt.AlignmentFlag.AlignCenter, name)
        self.cells = self._layout()
        for cell in self.cells:
            self._paint_cell(p, cell)
        p.end()

    def _paint_cell(self, p: QPainter, cell: _Cell) -> None:
        is_today = cell.day == self.today
        in_month = cell.day.startswith(self.month)
        if is_today:  # MV-2
            p.fillRect(cell.rect, QColor(TODAY_COLUMN))
        p.setPen(QPen(QColor(DIVIDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(cell.rect)
        if is_today:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TEXT))
            p.drawEllipse(cell.number)
        p.setFont(font(SMALL_PT, bold=True))
        p.setPen(QColor(BASE if is_today else TEXT if in_month else PAST))  # MV-1: other months dimmed
        p.drawText(cell.number, Qt.AlignmentFlag.AlignCenter, str(date.fromisoformat(cell.day).day))
        for kind, item, r in cell.bars:
            self._paint_bar(p, r, kind, item, self.selection == (kind, item["id"], cell.day),
                            hovered=kind == "event" and item["id"] == self.hover_id)
        if cell.more_rect is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TINT[TASK_BLOCK]))
            p.drawRoundedRect(cell.more_rect, R_CONTROL / 2, R_CONTROL / 2)
            p.setFont(font(SMALL_PT, bold=True))
            p.setPen(QColor(TEXT))
            p.drawText(cell.more_rect, Qt.AlignmentFlag.AlignCenter, f"+{cell.more} more")

    @staticmethod
    def _paint_bar(p: QPainter, r: QRect, kind: str, item: dict[str, Any], selected: bool,
                   hovered: bool = False) -> None:
        """MV-3: "6 pm Sunday dinner" on green; a tick and the title on blue; black text."""
        ink = draw_frame(p, r, kind, item, selected)
        f = font(SMALL_PT, bold=True)
        fm = QFontMetrics(f)
        left = r.left() + HALF + 1
        if kind == "task":
            tick = QRect(left, r.top() + (r.height() - S2 + 2) // 2, S2 - 2, S2 - 2)
            draw_tick(p, tick, item["done"], ink)
            left = tick.right() + HALF
            text = item["title"]
        else:
            text = f"{short_time(item['start_time'])} {item['title']}"
        right = r.right() - HALF
        if kind == "event":  # CR-9: the car, at the right end
            right -= paint_car(p, r, item, bar=True, hovered=hovered)
        high = kind == "task" and item.get("effective_priority", item["priority"]) == "high"  # WO-FS-012 DS-10
        if high and r.width() > S2 * 6:  # MV-5: "High" when there's room
            right -= pill_width("High") + 2
            draw_pill(p, right + 2, r.top() + 2, "High", HIGH, r.right(), r.height() - HALF)
        p.setFont(f)
        p.setPen(QColor(ink))
        p.drawText(QRect(left, r.top(), right - left, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, right - left))

    def _hit(self, pos: QPoint) -> tuple[_Cell, tuple[str, dict[str, Any], QRect] | None] | None:
        for cell in self.cells:
            if cell.rect.contains(pos):
                return cell, next((b for b in cell.bars if b[2].contains(pos)), None)
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        self.setFocus()
        hit = self._hit(pos)
        if hit is None:
            return
        cell, bar = hit
        if cell.number.adjusted(-HALF, -HALF, HALF, HALF).contains(pos):
            self.open_week.emit(cell.day)  # MV-7
        elif cell.more_rect is not None and cell.more_rect.contains(pos):
            self.more_items.emit(cell.day)  # MV-4
        elif bar is not None:
            kind, item, r = bar
            tick = QRect(r.left() + HALF + 1, r.top(), S2, r.height())
            if kind == "event" and not item.get("read_only") and car_rect(r, item, bar=True).contains(pos):
                self.car_clicked.emit(item["id"])  # CR-4, CR-5
            elif kind == "task" and tick.contains(pos):
                self.tick.emit(item["id"])
            else:
                self.select((kind, item["id"], cell.day))
        else:
            self.select(None)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        hit = self._hit(event.pos())
        if hit and hit[1]:
            self.context_item.emit(hit[1][0], hit[1][1]["id"], event.globalPos())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hit = self._hit(event.position().toPoint())
        hover = hit[1][1]["id"] if hit and hit[1] and hit[1][0] == "event" else ""
        if hover != self.hover_id:
            self.hover_id = hover
            self.update()

    def leaveEvent(self, event: QEvent) -> None:
        self.hover_id = ""
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        hit = self._hit(event.position().toPoint())
        if hit is None:
            return
        cell, bar = hit
        if bar is not None:
            self.open_item.emit(bar[0], bar[1]["id"])
        elif cell.more_rect is None or not cell.more_rect.contains(event.position().toPoint()):
            self.add_event_on.emit(cell.day)  # MV-7

    def event(self, e: QEvent) -> bool:
        if isinstance(e, QHelpEvent):
            hit = self._hit(e.pos())
            if hit and hit[1]:
                QToolTip.showText(e.globalPos(), item_tip(hit[1][0], hit[1][1]), self)
            elif hit and hit[0].more:
                QToolTip.showText(e.globalPos(), f"{hit[0].more} more on this day. Click to see them all.", self)
            elif hit and hit[0].number.contains(e.pos()):
                QToolTip.showText(e.globalPos(), "Open this week", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)
