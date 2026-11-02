"""The app's views (PC-6): Today, Events, Tasks, Reminders, History, Trash, Family & Settings.

WO-FS-004: every list is rows and cards (LC-1), with a header row of title and actions (NV-4).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from family_scheduler import __version__
from family_scheduler.app import dialogs
from family_scheduler.app.accounts import account_buttons, hub_status_line, reconnect_notice
from family_scheduler.app.calview import MonthGrid, WeekStack, event_tip
from family_scheduler.app.carbar_view import CarsToday
from family_scheduler.app.months import group_by_month
from family_scheduler.app.style import (
    BASE_PT,
    EVENT_ACCENT,
    FOOD,
    GROCERY,
    GROCERY_UNDO_S,
    HALF,
    HIGH,
    ITEMS,
    MEMBER_LIST_HEIGHT,
    MINUTE_MS,
    MUTED,
    OVERDUE,
    REM,
    REPEAT_LABELS,
    REPLIES_PANEL,
    ROW_HEIGHT,
    S1,
    S2,
    S3,
    S4,
    TAG_TEXT,
    TASK_LIST_ROWS,
    TEXT,
    TODAY_EVENT,
    TODAY_SECTION_PT,
    ago,
    day_label,
    month_colour,
    month_ink,
    repeat_note,
    short_email,
    show_month,
    synced_ago,
    time_range,
    today_iso,
    when,
)
from family_scheduler.app.today import day_changed, due_text, long_date, todays_events, todo_groups
from family_scheduler.app.week import (
    MONTH,
    WEEK,
    anchor_for_today,
    day_items,
    fetch_range,
    monday,
    month_grid,
    period_label,
    step,
    switch,
    week_days,
)
from family_scheduler.app.widgets import (
    ACTION_TIPS,
    BadgeButton,
    Entry,
    GroceryList,
    Header,
    Note,
    Row,
    RowList,
    button,
    car_tag,
    icon_button,
    view_header,
)
from family_scheduler.client.hub_client import HubError, HubUnreachable
from family_scheduler.digest import fmt_time
from family_scheduler.organizer import CAR_NAME_MAX, DUE_SOON_DAYS, GROCERY_MAX, MAX_CARS
from family_scheduler.palette import PRIORITY_COLOURS
from family_scheduler.timeutil import to_local

if TYPE_CHECKING:
    from family_scheduler.app.main_window import MainWindow


def _muted(text: str, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setProperty("muted", True)
    label.setWordWrap(wrap)
    return label


def _priority_tag(priority: str) -> tuple[str, str]:
    """TD-8, WO-FS-013 TP-d: High blue, Medium yellow, Low grey-green, the same as the task pop-up's switch."""
    return priority.title(), PRIORITY_COLOURS[priority]


def task_priority_tags(t: dict[str, Any]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """WO-FS-012 DS-4, DS-6, DS-7: the priority tag shows the effective priority; a bumped task also gets Due soon,
    whose tooltip says why and what it was set as."""
    shown = t.get("effective_priority", t["priority"]) if not t.get("completed_at") else t["priority"]
    tags = [_priority_tag(shown)]
    tips: dict[str, str] = {}
    if t.get("due_soon") and not t.get("completed_at"):
        tags.append(("Due soon", HIGH))
        tips["Due soon"] = (f"Due within {DUE_SOON_DAYS} days, so it's treated as high priority. "
                            f"Set as {t['priority'].title()}.")
    return tags, tips


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def event_extras(e: dict[str, Any]) -> tuple[list[tuple[str, str]], list[str] | None]:
    """WO-FS-010: an event row's car tag (CR-9), From Google (GY-4) and Not synced (GY-15) tags, and its actions:
    a read-only event from Google only opens in Google Calendar (GY-8, GY-11, GY-12). None keeps the usual ones."""
    tags: list[tuple[str, str]] = []
    if e.get("car"):
        tags.append((car_tag(e["car"]["name"] + (" double-booked" if e.get("car_double_booked") else "")),
                     OVERDUE if e.get("car_double_booked") else GROCERY))
    if e.get("not_synced"):
        tags.append(("Not synced", OVERDUE))
    elif e.get("from_google"):
        tags.append(("From Google", TAG_TEXT))
    if e.get("from_google"):  # 1.9.1: an event from Google is deleted from Home Base only; Google keeps it
        if e.get("read_only"):
            return tags, (["open"] if e.get("html_link") else []) + ["delete_app"]
        return tags, ["car", "edit", "delete_app"]
    if e.get("read_only"):  # WO-FS-011 HE-2: Hide is there exactly when Remove isn't
        return tags, (["open"] if e.get("html_link") else []) + ["hide"]
    return tags, None


def open_in_google(e: dict[str, Any] | None) -> None:
    """GY-8, GY-11: Open in Google Calendar."""
    if e and e.get("html_link"):
        QDesktopServices.openUrl(QUrl(e["html_link"]))


def refresh_button(view: View) -> QPushButton:
    """GY-2a: a labelled Refresh button beside every Replies button: it runs Sync with Google."""
    b = button("Refresh", lambda: sync_now(view, b), icon_name="refresh-cw",
               tip="Sync with Google: new, changed and removed events, and replies (Ctrl+R)")
    return b


def sync_now(view: View, b: QPushButton | None = None) -> None:
    """GY-2: Sync with Google, saying "Syncing…" while it works, then what changed."""
    if b is not None:
        b.setText("Syncing…")
        b.setEnabled(False)
        QApplication.processEvents()
    try:
        result = view.act(view.hub.sync)
    finally:
        if b is not None:
            b.setText("Refresh")
            b.setEnabled(True)
    if result is not None:
        view.window_.toast_message(result["message"], kind="error" if result["problems"] else "ok")
    view.window_.changed()


DIGEST_TIP = ("The digest email covers today's events, overdue and high-priority tasks and replies. It's only sent "
              "when you ask.")  # WO-FS-011 TF-1, TF-2: was the line at the bottom of Today


class View(QWidget):
    title = ""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, S1, 0, 0)
        self.outer.setSpacing(S2)
        head, self.heading, self.subtitle, self.head_right = view_header(self.title)  # NV-4
        self.outer.addLayout(head)

    @property
    def hub(self) -> Any:
        return self.window_.client

    def act(self, fn: Callable[[], Any], done: str | None = None) -> Any:
        return self.window_.act(fn, done)

    def reload(self) -> None:
        raise NotImplementedError

    def add_new(self) -> None:
        """Ctrl+N (CF-8)."""

    def refresh(self) -> None:
        """Ctrl+R (CF-8)."""

    def select(self, item_id: str) -> None:
        """Show this item selected, when another view links here."""


# --- Today: a list of today's events, the to-do list and reminders (WO-FS-007) -------------------

