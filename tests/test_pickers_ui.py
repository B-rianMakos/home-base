"""WO-FS-008 in the app: the event form's wheels and All day switch (TM, AD-1), the date picker (DP), week block
titles (FX-3), History wording (AD-5), the Reconnect notice (PS-7), Hub status (PS-9) and the last view (PS-5)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SECOND, FakeClock
from PySide6.QtCore import QDate, QRect, Qt
from PySide6.QtGui import QKeyEvent
from test_today import open_app

from family_scheduler.app import style, timepick
from family_scheduler.app.calview import event_block_text, whole, wrap
from family_scheduler.app.pickers import DarkCalendar, day_colour
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import Organizer


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


MEMBERS = [{"email": ADMIN, "is_admin": True}, {"email": ALEX, "is_admin": False}]


def timed(start: str = "16:12", end: str = "17:12", **more: Any) -> dict[str, Any]:
    return {"id": "e1", "title": "Dentist", "date": "2026-11-03", "start_time": start, "end_time": end,
            "all_day": False, "location": "", "repeat": "none", "invitees": [], "organizer_email": ADMIN,
            "organizer_connected": True, "sync_op": None} | more


def dialog(existing: dict[str, Any] | None = None) -> Any:
    from family_scheduler.app.dialogs import EventDialog

    sender = {"email": ADMIN, "preferred": ADMIN, "fallback": False}
    return EventDialog(MEMBERS, lambda body: body, existing=existing, sender=None if existing else sender)


# --- FX-3: week block titles --------------------------------------------------------------------------------

@pytest.mark.parametrize("title", ["Morning walk", "Sunday dinner", "Pick up dry cleaning before work",
                                   "Supercalifragilisticexpialidocious"])
def test_a_cut_title_always_ends_in_an_ellipsis(qt_app: Any, title: str) -> None:
    f = style.font(style.BASE_PT, bold=True)
    for width in range(30, 260, 7):
        for lines in (1, 2):
            shown = wrap(title, f, width, lines)
            assert len(shown) <= lines
            assert whole(title, shown) or shown[-1].endswith("…"), (width, lines, shown)


def test_morning_walk_is_never_just_morning(qt_app: Any) -> None:
    f = style.font(style.BASE_PT, bold=True)
    shown = wrap("Morning walk", f, 120, 1)  # the WO-FS-006 review: one line of room
    assert shown != ["Morning"] and shown[0].endswith("…")


def test_the_place_shows_only_when_the_whole_title_fits(qt_app: Any) -> None:
    item = {"title": "Morning walk", "location": "High Park", "start_time": "07:00", "end_time": "07:30"}
    wide = event_block_text(QRect(0, 0, 400, style.STACK_BLOCK), item)
    assert wide.lines == ["Morning walk"] and wide.place == "High Park"
    narrow = event_block_text(QRect(0, 0, 90, style.STACK_BLOCK), item)
    assert not whole("Morning walk", narrow.lines) and narrow.lines[-1].endswith("…") and narrow.place == ""
    all_day = event_block_text(QRect(0, 0, 400, style.STACK_BLOCK), item | {"start_time": "", "end_time": ""})
    assert all_day.time == "All day"  # AD-8


# --- the event form: wheels (TM) and All day (AD-1) ---------------------------------------------------------

def test_a_new_event_starts_on_a_five_minute_step_and_lasts_an_hour(qt_app: Any) -> None:
    dlg = dialog()
    start, end = dlg.start.value(), dlg.end.value()
    assert timepick.to_minutes(start) % 5 == 0  # TM-4
    assert timepick.to_minutes(end) - timepick.to_minutes(start) == 60
    assert dlg.save_button.isEnabled() and dlg.times_problem.isHidden()


def test_an_odd_minute_is_kept_and_the_length_follows_the_start(qt_app: Any) -> None:
    dlg = dialog(timed("16:12", "17:00"))
    assert (dlg.start.value(), dlg.end.value()) == ("16:12", "17:00")  # TM-6: kept exactly
    assert 12 in dlg.start.minute.values and 12 not in dlg.end.minute.values
    assert dlg.summary_text == "4:12 pm to 5:00 pm (48 min)"  # TM-3
    dlg.start.minute.move_by(1)  # 12 → 15
    assert (dlg.start.value(), dlg.end.value()) == ("16:15", "17:03")  # TM-4: the end moves too
    assert dlg.summary_text == "4:15 pm to 5:03 pm (48 min)"
    assert dlg.body_data()["start_time"] == "16:15" and dlg.body_data()["all_day"] is False


def test_an_end_before_the_start_disables_save(qt_app: Any) -> None:
    dlg = dialog(timed("16:00", "17:00"))
    dlg.end.am.click()  # 5:00 am
    assert dlg.end.value() == "05:00"
    assert dlg.times_problem.text() == "The end must be after the start, on the same day."  # TM-5
    assert not dlg.save_button.isEnabled()
    dlg.end.pm.click()
    assert dlg.save_button.isEnabled() and dlg.times_problem.isHidden()


def test_the_wheels_scroll_with_the_arrow_keys_and_wrap(qt_app: Any) -> None:
    dlg = dialog(timed("12:55", "13:55"))
    wheel = dlg.start.minute
    wheel.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    assert wheel.value == 0  # 55 → 00 (TM-2)
    assert wheel.shown_values() == [55, 0, 5]  # the chosen value in the middle
    hour = dlg.start.hour
    hour.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier))
    assert hour.value == 11


def test_all_day_hides_the_wheels_and_keeps_the_times(qt_app: Any) -> None:
    dlg = dialog(timed("16:00", "17:00"))
    dlg.all_day.setChecked(True)  # AD-1
    assert dlg.times.isHidden()
    body = dlg.body_data()
    assert (body["all_day"], body["start_time"], body["end_time"]) == (True, "", "")
    dlg.all_day.setChecked(False)
    assert not dlg.times.isHidden() and (dlg.start.value(), dlg.end.value()) == ("16:00", "17:00")


def test_an_all_day_event_opens_with_all_day_on(qt_app: Any) -> None:
    dlg = dialog(timed("", "", all_day=True))
    assert dlg.all_day.isChecked() and dlg.times.isHidden() and dlg.save_button.isEnabled()


# --- the date picker (DP-1 to DP-4) -------------------------------------------------------------------------

def test_the_date_picker_starts_on_monday(qt_app: Any) -> None:
    from family_scheduler.app.pickers import date_field

    field = date_field("2026-11-03")
    assert isinstance(field.calendarWidget(), DarkCalendar)
    assert field.calendarWidget().firstDayOfWeek() == Qt.DayOfWeek.Monday  # DP-4
    assert field.calendarWidget().weekdayTextFormat(Qt.DayOfWeek.Sunday).foreground().color().name().upper() == \
        style.WEEKEND


def test_weekends_are_light_blue_not_red(qt_app: Any) -> None:
    today, selected = QDate(2026, 10, 30), QDate(2026, 10, 14)
    assert day_colour(QDate(2026, 10, 31), 10, today, selected) == (None, style.WEEKEND, False)  # Saturday
    assert day_colour(QDate(2026, 11, 1), 10, today, selected) == (None, style.PAST, False)  # another month
    assert day_colour(QDate(2026, 10, 30), 10, today, selected) == (None, style.TEXT, True)  # today, circled
    assert day_colour(selected, 10, today, selected) == (style.SELECT, style.SELECT_TEXT, False)
    assert style.WEEKEND not in (style.OVERDUE, style.HIGH)  # DP-1, DP-2
    assert style.contrast_ratio(style.WEEKEND, style.RAISED) >= 4.5


# --- History (AD-5) -------------------------------------------------------------------------------------------

def test_history_says_the_time_became_all_day() -> None:
    from family_scheduler.app.views import describe_change

    entry = {"action": "edited", "before": {"start_time": "16:00", "end_time": "17:00", "all_day": False},
             "after": {"start_time": "", "end_time": "", "all_day": True, "invites_from": ADMIN}}
    assert describe_change(entry) == f"Time 4:00 pm–5:00 pm → All day  ·  Update sent from {ADMIN}"


# --- the app between uses (PS-5), Reconnect (PS-7), Hub status (PS-9) -----------------------------------------

def test_reconnect_on_today_opens_family_and_settings(qt_app: Any, ctx: HubContext, org: Organizer,
                                                      clock: FakeClock, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    today = window.current()
    assert today.reconnect.isHidden()
    org.set_account_status(SECOND, "needs_reconnect")
    window.safe_reload(today)
    assert not today.reconnect.isHidden() and SECOND in today.reconnect_text.text()
    today.go_reconnect()
    settings = window.current()
    assert settings.title == "Family & Settings" and settings.account_list.selected_id() == SECOND
    assert settings.hub_status.text().startswith("Hub status: ")  # PS-9


def test_the_app_opens_on_the_last_view(qt_app: Any, ctx: HubContext, clock: FakeClock, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    assert window.current().title == "Today"  # the first time (TP-2)
    window.show_view("Tasks")
    window.close()
    again = open_app(ctx, tmp_path, monkeypatch, clock)
    assert again.current().title == "Tasks"  # PS-5
