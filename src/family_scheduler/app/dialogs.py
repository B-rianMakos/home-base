"""Dialogs: Who's using the app, add/edit forms, confirmations, hub address.

WO-FS-004: every dialog is a rounded card on the raised layer, over a dimmed backdrop of the main window (MS-5),
with labels above the fields (CF-2).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import UTC, date, datetime
from functools import partial
from html import escape
from typing import Any

from PySide6.QtCore import QDate, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from family_scheduler.app import icons, timepick
from family_scheduler.app.pickers import TimePicker, date_field
from family_scheduler.app.style import (
    ANIMATION_MS,
    BACKDROP,
    BASE,
    CAR_CARDS_SHOWN,
    CAR_POPUP_WIDTH,
    DIALOG_ACCENTS,
    EVENT_POPUP_WIDTH,
    GROCERY_UNDO_S,
    HALF,
    HIDDEN_POPUP_WIDTH,
    ICON,
    MUTED,
    OVERDUE,
    REPEAT_LABELS,
    S1,
    S2,
    S3,
    SELECT,
    TEXT,
    TEXT_BOX_HEIGHT,
    animations_on,
    colour_am_pm,
    day_label,
    short_email,
    time_range,
    today_iso,
)
from family_scheduler.app.widgets import Backdrop, button, icon_button
from family_scheduler.client.hub_client import HubError, HubUnreachable
from family_scheduler.organizer import DUE_SOON_DAYS, effective_priority
from family_scheduler.recurrence import month_end_rule, ordinal, repeat_anchor
from family_scheduler.timeutil import to_local

SaveFn = Callable[[dict[str, Any]], Any]


class CardDialog(QDialog):
    """A rounded card over a dimmed backdrop of the window behind it (MS-5), fading in (CF-4)."""

    def __init__(self, title: str, parent: QWidget | None, width: int = 480) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("CardHost")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(S3, S3, S3, S3)
        self.card = QFrame()
        self.card.setObjectName("DialogCard")
        self.card.setMinimumWidth(width)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(S3 * 2)
        shadow.setOffset(0, S1)
        shadow.setColor(BACKDROP)
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(S3, S2 + HALF, S3, S3)
        self.body.setSpacing(S2)
        head = QHBoxLayout()
        head.setSpacing(S1)
        self.title_icon = QLabel()  # WO-FS-013 EC-1: shown by set_accent
        self.title_icon.hide()
        head.addWidget(self.title_icon)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("DialogTitle")
        head.addWidget(self.title_label)
        head.addStretch()
        head.addWidget(icon_button("x", "Close (Esc)", self.reject, MUTED))
        self.body.addLayout(head)
        self._backdrop: Backdrop | None = None
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(ANIMATION_MS)

    accent = ""

    def set_accent(self, key: str, icon_name: str = "") -> None:
        """WO-FS-013 TP-e, EC-1, WC-6: a coloured line along the top of the card (a priority, or "event"), and an
        icon beside the title in the same colour."""
        self.accent = key
        self.card.setProperty("accent", key)
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        if icon_name:
            self.title_icon.setPixmap(icons.pixmap(icon_name, DIALOG_ACCENTS[key], ICON + HALF))
            self.title_icon.show()

    def host(self) -> QWidget | None:
        parent = self.parentWidget()
        return parent.window() if parent is not None else None

    def open_backdrop(self) -> None:
        host = self.host()
        if host is not None and host.isVisible() and self._backdrop is None:
            self._backdrop = Backdrop(host)

    def close_backdrop(self) -> None:
        if self._backdrop is not None:
            self._backdrop.hide()  # at once: deleteLater waits for the event loop
            self._backdrop.deleteLater()
            self._backdrop = None

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.adjustSize()
        host = self.host()
        if host is not None:
            centre = host.frameGeometry().center()
            self.move(centre.x() - self.width() // 2, centre.y() - self.height() // 2)
        if animations_on():
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()

    def exec(self) -> int:
        self.open_backdrop()
        try:
            return super().exec()
        finally:
            self.close_backdrop()

    def button_row(self, *buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(S1)
        row.addStretch()
        for b in buttons:
            row.addWidget(b)
        return row


class MessageDialog(CardDialog):
    def __init__(self, parent: QWidget | None, title: str, text: str, detail: str, yes: str, *,
                 danger: bool, cancel: bool, cancel_text: str = "Cancel") -> None:
        super().__init__(title, parent, width=440)
        row = QHBoxLayout()
        row.setSpacing(S2)
        if danger:
            mark = QLabel()
            mark.setPixmap(icons.pixmap("triangle-alert", OVERDUE, ICON + S1))
            mark.setAlignment(Qt.AlignmentFlag.AlignTop)
            row.addWidget(mark)
        texts = QVBoxLayout()
        texts.setSpacing(S1)
        main = QLabel(text)
        main.setWordWrap(True)
        main.setObjectName("MemberEmail")
        texts.addWidget(main)
        if detail:
            more = QLabel(detail)
            more.setWordWrap(True)
            more.setProperty("muted", True)
            texts.addWidget(more)
        row.addLayout(texts, 1)
        self.body.addLayout(row)
        self.ok = button(yes, self.accept, primary=not danger, danger=danger)
        buttons = [button(cancel_text, self.reject)] if cancel else []
        self.body.addLayout(self.button_row(*buttons, self.ok))
        self.ok.setDefault(not danger)


def info(parent: QWidget | None, title: str, text: str) -> None:
    MessageDialog(parent, title, text, "", "OK", danger=False, cancel=False).exec()


def error(parent: QWidget | None, text: str) -> None:
    MessageDialog(parent, "Home Base", text, "", "OK", danger=True, cancel=False).exec()


def confirm(parent: QWidget | None, title: str, text: str, yes: str, *, danger: bool = True,
            detail: str = "") -> bool:
    return MessageDialog(parent, title, text, detail, yes, danger=danger, cancel=True).exec() == \
        QDialog.DialogCode.Accepted


class WhoDialog(CardDialog):
    """WU-2: pick a family member. No password."""

    def __init__(self, members: list[dict[str, Any]], current: str | None, parent: QWidget | None = None) -> None:
        super().__init__("Who's using the app?", parent, width=440)
        self.title_label.setObjectName("ViewTitle")
        self.chosen: str | None = None
        hint = QLabel("Pick your email. No password needed on the home network.")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        for m in members:
            label = m["email"] + ("   (admin)" if m.get("is_admin") else "")
            b = button(label, partial(self._pick, m["email"]), primary=m["email"] == current)
            b.setObjectName("MemberButton")
            self.body.addWidget(b)

    def _pick(self, email: str) -> None:
        self.chosen = email
        self.accept()


class HubAddressDialog(CardDialog):
    """PC-4 fallback: type the hub PC's name or address."""

    def __init__(self, message: str, parent: QWidget | None = None) -> None:
        super().__init__("Find the home hub", parent, width=480)
        text = QLabel(message)
        text.setWordWrap(True)
        self.body.addWidget(text)
        self.address = QLineEdit()
        self.address.setPlaceholderText("e.g. DESKTOP-ABC123 or 192.168.1.20")
        self.body.addLayout(field("Hub PC name or address", self.address))
        self.search_again = False
        self.body.addLayout(self.button_row(button("Quit", self.reject), button("Search again", self._search),
                                            button("Connect", self.accept, primary=True)))

    def _search(self) -> None:
        self.search_again = True
        self.accept()


