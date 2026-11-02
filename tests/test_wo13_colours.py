"""WO-FS-013 sections 6 and 7: priority colours in the task pop-up and on tags (TP-a to TP-f)."""

from __future__ import annotations

import os
from typing import Any

import pytest
from colour_distance import de2000
from conftest import ALEX, task

from family_scheduler.app import style
from family_scheduler.digest import build_digest
from family_scheduler.organizer import Organizer
from family_scheduler.palette import PRIORITY_COLOURS

MEANING = {"overdue red": style.OVERDUE, "today's-events green": style.TODAY_EVENT, "selection": style.SELECT,
           "reminder lavender": style.REM, "grocery teal": style.GROCERY, "amber accent": style.ACCENT}


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_one_set_of_priority_colours() -> None:  # TP-a, TP-d
    assert PRIORITY_COLOURS == {"high": "#5AA9FF", "medium": "#E6CF4A", "low": "#8FB89A"}
    assert tuple(PRIORITY_COLOURS.values()) == (style.HIGH, style.MEDIUM, style.LOW)
    assert all(colour in style.TINT for colour in PRIORITY_COLOURS.values())


@pytest.mark.parametrize("priority", ["medium", "low"])
def test_new_colours_stay_clear_of_the_meaning_colours(priority: str) -> None:  # TP-c
    colour = PRIORITY_COLOURS[priority]
    assert min(de2000(colour, other) for other in MEANING.values()) >= 12
    assert de2000(colour, style.HIGH) >= 12


def test_tags_everywhere_use_the_priority_colours() -> None:  # TP-d
    from family_scheduler.app.views import task_priority_tags

    base = {"due_date": "2026-12-20", "completed_at": None, "due_soon": False}
    for key, colour in PRIORITY_COLOURS.items():
        tags, _ = task_priority_tags(base | {"priority": key, "effective_priority": key})
        assert tags == [(key.title(), colour)]
    tags, _ = task_priority_tags(base | {"priority": "low", "effective_priority": "high", "due_soon": True})
    assert tags == [("High", style.HIGH), ("Due soon", style.HIGH)]  # Due soon stays blue (DS-4)


def test_the_priority_switch(qt_app: Any) -> None:  # TP-a, TP-b, TP-e, TP-f
    from PySide6.QtWidgets import QPushButton

    from family_scheduler.app import dialogs

    dlg = dialogs.TaskDialog(lambda body: body)
    parts = dlg.priority.buttons()
    assert [p.property("key") for p in parts] == ["high", "medium", "low"]
    assert all(isinstance(p, QPushButton) and p.isCheckable() for p in parts)
    assert dlg.chosen_priority() == "medium" and dlg.card.property("accent") == "medium"
    parts[2].click()
    assert dlg.chosen_priority() == "low" and dlg.card.property("accent") == "low"  # the accent follows
    assert [p.isChecked() for p in parts] == [False, False, True]  # one at a time
    assert dlg.body_data()["priority"] == "low"
    assert dlg.due_soon_note.objectName() == "DueSoonNote"
    for key, colour in PRIORITY_COLOURS.items():  # the switch's rules use the same colours
        assert f'#PrioritySwitch QPushButton[key="{key}"]:checked {{ background: {colour};' in style.STYLESHEET
        assert style.DIALOG_ACCENTS[key] == colour


def test_the_digest_shows_priorities_in_their_colours(org: Organizer) -> None:
    org.add_task(ALEX, task("Passport", due="2026-10-20", priority="medium"))
    org.add_task(ALEX, task("Recycling", due="2026-10-29", priority="low"))
    subject, text, html = build_digest(org.today_view(), ALEX)
    assert "Passport (Medium), due" in text  # the plain text is unchanged
    assert f"background:{PRIORITY_COLOURS['medium']};color:#000000" in html and "(Medium)" not in html
    assert f"High-priority tasks <span style=\"background:{PRIORITY_COLOURS['high']}" in html


# --- the event pop-up (EC-1 to EC-9) -------------------------------------------------------------------------

