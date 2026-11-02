"""The Setup guide page (WO-FS-009 section 2): opened from Family & Settings, not in the sidebar. Its words are in
guide.py (SG-8); this file only lays them out."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from family_scheduler.app import guide
from family_scheduler.app.style import HALF, S1, S2, S3
from family_scheduler.app.views import SettingsView, View, _muted
from family_scheduler.app.widgets import button, icon_button

if TYPE_CHECKING:
    from family_scheduler.app.main_window import MainWindow

SETTINGS = "Family & Settings"


class SetupGuideView(View):
    title = "Setup guide"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        # SG-3. "&&" shows a plain "&" on a button (a single one would underline the next letter).
        back = button("‹  Back to Family && Settings", self.back, tip="Back to Family & Settings (Esc)")
        back.setObjectName("LinkButton")
        top = QHBoxLayout()
        top.addWidget(back)
        top.addStretch()
        self.outer.insertLayout(0, top)
        self.subtitle.setText("Step by step, for the digest email, Google Calendar and another PC")
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.back,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        page = QWidget()
        page.setObjectName("PlainPage")
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, HALF, 0)
        column.setSpacing(S2)
        self.statuses: dict[str, QLabel] = {}
        self.texts: list[tuple[QLabel, str]] = []  # each label and its text with {admin} and {second} (TX-1)
        for section in guide.SECTIONS:
            column.addWidget(self._card(section))
        column.addStretch()
        scroller = QScrollArea()
        scroller.setObjectName("PlainScroll")
        scroller.setWidget(page)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.outer.addWidget(scroller, 1)

    def _card(self, section: guide.Section) -> QFrame:
        """SG-4: one card per section: title and live status, the steps numbered, then problems and a Go to button."""
        frame = QFrame()
        frame.setObjectName("Card")
        box = QVBoxLayout(frame)
        box.setContentsMargins(S3, S2, S3, S2)
        box.setSpacing(S1)
        head = QHBoxLayout()
        heading = QLabel(section.title)
        heading.setObjectName("Section")
        head.addWidget(heading)
        status = QLabel()
        status.setObjectName("Status")  # SG-5
        self.statuses[section.key] = status
        head.addWidget(status)
        head.addStretch()
        box.addLayout(head)
        box.addWidget(self._text(section.intro, muted=True))
        for sub, steps in section.steps:
            label = QLabel(sub)
            label.setObjectName("FieldLabel")
            box.addSpacing(HALF)
            box.addWidget(label)
            for n, step in enumerate(steps, start=1):
                row = QHBoxLayout()
                row.setSpacing(S1)
                number = QLabel(f"{n}.")
                number.setObjectName("StepNumber")
                number.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
                row.addWidget(number)
                row.addWidget(self._text(step), 1, Qt.AlignmentFlag.AlignTop)
                for url in guide.links(step):  # SG-6: a copy button beside each web address
                    row.addWidget(icon_button("copy", f"Copy {url}", partial(self._copy, url)),
                                  0, Qt.AlignmentFlag.AlignTop)
                box.addLayout(row)
        if section.table:
            label = QLabel("If something goes wrong")
            label.setObjectName("FieldLabel")
            box.addSpacing(HALF)
            box.addWidget(label)
            grid = QGridLayout()
            grid.setHorizontalSpacing(S2)
            grid.setVerticalSpacing(HALF)
            for r, (problem, fix) in enumerate(section.table):
                grid.addWidget(self._text(f"**{problem}**"), r, 0, Qt.AlignmentFlag.AlignTop)
                fix_row = QHBoxLayout()
                fix_row.addWidget(self._text(fix), 1, Qt.AlignmentFlag.AlignTop)
                for url in guide.links(fix):
                    fix_row.addWidget(icon_button("copy", f"Copy {url}", partial(self._copy, url)),
                                      0, Qt.AlignmentFlag.AlignTop)
                grid.addLayout(fix_row, r, 1)
            grid.setColumnStretch(0, 2)
            grid.setColumnStretch(1, 3)
            box.addLayout(grid)
        if section.after:
            box.addWidget(self._text(section.after, muted=True))
        go = QHBoxLayout()
        go.addStretch()
        go.addWidget(button(section.go_to, partial(self.go_to, section.key)))  # SG-7
        box.addLayout(go)
        return frame

    def _text(self, text: str, muted: bool = False) -> QLabel:
        label = _muted("") if muted else QLabel()
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(guide.to_html(guide.fill(text, {})))
        self.texts.append((label, text))
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)  # SG-6: opens in the browser
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        return label

    def _copy(self, url: str) -> None:
        QApplication.clipboard().setText(url)
        self.window_.toast_message("Link copied.")

    def reload(self) -> None:
        settings: dict[str, Any] = self.hub.settings()
        for text_label, text in self.texts:
            text_label.setText(guide.to_html(guide.fill(text, settings)))
        for key, label in self.statuses.items():
            text, ready = guide.status(key, settings, self.window_.client.address)
            label.setText(text)
            label.setProperty("ready", {True: "yes", False: "no", None: "info"}[ready])
            label.style().unpolish(label)
            label.style().polish(label)

    def back(self) -> None:
        self.window_.show_view(SETTINGS)

    def go_to(self, key: str) -> None:
        """SG-7: back to Family & Settings, with the matching card in view."""
        self.window_.show_view(SETTINGS)
        settings = self.window_.views[SETTINGS]
        if isinstance(settings, SettingsView):
            settings.show_card(key)