class TodayView(View):
    title = "Today"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.digest_button = button("Send me my digest", self.send_digest, primary=True, icon_name="send",
                                    tip=DIGEST_TIP)  # TP-4; WO-FS-011 TF-2
        self.head_right.addWidget(self.digest_button)
        self.reconnect = QFrame()  # WO-FS-008 PS-7: never drop an account silently
        self.reconnect.setObjectName("Banner")
        notice_row = QHBoxLayout(self.reconnect)
        notice_row.setContentsMargins(0, 0, 0, 0)
        notice_row.setSpacing(S2)
        self.reconnect_text = QLabel()
        self.reconnect_text.setWordWrap(True)
        notice_row.addWidget(self.reconnect_text, 1)
        notice_row.addWidget(button("Reconnect", self.go_reconnect, tip="Open Family & Settings, Calendar accounts"))
        self.reconnect.hide()
        self.outer.addWidget(self.reconnect)
        self.needs_reconnect: list[str] = []
        self.cars_today = CarsToday()  # WO-FS-010 CB-a: above Today's events
        self.cars_today.open_event.connect(lambda event_id: self.events_view.edit(self.events_view.fetch(event_id)))
        self.outer.addWidget(self.cars_today)
        self.list = RowList()  # TL-1: one list, so the whole page scrolls; section titles stick (TL-5)
        self.list.action.connect(self.on_action)
        self.outer.addWidget(self.list, 1)
        self.shown_day: str | None = None
        self.data: dict[str, Any] = {}
        self.midnight = QTimer(self)  # TP-5: move to the new day at midnight, Toronto time
        self.midnight.timeout.connect(self.check_day)
        self.midnight.start(MINUTE_MS)

    @property
    def events_view(self) -> EventsView:
        view = self.window_.views["Events"]
        assert isinstance(view, EventsView)
        return view

    @property
    def tasks_view(self) -> TasksView:
        view = self.window_.views["Tasks"]
        assert isinstance(view, TasksView)
        return view

    @property
    def reminders_view(self) -> RemindersView:
        view = self.window_.views["Reminders"]
        assert isinstance(view, RemindersView)
        return view

    def check_day(self) -> None:
        if day_changed(self.shown_day, today_iso()) and self.window_.current() is self:
            self.window_.safe_reload(self)

    def reload(self) -> None:
        today = today_iso()
        week = self.data = self.hub.week(monday(date.fromisoformat(today)).isoformat(), 7)
        tasks = self.hub.tasks()
        reminders = self.hub.reminders()
        today_data = self.hub.today()
        self.needs_reconnect = today_data.get("accounts_needing_reconnect", [])
        local = to_local(datetime.now(UTC))
        self.cars_today.show_day(today_data.get("cars_today", []), max(len(today_data.get("cars", [])), 1),
                                 local.hour * 60 + local.minute)
        self.reconnect_text.setText(reconnect_notice(self.needs_reconnect))
        self.reconnect.setVisible(bool(self.needs_reconnect))
        self.shown_day = week["date"]
        self.subtitle.setText(long_date(week["date"]))  # TP-3
        digest = week["digest"]
        self.digest_button.setEnabled(not digest["limit_reached"])
        self.digest_button.setToolTip(f"Email today's summary to {self.window_.member}. {DIGEST_TIP}")
        events = todays_events(week["week_events"], week["date"])
        groups = todo_groups(tasks["overdue"] + tasks["open"] + tasks["ongoing"], week["date"])
        entries: list[Entry] = []

        def section(key: str, text: str, colour: str) -> None:  # TL-2
            entries.append(Header(text, colour, TODAY_SECTION_PT, space_above=S3 if entries else S1, key=key,
                                  button="+ Add"))

        section("events", "Today's events", TODAY_EVENT)  # TE-4; WO-FS-008 ST-1: titles without counts
        entries.extend(self._event_row(e) for e in events)
        if not events:
            entries.append(Note("No events today."))  # TL-4
        section("tasks", "To-do list", TEXT)
        for name, group in groups:  # TD-b
            entries.append(Header(name, OVERDUE if name == "Overdue" else MUTED, BASE_PT,
                                  space_above=S1, level=1))
            entries.extend(self._task_row(t, week["date"]) for t in group)
        if not groups:
            entries.append(Note("Nothing to do."))
        section("reminders", "Reminders", TEXT)
        entries.extend(Row(f"reminder:{r['id']}", " ".join(r["message"].split()), accent=REM, wrap=True,
                           tooltip=f"{r['message']}\n— {r['created_by']}") for r in reminders)  # TR-a, TR-b
        if not reminders:
            entries.append(Note("No reminders."))
        self.list.set_entries(entries)

    @staticmethod
    def _event_row(e: dict[str, Any]) -> Row:
        """TE-3, TE-5: title; time and place; replies, repeat, overlap and not-sent tags; ended events faded."""
        c = e["rsvp_counts"]
        tags = [(f"✓ {c['accepted']}  ✗ {c['declined']}  ? {c['no_reply'] + c['tentative']}", TAG_TEXT)]
        if e["repeat"] != "none":
            tags.append((REPEAT_LABELS[e["repeat"]], TAG_TEXT))
        if e["sync_status"] == "error":
            tags.append(("Not sent to Google", OVERDUE))
        elif e["conflicts_with"]:
            tags.append(("Overlaps " + ", ".join(e["conflicts_with"][:2]), OVERDUE))
        place = f" · {e['location']}" if e["location"] else ""
        extra, actions = event_extras(e)
        return Row(f"event:{e['id']}", e["title"], time_range(e["start_time"], e["end_time"]) + place,
                   accent=TODAY_EVENT, title_colour=TODAY_EVENT, tags=extra + tags, dim=e["past"],
                   tooltip=event_tip(e),
                   actions=actions if actions is not None else ["car", "hide", "edit", "cancel"])

    @staticmethod
    def _task_row(t: dict[str, Any], today: str) -> Row:
        """TD-d: the tick, the title, when it's due, and priority and repeat tags (High in blue)."""
        overdue = bool(t["due_date"]) and t["due_date"] < today
        tags, tips = task_priority_tags(t)  # WO-FS-012 DS-9: the effective priority, and Due soon
        if t["repeat"] != "none":
            tags.append((REPEAT_LABELS[t["repeat"]], TAG_TEXT))
        if not t["due_date"]:
            tags.append(("Ongoing", TAG_TEXT))  # WO-FS-009 OT-6
        high = t.get("effective_priority", t["priority"]) == "high"
        accent = OVERDUE if overdue else HIGH if high else EVENT_ACCENT
        note = repeat_note(t)  # WO-FS-013 MR-9
        when_due = due_text(t["due_date"], today)
        return Row(f"task:{t['id']}", t["title"], when_due, accent=accent,
                   title_colour=OVERDUE if overdue else TEXT, tags=tags, tick=False, tag_tips=tips,
                   tooltip=f"{t['title']}\n{when_due}\n{note}" if note else "")

    def on_action(self, row_id: str, name: str) -> None:
        """TU-1, TU-2: rows work as on the other pages; the section titles' + Add buttons add (TL-2)."""
        kind, item_id = row_id.split(":", 1)
        if kind == "header":
            if item_id == "events":
                self.events_view.add(self.shown_day or today_iso())  # pre-filled with today
            elif item_id == "tasks":
                self.tasks_view.add(self.shown_day or today_iso())
            else:
                self.reminders_view.add()
            return
        if kind == "event":
            event = self.events_view.fetch(item_id)
            if name == "edit":
                self.events_view.edit(event)
            elif name in ("remove", "cancel"):
                self.events_view.remove(event)
            elif name == "hide":
                self.events_view.hide_event(event)
            elif name == "delete_app":
                self.events_view.delete_from_app(event)
            elif name == "car":
                self.events_view.choose_car(event)
            elif name == "open":
                open_in_google(event)
        elif kind == "task":
            task = self.tasks_view.fetch(item_id)
            if name == "tick":  # TD-e: checked off as in Tasks; it leaves the list
                self.tasks_view.toggle(task)
            elif name == "edit":
                self.tasks_view.edit(task)
            elif name == "remove":
                self.tasks_view.remove(task)
        elif kind == "reminder":
            reminder = self.reminders_view.fetch(item_id)
            if name == "edit":
                self.reminders_view.edit(reminder)
            elif name == "remove":
                self.reminders_view.remove(reminder)

    def add_new(self) -> None:
        self.events_view.add(self.shown_day or today_iso())

    def go_reconnect(self) -> None:
        """PS-7: Reconnect opens Family & Settings with the account selected."""
        self.window_.show_view("Family & Settings")
        settings = self.window_.views["Family & Settings"]
        if isinstance(settings, SettingsView) and self.needs_reconnect:
            settings.account_list.select_id(self.needs_reconnect[0])
            settings._account_buttons()

    def send_digest(self) -> None:
        result = self.act(self.hub.send_digest)
        if result:
            self.window_.toast_message(result["message"])


# --- Calendar: week and month views (WO-FS-006 section 2) -----------------------------------------

