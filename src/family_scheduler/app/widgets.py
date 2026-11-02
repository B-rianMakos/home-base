"""The building blocks of the WO-FS-004 look: rows and cards instead of tables, the sidebar, toasts, icon buttons."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from family_scheduler.app import icons
from family_scheduler.app.style import (
    ACCENT,
    ACCENT_BAR,
    ANIMATION_MS,
    AVATAR,
    BACKDROP,
    BASE,
    BASE_PT,
    DIMMED,
    EMPTY_ICON,
    EVENT_ACCENT,
    HALF,
    HEADER_HEIGHT,
    ICON,
    MUTED,
    OUTLINE,
    OVERDUE,
    PAST,
    R_CONTROL,
    RAISED,
    RAISED_HOVER,
    ROW_HEIGHT,
    S1,
    S2,
    S3,
    S4,
    SECTION_PT,
    SELECT,
    SELECT_TAG,
    SELECT_TEXT,
    SIDEBAR_WIDTH,
    SMALL_PT,
    SURFACE,
    TAG_HEIGHT,
    TAG_MAX_WIDTH,
    TAG_TEXT,
    TEXT,
    TICK,
    TINT,
    TOAST_WIDTH,
    TODAY_EVENT,
    animations_on,
    blend,
    font,
)

CAR_MARK = "\u2063"  # starts a tag that shows the car icon (WO-FS-010 CR-9); never shown itself


def car_tag(name: str) -> str:
    return CAR_MARK + name


ID_ROLE = Qt.ItemDataRole.UserRole
ROW_ROLE = Qt.ItemDataRole.UserRole + 1

ACTION_ICONS = {"edit": "pencil", "remove": "trash-2", "retry": "rotate-ccw", "discard": "x", "restore": "undo-2",
                "car": "car", "open": "external-link", "hide": "eye-off", "cancel": "trash-2",
                "grip": "grip-vertical", "up": "chevron-up", "down": "chevron-down", "to_item": "arrow-left-right",
                "to_food": "arrow-left-right", "delete_app": "trash-2", "delete_history": "trash-2",
                "delete_forever": "trash-2"}
DANGER_ACTIONS = {"delete_forever"}  # WO-FS-014 DT-1: red when hovered
ACTION_TIPS = {"edit": "Edit (Enter)", "remove": "Remove (Delete)", "retry": "Send to Google again",
               "car": "Which car? (WO-FS-010 CR-4)", "open": "Open in Google Calendar",
               "hide": "Hide from Home Base. It stays in Google Calendar.",  # WO-FS-011 HE-1
               "cancel": "Remove: cancel this event in Google Calendar. Invitees get a cancellation.",  # HE-9
               "grip": "Drag to move (Alt+Up, Alt+Down)", "up": "Move up (Alt+Up)", "down": "Move down (Alt+Down)",
               "to_item": "Move to Items", "to_food": "Move to Food",
               "delete_app": "Delete from Home Base. It stays in Google Calendar.",  # 1.9.1
               "delete_history": "Delete this entry from History",  # 1.10.1
               "delete_forever": "Delete forever. It can't be restored after 10 seconds.",  # WO-FS-014 DT-1
               "discard": "Discard: it never reached Google", "restore": "Restore"}


@dataclass
class Row:
    """One row of a list (LC-1): a main line, a second line, an accent, tags and hover actions."""

    id: str
    title: str
    subtitle: str = ""
    accent: str = EVENT_ACCENT          # LC-2
    title_colour: str = TEXT
    tags: list[tuple[str, str]] = field(default_factory=list)  # (text, meaning colour or TAG_TEXT), LC-3
    actions: list[str] = field(default_factory=lambda: ["edit", "remove"])  # LC-4
    tick: bool | None = None            # None: no tick; False/True: the round tick of a task (LC-8)
    dim: bool = False
    tooltip: str = ""
    wrap: bool = False                  # a row with no second line lets its main line wrap onto two (WO-FS-007 TR-b)
    disabled: tuple[str, ...] = ()      # actions shown dimmed that can't be clicked (WO-FS-011 GO-4)
    tag_tips: dict[str, str] = field(default_factory=dict)  # a tooltip of its own on a tag (WO-FS-012 DS-4)


@dataclass
class Header:
    """A month or section heading. It can't be selected and sticks to the top while its rows scroll (LC-6)."""

    text: str
    colour: str = TEXT
    pt: int = SECTION_PT
    note: str = ""                      # a smaller grey line under the title (FX-1)
    space_above: int = S2
    key: str = ""                       # names the section in the action signal ("header:<key>", "add")
    button: str = ""                    # a small button at the right of the title, e.g. "+ Add" (WO-FS-007 TL-2)
    level: int = 0                      # 0: a section title, which sticks (TL-5); 1: a sub-heading inside it
    band: str = ""                      # a rounded band behind the title, e.g. a month's colour (WO-FS-009 MC-4)


@dataclass
class Note:
    """A muted line inside a section, e.g. "Nothing on the calendar today."."""

    text: str


