"""Main window: sidebar, live refresh, offline banner, toasts and the Clear all countdown."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from family_scheduler.app import dialogs, icons
from family_scheduler.app.badges import badge_counts
from family_scheduler.app.guide_view import SetupGuideView
from family_scheduler.app.style import (
    BANNER_TEXT,
    GROCERY,
    ICON,
    MINUTE_MS,
    OVERDUE,
    S1,
    S2,
    S3,
    SYNC_ON_OPEN_MS,
    TODAY_EVENT,
    WINDOW_MIN,
    WINDOW_START,
    today_iso,
)
from family_scheduler.app.views import ALL_VIEWS, View
from family_scheduler.app.widgets import Sidebar, Toast
from family_scheduler.client.hub_client import UNREACHABLE, HubClient, HubError, HubUnreachable
from family_scheduler.client.local_settings import LocalSettings

POLL_MS = 3000
UNDO_SECONDS = 10


class MainWindow(QMainWindow):
    def __init__(self, client: HubClient, settings: LocalSettings, members: list[dict[str, Any]],
                 on_switch: Callable[[], str | None]) -> None:
        super().__init__()
        self.client = client
        self.settings = settings
        self.members = members
        self._on_switch = on_switch
        self.setWindowTitle("Home Base")  # WO-FS-011 HB-1
        self.setMinimumSize(*WINDOW_MIN)  # PC-6
        self.resize(*WINDOW_START)

        root = QWidget()
        root.setObjectName("Base")
        body = QHBoxLayout(root)
        body.setContentsMargins(S1, S1, S1, S1)
        body.setSpacing(S1)
        titles = [cls.title for cls in ALL_VIEWS]
        self.sidebar = Sidebar(titles, self.switch_member)
        body.addWidget(self.sidebar)

        self.panel = QFrame()
        self.panel.setObjectName("Panel")
        right = QVBoxLayout(self.panel)
        right.setContentsMargins(S3, S2, S3, S1)
        right.setSpacing(S1)
        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(0, 0, 0, 0)
        banner_icon = QLabel()
        banner_icon.setPixmap(icons.pixmap("wifi-off", BANNER_TEXT, ICON))
        banner_row.addWidget(banner_icon)
        banner_row.addWidget(QLabel(UNREACHABLE), 1)
        self.banner.hide()
        right.addWidget(self.banner)  # CF-7
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        body.addWidget(self.panel, 1)
        self.setCentralWidget(root)
        self.toast = Toast(self.panel)

        self.views: dict[str, View] = {}
        for cls in ALL_VIEWS:
            view = cls(self)
            self.views[cls.title] = view
            self.stack.addWidget(view)
        self.sidebar.nav.currentRowChanged.connect(self._on_nav)
        # WO-FS-009 SG-2: the Setup guide is a page of its own, not in the sidebar, which keeps Family & Settings
        # selected while it's open. Clicking Family & Settings there goes back.
        self.guide = SetupGuideView(self)
        self.stack.addWidget(self.guide)
        self.sidebar.nav.itemClicked.connect(lambda _item: self._leave_guide())

        # Clear all countdown (CA-3), shown in a toast (CF-3).
        self.clear_id = ""
        self.undo_with: Callable[[str], Any] | None = None  # None: a list's Clear all
        self.undone_message = "Clear all undone."
        self.clear_message = ""
        self.undo_left = 0
        self.undo_timer = QTimer(self)
        self.undo_timer.timeout.connect(self._undo_tick)

        # CF-8
        QShortcut(QKeySequence("Ctrl+N"), self, lambda: self.current().add_new())
        QShortcut(QKeySequence("Ctrl+R"), self, lambda: self.current().refresh())

        self.offline = False
        self.seq = -1
        self.poll = QTimer(self)
        self.poll.timeout.connect(self._poll)
        self.poll.start(POLL_MS)
        self._show_member()
        self._restore_place()
        QTimer.singleShot(SYNC_ON_OPEN_MS, self.sync_on_open)  # WO-FS-010 GY-2: once when the app opens
        self.day = today_iso()  # WO-FS-012 DS-3: at midnight the open page moves over (the due-soon bump with it)
        self.day_check = QTimer(self)
        self.day_check.timeout.connect(self.check_day)
        self.day_check.start(MINUTE_MS)

    def check_day(self) -> None:
        today = today_iso()
        if today != self.day:
            self.day = today
            self.safe_reload(self.current())

    def sync_on_open(self) -> None:
        """GY-2: one Sync with Google when the app opens; it only speaks up when something changed."""
        try:
            result = self.client.sync()
        except (HubUnreachable, HubError):
            return  # Refresh tries again; nothing to report at start
        if result.get("added") or result.get("changed") or result.get("removed") or result.get("replaced"):
            self.toast_message(result["message"])
        self.changed()

    @property
    def member(self) -> str:
        return self.client.member

    def _show_member(self) -> None:
        self.sidebar.set_member(self.member)

    # --- remembered size, position and view (NV-5) ------------------------------------------------

    def _restore_place(self) -> None:
        geometry = self.settings.get("window")
        if geometry:
            with contextlib.suppress(ValueError):
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        # WO-FS-008 PS-5: the app opens on the last view used, or on Today the first time (WO-FS-007 TP-2).
        last = self.settings.get("view")
        titles = list(self.views)
        self.sidebar.nav.setCurrentRow(titles.index(last) if last in titles else 0)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.set("window", bytes(self.saveGeometry().toBase64().data()).decode("ascii"))
        self.settings.set("view", "Family & Settings" if self.current() is self.guide else self.current().title)
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.toast.reposition()

    # --- navigation and refresh -------------------------------------------------------------------

    def _on_nav(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        self.safe_reload(self.current())

    def current(self) -> View:
        view = self.stack.currentWidget()
        assert isinstance(view, View)
        return view

    def show_view(self, title: str) -> None:
        row = list(self.views).index(title)
        if self.sidebar.nav.currentRow() == row:  # already selected, e.g. from the Setup guide: no change signal
            self._on_nav(row)
        else:
            self.sidebar.nav.setCurrentRow(row)

    def show_guide(self) -> None:
        """SG-2: open the Setup guide in the main area."""
        self.stack.setCurrentWidget(self.guide)
        self.safe_reload(self.guide)

    def _leave_guide(self) -> None:
        if self.current() is self.guide:
            self._on_nav(self.sidebar.nav.currentRow())

    def safe_reload(self, view: View) -> None:
        try:
            view.reload()
            self.update_badges()
            self.set_offline(False)
        except HubUnreachable:
            self.set_offline(True)
        except HubError as err:
            if err.status == 403:
                self.switch_member()
            else:
                dialogs.error(self, err.detail)

    def update_badges(self) -> None:
        """NV-2: overdue tasks (red) and today's events (green)."""
        today = self.client.today()
        counts = badge_counts(today)
        self.sidebar.set_badge("Tasks", counts["Tasks"], OVERDUE)
        self.sidebar.set_badge("Events", counts["Events"], TODAY_EVENT)
        self.sidebar.set_badge("Groceries", today.get("groceries_to_buy", 0), GROCERY)  # WO-FS-010 GR-7

    def changed(self) -> None:
        """After this member's own change: reload at once (PC-7)."""
        with contextlib.suppress(HubUnreachable, HubError):
            self.members = self.client.members()
        self.safe_reload(self.current())

    def _poll(self) -> None:
        """Changes made on other PCs show up within a few seconds (PC-7)."""
        try:
            seq = self.client.changes()
        except HubUnreachable:
            self.set_offline(True)
            return
        except HubError:
            return
        was_offline = self.offline
        self.set_offline(False)
        if seq != self.seq or was_offline:
            self.seq = seq
            self.changed()

    def set_offline(self, offline: bool) -> None:
        """PC-8: say so plainly and block changes until the hub is back."""
        if offline == self.offline:
            return
        self.offline = offline
        self.banner.setVisible(offline)
        self.stack.setEnabled(not offline)
        self.poll.setInterval(5000 if offline else POLL_MS)

    # --- actions ----------------------------------------------------------------------------------

    def act(self, fn: Callable[[], Any], done: str | None = None) -> Any:
        try:
            result = fn()
        except HubUnreachable:
            self.set_offline(True)
            return None
        except HubError as err:
            self.toast.show_message(err.detail, kind="error", seconds=8)  # CF-3
            return None
        if done:
            self.toast_message(done)
        return True if result is None else result

    def toast_message(self, text: str, kind: str = "ok") -> None:
        if not self.undo_timer.isActive():
            self.toast.show_message(text, kind=kind)

    def clear_all(self, list_name: str) -> None:
        preview = self.act(lambda: self.client.clear_preview(list_name))
        if not preview:
            return
        n = preview["count"]
        if n == 0:
            dialogs.info(self, "Clear all", f"There are no {list_name} to clear.")
            return
        if list_name == "events":
            detail = (f"After 10 seconds, all {n} events are cancelled in Google Calendar and "
                      f"{dialogs.invitee_list(preview['invitees'])} get cancellation emails. "
                      "You can't undo that.")
            yes = f"Clear {n} events"
        else:
            detail = "They move to Trash. You can restore them for 30 days."
            yes = f"Clear {n} {list_name}"
        if not dialogs.confirm(self, "Clear all", f"Clear all {n} {list_name}?", yes, detail=detail):
            return
        pending = self.act(lambda: self.client.clear_all(list_name))
        if pending:
            verb = "Cancelling in Google in {seconds} s" if list_name == "events" else "{seconds} s to undo"
            self.start_undo(pending["id"], f"{n} {list_name} cleared. {verb}.")
            self.changed()

    def start_undo(self, clear_id: str, message: str, seconds: int = UNDO_SECONDS,
                   undo_with: Callable[[str], Any] | None = None, undone: str = "Clear all undone.") -> None:
        """CA-3: ten seconds to undo, counting down in the toast. History passes its own undo."""
        self.clear_id, self.clear_message, self.undo_left = clear_id, message, seconds
        self.undo_with, self.undone_message = undo_with, undone
        self._undo_render()
        self.undo_timer.start(1000)

    def _undo_render(self) -> None:
        self.toast.show_message(self.clear_message.format(seconds=self.undo_left), kind="info",
                                button=f"Undo · {self.undo_left} s", on_click=self.undo, seconds=0)

    def _undo_tick(self) -> None:
        self.undo_left -= 1
        if self.undo_left <= 0:
            self.undo_timer.stop()
            self.toast.dismiss()
        else:
            self._undo_render()

    def undo(self) -> None:
        self.undo_timer.stop()
        undo = self.undo_with or self.client.undo_clear
        if self.act(lambda: undo(self.clear_id)):
            self.toast_message(self.undone_message)
            self.changed()

    def switch_member(self) -> None:
        chosen = self._on_switch()
        if chosen:
            self.client.member = chosen
            self._show_member()
            self.changed()