class CalendarView(View):
    title = "Calendar"  # CP-1: was Today

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.replies_b = BadgeButton("Replies", self.toggle_replies, "mail",
                                     "Who hasn't replied to an invite yet (WT-2)")
        self.head_right.addWidget(self.replies_b)  # Send me my digest moved to Today (WO-FS-007 TP-4)
        self.head_right.addWidget(refresh_button(self))  # WO-FS-010 GY-2a
        nav = QHBoxLayout()
        nav.setSpacing(S1)
        nav.addWidget(icon_button("chevron-left", "Previous (←)", lambda: self.step_period(-1)))
        nav.addWidget(button("Today", self.go_today, tip="Back to today (T)"))
        nav.addWidget(icon_button("chevron-right", "Next (→)", lambda: self.step_period(1)))
        self.period = QLabel()  # SV-5
        self.period.setObjectName("Section")
        nav.addWidget(self.period)
        nav.addStretch()
        segment = QFrame()  # SV-1: Week · Month
        segment.setObjectName("Segment")
        seg = QHBoxLayout(segment)
        seg.setContentsMargins(2, 2, 2, 2)
        seg.setSpacing(2)
        self.week_b = button("Week", lambda: self.show_mode(WEEK), tip="Week view (W)")
        self.month_b = button("Month", lambda: self.show_mode(MONTH), tip="Month view (M)")
        for b in (self.week_b, self.month_b):
            b.setCheckable(True)
            seg.addWidget(b)
        nav.addWidget(segment)
        self.outer.addLayout(nav)
        self.notice = QLabel()  # AS-3
        self.notice.setObjectName("Banner")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.outer.addWidget(self.notice)
        body = QHBoxLayout()
        body.setSpacing(S2)
        self.week = WeekStack()
        self.month = MonthGrid()
        self.views_ = QStackedWidget()
        self.views_.addWidget(self.week)
        self.views_.addWidget(self.month)
        for cal in (self.week, self.month):
            cal.open_item.connect(self.open_item)
            cal.tick.connect(self.tick_task)
            cal.car_clicked.connect(lambda event_id: self.events_view.choose_car(self.events_view.fetch(event_id)))
            cal.context_item.connect(self.item_menu)
            cal.remove_item.connect(self.remove_item)
            cal.add_event_on.connect(lambda day: self.events_view.add(day))
            cal.more_items.connect(self.show_day)
        self.month.open_week.connect(self.open_week)
        body.addWidget(self.views_, 1)
        self.replies = QFrame()  # WT-2: the Replies side panel
        self.replies.setObjectName("Card")
        self.replies.setFixedWidth(REPLIES_PANEL)
        panel = QVBoxLayout(self.replies)
        panel.setContentsMargins(S2, S2, S1, S1)
        panel.setSpacing(S1)
        top = QHBoxLayout()
        self.replies_title = QLabel()
        self.replies_title.setObjectName("Section")
        top.addWidget(self.replies_title, 1)
        top.addWidget(refresh_button(self))  # GY-2a: at the top of the Replies panel too
        top.addWidget(icon_button("x", "Close", self.toggle_replies, MUTED))
        panel.addLayout(top)
        self.replies_when = _muted("")
        panel.addWidget(self.replies_when)
        self.replies_list = RowList()
        self.replies_list.action.connect(lambda row_id, name: self.open_item("event", row_id) if name == "edit"
                                         else None)
        panel.addWidget(self.replies_list, 1)
        self.replies.hide()
        body.addWidget(self.replies)
        self.outer.addLayout(body, 1)
        self.outer.addWidget(_muted("Double-click an empty spot to add an event on that day."))
        for key, handler in (("W", lambda: self.show_mode(WEEK)), ("M", lambda: self.show_mode(MONTH)),
                             ("T", self.go_today), ("Left", lambda: self.step_period(-1)),
                             ("Right", lambda: self.step_period(1))):
            shortcut = QShortcut(QKeySequence(key), self)  # SV-6
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
        self.mode = WEEK  # SV-2: always opens on the week; not saved between runs
        self.anchor: date | None = None  # None: the period containing today
        self.data: dict[str, Any] = {}
        self._show_switch()

    @property
    def events_view(self) -> EventsView:
        view = self.window_.views["Events"]
        assert isinstance(view, EventsView)
        return view

    @property
    def tasks_view(self) -> TasksView:
        view = self.window_.views["Tasks"]
        assert isinstance(view, TasksView)
        return view

    def _today(self) -> date:
        return date.fromisoformat(self.data["date"]) if self.data else date.fromisoformat(today_iso())

    def _anchor(self) -> date:
        return self.anchor or anchor_for_today(self.mode, self._today())

    def _show_switch(self) -> None:
        self.week_b.setChecked(self.mode == WEEK)
        self.month_b.setChecked(self.mode == MONTH)
        self.views_.setCurrentWidget(self.week if self.mode == WEEK else self.month)

    def show_mode(self, mode: str) -> None:
        """SV-4: switching keeps your place."""
        if mode != self.mode:
            self.anchor = switch(mode, self._anchor(), self._today())
            self.mode = mode
            self._show_switch()
        self.window_.safe_reload(self)

    def step_period(self, direction: int) -> None:
        self.anchor = step(self.mode, self._anchor(), direction)  # SV-3
        self.window_.safe_reload(self)

    def go_today(self) -> None:
        self.anchor = None
        self.window_.safe_reload(self)

    def open_week(self, day: str) -> None:
        """MV-7: a day number opens the week view for that week."""
        self.mode, self.anchor = WEEK, monday(date.fromisoformat(day))
        self._show_switch()
        self.window_.safe_reload(self)

    def reload(self) -> None:
        anchor = self._anchor()
        start, days = fetch_range(self.mode, anchor)
        data = self.data = self.hub.week(start.isoformat(), days)
        if self.mode == WEEK:
            self.week.load(data, [d.isoformat() for d in week_days(start)])
        else:
            self.month.load_month(data, month_grid(anchor), anchor)
        self.period.setText(period_label(self.mode, anchor))
        # MC-5, MC-6: underlined in the month's colour; a week takes the month its Thursday is in (as SV-4)
        shown = anchor if self.mode == MONTH else monday(anchor) + timedelta(days=3)
        show_month(self.period, shown.month)
        self.subtitle.setText(day_label(data["date"]))
        waiting = data["waiting_on_rsvps"]
        self.replies_b.set_count(len(waiting))
        self.replies_title.setText(f"Waiting on replies ({len(waiting)})")
        self.replies_when.setText(synced_ago(data.get("synced_at") or None) if data.get("synced_at")
                                  else ago(data["rsvp_refreshed_at"]))
        self.replies_list.set_entries([
            Row(w["id"], w["title"], f"{day_label(w['next_date'])} · " + ", ".join(short_email(x)
                                                                                   for x in w["waiting_on"]),
                actions=[], tooltip=f"{w['title']}\nWaiting on " + ", ".join(w["waiting_on"]))
            for w in waiting] or [Note("Everyone has replied.")])
        unsent = data["unsent"]
        self.notice.setVisible(bool(unsent))
        if unsent:
            names = ", ".join(u["title"] for u in unsent[:3]) + ("…" if len(unsent) > 3 else "")
            self.notice.setText(f"Not sent to Google after 24 hours of trying: {names}. Open Events and click Retry "
                                "on the event, or Discard it.")

    def toggle_replies(self) -> None:
        self.replies.setVisible(not self.replies.isVisible())

    def _task(self, task_id: str) -> dict[str, Any] | None:
        return next((t for t in self.data.get("week_tasks", []) if t["id"] == task_id), None)

    def open_item(self, kind: str, item_id: str) -> None:
        """WU-b, MV-6: double-click opens the event or task to edit."""
        if kind == "event":
            self.events_view.edit(self.events_view.fetch(item_id))
        else:
            self.tasks_view.edit(self.tasks_view.fetch(item_id))

    def item_menu(self, kind: str, item_id: str, where: QPoint) -> None:
        """WO-FS-011 HE-1: right-click a block or bar: Hide from app, and the event's other actions."""
        if kind != "event":
            return
        e = self.events_view.fetch(item_id)
        if e is None:
            return
        menu = QMenu(self)
        if e.get("read_only"):
            if e.get("html_link"):
                menu.addAction("Open in Google Calendar", lambda: open_in_google(e))
        else:
            menu.addAction("Edit", lambda: self.events_view.edit(e))
            menu.addAction("Which car?", lambda: self.events_view.choose_car(e))
        if e.get("from_google"):  # 1.9.1
            menu.addSeparator()
            menu.addAction("Delete from Home Base (stays in Google Calendar)",
                           lambda: self.events_view.delete_from_app(e))
            menu.exec(where)
            return
        hide = menu.addAction("Hide from app", lambda: self.events_view.hide_event(e))
        hide.setToolTip(ACTION_TIPS["hide"])
        if not e.get("read_only"):
            menu.addSeparator()
            menu.addAction("Remove (cancel in Google Calendar)", lambda: self.events_view.remove(e))
        menu.exec(where)

    def remove_item(self, kind: str, item_id: str) -> None:
        if kind == "event":
            self.events_view.remove(self.events_view.fetch(item_id))
        else:
            self.tasks_view.remove(self.tasks_view.fetch(item_id))

    def tick_task(self, task_id: str) -> None:
        """WU-c: the round tick checks a task off, or reopens it."""
        self.tasks_view.toggle(self._task(task_id) or self.tasks_view.fetch(task_id))

    def show_day(self, day: str) -> None:
        """MV-4: "+N more" lists everything on that day."""
        self.day_dialog(day).exec()

    def day_dialog(self, day: str) -> dialogs.CardDialog:
        events = [e for e in self.data.get("week_events", []) if e["day"] == day]
        tasks = [t for t in self.data.get("week_tasks", []) if t["day"] == day]
        dlg = dialogs.CardDialog(day_label(day), self, width=REPLIES_PANEL + S4 * 4)
        rows = RowList()
        rows.setMinimumHeight(min(len(events) + len(tasks), TASK_LIST_ROWS) * ROW_HEIGHT)
        entries: list[Entry] = []
        for kind, item in day_items(events, tasks):
            if kind == "event":
                entries.append(Row(f"event:{item['id']}", item["title"],
                                   f"{time_range(item['start_time'], item['end_time'])} · {item['location']}",
                                   accent=TODAY_EVENT, dim=item["past"], actions=["edit", "remove"],
                                   tags=[("Not sent to Google", OVERDUE)] if item["sync_status"] == "error" else []))
            else:
                row = TasksView.task_row(item, "overdue" if item["overdue"] else "open")
                row.id = f"task:{item['id']}"
                entries.append(row)
        rows.set_entries(entries)

        def act(row_id: str, name: str) -> None:
            dlg.accept()
            kind, item_id = row_id.split(":", 1)
            if name == "tick":
                self.tick_task(item_id)
            elif name == "edit":
                self.open_item(kind, item_id)
            elif name == "remove":
                self.remove_item(kind, item_id)

        rows.action.connect(act)
        dlg.body.addWidget(rows)
        return dlg


# --- Events ------------------------------------------------------------------------------------