Entry = Row | Header | Note


def tag_colours(colour: str, selected: bool) -> tuple[str, str]:
    """(background, text) of a tag."""
    if selected:
        return SELECT_TAG, SELECT_TEXT
    return TINT.get(colour, RAISED_HOVER), colour


class RowDelegate(QStyledItemDelegate):
    """Paints rows, headers and notes, with the hover actions and the round tick (LC-1 to LC-5, LC-8)."""

    def __init__(self, view: RowList) -> None:
        super().__init__(view)
        self.view = view
        self.title_font = font(BASE_PT, weight=font().Weight.DemiBold)
        self.body_font = font(BASE_PT)
        self.tag_font = font(SMALL_PT, weight=font().Weight.DemiBold)

    # geometry --------------------------------------------------------------------------------------

    @staticmethod
    def entry(index: QModelIndex | QPersistentModelIndex) -> Entry | None:
        value = index.data(ROW_ROLE)
        return value if isinstance(value, Row | Header | Note) else None

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        e = self.entry(index)
        width = self.view.viewport().width()
        if isinstance(e, Header):
            height = QFontMetrics(font(e.pt, bold=True)).height() + e.space_above + S1
            if e.note:
                height += QFontMetrics(self.body_font).height()
            return QSize(width, max(height, HEADER_HEIGHT))
        if isinstance(e, Note):
            return QSize(width, HEADER_HEIGHT)
        if isinstance(e, Row) and not e.subtitle:
            return QSize(width, ROW_HEIGHT - S2)
        return QSize(width, ROW_HEIGHT)

    @staticmethod
    def card(rect: QRect) -> QRect:
        return rect.adjusted(0, HALF // 2, -HALF, -HALF // 2)

    def action_rects(self, rect: QRect, row: Row) -> dict[str, QRect]:
        card = self.card(rect)
        size = ICON + S2
        x = card.right() - S1 - size
        out = {}
        for name in reversed(row.actions):
            out[name] = QRect(x, card.center().y() - size // 2, size, size)
            x -= size + HALF
        return out

    def tick_rect(self, rect: QRect) -> QRect:
        card = self.card(rect)
        return QRect(card.left() + S2 + ACCENT_BAR, card.center().y() - TICK // 2, TICK, TICK)

    def text_left(self, rect: QRect, row: Row) -> int:
        card = self.card(rect)
        return card.left() + S2 + ACCENT_BAR + (TICK + S2 if row.tick is not None else 0)

    def tag_width(self, text: str) -> int:
        icon = ICON if text.startswith(CAR_MARK) else 0  # CR-9: a car tag has the car icon in front
        return min(QFontMetrics(self.tag_font).horizontalAdvance(text.lstrip(CAR_MARK)) + S2 + icon, TAG_MAX_WIDTH)

    # painting ----------------------------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex | QPersistentModelIndex) -> None:
        e = self.entry(index)
        rect: QRect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if isinstance(e, Header):
            self.paint_header(painter, rect, e)
        elif isinstance(e, Note):
            painter.setFont(self.body_font)
            painter.setPen(QColor(MUTED))
            painter.drawText(rect.adjusted(S2, 0, -S2, 0), Qt.AlignmentFlag.AlignVCenter, e.text)
        elif isinstance(e, Row):
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            hovered = self.view.hover_row == index.row()
            level = self.view.select_level if selected else self.view.hover_level
            self.paint_row(painter, rect, e, selected, hovered, level)
        painter.restore()

    def paint_header(self, p: QPainter, rect: QRect, h: Header, sticky: bool = False) -> None:
        if sticky:
            p.fillRect(rect, QColor(SURFACE))
        title = font(h.pt, bold=True)
        tm = QFontMetrics(title)
        bottom = rect.bottom() - HALF - (QFontMetrics(self.body_font).height() if h.note else 0)
        p.setFont(title)
        left = rect.left() + HALF
        if h.band:  # MC-4: the month's colour as a rounded band, with black or white text on it (MC-8)
            band = QRect(left, bottom - tm.height() - HALF // 2, tm.horizontalAdvance(h.text) + S2, tm.height() + HALF)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(h.band))
            p.drawRoundedRect(band, R_CONTROL, R_CONTROL)
            left += S1
        p.setPen(QColor(h.colour))
        p.drawText(QRect(left, bottom - tm.height(), rect.width() - S1, tm.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, h.text)
        if h.note:
            p.setFont(self.body_font)
            p.setPen(QColor(MUTED))
            p.drawText(QRect(rect.left() + HALF, bottom, rect.width() - S1, rect.bottom() - bottom),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, h.note)
        if h.button:
            r = self.header_button_rect(rect, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(RAISED_HOVER))
            p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
            p.setFont(self.tag_font)
            p.setPen(QColor(TEXT))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, h.button)

    def header_button_rect(self, rect: QRect, h: Header) -> QRect:
        """Where a section title's small button sits: at the right, level with the title."""
        title_h = QFontMetrics(font(h.pt, bold=True)).height()
        bottom = rect.bottom() - HALF - (QFontMetrics(self.body_font).height() if h.note else 0)
        width = QFontMetrics(self.tag_font).horizontalAdvance(h.button) + S2 + S1
        return QRect(rect.right() - S1 - width, bottom - title_h // 2 - TAG_HEIGHT // 2 - 1, width, TAG_HEIGHT + 2)

    def paint_row(self, p: QPainter, rect: QRect, row: Row, selected: bool, hovered: bool,
                  level: float = 1.0) -> None:
        card = self.card(rect)
        if selected or hovered:  # fades in over ANIMATION_MS (CF-4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(blend(SURFACE, SELECT if selected else RAISED, level)))
            p.drawRoundedRect(card, R_CONTROL, R_CONTROL)
        # Accent bar (LC-2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(SELECT_TEXT if selected else row.accent))
        bar = QRect(card.left() + S1, card.top() + S1 + HALF, ACCENT_BAR, card.height() - S2 - S1)
        p.drawRoundedRect(bar, ACCENT_BAR / 2, ACCENT_BAR / 2)
        # Round tick (LC-8)
        if row.tick is not None:
            t = self.tick_rect(rect)
            ink = SELECT_TEXT if selected else TEXT
            if row.tick:
                p.setBrush(QColor(ink))
                p.drawEllipse(t)
                p.drawPixmap(t.adjusted(HALF, HALF, -HALF, -HALF),
                             icons.pixmap("check", SELECT if selected else BASE, TICK - S1))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QColor(SELECT_TEXT if selected else MUTED))
                p.drawEllipse(t.adjusted(1, 1, -1, -1))
        # Actions on hover or selection (LC-4)
        show_actions = hovered or selected
        if show_actions:
            hover_action = self.view.hover_action
            for name, r in self.action_rects(rect, row).items():
                if hover_action == name:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(SELECT_TAG if selected else RAISED_HOVER))
                    p.drawRoundedRect(r, R_CONTROL, R_CONTROL)
                colour = SELECT_TEXT if selected else TEXT
                if hover_action == name and name in DANGER_ACTIONS and not selected:
                    colour = OVERDUE
                if name in row.disabled:
                    colour = blend(colour, SELECT if selected else RAISED, DIMMED)
                p.drawPixmap(r.adjusted(S1, S1, -S1, -S1), icons.pixmap(ACTION_ICONS[name], colour))
        text_rect, tags = self.layout_row(rect, row, show_actions)
        # Tags, right-aligned (LC-3)
        p.setFont(self.tag_font)
        x = text_rect.right() + S1
        for text, colour in tags:
            w = self.tag_width(text)
            r = QRect(x, card.center().y() - TAG_HEIGHT // 2, w, TAG_HEIGHT)
            bg, fg = tag_colours(colour, selected)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(r, TAG_HEIGHT / 2, TAG_HEIGHT / 2)
            p.setPen(QColor(fg))
            inner = r
            if text.startswith(CAR_MARK):  # CR-9: the filled car icon, then the car's name
                p.drawPixmap(QRect(r.left() + HALF + 2, r.center().y() - ICON // 2 + 1, ICON - 2, ICON - 2),
                             icons.pixmap("car", fg, ICON))
                inner = r.adjusted(ICON, 0, 0, 0)
                text = text.lstrip(CAR_MARK)
            p.drawText(inner, Qt.AlignmentFlag.AlignCenter,
                       QFontMetrics(self.tag_font).elidedText(text, Qt.TextElideMode.ElideRight, inner.width() - S1))
            x += w + HALF
        # Main line and second line
        left, width = text_rect.left(), text_rect.width()
        title_colour = SELECT_TEXT if selected else (PAST if row.dim else row.title_colour)
        sub_colour = SELECT_TEXT if selected else (PAST if row.dim else MUTED)
        tm, bm = QFontMetrics(self.title_font), QFontMetrics(self.body_font)
        if row.subtitle:
            top = card.center().y() - (tm.height() + bm.height()) // 2
        else:
            top = card.center().y() - tm.height() // 2
        p.setFont(self.title_font)
        p.setPen(QColor(title_colour))
        if row.wrap and not row.subtitle:  # TR-b: two lines, then "…" (the full text is in the tooltip)
            from family_scheduler.app.calview import wrap

            lines = wrap(row.title, self.title_font, width, 2)
            top = card.center().y() - tm.height() * len(lines) // 2
            for n, line in enumerate(lines):
                p.drawText(QRect(left, top + n * tm.height(), width, tm.height()), Qt.AlignmentFlag.AlignVCenter,
                           line)
            return
        p.drawText(QRect(left, top, width, tm.height()), Qt.AlignmentFlag.AlignVCenter,
                   tm.elidedText(row.title, Qt.TextElideMode.ElideRight, width))
        if row.subtitle:
            p.setFont(self.body_font)
            p.setPen(QColor(sub_colour))
            p.drawText(QRect(left, top + tm.height(), width, bm.height()), Qt.AlignmentFlag.AlignVCenter,
                       bm.elidedText(row.subtitle, Qt.TextElideMode.ElideRight, width))

    @staticmethod
    def fixed_part(row: Row) -> str:
        """The start of the second line that is the app's own text (dates, times); only the end, what people
        typed (a place, a name), may be shortened (FN-5)."""
        return row.subtitle.rsplit(" · ", 1)[0] + " · …" if " · " in row.subtitle else ""

    def layout_row(self, rect: QRect, row: Row, show_actions: bool) -> tuple[QRect, list[tuple[str, str]]]:
        """Where the two lines go, and which tags fit. The fixed text keeps its room; tags that don't fit fold
        into a "+N" tag from the left, and every tag stays in the tooltip."""
        card = self.card(rect)
        right = card.right() - S1
        if show_actions and row.actions:
            right = min(r.left() for r in self.action_rects(rect, row).values()) - HALF
        left = self.text_left(rect, row)
        need = max(QFontMetrics(self.body_font).horizontalAdvance(self.fixed_part(row)), S4 * 4)
        tags, hidden = list(row.tags), 0

        def tags_width() -> int:
            folded = [(f"+{hidden}", TAG_TEXT)] if hidden else []
            return sum(self.tag_width(t) + HALF for t, _ in folded + tags)

        while tags and right - tags_width() - S1 - left < need:
            tags.pop(0)
            hidden += 1
        shown = ([(f"+{hidden}", TAG_TEXT)] if hidden else []) + tags
        x = right - tags_width() + (HALF if shown else 0)
        return QRect(left, card.top(), max(x - S1 - left, S3), card.height()), shown

    # interaction ---------------------------------------------------------------------------------------

    def hit(self, index: QModelIndex | QPersistentModelIndex, pos: QPoint) -> str | None:
        """Which action, if any, is under the mouse: an action name, or "tick"."""
        e = self.entry(index)
        if not isinstance(e, Row):
            return None
        model_index = self.view.model().index(index.row(), 0)
        rect = self.view.visualRect(model_index)
        if e.tick is not None and self.tick_rect(rect).adjusted(-HALF, -HALF, HALF, HALF).contains(pos):
            return "tick"
        selected = self.view.selectionModel().isSelected(model_index)
        if self.view.hover_row == index.row() or selected:
            for name, r in self.action_rects(rect, e).items():
                if r.contains(pos):
                    return None if name in e.disabled else name
        return None

    def tag_tip(self, index: QModelIndex | QPersistentModelIndex, row: Row, pos: QPoint) -> str:
        """WO-FS-012 DS-4: the tooltip of the tag under the mouse, if it has one of its own."""
        if not row.tag_tips:
            return ""
        model_index = self.view.model().index(index.row(), 0)
        rect = self.view.visualRect(model_index)
        selected = self.view.selectionModel().isSelected(model_index)
        text_rect, shown = self.layout_row(rect, row, self.view.hover_row == index.row() or selected)
        x = text_rect.right() + S1
        for text, _ in shown:
            w = self.tag_width(text)
            if x <= pos.x() < x + w and text in row.tag_tips:
                return row.tag_tips[text]
            x += w + HALF
        return ""

    def helpEvent(self, event: QHelpEvent, view: QAbstractItemView, option: QStyleOptionViewItem,
                  index: QModelIndex | QPersistentModelIndex) -> bool:
        """Tooltips for row actions (CF-6), and the full text of a shortened row."""
        e = self.entry(index)
        name = self.hit(index, event.pos())
        if name:
            QToolTip.showText(event.globalPos(), "Check off (Space)" if name == "tick" else ACTION_TIPS[name], view)
            return True
        if isinstance(e, Row):
            tip = self.tag_tip(index, e, event.pos()) or e.tooltip or f"{e.title}\n{e.subtitle}".strip()
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        return super().helpEvent(event, view, option, index)


class _Sticky(QWidget):
    """The current section's header, kept at the top while its rows scroll beneath (LC-6)."""

    def __init__(self, view: RowList) -> None:
        super().__init__(view.viewport())
        self.view = view
        self.header: Header | None = None
        # Clicks go through to the list, which knows where the title's "+ Add" button is.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        if self.header:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.view.row_delegate.paint_header(p, self.rect(), self.header, sticky=True)
            p.end()


class _Empty(QWidget):
    """LC-7: an icon, a short message and the add button when a list is empty."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(S2)
        layout.addStretch()
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = QLabel()
        self.text.setProperty("muted", True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setWordWrap(True)
        self.button = QPushButton()
        self.button.setProperty("primary", True)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.button)
        row.addStretch()
        layout.addWidget(self.icon)
        layout.addWidget(self.text)
        layout.addLayout(row)
        layout.addStretch()
        self.handler: Callable[[], None] | None = None
        self.button.clicked.connect(lambda _=False: self.handler and self.handler())
        self.hide()


class RowList(QListWidget):
    """A list of rows, headers and notes, with hover actions, sticky headers and an empty state."""

    action = Signal(str, str)  # (row id, action name), including "tick" and "edit" on double-click

    def __init__(self) -> None:
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setUniformItemSizes(False)
        self.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.row_delegate = RowDelegate(self)
        self.setItemDelegate(self.row_delegate)
        self.hover_row = -1
        self.hover_action: str | None = None
        self.hover_level = 1.0
        self.select_level = 1.0
        self.hover_fade = self._fader("hover_level")
        self.select_fade = self._fader("select_level")
        self.itemSelectionChanged.connect(lambda: self._fade_in(self.select_fade))
        self.sticky = _Sticky(self)
        self.empty = _Empty(self)
        self.has_empty_state = False
        self.verticalScrollBar().valueChanged.connect(lambda _: self.update_sticky())
        self.itemDoubleClicked.connect(self._double_clicked)

    def _fader(self, attribute: str) -> QVariantAnimation:
        anim = QVariantAnimation(self)
        anim.setDuration(ANIMATION_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def step(value: object) -> None:
            setattr(self, attribute, float(value))  # type: ignore[arg-type]
            self.viewport().update()

        anim.valueChanged.connect(step)
        return anim

    def _fade_in(self, anim: QVariantAnimation) -> None:
        """CF-4: hover and selection fade in, unless Windows animations are off."""
        if animations_on() and not self.signalsBlocked():
            anim.stop()
            anim.start()

    # content -------------------------------------------------------------------------------------------

    def set_entries(self, entries: list[Entry]) -> None:
        """Replace the rows, keeping the same row selected (SL-3)."""
        selected = self.selected_id()
        scroll = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.clear()
        for e in entries:
            item = QListWidgetItem()
            item.setData(ROW_ROLE, e)
            if isinstance(e, Row):
                item.setData(ID_ROLE, e.id)
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.addItem(item)
            if isinstance(e, Row) and e.id == selected:
                item.setSelected(True)
        self.blockSignals(False)
        self.verticalScrollBar().setValue(scroll)
        self.itemSelectionChanged.emit()
        rows = sum(isinstance(e, Row) for e in entries)
        self.empty.setVisible(self.has_empty_state and rows == 0)
        self._place_overlays()
        self.update_sticky()

    def set_empty_state(self, icon_name: str, text: str, button: str, handler: Callable[[], None] | None) -> None:
        self.has_empty_state = True
        self.empty.icon.setPixmap(icons.pixmap(icon_name, MUTED, EMPTY_ICON))
        self.empty.text.setText(text)
        self.empty.button.setText(button)
        self.empty.button.setIcon(icons.icon("plus", BASE))
        self.empty.button.setVisible(bool(button))
        self.empty.handler = handler

    def selected_id(self) -> str | None:
        items = self.selectedItems()
        value = items[0].data(ID_ROLE) if items else None
        return str(value) if value else None

    def select_id(self, row_id: str) -> None:
        for i in range(self.count()):
            item = self.item(i)
            if item is not None and item.data(ID_ROLE) == row_id:
                self.setCurrentItem(item)
                return

    def row_index(self, row_id: str) -> int:
        for i in range(self.count()):
            item = self.item(i)
            if item is not None and item.data(ID_ROLE) == row_id:
                return i
        return -1

    # overlays --------------------------------------------------------------------------------------------

    def _place_overlays(self) -> None:
        self.empty.setGeometry(self.viewport().rect())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place_overlays()
        self.update_sticky()

    def update_sticky(self) -> None:
        top = self.indexAt(QPoint(S1, 1))
        header: Header | None = None
        if top.isValid():
            for i in range(top.row(), -1, -1):
                e = self.row_delegate.entry(self.model().index(i, 0))
                if isinstance(e, Header) and e.level == 0:  # sub-headings never stick (TL-5)
                    # A header still fully in view needs no copy at the top.
                    at_top = i == top.row() and self.visualRect(self.model().index(i, 0)).top() >= 0
                    header = None if at_top else e
                    break
        self.sticky.header = header
        if header is None:
            self.sticky.hide()
            return
        height = QFontMetrics(font(header.pt, bold=True)).height() + S1 + (
            QFontMetrics(font()).height() if header.note else 0)
        self.sticky.setGeometry(0, 0, self.viewport().width(), height)
        self.sticky.show()
        self.sticky.raise_()
        self.sticky.update()

    # mouse and keys --------------------------------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() and isinstance(self.row_delegate.entry(index), Row) else -1
        name = self.row_delegate.hit(index, event.position().toPoint()) if row >= 0 else None
        if (row, name) != (self.hover_row, self.hover_action):
            if row != self.hover_row and row >= 0:
                self._fade_in(self.hover_fade)
            self.hover_row, self.hover_action = row, name
            self.viewport().update()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.hover_row, self.hover_action = -1, None
        self.viewport().update()

    def header_button_at(self, pos: QPoint) -> Header | None:
        """A section title's "+ Add" button under the mouse, in the list or on the sticky copy (TL-2)."""
        if self.sticky.isVisible() and self.sticky.geometry().contains(pos):
            h = self.sticky.header
            if h and h.button and self.row_delegate.header_button_rect(self.sticky.rect(), h).contains(pos):
                return h
            return None
        index = self.indexAt(pos)
        e = self.row_delegate.entry(index) if index.isValid() else None
        if isinstance(e, Header) and e.button and \
                self.row_delegate.header_button_rect(self.visualRect(index), e).contains(pos):
            return e
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        button = self.header_button_at(event.position().toPoint())
        if button is not None and event.button() == Qt.MouseButton.LeftButton:
            self.action.emit(f"header:{button.key}", "add")
            return
        index = self.indexAt(event.position().toPoint())
        name = self.row_delegate.hit(index, event.position().toPoint()) if index.isValid() else None
        if name and event.button() == Qt.MouseButton.LeftButton:
            item = self.item(index.row())
            if item is not None:
                self.setCurrentItem(item)
                self.action.emit(str(item.data(ID_ROLE)), name)
            return
        super().mousePressEvent(event)

    def _double_clicked(self, item: QListWidgetItem) -> None:
        if item.data(ID_ROLE):
            self.action.emit(str(item.data(ID_ROLE)), "edit")  # LC-5

    def keyPressEvent(self, event: QKeyEvent) -> None:
        row_id = self.selected_id()
        keys = {Qt.Key.Key_Delete: "remove", Qt.Key.Key_Return: "edit", Qt.Key.Key_Enter: "edit",
                Qt.Key.Key_Space: "tick"}
        if row_id and event.key() in keys:
            self.action.emit(row_id, keys[Qt.Key(event.key())])
            return
        super().keyPressEvent(event)


class GroceryList(RowList):
    """WO-FS-011 GO-2, GO-3: the Groceries list, whose items move by dragging the grip, with a line where the item
    will land, and with Alt+Up and Alt+Down. `place` is a function from a row to its group, or None for rows that
    can't be a drop target (headers carry their group in their key)."""

    dropped = Signal(str, str, int)  # (item id, group, index in that group's to-buy list)

    def __init__(self) -> None:
        super().__init__()
        self.groups: dict[str, str] = {}       # item id -> group, for the to-buy rows
        self.dragging: str | None = None
        self.drop_line: int | None = None      # y of the drop line
        self.target: tuple[str, int] | None = None

    def _rows(self) -> list[tuple[int, Any]]:
        return [(i, self.row_delegate.entry(self.model().index(i, 0))) for i in range(self.count())]

    def _target(self, pos: QPoint) -> tuple[tuple[str, int], int] | None:
        """Where a drop at `pos` lands: (group, index), and the y of the line to draw."""
        group, index, y = None, 0, 0
        best: tuple[tuple[str, int], int] | None = None
        for i, e in self._rows():
            rect = self.visualRect(self.model().index(i, 0))
            if isinstance(e, Header) and e.key in ("food", "item"):
                group, index = e.key, 0
                y = rect.bottom()
                if best is None or pos.y() >= rect.top():
                    best = ((group, 0), y)
                continue
            row_group = self.groups.get(e.id) if isinstance(e, Row) else None
            if group is None or row_group is None:
                continue
            if e.id == self.dragging:
                continue
            if pos.y() >= rect.top():
                above = pos.y() < rect.center().y()
                best = ((group, index if above else index + 1), rect.top() if above else rect.bottom())
            index += 1
        return best

    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.position().toPoint())
        name = self.row_delegate.hit(index, event.position().toPoint()) if index.isValid() else None
        if name == "grip" and event.button() == Qt.MouseButton.LeftButton:
            item = self.item(index.row())
            if item is not None:
                self.setCurrentItem(item)
                self.dragging = str(item.data(ID_ROLE))
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging is None:
            super().mouseMoveEvent(event)
            return
        found = self._target(event.position().toPoint())
        self.target, self.drop_line = (found[0], found[1]) if found else (None, None)
        self.viewport().update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.dragging is None:
            super().mouseReleaseEvent(event)
            return
        item_id, target = self.dragging, self.target
        self.dragging, self.target, self.drop_line = None, None, None
        self.unsetCursor()
        self.viewport().update()
        if target is not None:
            self.dropped.emit(item_id, target[0], target[1])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        row_id = self.selected_id()
        if row_id and event.modifiers() & Qt.KeyboardModifier.AltModifier and \
                event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.action.emit(row_id, "up" if event.key() == Qt.Key.Key_Up else "down")  # GO-2
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.dragging is not None and self.drop_line is not None:  # GO-3: where it will land
            p = QPainter(self.viewport())
            p.setPen(QPen(QColor(ACCENT), OUTLINE))
            p.drawLine(S2, self.drop_line, self.viewport().width() - S2, self.drop_line)
            p.end()


# --- buttons -----------------------------------------------------------------------------------------------

def button(text: str, handler: Callable[[], None], *, primary: bool = False, danger: bool = False,
           icon_name: str | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    if icon_name:
        b.setIcon(icons.icon(icon_name, BASE if primary else TEXT))
        b.setIconSize(QSize(ICON, ICON))
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(lambda _=False: handler())
    return b


def icon_button(icon_name: str, tip: str, handler: Callable[[], None], colour: str = TEXT) -> QPushButton:
    """CF-1: no border until hovered, with a tooltip (CF-6)."""
    b = QPushButton()
    b.setProperty("iconOnly", True)
    b.setIcon(icons.icon(icon_name, colour))
    b.setIconSize(QSize(ICON, ICON))
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(lambda _=False: handler())
    return b


class BadgeButton(QPushButton):
    """A button with a count pill at its right edge, e.g. Replies (WO-FS-005 WT-2). Hidden at zero."""

    def __init__(self, text: str, handler: Callable[[], None], icon_name: str, tip: str) -> None:
        super().__init__(text)
        self.setIcon(icons.icon(icon_name, TEXT))
        self.setIconSize(QSize(ICON, ICON))
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda _=False: handler())
        self.count = 0

    def set_count(self, count: int) -> None:
        self.count = count
        self.setProperty("badged", bool(count))  # QSS keeps room on the right for the pill
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.count:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tf = font(SMALL_PT, bold=True)
        text = str(self.count)
        w = max(QFontMetrics(tf).horizontalAdvance(text) + S1, TAG_HEIGHT - HALF)
        r = QRect(self.width() - w - S1, (self.height() - TAG_HEIGHT + HALF) // 2, w, TAG_HEIGHT - HALF)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TEXT))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(tf)
        p.setPen(QColor(BASE))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


# --- sidebar ---------------------------------------------------------------------------------------------

class _NavDelegate(QStyledItemDelegate):
    def __init__(self, nav: QListWidget, badges: dict[str, tuple[int, str]]) -> None:
        super().__init__(nav)
        self.nav = nav
        self.badges = badges

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        return QSize(SIDEBAR_WIDTH - S2, HEADER_HEIGHT)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        rect: QRect = option.rect.adjusted(0, 2, 0, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        title = str(index.data(Qt.ItemDataRole.DisplayRole))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(SELECT if selected else RAISED))
            p.drawRoundedRect(rect, R_CONTROL, R_CONTROL)
        ink = SELECT_TEXT if selected else TEXT
        p.drawPixmap(QRect(rect.left() + S1 + HALF, rect.center().y() - ICON // 2, ICON, ICON),
                     icons.pixmap(icons.VIEW_ICONS[title], ink))
        p.setFont(font(BASE_PT, weight=font().Weight.Medium if not selected else font().Weight.DemiBold))
        p.setPen(QColor(ink))
        p.drawText(rect.adjusted(S1 + HALF + ICON + S1 + HALF, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, title)
        count, colour = self.badges.get(title, (0, ""))
        if count:  # NV-2: hidden when zero
            tf = font(SMALL_PT, bold=True)
            p.setFont(tf)
            text = str(count)
            w = max(QFontMetrics(tf).horizontalAdvance(text) + S2, TAG_HEIGHT)
            badge = QRect(rect.right() - S1 - w, rect.center().y() - TAG_HEIGHT // 2, w, TAG_HEIGHT)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colour))
            p.drawRoundedRect(badge, TAG_HEIGHT / 2, TAG_HEIGHT / 2)
            p.setPen(QColor(BASE))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()


class ElidedLabel(QLabel):
    """A one-line label that ends in "…" when it's wider than the space it has."""

    def __init__(self) -> None:
        super().__init__()
        self.full = ""
        self.setMinimumWidth(S1)

    def set_full_text(self, text: str) -> None:
        self.full = text
        self.setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self) -> QSize:
        return QSize(S1, super().minimumSizeHint().height())

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideRight, self.width()))
        p.end()


class Avatar(QLabel):
    """NV-3: a round avatar with the first letter of the member's email."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(AVATAR, AVATAR)
        self.letter = "?"

    def set_email(self, email: str) -> None:
        self.letter = (email[:1] or "?").upper()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(self.rect())
        p.setPen(QColor(BASE))
        p.setFont(font(BASE_PT, bold=True))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.letter)
        p.end()


class Sidebar(QFrame):
    """NV-1 to NV-3: the app's name, the views with icons and badges, and the current member at the bottom."""

    def __init__(self, titles: list[str], on_switch: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(S1, S2, S1, S1)
        layout.setSpacing(S1)
        brand = QHBoxLayout()
        brand.setContentsMargins(S1, 0, 0, S1)
        mark = QLabel()
        mark.setPixmap(icons.app_icon().pixmap(S3 + HALF, S3 + HALF))
        brand.addWidget(mark)
        name = QLabel("Home Base")  # WO-FS-011 HB-1
        name.setFont(font(BASE_PT + 1, bold=True))
        brand.addWidget(name)
        brand.addStretch()
        layout.addLayout(brand)
        self.badges: dict[str, tuple[int, str]] = {}
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setMouseTracking(True)
        self.nav.setItemDelegate(_NavDelegate(self.nav, self.badges))
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for title in titles:
            self.nav.addItem(title)
        layout.addWidget(self.nav, 1)
        member = QFrame()
        member.setObjectName("Card")
        row = QHBoxLayout(member)
        row.setContentsMargins(S1, S1, HALF, S1)
        row.setSpacing(S1)
        self.avatar = Avatar()
        row.addWidget(self.avatar)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.email = ElidedLabel()
        self.email.setObjectName("MemberEmail")
        self.domain = ElidedLabel()
        self.domain.setObjectName("MemberDomain")
        for label in (self.email, self.domain):
            names.addWidget(label)
        row.addLayout(names, 1)
        self.switch = icon_button("arrow-left-right", "Switch to another family member", on_switch)
        row.addWidget(self.switch)
        layout.addWidget(member)

    def set_member(self, email: str) -> None:
        self.avatar.set_email(email)
        name, _, domain = email.partition("@")
        self.email.set_full_text(name + "@")
        self.domain.set_full_text(domain)
        self.setToolTip(f"Using the app as {email}")

    def set_badge(self, title: str, count: int, colour: str) -> None:
        self.badges[title] = (count, colour)
        self.nav.viewport().update()


# --- toasts -----------------------------------------------------------------------------------------------

class Toast(QFrame):
    """CF-3: a small rounded notice in the corner that fades out. Also carries the Clear all countdown."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(S2, S1 + HALF, S1, S1 + HALF)
        layout.setSpacing(S1 + HALF)
        self.icon = QLabel()
        self.text = QLabel()
        self.button = QPushButton()
        self.button.setProperty("primary", True)
        self.button.hide()
        layout.addWidget(self.icon)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.button)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade.setDuration(ANIMATION_MS)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        self.on_click: Callable[[], None] | None = None
        self.button.clicked.connect(self._clicked)
        self.hide()

    def show_message(self, text: str, *, kind: str = "ok", button: str = "",
                     on_click: Callable[[], None] | None = None, seconds: float = 5) -> None:
        colour = {"ok": TODAY_EVENT, "error": OVERDUE, "info": TEXT}[kind]
        name = {"ok": "circle-check", "error": "triangle-alert", "info": "undo-2"}[kind]
        self.icon.setPixmap(icons.pixmap(name, colour))
        self.text.setText(text)
        self.button.setText(button)
        self.button.setVisible(bool(button))
        self.on_click = on_click
        self.adjustSize()
        self.reposition()
        self.raise_()
        if not self.isVisible():
            self.show()
            if animations_on():
                self.fade.stop()
                self.fade.setStartValue(0.0)
                self.fade.setEndValue(1.0)
                self.fade.start()
            else:
                self.effect.setOpacity(1.0)
        if seconds:
            self.timer.start(int(seconds * 1000))
        else:
            self.timer.stop()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(max(self.sizeHint().width(), TOAST_WIDTH), parent.width() - S4 * 2)
        self.resize(width, self.sizeHint().height())
        self.move(parent.width() - width - S3, parent.height() - self.height() - S3)

    def dismiss(self) -> None:
        self.timer.stop()
        if not self.isVisible():
            return
        if animations_on():
            self.fade.stop()
            self.fade.setStartValue(self.effect.opacity())
            self.fade.setEndValue(0.0)
            self.fade.finished.connect(self._hide_after_fade)
            self.fade.start()
        else:
            self.hide()

    def _hide_after_fade(self) -> None:
        self.fade.finished.disconnect(self._hide_after_fade)
        if self.effect.opacity() == 0.0:
            self.hide()

    def _clicked(self) -> None:
        handler = self.on_click
        self.dismiss()
        if handler:
            handler()



# --- backdrop ----------------------------------------------------------------------------------------------

class Backdrop(QWidget):
    """MS-5: dims the window behind a dialog."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setGeometry(window.rect())
        window.installEventFilter(self)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.show()
        self.raise_()
        if animations_on():
            anim = QPropertyAnimation(self.effect, b"opacity", self)
            anim.setDuration(ANIMATION_MS)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.start()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and isinstance(obj, QWidget):
            self.setGeometry(obj.rect())
        return False

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), BACKDROP)
        p.end()


def view_header(title: str) -> tuple[QHBoxLayout, QLabel, QLabel, QHBoxLayout]:
    """NV-4: title and a muted line on the left; actions on the right. Returns (row, title, subtitle, right)."""
    row = QHBoxLayout()
    row.setSpacing(S1)
    left = QVBoxLayout()
    left.setSpacing(2)
    heading = QLabel(title)
    heading.setObjectName("ViewTitle")
    left.addWidget(heading)
    subtitle = QLabel()
    subtitle.setProperty("muted", True)
    left.addWidget(subtitle)
    row.addLayout(left)
    row.addStretch()
    right = QHBoxLayout()
    right.setSpacing(S1)
    row.addLayout(right)
    return row, heading, subtitle, right