def section_label(text: str) -> QLabel:
    """WO-FS-013 EC-5: a small label that sets a block of the form apart."""
    label = QLabel(text)
    label.setObjectName("FormSection")
    return label


def field(label: str, widget: QWidget | QLayout) -> QVBoxLayout:
    """CF-2: the label sits above the field."""
    box = QVBoxLayout()
    box.setSpacing(HALF + 2)
    text = QLabel(label)
    text.setObjectName("FieldLabel")
    box.addWidget(text)
    if isinstance(widget, QLayout):
        box.addLayout(widget)
    else:
        box.addWidget(widget)
    return box


class _SavingDialog(CardDialog):
    """A form that saves through the hub and stays open on errors."""

    def __init__(self, title: str, save: SaveFn, parent: QWidget | None, width: int = 480) -> None:
        super().__init__(title, parent, width)
        self._save = save
        self.result_value: Any = None
        self.form = QVBoxLayout()
        self.form.setSpacing(S2)
        self.body.addLayout(self.form)
        self.error = QLabel()
        self.error.setObjectName("Banner")
        self.error.setWordWrap(True)
        self.error.hide()
        self.body.addWidget(self.error)
        self.save_button = button("Save", self._try_save, primary=True, tip="Save (Ctrl+Enter)")
        self.save_button.setShortcut("Ctrl+Return")
        self.cancel_button = button("Cancel", self.reject)

    def finish_layout(self) -> None:
        self.body.addLayout(self.button_row(self.cancel_button, self.save_button))

    def body_data(self) -> dict[str, Any]:
        raise NotImplementedError

    def _try_save(self) -> None:
        self.error.hide()
        try:
            self.result_value = self._save(self.body_data())
        except HubError as err:
            if not self.handle_error(err):
                self.error.setText(err.detail)
                self.error.show()
            return
        except HubUnreachable as err:
            self.error.setText(str(err))
            self.error.show()
            return
        self.accept()

    def handle_error(self, err: HubError) -> bool:
        return False