class EventsView(View):
    title = "Events"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.hidden_b = button("Hidden", self.show_hidden, icon_name="eye-off",
                               tip="Events hidden from Home Base: show them again")  # WO-FS-011 HE-7
        self.hidden_b.hide()
        self.head_right.addWidget(self.hidden_b)
        self.refresh_b = refresh_button(self)  # GY-2a: labelled, beside the replies on the rows
        self.head_right.addWidget(self.refresh_b)
        self.head_right.addWidget(button("Clear all", lambda: self.window_.clear_all("events"), danger=True,
                                         tip="Cancel every event, with 10 seconds to undo"))
        self.head_right.addWidget(button("Add event", self.add, primary=True, icon_name="plus",
                                         tip="Add event (Ctrl+N)"))
        self.list = RowList()
        self.list.set_empty_state("calendar-days", "No events yet.", "Add event", self.add)  # EM-5, LC-7
        self.list.action.connect(self.on_action)
        self.outer.addWidget(self.list, 1)
        self.outer.addWidget(_muted("Replies, and events added or changed in Google Calendar, come in when someone "
                                    "clicks Refresh. Changes to a repeating event apply to the whole series."))
        self.events: dict[str, dict[str, Any]] = {}

    def reload(self) -> None:
        data = self.hub.events()
        self.subtitle.setText(synced_ago(data.get("synced_at")))  # GY-2: "Synced 10 minutes ago"
        hidden = data.get("hidden_count", 0)
        self.hidden_b.setText(f"Hidden ({hidden})")  # HE-7: shown only when something is hidden
        self.hidden_b.setVisible(bool(hidden))
        self.events = {e["id"]: e for e in data["upcoming"] + data["past"]}
        entries: list[Entry] = []
        for group in group_by_month(data["upcoming"], data["past"]):  # EM-1 to EM-6
            month = int(group.month[5:])  # WO-FS-009 MC-4: each month header on its own colour
            entries.append(Header(group.label, month_ink(month), band=month_colour(month),
                                  space_above=S3 if entries else S1))
            entries.extend(self._row(r.event, r.day, r.past) for r in group.rows)
        self.list.set_entries(entries)

    def _row(self, e: dict[str, Any], day: str, past: bool) -> Row:
        c = e["rsvp_counts"]
        tags = [(REPEAT_LABELS[e["repeat"]], TAG_TEXT)] if e["repeat"] != "none" else []  # EM-3
        tags.append((f"✓ {c['accepted']}  ✗ {c['declined']}  ? {c['no_reply'] + c['tentative']}", TAG_TEXT))
        actions = ["edit", "remove"]
        error = e["sync_status"] == "error"
        if error:  # EM-7
            tags.append(("Cancel failed" if e["sync_op"] == "delete" else "Not sent to Google", OVERDUE))
            actions = ["retry"] + (["discard"] if e["sync_op"] == "create" else []) + \
                ([] if e["sync_op"] == "delete" else ["edit"])
        elif e["sync_status"] == "pending":
            tags.append(("Sending…", TAG_TEXT))
        elif e["conflicts_with"]:
            tags.append(("Overlaps " + ", ".join(e["conflicts_with"][:2]), OVERDUE))
        if not e["organizer_connected"] and e["sync_op"] != "create":  # GS-6
            tags.append((f"Reconnect {e['organizer_email']}", OVERDUE))
            actions = [a for a in actions if a not in ("edit", "remove")]
        actions = ["cancel" if a == "remove" else a for a in actions]  # HE-9: Remove cancels it in Google
        today = day == today_iso() and not past
        colour = OVERDUE if error else (TODAY_EVENT if today else TEXT)
        replies = "\n".join(f"{i['email']}: {i['rsvp'].replace('_', ' ')}" for i in e["invitees"])
        subtitle = f"{day_label(day)} · {time_range(e['start_time'], e['end_time'])}"
        subtitle += f" · {e['location']}" if e["location"] else ""
        tip = "\n".join(x for x in (e["title"], subtitle, f"Organizer: {e['organizer_email']}",  # GS-7
                                    ", ".join(t for t, _ in tags), e["sync_error"] or "", replies) if x)
        extra, only = event_extras(e)
        if only is not None:
            actions = only
        elif not error:
            actions = ["car", "hide", *actions]
        else:
            actions = [*actions, "hide"]  # HE-2: even an event that never reached Google
        if e.get("read_only"):
            tip += f"\n{e['google_note']}: change it in Google Calendar."
        return Row(e["id"], e["title"], subtitle, accent=colour if colour != TEXT else EVENT_ACCENT,
                   title_colour=colour, tags=extra + tags, actions=actions, dim=past, tooltip=tip)

    def on_action(self, row_id: str, name: str) -> None:
        handlers = {"edit": self.edit, "remove": self.remove, "cancel": self.remove, "retry": self.retry,
                    "discard": self.discard, "hide": lambda: self.hide_event(self._selected()),
                    "delete_app": lambda: self.delete_from_app(self._selected()),
                    "car": lambda: self.choose_car(self._selected()), "open": lambda: open_in_google(self._selected())}
        if name in handlers:
            handlers[name]()

    def add_new(self) -> None:
        self.add()

    def refresh(self) -> None:
        self.refresh_rsvps()

    def select(self, item_id: str) -> None:
        self.list.select_id(item_id)

    def _selected(self) -> dict[str, Any] | None:
        event_id = self.list.selected_id()
        return self.events.get(event_id) if event_id else None

    def fetch(self, event_id: str) -> dict[str, Any] | None:
        """The full event, for the Today calendar (fetched fresh: Events may not have loaded yet)."""
        data = self.act(self.hub.events)
        if data:
            self.events = {e["id"]: e for e in data["upcoming"] + data["past"]}
        return self.events.get(event_id)

    def add(self, day: str | None = None, start: str | None = None, end: str | None = None) -> None:
        sender = self.act(self.hub.calendar_sender)  # GS-3
        dlg = dialogs.EventDialog(self.window_.members, self.hub.add_event, parent=self, sender=sender or None,
                                  day=day, start=start, end=end)
        dlg.set_cars(self.act(self.hub.cars) or [])
        if dlg.exec():
            who = (dlg.result_value or {}).get("organizer_email", "")
            self.window_.toast_message(f"Event added. Invites sent from {who}." if who else "Event added.")
            if dlg.car_id is not None and dlg.result_value:
                self._apply_car(dlg.result_value["id"], dlg.car_id)
            self.window_.changed()

    def edit(self, e: dict[str, Any] | None = None) -> None:
        e = e or self._selected()
        if not e or e.get("sync_op") == "delete":
            return

        def save(body: dict[str, Any]) -> Any:
            if (e["repeat"] != "none" or body["repeat"] != "none") and not body["acknowledge_conflicts"]:
                # EV-10: say plainly that the whole series changes, and who is told.
                note = e["series_note"] or f"This turns {e['title']} into a repeating event."
                notified = dialogs.invitee_list([i["email"] for i in e["invitees"] if i["rsvp"] != "organizer"])
                if not dialogs.confirm(self, "Change the whole series?", note,
                                       "Change all occurrences", danger=False,
                                       detail=f"{notified} will get one update from Google Calendar."):
                    raise _Cancelled()
            return self.hub.edit_event(e["id"], body)

        dlg = dialogs.EventDialog(self.window_.members, _cancellable(save), existing=e, parent=self)
        dlg.set_cars(self.act(self.hub.cars) or [], lambda: self.hub.event_cars(e["id"]))
        if dlg.exec():
            self.window_.toast_message("Event saved.")
            if dlg.car_changed:
                self._apply_car(e["id"], dlg.car_id)
            self.window_.changed()

    def _apply_car(self, event_id: str, car_id: int | None) -> None:
        """CR-7 from the form: a car that's already needed asks Use anyway first."""
        try:
            self.hub.set_event_car(event_id, car_id)
        except HubError as err:
            if err.status == 409 and dialogs.confirm(self, "Car already in use", err.detail, "Use anyway",
                                                     detail="Both events will show it as double-booked."):
                self.act(lambda: self.hub.set_event_car(event_id, car_id, use_anyway=True))
            elif err.status != 409:
                dialogs.error(self, err.detail)
        except HubUnreachable:
            self.window_.set_offline(True)

    def remove(self, e: dict[str, Any] | None = None) -> None:
        e = e or self._selected()
        if not e or e.get("sync_op") == "delete":
            return
        if e.get("from_google"):  # 1.9.1: the Delete key on a Google event deletes it from Home Base only
            self.delete_from_app(e)
            return
        if not e["organizer_connected"] and e["sync_op"] != "create":  # GS-6
            dialogs.info(self, "Remove event", f"Reconnect {e['organizer_email']} to remove {e['title']}. It was "
                                               "sent from that account, and Google only lets the organizer cancel it.")
            return
        notified = [i["email"] for i in e["invitees"] if i["rsvp"] != "organizer"]
        lines = []
        if e["series_note"]:
            lines.append(e["series_note"].replace("This changes", "This removes"))
        if e["sync_op"] != "create":
            lines.append(f"Remove cancels this event in Google Calendar: a cancellation goes from "
                         f"{e['organizer_email']} to {dialogs.invitee_list(notified)}. This can't be undone.")
        lines.append("To take it out of Home Base only, and keep it in Google Calendar, use Hide from app.")  # HE-9
        if dialogs.confirm(self, "Remove event", f"Cancel {e['title']} in Google Calendar?", "Remove event",
                           detail="\n\n".join(lines)):
            self.act(lambda: self.hub.remove_event(e["id"]), "Event removed.")
            self.window_.changed()

    def retry(self) -> None:
        e = self._selected()
        if e:
            self.act(lambda: self.hub.retry_event(e["id"]))
            self.window_.changed()

    def discard(self) -> None:
        e = self._selected()
        if e and dialogs.confirm(self, "Discard event", f"Discard {e['title']}?", "Discard",
                                 detail="It never reached Google Calendar, so nobody was invited."):
            self.act(lambda: self.hub.discard_event(e["id"]))
            self.window_.changed()

    def refresh_rsvps(self) -> None:
        """WO-FS-010 GY-2: Refresh replies became Sync with Google."""
        sync_now(self, self.refresh_b)

    def hide_event(self, e: dict[str, Any] | None) -> None:
        """HE-1 to HE-3: out of Home Base only, for the whole family; no confirmation, with Undo."""
        if not e or self.act(lambda: self.hub.hide_event(e["id"])) is None:
            return
        self.window_.changed()

        def undo() -> None:
            self.act(lambda: self.hub.show_event(e["id"]))
            self.window_.toast.hide()
            self.window_.changed()

        self.window_.toast.show_message("Hidden from Home Base", kind="info", button="Undo", on_click=undo,
                                        seconds=GROCERY_UNDO_S)

    def delete_from_app(self, e: dict[str, Any] | None) -> None:
        """1.9.1: a Google event out of Home Base for the whole family; the Google event stays. No confirmation,
        with Undo."""
        if not e or self.act(lambda: self.hub.delete_from_app(e["id"])) is None:
            return
        self.window_.changed()

        def undo() -> None:
            self.act(lambda: self.hub.undo_delete_from_app(e["id"]))
            self.window_.toast.hide()
            self.window_.changed()

        self.window_.toast.show_message("Deleted from Home Base. It's still in Google Calendar.", kind="info",
                                        button="Undo", on_click=undo, seconds=GROCERY_UNDO_S)

    def show_hidden(self) -> None:
        """HE-7: the Hidden list, with Show again on each."""
        events = self.act(self.hub.hidden_events)
        if events is None:
            return
        dlg = dialogs.HiddenDialog(events, self.hub.show_event, self, delete=self.hub.delete_from_app,
                                   delete_all=self.hub.delete_all_hidden, undo_delete=self.hub.undo_delete_from_app)
        dlg.exec()
        if dlg.shown:
            self.window_.toast_message(f"Shown again: {', '.join(dlg.shown)}.")
        if dlg.shown or dlg.deleted:
            self.window_.changed()

    def choose_car(self, e: dict[str, Any] | None) -> None:
        """CR-4, CR-5: the car button opens Which car?."""
        if e and not e.get("read_only") and dialogs.choose_car(self, self.hub, e):
            self.window_.changed()


class _Cancelled(Exception):
    pass