def test_am_and_pm_colours_stay_clear() -> None:  # EC-4
    assert (style.AM, style.PM) == ("#FFDD55", "#7F8CFF")
    assert de2000(style.AM, style.PM) >= 12
    for colour in (style.AM, style.PM):
        assert min(de2000(colour, other) for other in MEANING.values()) >= 12
    assert de2000(style.PM, style.REM) >= 12 and de2000(style.PM, style.HIGH) >= 12


def test_am_and_pm_in_the_wheels_and_summary(qt_app: Any) -> None:  # EC-3, EC-9
    from family_scheduler.app import dialogs

    dlg = dialogs.EventDialog([{"email": ALEX}], lambda body: body, start="08:30", end="13:15", day="2026-11-03")
    assert (dlg.start.am.property("side"), dlg.start.pm.property("side")) == ("am", "pm")
    assert dlg.start.am.parentWidget().objectName() == "AmPm"
    assert dlg.summary_text == "8:30 am to 1:15 pm (4 h 45 min)"
    shown = dlg.times_summary.text()
    assert f'<span style="color:{style.AM}">am</span>' in shown and f'<span style="color:{style.PM}">pm</span>' in shown
    for side, colour in (("am", style.AM), ("pm", style.PM)):
        assert f'#AmPm QPushButton[side="{side}"]:checked {{ background: {colour}; color: {style.BASE}; }}' \
            in style.STYLESHEET
    assert style.colour_am_pm("Sam & Alex at 4 pm") == f'Sam &amp; Alex at 4 <span style="color:{style.PM}">pm</span>'
    assert style.colour_am_pm("Campbell") == "Campbell"  # only whole words


def test_the_event_pop_up_reads_in_blocks(qt_app: Any) -> None:  # EC-1, EC-2, EC-5, EC-6, EC-7
    from PySide6.QtWidgets import QLabel

    from family_scheduler.app import dialogs

    members = [{"email": e} for e in (ALEX, "sam@example.com", "jordan@example.com")]
    existing_sender = {"email": "admin@example.com"}
    dlg = dialogs.EventDialog(members, lambda body: body, sender=existing_sender)
    assert dlg.card.property("accent") == "event" and dlg.title_icon.isVisibleTo(dlg)
    assert style.DIALOG_ACCENTS["event"] == style.TODAY_EVENT
    assert dlg.all_day.objectName() == "Switch"
    sections = [w.text() for w in dlg.findChildren(QLabel) if w.objectName() == "FormSection"]
    assert sections == ["When", "Where", "Who", "Car"]
    chips = dlg.invitees
    assert [c.objectName() for c in chips] == ["Chip"] * 3 and all(c.isChecked() for c in chips)
    assert not chips[0].icon().isNull()  # the tick
    chips[2].click()
    assert not chips[2].isChecked() and chips[2].icon().isNull()
    assert dlg.body_data()["invitees"] == [ALEX, "sam@example.com"]
    assert dlg.sender_note.text() == "Google Calendar sends the invites from <b>admin@example.com</b>."
    assert dlg.save_button.property("primary") is True


def test_calendar_blocks_break_long_words_after_a_hyphen(qt_app: Any) -> None:  # SB-9, FX-3, found in WO-FS-013
    from family_scheduler.app.calview import whole, wrap

    f = style.font(style.BASE_PT, bold=True)
    lines = wrap("Back-to-school night", f, 400, 2)
    assert lines == ["Back-to-school night"] and whole("Back-to-school night", lines)
    from PySide6.QtGui import QFontMetrics

    width = QFontMetrics(f).horizontalAdvance("Back-to-school") - 1  # the long word no longer fits on one line
    narrow = wrap("Back-to-school night", f, width, 2)
    assert narrow[0].endswith("-")  # broken after a hyphen, not cut to "Back-to…"
    assert whole("Back-to-school night", narrow) or narrow[1].endswith("…")
    assert len("".join(narrow).replace("…", "")) >= 10
    assert whole("Back-to- school", ["Back-to-", "school"]) is False  # a hyphen run-on isn't a space
    assert whole("Back-to-school", ["Back-to-", "school"]) is True