class EventDialog(_SavingDialog):
    def __init__(self, members: list[dict[str, Any]], save: SaveFn, existing: dict[str, Any] | None = None,
                 parent: QWidget | None = None, *, sender: dict[str, Any] | None = None, day: str | None = None,
                 start: str | None = None, end: str | None = None) -> None:
        """`sender`: who sends a new event's invites (GS-3). `day`, `start`, `end`: a slot double-clicked on the
        Today calendar (WU-d)."""
        super().__init__("Edit event" if existing else "New event", save, parent, width=EVENT_POPUP_WIDTH)
        self.set_accent("event", "calendar-clock")  # WO-FS-013 EC-1
        self.existing = existing
        self.acknowledge = False
        self.title = QLineEdit(existing["title"] if existing else "")
        self.title.setMaxLength(120)
        self.title.setPlaceholderText("e.g. Dentist")
        self.date = date_field(existing["date"] if existing else day or today_iso())
        # TM-4: a new event starts at the next 5-minute step and lasts an hour; an existing one keeps its exact
        # times (TM-6); an all-day one gets the defaults, used if All day is switched off (AD-1).
        first, last = timepick.default_times(to_local(datetime.now(UTC)))
        if existing and existing["start_time"]:
            first, last = existing["start_time"], existing["end_time"]
        elif start and end:
            first, last = start, end
        self.all_day = QCheckBox("All day")  # AD-1; WO-FS-013 EC-2: a switch, green when on
        self.all_day.setObjectName("Switch")
        self.all_day.setChecked(bool(existing and existing.get("all_day")))
        self.start = TimePicker(first, "Start")
        self.end = TimePicker(last, "End")
        self._shown_start = first
        self.times_summary = QLabel()  # TM-3: "4:15 pm to 5:00 pm (45 min)"
        self.times_summary.setProperty("muted", True)
        self.times_problem = QLabel()  # TM-5
        self.times_problem.setObjectName("Banner")
        self.times_problem.setWordWrap(True)
        self.location = QLineEdit(existing["location"] if existing else "")
        self.location.setPlaceholderText("Where it happens")
        self.repeat = QComboBox()
        for key, label in REPEAT_LABELS.items():
            self.repeat.addItem(label, key)
        self.repeat.setCurrentIndex(list(REPEAT_LABELS).index(existing["repeat"] if existing else "none"))
        self.form.addLayout(field("Title", self.title))
        when = QHBoxLayout()  # WO-FS-013 EC-5: When, Where, Who, Car; All day sits on the When line
        when.addWidget(section_label("When"))
        when.addStretch()
        when.addWidget(self.all_day)
        self.form.addLayout(when)
        grid = QGridLayout()
        grid.setHorizontalSpacing(S2)
        grid.setVerticalSpacing(S2)
        grid.addWidget(self.date, 0, 0)  # "When" names the block, so the date and Repeats need no labels of their own
        grid.addWidget(self.repeat, 0, 1)
        self.form.addLayout(grid)
        self.times = QWidget()
        times = QVBoxLayout(self.times)
        times.setContentsMargins(0, 0, 0, 0)
        times.setSpacing(S1)
        wheels = QHBoxLayout()
        wheels.setSpacing(S2)
        wheels.addLayout(field("Starts", self.start))
        wheels.addLayout(field("Ends", self.end))
        wheels.addStretch()
        times.addLayout(wheels)
        times.addWidget(self.times_summary)
        times.addWidget(self.times_problem)
        self.form.addWidget(self.times)
        self.start.changed.connect(self._start_moved)
        self.end.changed.connect(self._check_times)
        self.all_day.toggled.connect(self._check_times)
        self.form.addWidget(section_label("Where"))
        self.form.addWidget(self.location)
        self.form.addWidget(section_label("Who"))
        chips = QGridLayout()  # EC-6: invitees as chips with a tick, on when invited
        chips.setHorizontalSpacing(S1)
        chips.setVerticalSpacing(S1)
        chosen = {i["email"] for i in existing["invitees"]} if existing else None
        self.invitees: list[QPushButton] = []
        for n, m in enumerate(members):
            chip = QPushButton(m["email"])
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.toggled.connect(partial(self._chip_changed, chip))
            chip.setChecked(chosen is None or m["email"] in chosen)  # EV-2: everyone by default
            self._chip_changed(chip, chip.isChecked())
            self.invitees.append(chip)
            chips.addWidget(chip, n // 2, n % 2)
        self.form.addLayout(chips)
        # WO-FS-010 CR-4: the car button. The choice is applied once the event is saved.
        self.cars: list[dict[str, Any]] = []
        self.car_status: Callable[[], list[dict[str, Any]]] | None = None
        self.car_id: int | None = (existing.get("car") or {}).get("id") if existing else None
        self.car_changed = False
        self.car_cards = QGridLayout()  # WO-FS-013 WC-9: the same card buttons as Which car?, compact
        self.car_cards.setHorizontalSpacing(S1)
        self.car_cards.setVerticalSpacing(S1)
        self.car_read_only = bool(existing and existing.get("read_only"))
        self.form.addWidget(section_label("Car"))
        self.form.addLayout(self.car_cards)
        self.conflict = QWidget()
        conflict_layout = QVBoxLayout(self.conflict)
        conflict_layout.setContentsMargins(0, 0, 0, 0)
        conflict_layout.setSpacing(S1)
        self.conflict_text = QLabel()
        self.conflict_text.setObjectName("Banner")
        self.conflict_text.setWordWrap(True)
        conflict_layout.addWidget(self.conflict_text)
        conflict_layout.addLayout(self.button_row(button("Change time", self._change_time),
                                                  button("Save anyway", self._save_anyway, danger=True)))
        self.conflict.hide()
        self.body.addWidget(self.conflict)
        self.sender_note = QLabel()  # GS-3, GS-7: never a surprise who sends it; EC-7: the account in bold
        self.sender_note.setObjectName("SenderNote")
        self.sender_note.setWordWrap(True)
        self.body.addWidget(self.sender_note)
        if existing:
            self.sender_note.setText(f"Organizer: <b>{escape(existing['organizer_email'])}</b>. Changes are sent "
                                     "from this account.")
            if not existing.get("organizer_connected", True) and existing.get("sync_op") != "create":  # GS-6
                self._block(f"Reconnect {existing['organizer_email']} to change this event. On the hub PC, open "
                            "Family & Settings, Calendar accounts.")
        elif sender is not None:
            if sender.get("error"):  # GS-2: no account connected
                self.sender_note.hide()
                self._block(sender["error"])
            else:
                sent_from = f"Google Calendar sends the invites from <b>{escape(sender['email'])}</b>"
                if sender.get("fallback"):
                    sent_from += f" ({escape(sender['preferred'])} isn't connected)"
                # one line, so the form fits a 768-pixel-high screen with the time wheels (WO-FS-008)
                self.sender_note.setText(sent_from + ".")
        self.finish_layout()
        self._check_times()

    @staticmethod
    def _chip_changed(chip: QPushButton, on: bool) -> None:
        chip.setIcon(icons.icon("check", BASE) if on else QIcon())
        chip.setToolTip("Invited: click to leave out" if on else "Click to invite")

    def set_cars(self, cars: list[dict[str, Any]],
                 status: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        """The family's cars, and for an existing event a way to see which are free at its time (CR-5). A new
        event's time isn't saved yet, so its cars show no status; picking one in use still asks Use anyway once the
        event is saved (CR-7)."""
        self.cars, self.car_status = cars, status
        if status is not None:
            with contextlib.suppress(HubError, HubUnreachable):
                self.cars = status()
        self._show_car()

    def _show_car(self) -> None:
        """WC-9: No car, then a compact card for each car, the chosen one outlined."""
        while self.car_cards.count():
            item = self.car_cards.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()  # at once: deleteLater waits for the event loop
                widget.deleteLater()
        none = {"id": None, "name": "No car", "selected": self.car_id is None}
        for n, car in enumerate([none, *self.cars]):
            card = CarCard(car | {"selected": car["id"] == self.car_id}, compact=True)
            card.clicked.connect(partial(self.pick_car, car["id"]))
            card.setEnabled(not self.car_read_only)
            self.car_cards.addWidget(card, n // 3, n % 3)

    def pick_car(self, car_id: int | None) -> None:
        self.car_changed = self.car_changed or car_id != self.car_id
        self.car_id = car_id
        self._show_car()

    def chosen_car_cards(self) -> list[CarCard]:
        return [w for w in self.findChildren(CarCard) if not w.isHidden() and w.property("selected")]

    def _block(self, message: str) -> None:
        self._blocked = True
        self.error.setText(message)
        self.error.show()
        self.save_button.setEnabled(False)

    _blocked = False

    def _start_moved(self) -> None:
        """TM-4: the end moves with the start, so the length stays the same."""
        moved = self.start.value()
        self.end.set_value(timepick.follow_start(self._shown_start, moved, self.end.value()))
        self._shown_start = moved
        self._check_times()

    def _check_times(self) -> None:
        """AD-1: All day hides the wheels (their times are kept for switching back). TM-3, TM-5: the times in words,
        or why they can't be saved, with Save disabled."""
        all_day = self.all_day.isChecked()
        self.times.setVisible(not all_day)
        problem = "" if all_day else timepick.problem(self.start.value(), self.end.value())
        self.summary_text = timepick.summary(self.start.value(), self.end.value())
        self.times_summary.setText(colour_am_pm(self.summary_text))  # WO-FS-013 EC-3
        self.times_summary.setVisible(not problem)  # the message says it instead
        self.times_problem.setText(problem)
        self.times_problem.setVisible(bool(problem))
        self.save_button.setEnabled(not problem and not self._blocked and self.conflict.isHidden())
        if self.isVisible():
            self.adjustSize()

    def body_data(self) -> dict[str, Any]:
        return {
            "title": self.title.text(),
            "date": self.date.date().toString("yyyy-MM-dd"),
            "all_day": self.all_day.isChecked(),
            "start_time": "" if self.all_day.isChecked() else self.start.value(),
            "end_time": "" if self.all_day.isChecked() else self.end.value(),
            "location": self.location.text(),
            "repeat": self.repeat.currentData(),
            "invitees": [b.text() for b in self.invitees if b.isChecked()],
            "acknowledge_conflicts": self.acknowledge,
        }

    def handle_error(self, err: HubError) -> bool:
        conflicts = err.extra.get("conflicts")
        if err.status != 409 or not conflicts:
            return False
        lines = [f"{err.detail}"]
        for c in conflicts[:3]:
            lines.append(f"  {c['title']}: {day_label(c['date'])}, {time_range(c['start_time'], c['end_time'])}")
        lines.append("Both events will be on the calendar at the same time.")
        self.conflict_text.setText("\n".join(lines))
        self.conflict.show()
        self.save_button.setEnabled(False)
        self.adjustSize()
        return True

    def _change_time(self) -> None:
        self.conflict.hide()
        self._check_times()
        self.start.hour.setFocus()

    def _save_anyway(self) -> None:
        self.acknowledge = True
        self.conflict.hide()
        self._check_times()
        self._try_save()


class TaskDialog(_SavingDialog):
    def __init__(self, save: SaveFn, existing: dict[str, Any] | None = None, parent: QWidget | None = None,
                 due: str | None = None) -> None:
        super().__init__("Edit task" if existing else "New task", save, parent)
        self.existing = existing
        self.title = QLineEdit(existing["title"] if existing else "")
        self.title.setMaxLength(200)
        self.title.setPlaceholderText("e.g. Take out recycling")
        # WO-FS-013 TP-a, TP-b: a three-part switch, each part in its priority's colour, filled when chosen
        self.priority = QButtonGroup(self)
        switch = QFrame()
        switch.setObjectName("PrioritySwitch")
        parts = QHBoxLayout(switch)
        parts.setContentsMargins(0, 0, 0, 0)
        parts.setSpacing(S1)
        for key in ("high", "medium", "low"):
            part = QPushButton(key.title())
            part.setProperty("key", key)
            part.setCheckable(True)
            part.setCursor(Qt.CursorShape.PointingHandCursor)
            part.setChecked(key == (existing["priority"] if existing else "medium"))
            self.priority.addButton(part)
            parts.addWidget(part)
        row = QHBoxLayout()
        row.addWidget(switch)
        row.addStretch()
        self.due = date_field((existing["due_date"] if existing else due) or today_iso())
        self.no_due = QCheckBox("No due date")  # WO-FS-009 OT-1: an ongoing task
        self.no_due.setChecked(existing is not None and not existing["due_date"])
        self.no_due.setToolTip("An ongoing task: never overdue, not on the Calendar")
        self.repeat = QComboBox()
        for key, label in REPEAT_LABELS.items():
            self.repeat.addItem(label, key)
        self.repeat.setCurrentIndex(list(REPEAT_LABELS).index(existing["repeat"] if existing else "none"))
        self.form.addLayout(field("Title", self.title))
        self.form.addLayout(field("Priority", row))
        self.due_soon_note = QLabel()  # WO-FS-012 DS-5, in the High blue (WO-FS-013 TP-f)
        self.due_soon_note.setObjectName("DueSoonNote")
        self.due_soon_note.setWordWrap(True)
        self.form.addWidget(self.due_soon_note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(S2)
        when = QHBoxLayout()
        when.setSpacing(S2)
        when.addWidget(self.due, 1)
        when.addWidget(self.no_due)
        grid.addLayout(field("Due date", when), 0, 0)
        self.repeats = QWidget()
        repeats = QVBoxLayout(self.repeats)
        repeats.setContentsMargins(0, 0, 0, 0)
        repeats.addLayout(field("Repeats", self.repeat))
        grid.addWidget(self.repeats, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.form.addLayout(grid)
        self.repeat_rule = QLabel()  # WO-FS-013 MR-6, MR-8
        self.repeat_rule.setProperty("muted", True)
        self.repeat_rule.setWordWrap(True)
        self.form.addWidget(self.repeat_rule)
        self.hint = QLabel("When a repeating task is checked off, the next one is added straight away.")
        self.hint.setProperty("muted", True)
        self.hint.setWordWrap(True)
        self.form.addWidget(self.hint)
        self.no_due.toggled.connect(self._no_due_changed)
        self._no_due_changed(self.no_due.isChecked())
        self.priority.buttonClicked.connect(lambda _b: self._show_due_soon())
        self.priority.buttonClicked.connect(lambda _b: self._show_accent())
        self._show_accent()
        self.due.dateChanged.connect(lambda _d: self._show_due_soon())
        self.no_due.toggled.connect(lambda _on: self._show_due_soon())
        self.due.dateChanged.connect(lambda _d: self._show_repeat_rule())
        self.repeat.currentIndexChanged.connect(lambda _i: self._show_repeat_rule())
        self.no_due.toggled.connect(lambda _on: self._show_repeat_rule())
        self._show_due_soon()
        self._show_repeat_rule()
        self.finish_layout()

    def chosen_priority(self) -> str:
        checked = self.priority.checkedButton()
        return str(checked.property("key")) if checked else "medium"

    def _show_accent(self) -> None:
        """TP-e: the header's accent line follows the chosen priority."""
        self.set_accent(self.chosen_priority())

    def _show_due_soon(self) -> None:
        """DS-5: what's picked is saved; a task due within the window will show as high priority."""
        checked = self.priority.checkedButton()
        priority = checked.property("key") if checked else "medium"
        due = "" if self.no_due.isChecked() else self.due.date().toString("yyyy-MM-dd")
        shown, bumped = effective_priority(priority, due, None, date.fromisoformat(today_iso()))
        self.due_soon_note.setText(f"Due within {DUE_SOON_DAYS} days: it will show as high priority." if bumped
                                   else "")
        self.due_soon_note.setVisible(bumped)

    def repeat_day(self) -> tuple[int | None, int | None]:
        """The repeat day the task will have once saved: an occurrence left on its date keeps its own (a Feb 28
        occurrence of a task on the 31st still repeats on the 31st); a new date or repeat sets a new one (MR-1,
        MR-6)."""
        repeat = self.repeat.currentData()
        shown = self.due.date().toString("yyyy-MM-dd")
        old = self.existing
        if old and old.get("repeat_day") and old["repeat"] == repeat and old["due_date"] == shown:
            return old["repeat_day"], old.get("repeat_month")
        return repeat_anchor(date.fromisoformat(shown), repeat)

    def _show_repeat_rule(self) -> None:
        """MR-8: how a repeat on the 29th to 31st, or on February 29, works in shorter months. MR-6: moving an
        occurrence's date gives later ones a new repeat day, so say so."""
        repeat = self.repeat.currentData()
        text = ""
        if not self.no_due.isChecked() and repeat in ("monthly", "yearly"):
            day, month = self.repeat_day()
            text = month_end_rule(repeat, day, month)
            old = self.existing
            if (old and old.get("repeat_day") and old["repeat"] == repeat and day != old["repeat_day"]
                    and repeat == "monthly"):
                text = f"Later ones will repeat on the {ordinal(day or 1)}. " + text
        self.repeat_rule.setText(text.strip())
        self.repeat_rule.setVisible(bool(text))
        if self.isVisible():
            self.adjustSize()

    def _no_due_changed(self, on: bool) -> None:
        """OT-1, OT-2: No due date hides the date and Repeats (repeats need a date); off brings back today."""
        if not on and not self.due.isVisibleTo(self) and self.no_due.isVisibleTo(self):
            self.due.setDate(QDate.fromString(today_iso(), "yyyy-MM-dd"))
        self.due.setVisible(not on)
        self.repeats.setVisible(not on)
        self.hint.setVisible(not on)
        if on:
            self.repeat.setCurrentIndex(0)
        if self.isVisible():
            self.adjustSize()

    def body_data(self) -> dict[str, Any]:
        checked = self.priority.checkedButton()
        ongoing = self.no_due.isChecked()
        return {"title": self.title.text(), "priority": checked.property("key") if checked else "medium",
                "due_date": "" if ongoing else self.due.date().toString("yyyy-MM-dd"),
                "repeat": "none" if ongoing else self.repeat.currentData()}


def car_uses(car: dict[str, Any]) -> str:
    """WC-2: what an in-use car is needed for: "Soccer pickup, 4:15–4:45 pm" (two at most, then "and N more")."""
    uses = car.get("in_use") or []
    text = "; ".join(f"{u['title']}, {time_range(u['start_time'], u['end_time'])}" for u in uses[:2])
    return text + (f" and {len(uses) - 2} more" if len(uses) > 2 else "")


class CarCard(QPushButton):
    """WO-FS-013 WC-1 to WC-4: a car as a large button card: a car icon, its name in bold, what it's used for, and a
    Free (green) or In use (yellow) pill; a red note if picking it double-books it; a light green outline and a check
    mark when it's the event's car. `compact` (WC-9): one line, for the event form. A car with no "in_use" (a new
    event, or No car) shows no pill."""

    def __init__(self, car: dict[str, Any], compact: bool = False) -> None:
        super().__init__()
        self.car = car
        self.setObjectName("CarCard")
        self.setProperty("compact", compact)
        self.setProperty("selected", bool(car.get("selected")))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(S1 + HALF, HALF if compact else S1 + HALF, S1 + HALF, HALF if compact else S1 + HALF)
        row.setSpacing(S1)
        self.icon_label = QLabel()
        self.icon_label.setPixmap(icons.pixmap("car" if car["id"] is not None else "x", TEXT, ICON))
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.name_label = QLabel(car["name"])
        self.name_label.setObjectName("CarName")
        texts.addWidget(self.name_label)
        in_use = car.get("in_use")
        self.detail = QLabel(car_uses(car))
        self.detail.setObjectName("CarDetail")
        self.detail.setWordWrap(True)
        self.detail.setVisible(bool(in_use) and not compact)
        texts.addWidget(self.detail)
        self.clash = QLabel("Double-booked" if car.get("selected") else "Will be double-booked")
        self.clash.setObjectName("CarClash")
        self.clash.setVisible(bool(in_use) and not compact)
        texts.addWidget(self.clash)
        row.addLayout(texts, 1)
        self.pill = QLabel("In use" if in_use else "Free")
        self.pill.setObjectName("CarPill")
        self.pill.setProperty("level", "busy" if in_use else "free")
        self.pill.setVisible(in_use is not None and car["id"] is not None)
        row.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self.check = QLabel()
        self.check.setPixmap(icons.pixmap("circle-check", SELECT, ICON))
        self.check.setVisible(bool(car.get("selected")))
        row.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)
        for label in (self.icon_label, self.name_label, self.detail, self.clash, self.pill, self.check):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        tip = car["name"] + (f"\nIn use: {car_uses(car)}" if in_use else "\nFree" if in_use is not None else "")
        self.setToolTip(tip + ("\nThis event's car" if car.get("selected") and car["id"] is not None else ""))

    def sizeHint(self) -> QSize:
        layout = self.layout()
        return layout.sizeHint() if layout is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        layout = self.layout()
        return layout.minimumSize() if layout is not None else super().minimumSizeHint()


class WhichCarDialog(CardDialog):
    """WO-FS-010 CR-5 to CR-7: every car, Free or In use (and what for) at this event's time. Clicking one assigns it;
    No car needed clears it; a car that's already needed asks Use anyway or Pick another. WO-FS-013 WC-1 to WC-10:
    each car is a large button card; arrow keys move between them and Enter picks."""

    def __init__(self, event: dict[str, Any], cars: list[dict[str, Any]], choose: Callable[[int | None, bool], Any],
                 parent: QWidget | None = None) -> None:
        has_car = any(car["selected"] for car in cars)
        super().__init__("Change car" if has_car else "Which car?", parent, width=CAR_POPUP_WIDTH)  # WC-4
        self.set_accent("event", "car")  # WC-6: the event, with the green event accent
        self._choose = choose
        self.result_value: Any = None
        when = time_range(event["start_time"], event["end_time"]) if event["start_time"] else "all day"
        about = QLabel(f"<b>{escape(event['title'])}</b> · "
                       f"{escape(day_label(event.get('next_date') or event['date']))}, {escape(when)}")
        about.setObjectName("CarEvent")
        about.setWordWrap(True)
        self.body.addWidget(about)
        self.cards: list[CarCard] = []
        self.buttons: dict[int | None, QPushButton] = {}
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(S1)
        for car in cars:
            card = CarCard(car)
            card.clicked.connect(partial(self.pick, car["id"], car["name"]))
            column.addWidget(card)
            self.cards.append(card)
            self.buttons[car["id"]] = card
        self.card_scroll: QScrollArea | None = None
        if len(cars) > CAR_CARDS_SHOWN:  # WC-10: more than four scroll inside the pop-up
            self.card_scroll = QScrollArea()
            self.card_scroll.setObjectName("PlainScroll")
            self.card_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.card_scroll.setWidgetResizable(True)
            self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.card_scroll.setWidget(holder)
            shown = sum(card.sizeHint().height() for card in self.cards[:CAR_CARDS_SHOWN])
            self.card_scroll.setFixedHeight(shown + S1 * (CAR_CARDS_SHOWN - 1) + S1)
            self.body.addWidget(self.card_scroll)
        else:
            self.body.addWidget(holder)
        if has_car:  # WC-5: its own smaller, secondary button below the cards
            self.buttons[None] = button("No car needed", partial(self.pick, None, ""), icon_name="x")
            none_row = QHBoxLayout()
            none_row.addWidget(self.buttons[None])
            none_row.addStretch()
            self.body.addLayout(none_row)
        self.body.addLayout(self.button_row(button("Cancel", self.reject)))

    def showEvent(self, event: QShowEvent) -> None:
        """Focus starts on the pop-up itself, so the chosen car shows its light green outline (WC-4) rather than the
        focus ring; the first arrow key moves onto the cards."""
        super().showEvent(event)
        self.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """WC-3: Up and Down move between the cards; Enter picks the one with focus."""
        keys = {Qt.Key.Key_Down: 1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_Left: -1}
        step = keys.get(Qt.Key(event.key()))
        if step and self.cards:
            chosen = next((n for n, c in enumerate(self.cards) if c.car["selected"]), -1)
            focused = next((n for n, c in enumerate(self.cards) if c.hasFocus()), chosen - step if chosen >= 0 else -1)
            card = self.cards[max(0, min(len(self.cards) - 1, focused + step))]
            card.setFocus()
            if self.card_scroll is not None:
                self.card_scroll.ensureWidgetVisible(card)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused_card = next((c for c in self.cards if c.hasFocus()), None)
            if focused_card is not None:
                focused_card.click()
                return
        super().keyPressEvent(event)

    def pick(self, car_id: int | None, name: str) -> None:
        try:
            self.result_value = self._choose(car_id, False)
        except HubError as err:
            if err.status != 409:
                error(self, err.detail)
                return
            # CR-7: already needed at an overlapping time
            ask = MessageDialog(self, "Car already in use", err.detail, "Both events will show it as double-booked.",
                                "Use anyway", danger=True, cancel=True, cancel_text="Pick another")
            ask.set_accent("event", "car")  # WC-8: in the same card style
            if not ask.exec():
                return  # Pick another
            self.result_value = self._choose(car_id, True)
        except HubUnreachable as err:
            error(self, str(err))
            return
        self.accept()


def choose_car(parent: QWidget | None, hub: Any, event: dict[str, Any]) -> bool:
    """CR-4: the car button: opens Which car? for an event. True if the car changed."""
    try:
        cars = hub.event_cars(event["id"])
    except (HubError, HubUnreachable) as err:
        error(parent, getattr(err, "detail", str(err)))
        return False
    dlg = WhichCarDialog(event, cars, lambda car_id, anyway: hub.set_event_car(event["id"], car_id, anyway), parent)
    return dlg.exec() == QDialog.DialogCode.Accepted


class HiddenDialog(CardDialog):
    """WO-FS-011 HE-7: the events hidden from Home Base, each with Show again. WO-FS-013 DH-1, DH-5, DH-6: and Delete
    from Home Base on each, Delete all hidden in the header, with Undo. The pop-up is modal, so its Undo notice sits
    inside it, above Done, rather than in the window's corner behind the backdrop."""

    def __init__(self, events: list[dict[str, Any]], show: Callable[[str], Any], parent: QWidget | None = None, *,
                 delete: Callable[[str], Any] | None = None, delete_all: Callable[[], Any] | None = None,
                 undo_delete: Callable[[str], Any] | None = None) -> None:
        super().__init__("Hidden from Home Base", parent, width=HIDDEN_POPUP_WIDTH)
        self._show = show
        self._delete: Callable[[str], Any] | None = delete
        self._delete_all: Callable[[], Any] | None = delete_all
        self._undo_delete: Callable[[str], Any] | None = undo_delete
        self.events = list(events)
        self.order = {e["id"]: n for n, e in enumerate(events)}  # Undo puts events back where they were
        self.shown: list[str] = []
        self.shown_ids: set[str] = set()
        self.deleted: list[str] = []
        self.last_deleted: list[dict[str, Any]] = []
        head = QHBoxLayout()
        note = QLabel("These stay in Google Calendar. Show one again to put it back everywhere in Home Base.")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        head.addWidget(note, 1)
        self.delete_all_b = button("Delete all hidden", self.delete_every, danger=True,
                                   tip="Delete every hidden event from Home Base. They stay in Google Calendar.")
        self.delete_all_b.setVisible(delete_all is not None)
        head.addWidget(self.delete_all_b, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(S1)
        self.body.addLayout(self.rows)
        self.undo_bar = QFrame()  # DH-5: "Deleted from Home Base. It's still in Google Calendar." with Undo
        self.undo_bar.setObjectName("Toast")
        bar = QHBoxLayout(self.undo_bar)
        bar.setContentsMargins(S2, S1, S1, S1)
        self.undo_text = QLabel()
        self.undo_text.setWordWrap(True)
        bar.addWidget(self.undo_text, 1)
        self.undo_b = button("Undo", self.undo, primary=True)
        bar.addWidget(self.undo_b)
        self.undo_bar.hide()
        self.body.addWidget(self.undo_bar)
        self.undo_timer = QTimer(self)
        self.undo_timer.setSingleShot(True)
        self.undo_timer.timeout.connect(self.undo_bar.hide)
        self.body.addLayout(self.button_row(button("Done", self.accept, primary=True)))
        self._fill()

    def _fill(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            row = item.layout() if item is not None else None
            if row is not None:
                while row.count():
                    child = row.takeAt(0)
                    widget = child.widget() if child is not None else None
                    if widget is not None:
                        widget.hide()
                        widget.deleteLater()
                row.deleteLater()
            elif item is not None:
                lone = item.widget()
                if lone is not None:
                    lone.hide()
                    lone.deleteLater()
        for e in self.events:
            row = QHBoxLayout()
            text = QLabel(f"{e['title']}\n{day_label(e['next_date'])}, {time_range(e['start_time'], e['end_time'])}")
            text.setWordWrap(True)
            row.addWidget(text, 1)
            again = button("Shown" if e["id"] in self.shown_ids else "Show again", partial(self.show_again, e))
            again.setEnabled(e["id"] not in self.shown_ids)
            row.addWidget(again)
            if self._delete is not None and e["id"] not in self.shown_ids:
                row.addWidget(icon_button("trash-2", "Delete from Home Base. It stays in Google Calendar.",
                                          partial(self.delete_one, e)))
            self.rows.addLayout(row)
        if not self.events:
            self.rows.addWidget(QLabel("Nothing is hidden."))
        self.delete_all_b.setEnabled(any(e["id"] not in self.shown_ids for e in self.events))
        if self.isVisible():
            self.adjustSize()

    def show_again(self, e: dict[str, Any]) -> None:
        try:
            self._show(e["id"])
        except (HubError, HubUnreachable) as err:
            error(self, getattr(err, "detail", str(err)))
            return
        self.shown.append(e["title"])
        self.shown_ids.add(e["id"])
        self._fill()

    def delete_one(self, e: dict[str, Any]) -> None:
        """DH-1, DH-5: no confirmation for one event; Undo puts it back in the list."""
        delete = self._delete
        if delete is None or not self._call(partial(delete, e["id"])):
            return
        self._removed([e], "Deleted from Home Base. It's still in Google Calendar.")

    def delete_every(self) -> None:
        """DH-6: asks once, then the same Undo."""
        left = [e for e in self.events if e["id"] not in self.shown_ids]
        n = len(left)
        delete_all = self._delete_all
        question = f"Delete all {n} hidden events from Home Base? They stay in Google Calendar."
        if not left or delete_all is None or not confirm(
                self, "Delete all hidden", question, f"Delete {n} events",
                detail="Nothing changes in Google Calendar and nobody is emailed. You'll have a few seconds to undo."):
            return
        if not self._call(delete_all):
            return
        self._removed(left, f"{n} hidden events deleted from Home Base. They're still in Google Calendar.")

    def _call(self, fn: Callable[[], Any]) -> bool:
        try:
            fn()
        except (HubError, HubUnreachable) as err:
            error(self, getattr(err, "detail", str(err)))
            return False
        return True

    def _removed(self, gone: list[dict[str, Any]], message: str) -> None:
        ids = {e["id"] for e in gone}
        self.events = [e for e in self.events if e["id"] not in ids]
        self.last_deleted = gone
        self.deleted.extend(e["title"] for e in gone)
        self._fill()
        self.undo_text.setText(message)
        self.undo_bar.show()
        self.undo_timer.start(GROCERY_UNDO_S * 1000)
        if self.isVisible():
            self.adjustSize()

    def undo(self) -> None:
        """DH-5: back in the Hidden list."""
        undo_delete = self._undo_delete
        back = [e for e in self.last_deleted if undo_delete is not None and self._call(partial(undo_delete, e["id"]))]
        titles = {e["title"] for e in back}
        self.deleted = [t for t in self.deleted if t not in titles]
        self.events = sorted(back + self.events, key=lambda e: self.order.get(e["id"], 0))
        self.last_deleted = []
        self.undo_bar.hide()
        self._fill()


class SetupFamilyDialog(_SavingDialog):
    """WO-FS-010 AC-4: a new hub asks for the family's details before anything else. Only the admin is required."""

    def __init__(self, save: SaveFn, parent: QWidget | None = None) -> None:
        super().__init__("Set up your family", save, parent, width=520)
        self.title_label.setObjectName("ViewTitle")
        intro = QLabel("This hub is new. Enter the family's details once; you can change them later in "
                       "Family & Settings.")
        intro.setProperty("muted", True)
        intro.setWordWrap(True)
        self.form.addWidget(intro)
        self.admin = QLineEdit()
        self.admin.setPlaceholderText("admin@example.com")
        self.second = QLineEdit()
        self.second.setPlaceholderText("Optional, e.g. second@example.com")
        self.members = QPlainTextEdit()
        self.members.setFixedHeight(TEXT_BOX_HEIGHT)
        self.members.setPlaceholderText("One email address per line")
        self.form.addLayout(field("Admin's Gmail address (sends the digest and most invites)", self.admin))
        self.form.addLayout(field("Second calendar account", self.second))
        self.form.addLayout(field("Other family members", self.members))
        self.save_button.setText("Save and continue")
        self.admin.textChanged.connect(lambda text: self.save_button.setEnabled(bool(text.strip())))
        self.save_button.setEnabled(False)  # nothing can be saved until an admin is set
        self.finish_layout()

    def body_data(self) -> dict[str, Any]:
        members = [m.strip() for m in self.members.toPlainText().replace(",", "\n").splitlines() if m.strip()]
        return {"admin": self.admin.text(), "second": self.second.text(), "members": members}


class ReminderDialog(_SavingDialog):
    def __init__(self, save: SaveFn, existing: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Edit reminder" if existing else "New reminder", save, parent)
        self.message = QPlainTextEdit(existing["message"] if existing else "")
        self.message.setFixedHeight(TEXT_BOX_HEIGHT)
        self.message.setPlaceholderText("Something everyone should know")
        self.count = QLabel()
        self.count.setProperty("muted", True)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.message.textChanged.connect(self._update_count)
        self._update_count()
        self.form.addLayout(field("Message", self.message))
        self.form.addWidget(self.count)
        self.finish_layout()

    def _update_count(self) -> None:
        self.count.setText(f"{len(self.message.toPlainText())} / 500")

    def body_data(self) -> dict[str, Any]:
        return {"message": self.message.toPlainText()}


def invitee_list(emails: list[str]) -> str:
    return ", ".join(short_email(e) for e in emails) or "nobody"