def _cancellable(save: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    """Lets a confirmation inside a save quietly keep the dialog open."""
    from family_scheduler.client.hub_client import HubError

    def run(body: dict[str, Any]) -> Any:
        try:
            return save(body)
        except _Cancelled as err:
            raise HubError(0, "Not saved. Change the event or click Cancel.", {}) from err

    return run


# --- Tasks -------------------------------------------------------------------------------------

class TasksView(View):
    title = "Tasks"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.head_right.addWidget(icon_button("trash-2", "Open Trash", lambda: self.window_.show_view("Trash")))
        self.head_right.addWidget(button("Clear all", lambda: self.window_.clear_all("tasks"), danger=True,
                                         tip="Move every task to Trash, with 10 seconds to undo"))
        self.head_right.addWidget(button("Add task", self.add, primary=True, icon_name="plus",
                                         tip="Add task (Ctrl+N)"))
        self.list = RowList()
        self.list.set_empty_state("list-checks", "No tasks yet.", "Add task", self.add)
        self.list.action.connect(self.on_action)
        self.outer.addWidget(self.list, 1)
        self.tasks: dict[str, dict[str, Any]] = {}

    def reload(self) -> None:
        data = self.hub.tasks()
        self.tasks = {t["id"]: t for group in data.values() for t in group}
        open_count = len(data["open"]) + len(data["overdue"]) + len(data["ongoing"])
        self.subtitle.setText(f"{open_count} open · {len(data['overdue'])} overdue")
        entries: list[Entry] = []
        # TD-8: overdue in red and "High" in blue, as on Today. WO-FS-009 OT-5: Ongoing after Open.
        # WO-FS-012 PL-1 to PL-7: Open became High, Medium and Low priority, each with its count; empty lists are
        # left out. (PL-6 asks for these counts, which WO-FS-008 ST-1 had removed from titles.)
        for key, label, colour in (("overdue", "Overdue", OVERDUE), ("high", "High priority", HIGH),
                                   ("medium", "Medium priority", TEXT), ("low", "Low priority", TEXT),
                                   ("ongoing", "Ongoing", TEXT), ("done", "Done, last 30 days", MUTED)):
            if not data.get(key):
                continue
            if key in ("high", "medium", "low"):
                label = f"{label} · {len(data[key])}"
            entries.append(Header(label, colour, space_above=S3 if entries else S1))
            entries.extend(self.task_row(task, key) for task in data[key])
        self.list.set_entries(entries)

    @staticmethod
    def task_row(task: dict[str, Any], key: str) -> Row:
        done = bool(task["completed_at"])
        tags, tips = task_priority_tags(task)  # DS-4: the effective priority, and Due soon
        if task["repeat"] != "none":
            tags.append((REPEAT_LABELS[task["repeat"]], TAG_TEXT))
        if not task["due_date"]:
            tags.append(("Ongoing", TAG_TEXT))  # WO-FS-009 OT-6
        if done:
            subtitle = f"Done {when(task['completed_at'])} by {short_email(task['completed_by'])}"
        elif not task["due_date"]:
            subtitle = "No due date"
        else:
            subtitle = f"Due {day_label(task['due_date'])}"
        high = task.get("effective_priority", task["priority"]) == "high"
        accent = OVERDUE if key == "overdue" else (HIGH if high and not done else EVENT_ACCENT)
        note = repeat_note(task)  # WO-FS-013 MR-9
        return Row(task["id"], task["title"], subtitle, accent=accent,
                   title_colour=OVERDUE if key == "overdue" else TEXT, tags=tags, tick=done, dim=done,
                   tag_tips=tips, tooltip=f"{task['title']}\n{subtitle}\n{note}" if note else "")

    def on_action(self, row_id: str, name: str) -> None:
        handlers = {"edit": self.edit, "remove": self.remove, "tick": self.toggle}
        if name in handlers:
            handlers[name]()

    def add_new(self) -> None:
        self.add()

    def select(self, item_id: str) -> None:
        self.list.select_id(item_id)

    def _selected(self) -> dict[str, Any] | None:
        task_id = self.list.selected_id()
        return self.tasks.get(task_id) if task_id else None

    def fetch(self, task_id: str) -> dict[str, Any] | None:
        data = self.act(self.hub.tasks)
        if data:
            self.tasks = {t["id"]: t for group in data.values() for t in group}
        return self.tasks.get(task_id)

    def add(self, due: str | None = None) -> None:
        if dialogs.TaskDialog(self.hub.add_task, parent=self, due=due).exec():
            self.window_.toast_message("Task added.")
            self.window_.changed()

    def edit(self, t: dict[str, Any] | None = None) -> None:
        t = t or self._selected()
        if t and dialogs.TaskDialog(lambda body: self.hub.edit_task(t["id"], body), existing=t, parent=self).exec():
            self.window_.toast_message("Task saved.")
            self.window_.changed()

    def toggle(self, t: dict[str, Any] | None = None) -> None:
        """LC-8: the round tick checks a task off, or reopens it."""
        t = t or self._selected()
        if not t:
            return
        if t["completed_at"]:
            self.act(lambda: self.hub.reopen_task(t["id"]), "Task reopened.")
        else:
            self.act(lambda: self.hub.complete_task(t["id"]), "Task checked off.")
        self.window_.changed()

    def remove(self, t: dict[str, Any] | None = None) -> None:
        t = t or self._selected()
        if t and dialogs.confirm(self, "Remove task", f"Remove {t['title']}?", "Move to Trash", danger=False,
                                 detail="You can restore it from Trash for 30 days."):  # TR-3
            self.act(lambda: self.hub.remove_task(t["id"]), "Moved to Trash.")
            self.window_.changed()


# --- Reminders ---------------------------------------------------------------------------------

class RemindersView(View):
    title = "Reminders"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.subtitle.setText("In the order added")
        self.head_right.addWidget(icon_button("trash-2", "Open Trash", lambda: self.window_.show_view("Trash")))
        self.head_right.addWidget(button("Clear all", lambda: self.window_.clear_all("reminders"), danger=True,
                                         tip="Move every reminder to Trash, with 10 seconds to undo"))
        self.head_right.addWidget(button("Add reminder", self.add, primary=True, icon_name="plus",
                                         tip="Add reminder (Ctrl+N)"))
        self.list = RowList()
        self.list.set_empty_state("sticky-note", "No reminders yet.", "Add reminder", self.add)  # LC-7
        self.list.action.connect(self.on_action)
        self.outer.addWidget(self.list, 1)
        self.reminders: dict[str, dict[str, Any]] = {}

    def reload(self) -> None:
        rows = self.hub.reminders()
        self.reminders = {r["id"]: r for r in rows}
        self.list.set_entries([Row(r["id"], " ".join(r["message"].split()), f"— {short_email(r['created_by'])}",
                                   accent=REM, tooltip=f"{r['message']}\n— {r['created_by']}") for r in rows])

    def on_action(self, row_id: str, name: str) -> None:
        handlers = {"edit": self.edit, "remove": self.remove}
        if name in handlers:
            handlers[name]()

    def add_new(self) -> None:
        self.add()

    def _selected(self) -> dict[str, Any] | None:
        rid = self.list.selected_id()
        return self.reminders.get(rid) if rid else None

    def fetch(self, reminder_id: str) -> dict[str, Any] | None:
        rows = self.act(self.hub.reminders)
        if rows:
            self.reminders = {r["id"]: r for r in rows}
        return self.reminders.get(reminder_id)

    def add(self) -> None:
        if dialogs.ReminderDialog(lambda body: self.hub.add_reminder(body["message"]), parent=self).exec():
            self.window_.toast_message("Reminder added.")
            self.window_.changed()

    def edit(self, r: dict[str, Any] | None = None) -> None:
        r = r or self._selected()
        if r and dialogs.ReminderDialog(lambda body: self.hub.edit_reminder(r["id"], body["message"]), existing=r,
                                        parent=self).exec():
            self.window_.toast_message("Reminder saved.")
            self.window_.changed()

    def remove(self, r: dict[str, Any] | None = None) -> None:
        r = r or self._selected()
        if r and dialogs.confirm(self, "Remove reminder", "Remove this reminder?", "Move to Trash", danger=False,
                                 detail=f"“{r['message']}”\n\nYou can restore it from Trash for 30 days."):
            self.act(lambda: self.hub.remove_reminder(r["id"]), "Moved to Trash.")
            self.window_.changed()


# --- History -----------------------------------------------------------------------------------

_FIELD = {"title": "Title", "date": "Date", "start_time": "Start", "end_time": "End", "location": "Location",
          "repeat": "Repeats", "invitees": "Invitees", "priority": "Priority", "due_date": "Due",
          "message": "Message", "next_due_date": "Next due", "car": "Car", "cars": "Cars", "end_date": "Until"}


def _value(key: str, value: Any) -> str:
    if value is None:
        return "–"
    if key in ("start_time", "end_time"):
        return fmt_time(str(value))
    if key == "due_date" and not value:
        return "No due date"
    if key in ("date", "due_date", "next_due_date"):
        return day_label(str(value))
    if key == "cars" and isinstance(value, list):  # CR-3
        return ", ".join(str(v) for v in value)
    if key == "invitees" and isinstance(value, list):
        return ", ".join(short_email(v) for v in value)
    if key == "repeat":
        return REPEAT_LABELS.get(str(value), str(value))
    return str(value)


def describe_change(entry: dict[str, Any]) -> str:
    before, after = entry["before"] or {}, dict(entry["after"] or {})
    sender = after.pop("invites_from", None)  # WO-FS-005 AS-5
    sent = {"added": f"Invites sent from {sender}", "edited": f"Update sent from {sender}",
            "removed": f"Cancellation sent from {sender}"}.get(entry["action"], "") if sender else ""
    # WO-FS-010 GY-4 to GY-6, GY-10: changes Sync with Google brought in
    source, note = after.pop("source", None), after.pop("note", None)
    after.pop("calendar", None)
    if source:
        sent = {"added": f"Added in {source}", "edited": f"Changed in {source}",
                "removed": f"Removed in {source}"}.get(entry["action"], "")
        if note:
            sent += f". {note}"
    if entry["action"] == "edited":
        changes = []
        if "all_day" in after:  # WO-FS-008 AD-5: "Time 4:00 pm–5:00 pm → All day", as one change
            def times(side: dict[str, Any]) -> str:
                return time_range(str(side.get("start_time") or ""), str(side.get("end_time") or ""))
            changes.append(f"Time {times(before)} → {times(after)}")
            after = {k: v for k, v in after.items() if k not in ("all_day", "start_time", "end_time")}
        changes += [f"{_FIELD.get(k, k)} {_value(k, before.get(k))} → {_value(k, after.get(k))}" for k in after]
        if note and not source:  # WO-FS-013 ET-4: "Repeat day restored to the 31st"
            changes.insert(0, note)
        if source:  # "Changed in Google Calendar" first, then what changed
            return "  ·  ".join([sent, *changes])
        return "  ·  ".join([*changes, sent] if sent else changes)
    if sent:
        return sent
    if entry["action"] == "completed" and after.get("next_due_date"):
        return f"Next one due {day_label(after['next_due_date'])}"
    if entry["action"] == "removed" and after.get("to") == "Trash":
        return "Moved to Trash"
    if entry["action"] == "removed" and after.get("to") == "deleted for good":  # WO-FS-014 RL-2
        return "Deleted from Trash for good"
    if entry["action"] == "cleared" and after.get("to") == "deleted for good":
        return f"Cleared Trash ({_plural(int(after.get('count', 0)), 'item')})"
    if entry["action"] == "removed" and after.get("to") == "deleted from app":  # 1.9.1
        return "Deleted from Home Base (still in Google Calendar)"
    if entry["action"] == "removed" and after.get("to") == "hidden":  # WO-FS-011 HE-8
        return "Hidden from Home Base"
    if entry["action"] == "restored" and after.get("from") in ("hidden", "deleted from app"):
        return "Back in Home Base"
    return ""


class HistoryView(View):
    title = "History"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.subtitle.setText("Newest first · kept 30 days")
        self.list_filter = QComboBox()
        for label, key in (("All lists", ""), ("Events", "events"), ("Tasks", "tasks"), ("Reminders", "reminders")):
            self.list_filter.addItem(label, key)
        self.member_filter = QComboBox()
        self.list_filter.currentIndexChanged.connect(lambda *_: self.window_.safe_reload(self))
        self.member_filter.currentIndexChanged.connect(lambda *_: self.window_.safe_reload(self))
        self.head_right.addWidget(self.list_filter)
        self.head_right.addWidget(self.member_filter)
        self.head_right.addWidget(button("Clear all", self.clear, danger=True,
                                         tip="Delete every History entry, with 10 seconds to undo"))
        self.list = RowList()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.action.connect(self.on_action)
        self.outer.addWidget(self.list, 1)

    def reload(self) -> None:
        current = self.member_filter.currentData()
        self.member_filter.blockSignals(True)
        self.member_filter.clear()
        self.member_filter.addItem("Anyone", "")
        for m in self.window_.members:
            self.member_filter.addItem(m["email"], m["email"])
        index = self.member_filter.findData(current) if current else 0
        self.member_filter.setCurrentIndex(max(index, 0))
        self.member_filter.blockSignals(False)
        rows = self.hub.history(self.list_filter.currentData() or None, self.member_filter.currentData() or None)
        entries: list[Entry] = []
        for entry in rows:
            kind = {"event": "event", "task": "task", "reminder": "reminder"}.get(entry["item_type"], "")
            action = f"{entry['action']} {kind}".strip() if entry["item_type"] != "list" else entry["action"]
            change = describe_change(entry)
            subtitle = f"{when(entry['at'])} · {short_email(entry['who'])} {action}"
            subtitle += f" · {change}" if change else ""
            accent = REM if kind == "reminder" else EVENT_ACCENT
            entries.append(Row(f"h{entry['id']}", entry["label"], subtitle, accent=accent,
                               actions=["delete_history"],
                               tooltip=f"{entry['label']}\n{when(entry['at'])} · {entry['who']} {action}"
                                       + (f"\n{change}" if change else "")))
        if not rows:
            entries.append(Note("Nothing has changed in the last 30 days."))
        self.list.set_entries(entries)

    def on_action(self, row_id: str, name: str) -> None:
        if name == "delete_history":
            self.delete_entry(int(row_id[1:]))

    def delete_entry(self, entry_id: int) -> None:
        """1.10.1: one entry out of History for the whole family; no confirmation, with Undo."""
        done = self.act(lambda: self.hub.delete_history(entry_id))
        if done:
            self.window_.start_undo(done["id"], "Deleted from History. {seconds} s to undo.",
                                    undo_with=self.hub.undo_history_delete, undone="Back in History.")
            self.window_.changed()

    def clear(self) -> None:
        """1.10.1: Clear all on History deletes every entry, whatever the filters show, with 10 seconds to undo."""
        if not dialogs.confirm(self, "Clear all", "Clear all History?", "Clear History",
                               detail="Every entry is deleted, for the whole family, not just the ones shown. "
                                      "Nothing else changes. You'll have 10 seconds to undo."):
            return
        done = self.act(self.hub.clear_history)
        if done:
            n = done["count"]
            self.window_.start_undo(done["id"], f"History cleared ({n} {'entry' if n == 1 else 'entries'}). "
                                    "{seconds} s to undo.", undo_with=self.hub.undo_history_delete,
                                    undone="History is back.")
            self.window_.changed()


# --- Trash -------------------------------------------------------------------------------------

class TrashView(View):
    title = "Trash"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.subtitle.setText("Kept 30 days")  # short, so the four buttons fit at 1024 wide (WO-FS-014)
        self.head_right.addWidget(button("Select all", self.select_all))
        self.delete_b = button("Delete ticked", self.delete_ticked, danger=True,  # WO-FS-014 TK-a
                               tip="Delete every ticked item forever, with 10 seconds to undo")
        self.head_right.addWidget(self.delete_b)
        self.clear_b = button("Clear all", self.clear, danger=True,  # CT-1
                              tip="Delete everything in Trash forever, with 10 seconds to undo")
        self.head_right.addWidget(self.clear_b)
        self.restore_b = button("Restore ticked", self.restore, primary=True, icon_name="undo-2",
                                tip="Restore every ticked item")
        self.head_right.addWidget(self.restore_b)
        self.list = RowList()
        self.list.set_empty_state("trash-2", "Trash is empty.", "", None)
        self.list.action.connect(self.on_action)
        self.outer.addWidget(self.list, 1)
        self.outer.addWidget(_muted("Removed tasks and reminders are deleted for good after 30 days. "
                                    "Removed events aren't here: they were cancelled in Google Calendar."))
        self.rows: list[dict[str, Any]] = []
        self.ticked: set[str] = set()

    @staticmethod
    def _key(r: dict[str, Any]) -> str:
        return f"{r['type']}:{r['id']}"

    def reload(self) -> None:
        self.rows = self.hub.trash()
        self.ticked &= {self._key(r) for r in self.rows}
        self.show_rows()

    def show_rows(self) -> None:
        self.list.set_entries([
            Row(self._key(r), r["label"], f"{r['type'].title()} · removed {when(r['deleted_at'])} by "
                f"{short_email(r['deleted_by'] or '')} · {_plural(r['days_left'], 'day')} left",
                accent=REM if r["type"] == "reminder" else EVENT_ACCENT, actions=["restore", "delete_forever"],
                tick=self._key(r) in self.ticked)
            for r in self.rows])
        self.restore_b.setEnabled(bool(self.ticked))
        self.delete_b.setEnabled(bool(self.ticked))
        self.clear_b.setEnabled(bool(self.rows))  # CT-1: disabled when Trash is empty

    def on_action(self, row_id: str, name: str) -> None:
        if name == "tick":
            self.ticked ^= {row_id}
            self.show_rows()
        elif name == "restore":
            self._restore([row_id])
        elif name in ("delete_forever", "remove"):  # DT-1; DT-2: the Delete key sends "remove"
            self._delete([row_id], "Deleted forever. {seconds} s to undo.")

    def delete_ticked(self) -> None:
        """TK-a: asks once, then works like Clear all for just the ticked items."""
        n = len(self.ticked)
        if n and dialogs.confirm(self, "Delete ticked", f"Delete {_plural(n, 'item')} forever?", "Delete forever",
                                 detail="You'll have 10 seconds to undo."):
            self._delete(sorted(self.ticked), f"Deleted {_plural(n, 'item')} forever. {{seconds}} s to undo.")

    def clear(self) -> None:
        """CT-1 to CT-4: everything in Trash, for the whole family, not just the ticked items."""
        n = len(self.rows)
        if not n or not dialogs.confirm(
                self, "Clear all", "Clear all Trash?", "Clear all",
                detail=f"All {_plural(n, 'item')} in Trash are deleted for good, for the whole family. "
                       "You'll have 10 seconds to undo."):
            return
        done = self.act(self.hub.clear_trash)
        if done:
            self.ticked.clear()
            self._offer_undo(done["id"], f"Trash cleared ({_plural(done['count'], 'item')}). {{seconds}} s to undo.")

    def _delete(self, keys: list[str], message: str) -> None:
        """DT-3: no confirmation for one item; a toast with the 10-second Undo."""
        chosen = [{"type": k.split(":", 1)[0], "id": k.split(":", 1)[1]} for k in keys]
        done = self.act(lambda: self.hub.delete_from_trash(chosen))
        if done:
            self.ticked -= set(keys)
            self._offer_undo(done["id"], message)

    def _offer_undo(self, batch: str, message: str) -> None:
        self.window_.start_undo(batch, message, undo_with=self.hub.undo_trash_delete, undone="Back in Trash.")
        self.window_.changed()

    def select_all(self) -> None:
        self.ticked = {self._key(r) for r in self.rows}
        self.show_rows()

    def restore(self) -> None:
        if not self.ticked:
            dialogs.info(self, "Restore", "Tick the items to restore first.")
            return
        self._restore(sorted(self.ticked))

    def _restore(self, keys: list[str]) -> None:
        chosen = [{"type": k.split(":", 1)[0], "id": k.split(":", 1)[1]} for k in keys]
        result = self.act(lambda: self.hub.restore(chosen))
        if result:
            self.ticked -= set(keys)
            self.window_.toast_message(f"Restored {_plural(result['restored'], 'item')}.")
        self.window_.changed()


# --- Family & Settings -------------------------------------------------------------------------

class SettingsView(View):
    title = "Family & Settings"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.head_right.addWidget(button("Setup guide", self.window_.show_guide, icon_name="book-open",  # SG-1
                                         tip="Step-by-step setup: digest email, Google Calendar, another PC"))
        self.test_notice = QLabel("Test mode: Google isn't used. Invites aren't sent.")  # CB-7
        self.test_notice.setObjectName("Notice")
        self.test_notice.hide()
        self.outer.addWidget(self.test_notice)
        grid = QGridLayout()
        grid.setHorizontalSpacing(S2)
        grid.setVerticalSpacing(S2)
        page = QWidget()  # the page scrolls when it's taller than the window (FN-5)
        page.setObjectName("PlainPage")
        page.setLayout(grid)
        grid.setContentsMargins(0, 0, HALF, 0)
        scroller = QScrollArea()
        scroller.setObjectName("PlainScroll")
        scroller.setWidget(page)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.outer.addWidget(scroller, 1)
        self.scroller = scroller
        self.cards: dict[str, QFrame] = {}  # the Setup guide's Go to buttons (WO-FS-009 SG-7)

        # Family members, as rows (LC-1)
        members_frame, members = self._card("Family members")
        self.member_list = RowList()
        self.member_list.action.connect(self._member_action)
        members.addWidget(self.member_list, 1)
        row = QHBoxLayout()
        row.setSpacing(S1)
        self.new_member = QLineEdit()
        self.new_member.setPlaceholderText("name@gmail.com")
        self.new_member.returnPressed.connect(self.add_member)
        row.addWidget(self.new_member, 1)
        row.addWidget(button("Add", self.add_member, primary=True, icon_name="plus"))
        members.addLayout(row)
        grid.addWidget(members_frame, 1, 0)

        # Calendar accounts (WO-FS-005 GA-5): the two Google accounts that send invites
        google_frame, google = self._card("Calendar accounts")
        self.google_status = _muted("")
        google.addWidget(self.google_status)
        client_row = QHBoxLayout()  # OA-3: the Google client file, chosen once
        client_row.setSpacing(S1)
        self.client_status = _muted("")
        client_row.addWidget(self.client_status, 1)
        self.client_b = button("Choose Google client file", self.choose_client_file,
                               tip="The client_secret JSON of the Desktop app client in Google Cloud")
        client_row.addWidget(self.client_b)
        google.addLayout(client_row)
        self.account_list = RowList()
        self.account_list.itemSelectionChanged.connect(self._account_buttons)
        self.account_list.setFixedHeight(2 * ROW_HEIGHT + HALF)
        google.addWidget(self.account_list, 1)
        row = QHBoxLayout()
        row.setSpacing(S1)
        self.connect_b = button("Connect", self.connect_google, primary=True,
                                tip="Sign in to Google as the selected account, on this hub PC")
        self.disconnect_b = button("Disconnect", self.disconnect_google, danger=True)
        row.addWidget(self.connect_b)
        row.addWidget(self.disconnect_b)
        row.addStretch()
        google.addLayout(row)
        self.account_reason = _muted("")  # CB-3: why a button is greyed out
        google.addWidget(self.account_reason)
        second = QHBoxLayout()  # WO-FS-010 AC-5: the second calendar account is a setting, chosen on the hub PC
        second.setSpacing(S1)
        self.second_account = QLineEdit()
        self.second_account.setPlaceholderText("Second calendar account (optional), e.g. name@gmail.com")
        self.second_b = button("Set second account", self.set_second_account,
                               tip="The other Google account whose events go out from itself")
        second.addWidget(self.second_account, 1)
        second.addWidget(self.second_b)
        google.addLayout(second)
        grid.addWidget(google_frame, 0, 0, 1, 2)  # WO-FS-006: full width, first
        self.cards["calendar"] = google_frame

        # Digest email
        mail_frame, mail = self._card("Digest email")
        self.mail_status = _muted("")
        mail.addWidget(self.mail_status)
        row = QHBoxLayout()
        row.setSpacing(S1)
        self.app_password = QLineEdit()
        self.app_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.app_password.setPlaceholderText("Gmail app password, 16 letters")
        self.password_b = button("Save", self.save_password)
        row.addWidget(self.app_password, 1)
        row.addWidget(self.password_b)
        mail.addLayout(row)
        row = QHBoxLayout()
        self.test_mail_b = button("Send test email", self.send_test_email, icon_name="mail",
                                  tip="Send a short test message to the admin address. It counts towards the "
                                      "20 emails a day.")  # WO-FS-007 EL-5
        row.addWidget(self.test_mail_b)
        row.addStretch()
        mail.addLayout(row)
        self.admin_label = _muted("")
        mail.addWidget(self.admin_label)
        row = QHBoxLayout()
        row.setSpacing(S1)
        self.new_admin = QLineEdit()
        self.new_admin.setPlaceholderText("New admin address")
        self.admin_b = button("Change admin", self.change_admin, tip="Change the account that sends the digest")
        row.addWidget(self.new_admin, 1)
        row.addWidget(self.admin_b)
        mail.addLayout(row)
        grid.addWidget(mail_frame, 1, 1)
        self.cards["digest"] = mail_frame

        # Cars (WO-FS-010 CR-1 to CR-3)
        cars_frame, cars = self._card("Cars")
        cars.addWidget(_muted("Pick a car on any event with its car button. Today shows which cars are in use."))
        count_row = QHBoxLayout()
        count_row.setSpacing(S1)
        count_row.addWidget(QLabel("Number of cars"))
        self.car_count = QSpinBox()
        self.car_count.setRange(1, MAX_CARS)
        self.car_count.valueChanged.connect(self._show_car_names)
        count_row.addWidget(self.car_count)
        count_row.addStretch()
        cars.addLayout(count_row)
        names = QGridLayout()
        names.setHorizontalSpacing(S1)
        names.setVerticalSpacing(HALF)
        self.car_names: list[QLineEdit] = []
        for n in range(MAX_CARS):
            name = QLineEdit()
            name.setMaxLength(CAR_NAME_MAX)
            name.setPlaceholderText(f"Car {n + 1}")
            self.car_names.append(name)
            names.addWidget(name, n // 2, n % 2)
        cars.addLayout(names)
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_row.addWidget(button("Save cars", self.save_cars, primary=True))
        cars.addLayout(save_row)
        grid.addWidget(cars_frame, 2, 0, 1, 2)
        self.cards["cars"] = cars_frame

        # This PC
        pc_frame, pc = self._card("This PC")
        self.pc_info = _muted("")
        pc.addWidget(self.pc_info)
        self.hub_status = _muted("")  # WO-FS-008 PS-9
        self.hub_status.setWordWrap(True)
        pc.addWidget(self.hub_status)
        grid.addWidget(pc_frame, 3, 0, 1, 2)
        self.cards["pc"] = pc_frame
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.member_list.setMinimumHeight(MEMBER_LIST_HEIGHT)
        self.settings: dict[str, Any] = {}
        self.accounts: dict[str, dict[str, Any]] = {}

    def show_card(self, key: str) -> None:
        """SG-7: scroll a card into view (after the page has laid itself out) and briefly put focus in it."""
        card = self.cards.get(key)
        if card is None:
            return

        def scroll() -> None:
            self.scroller.ensureWidgetVisible(card, 0, S2)
            card.setFocus()

        QTimer.singleShot(0, scroll)

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(S2, S2, S2, S2)
        layout.setSpacing(S1)
        heading = QLabel(title)
        heading.setObjectName("Section")
        layout.addWidget(heading)
        return frame, layout

    def reload(self) -> None:
        s = self.settings = self.hub.settings()
        self.member_list.set_entries([
            Row(m["email"], m["email"], "Admin" if m["is_admin"] else "Family member",
                actions=[] if m["is_admin"] else ["remove"])
            for m in self.window_.members])
        self.test_notice.setVisible(s["google_mode"] == "fake")
        self.client_status.setText("Google client file: set up" if s["google_client_set"]
                                   else "Google client file: not set up yet")
        self.client_b.setText("Replace client file" if s["google_client_set"] else "Choose Google client file")
        self.client_b.setEnabled(s["on_hub_pc"] and s["google_mode"] != "fake")
        if s["google_mode"] == "fake":
            self.google_status.setText("Test mode: nobody is invited.")
        else:
            self.google_status.setText(  # WO-FS-010 TX-2: no names, only the hub's own settings
                f"Each calendar account's events go out from that account; everyone else's go out from "
                f"{s['admin_email'] or 'the admin'}. " + ("" if s["on_hub_pc"] else "Connect on the hub PC."))
        labels = {"connected": ("Connected", TODAY_EVENT), "not_connected": ("Not connected", TAG_TEXT),
                  "needs_reconnect": ("Needs reconnecting", OVERDUE)}
        self.accounts = {a["email"]: a for a in s["calendar_accounts"]}
        self.account_list.set_entries([
            Row(a["email"], a["email"], labels[a["status"]][0],
                accent=labels[a["status"]][1] if a["status"] != "not_connected" else EVENT_ACCENT,
                title_colour=OVERDUE if a["status"] == "needs_reconnect" else TEXT, actions=[],
                tooltip="\n".join(x for x in (a["email"], labels[a["status"]][0], a["last_error"] or "",
                                              f"Connected {when(a['connected_at'])}" if a["connected_at"] else "")
                                  if x))
            for a in s["calendar_accounts"]])
        if self.account_list.selected_id() is None and s["calendar_accounts"]:
            first = next((a for a in s["calendar_accounts"] if a["status"] != "connected"), s["calendar_accounts"][0])
            self.account_list.select_id(first["email"])
        self._account_buttons()
        accounts = [a["email"] for a in s["calendar_accounts"]]
        self.second_account.setText(accounts[1] if len(accounts) > 1 else "")
        self.load_cars()
        self.second_account.setEnabled(s["on_hub_pc"])
        self.second_b.setEnabled(s["on_hub_pc"])
        self.admin_label.setText(f"Admin: {s['admin_email']}. Disconnect calendar accounts to change it.")
        self.admin_b.setEnabled(not s["google_connected"] or s["google_mode"] == "fake")
        self.new_admin.setEnabled(self.admin_b.isEnabled())
        self.mail_status.setText(
            ("Ready. Digests are sent from " + s["admin_email"] + " through Gmail." if s["mail_configured"]
             else "Not set up. Create an app password in the admin's Google Account (Security → App passwords).")
            + ("" if s["on_hub_pc"] else "  The app password can only be entered on the hub PC."))
        self.password_b.setEnabled(s["on_hub_pc"])
        self.app_password.setEnabled(s["on_hub_pc"])
        self.pc_info.setText(f"App version {__version__} · hub version {s['hub_version']} · "
                             f"Hub address: {self.window_.client.address}"
                             + ("  (this PC is the home hub)" if s["on_hub_pc"] else ""))
        self.hub_status.setText("Hub status: " + hub_status_line(s.get("hub_status"), when)
                                if s.get("hub_status") else "")

    def _account_buttons(self) -> None:
        """GA-5, CB-3: the buttons work on the hub PC only, and say why when they can't."""
        state = account_buttons(self.settings, self.accounts.get(self.account_list.selected_id() or ""))
        self.connect_b.setText(state.connect_label)
        self.connect_b.setEnabled(state.connect)
        self.disconnect_b.setEnabled(state.disconnect)
        self.account_reason.setText(state.reason)
        self.account_reason.setVisible(bool(state.reason))

    def _member_action(self, email: str, name: str) -> None:
        if name == "remove":
            self.remove_member(email)

    def add_member(self) -> None:
        email = self.new_member.text().strip()
        if email and self.act(lambda: self.hub.add_member(email), f"{email} added."):
            self.new_member.clear()
            self.window_.changed()

    def remove_member(self, email: str) -> None:
        if dialogs.confirm(self, "Remove family member", f"Remove {email} from the family list?", "Remove",
                           detail="They won't be able to pick their name or be invited to new events."):
            self.act(lambda: self.hub.remove_member(email), f"{email} removed.")
            self.window_.changed()

    def choose_client_file(self) -> None:
        """OA-2, OA-3: check the client file here, then keep it on the hub so Connect never asks again."""
        from family_scheduler.google_calendar import CalendarError, check_client_file

        path, _ = QFileDialog.getOpenFileName(self, "Choose the Google client file (client_secret….json)", "",
                                              "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            check_client_file(text)
        except (OSError, UnicodeDecodeError) as err:
            dialogs.error(self, f"Couldn't read that file: {err}")
            return
        except CalendarError as err:
            dialogs.error(self, str(err))
            return
        if self.act(lambda: self.hub.set_client_file(text), "Google client file is set up."):
            self.window_.changed()

    def connect_google(self) -> None:
        """GA-2, CB-4, CB-6: Google's sign-in in this hub PC's browser, for the selected account. The waiting
        happens off the window's thread, so the app never freezes."""
        account = self.account_list.selected_id()
        if not account:
            return
        client = self.act(self.hub.client_file)
        if not client:
            return
        waiting = dialogs.MessageDialog(
            self, "Connect a calendar account", f"Finish signing in as {account} in your browser.",
            "If Google says the app isn't verified, click Advanced, then Go to Home Base. This is expected. "
            "This window closes by itself when you're done.", "Cancel", danger=False, cancel=False)
        worker = _SignIn(client, account)
        result: dict[str, str] = {}

        def signed_in(token: str, email: str) -> None:
            result.update(token=token, email=email)
            waiting.accept()

        def failed(message: str) -> None:
            result.update(error=message)
            waiting.accept()

        worker.done.connect(signed_in)
        worker.failed.connect(failed)
        self._sign_in = worker  # keep it alive until it finishes
        worker.start()
        waiting.exec()
        if not result:
            self.window_.toast_message("Sign-in cancelled. Close the browser tab if it's still open.", "error")
            return
        if "error" in result:
            dialogs.error(self, result["error"])
            return
        self.act(lambda: self.hub.connect_google(result["token"], result["email"], account), f"{account} is connected.")
        self.window_.changed()

    def disconnect_google(self) -> None:
        """CB-5: confirm, remove the token from the hub and revoke it at Google."""
        account = self.account_list.selected_id()
        if not account or not dialogs.confirm(
                self, "Disconnect calendar account", f"Disconnect {account}?", "Disconnect",
                detail="Its access is removed from the hub and revoked at Google. Its events can't be changed or "
                       "removed until it's connected again. New events are sent from the other account."):
            return
        result = self.act(lambda: self.hub.disconnect_google(account))
        if result:
            note = result.get("note") if isinstance(result, dict) else None
            self.window_.toast_message(f"{account} is disconnected." + (f" {note}" if note else ""),
                                       "error" if note else "ok")
        self.window_.changed()

    def _show_car_names(self, count: int) -> None:
        for n, name in enumerate(self.car_names):
            name.setVisible(n < count)
            if n < count and not name.text().strip():
                name.setText(f"Car {n + 1}")  # CR-1: a new car's default name

    def load_cars(self) -> None:
        cars = self.hub.cars()
        for n, name in enumerate(self.car_names):
            name.setText(cars[n]["name"] if n < len(cars) else "")
        self.car_count.blockSignals(True)
        self.car_count.setValue(len(cars))
        self.car_count.blockSignals(False)
        self._show_car_names(len(cars))

    def save_cars(self) -> None:
        """CR-1 to CR-3: renames apply everywhere; removing a car that's needed asks Remove anyway or Cancel."""
        names = [n.text() for n in self.car_names[:self.car_count.value()]]
        try:
            self.hub.set_cars(names)
        except HubError as err:
            if err.status != 409:
                dialogs.error(self, err.detail)
                return
            if not dialogs.confirm(self, "Remove car", err.detail, "Remove anyway",
                                   detail="The car is cleared from those events."):
                self.load_cars()
                return
            if self.act(lambda: self.hub.set_cars(names, remove_anyway=True)) is None:
                return
        except HubUnreachable:
            self.window_.set_offline(True)
            return
        self.window_.toast_message("Cars saved.")
        self.window_.changed()

    def set_second_account(self) -> None:
        """AC-5: add, change or remove (empty) the second calendar account."""
        email = self.second_account.text().strip()
        done = f"{email} is the second calendar account. Select it and click Connect." if email else \
            "Second calendar account removed."
        self.act(lambda: self.hub.set_second_account(email), done)
        self.window_.changed()

    def change_admin(self) -> None:
        email = self.new_admin.text().strip()
        if email and dialogs.confirm(self, "Change admin account", f"Make {email} the admin account?", "Change",
                                     danger=False, detail="Then connect Google Calendar and set the Gmail app "
                                                          "password again, signed in as the new account."):
            self.act(lambda: self.hub.set_admin_email(email), "Admin account changed.")
            self.new_admin.clear()
            self.window_.changed()

    def send_test_email(self) -> None:
        result = self.act(self.hub.send_test_email)
        if result:
            self.window_.toast_message(result["message"])

    def save_password(self) -> None:
        password = self.app_password.text()
        if password and self.act(lambda: self.hub.set_app_password(password), "App password saved on the hub."):
            self.app_password.clear()
            self.window_.changed()


class _SignIn(QThread):
    """Runs Google's sign-in and waits for the browser, away from the window (CB-4)."""

    done = Signal(str, str)
    failed = Signal(str)

    def __init__(self, client: dict[str, Any], account: str) -> None:
        super().__init__()
        self.client, self.account = client, account

    def run(self) -> None:
        from family_scheduler.google_calendar import SignInError, run_admin_sign_in

        try:
            token, email = run_admin_sign_in(self.client, self.account)
        except SignInError as err:
            self.failed.emit(str(err))
        except Exception as err:  # noqa: BLE001 - whatever happens, the window gets a message
            self.failed.emit(f"Google sign-in didn't finish: {err}")
        else:
            self.done.emit(token, email)


GROUPS = {"food": ("Food", FOOD, "No food on the list."), "item": ("Items", ITEMS, "No items on the list.")}


class GroceriesView(View):
    """WO-FS-010 section 10, WO-FS-011 section 4: one shared list in two groups, Food (green) then Items (blue). In
    each, the items to buy in their shared order, then the bought ones, faded."""

    title = "Groceries"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.head_right.addWidget(button("Clear bought", self.clear_bought, danger=True,
                                         tip="Remove every bought item, in both groups, with Undo"))  # GR-5, GG-5
        add_row = QHBoxLayout()
        add_row.setSpacing(S1)
        self.entry = QLineEdit()
        self.entry.setMaxLength(GROCERY_MAX)
        self.entry.setPlaceholderText("Add an item, e.g. Milk (2 L)")
        self.entry.returnPressed.connect(self.add_new)  # GR-2: Enter adds too
        add_row.addWidget(self.entry, 1)
        switch = QFrame()  # GG-3: Food / Items, beside the name box
        switch.setObjectName("Segment")
        seg = QHBoxLayout(switch)
        seg.setContentsMargins(2, 2, 2, 2)
        seg.setSpacing(2)
        self.food_b = button("Food", lambda: self.choose_group("food"), tip="Add to Food")
        self.items_b = button("Items", lambda: self.choose_group("item"), tip="Add to Items (household items)")
        for b in (self.food_b, self.items_b):
            b.setCheckable(True)
            seg.addWidget(b)
        add_row.addWidget(switch)
        add_row.addWidget(button("+ Add", self.add_new, primary=True))
        self.outer.addLayout(add_row)
        self.list = GroceryList()
        self.list.action.connect(self.on_action)
        self.list.dropped.connect(self.dropped)
        self.outer.addWidget(self.list, 1)
        self.items: dict[str, dict[str, Any]] = {}
        self.group = "food"
        self.choose_group(self.window_.settings.get("grocery_group") or "food")  # GG-3: the last choice

    def choose_group(self, group: str) -> None:
        self.group = group if group in GROUPS else "food"
        self.food_b.setChecked(self.group == "food")
        self.items_b.setChecked(self.group == "item")
        self.window_.settings.set("grocery_group", self.group)

    def reload(self) -> None:
        data = self.hub.groceries()
        self.items = {i["id"]: i for i in data["to_buy"] + data["bought"]}
        self.subtitle.setText(f"{len(data['to_buy'])} to buy")
        entries: list[Entry] = []
        self.list.groups = {}
        for key, (label, colour, empty) in GROUPS.items():  # GG-1: Food first; ST-1: no counts
            group = data["groups"][key]
            entries.append(Header(label, colour, space_above=S3 if entries else S1, key=key))
            to_buy = group["to_buy"]
            for n, item in enumerate(to_buy):
                self.list.groups[item["id"]] = key
                entries.append(self._row(item, colour, first=n == 0, last=n == len(to_buy) - 1))
            entries.extend(self._row(item, colour) for item in group["bought"])  # GG-5: inside their own group
            if not group["to_buy"] and not group["bought"]:
                entries.append(Note(empty))  # GG-6
        self.list.set_entries(entries)

    @staticmethod
    def _row(item: dict[str, Any], colour: str, first: bool = False, last: bool = False) -> Row:
        move = "to_item" if item["category"] == "food" else "to_food"  # GG-4
        disabled: tuple[str, ...] = ()
        if item["bought"]:  # GO-6: bought items aren't reordered
            actions = [move, "remove"]
        else:  # GO-2, GO-4
            actions = ["grip", "up", "down", move, "remove"]
            disabled = tuple(name for name, off in (("up", first), ("down", last)) if off)
        return Row(item["id"], item["text"], accent=colour, tick=item["bought"], dim=item["bought"],
                   actions=actions, disabled=disabled,
                   tooltip=f"{item['text']}\nAdded by {item['added_by']}, {when(item['added_at'])}")

    def add_new(self) -> None:
        text = self.entry.text().strip()
        if not text:
            self.entry.setFocus()
            return
        try:
            self.hub.add_grocery(text, self.group)
        except HubError as err:
            if err.status != 409:
                dialogs.error(self, err.detail)
                return
            if not dialogs.confirm(self, "Already on the list", err.detail, "Add anyway", danger=False):
                return  # GR-2: the person can cancel
            if self.act(lambda: self.hub.add_grocery(text, self.group, allow_duplicate=True)) is None:
                return
        except HubUnreachable:
            self.window_.set_offline(True)
            return
        self.entry.clear()
        self.window_.changed()

    def on_action(self, row_id: str, name: str) -> None:
        item = self.items.get(row_id)
        if item is None:
            return
        if name == "tick":  # GR-3, GG-5
            self.act(lambda: self.hub.set_grocery_bought(row_id, not item["bought"]))
        elif name in ("up", "down") and not item["bought"]:  # GO-2, GO-4
            self.act(lambda: self.hub.move_grocery(row_id, -1 if name == "up" else 1))
        elif name in ("to_item", "to_food"):  # GG-4: to the bottom of the other group
            self.act(lambda: self.hub.place_grocery(row_id, "item" if name == "to_item" else "food"))
        elif name == "remove":  # GR-4: straight away, with Undo
            self._removed(self.act(lambda: self.hub.remove_groceries([row_id])), f"{item['text']} removed.")
            return
        else:
            return
        self.window_.changed()
        self.list.select_id(row_id)

    def dropped(self, item_id: str, group: str, index: int) -> None:
        """GO-3: dropped within its group, or in the other one."""
        self.act(lambda: self.hub.place_grocery(item_id, group, index))
        self.window_.changed()
        self.list.select_id(item_id)

    def clear_bought(self) -> None:
        self._removed(self.act(lambda: self.hub.remove_groceries(None)), "Bought items cleared.")

    def _removed(self, items: list[dict[str, Any]] | None, message: str) -> None:
        self.window_.changed()
        if not items:
            return

        def undo() -> None:
            self.act(lambda: self.hub.restore_groceries(items))
            self.window_.toast.hide()
            self.window_.changed()

        self.window_.toast.show_message(message, kind="info", button="Undo", on_click=undo, seconds=GROCERY_UNDO_S)


ALL_VIEWS: list[type[View]] = [TodayView, CalendarView, EventsView, TasksView, RemindersView, GroceriesView,
                               HistoryView, TrashView, SettingsView]  # TP-1: Today first
