"""Runs a throwaway test hub with sample data, opens the real PC app against it and saves a PNG of every screen.

    python tools/screenshots.py OUTPUT_FOLDER            all screens, plus cut-off-check.txt at 1024 x 700
    python tools/screenshots.py OUTPUT_FOLDER --part-a   WO-FS-004 Part A samples: Calendar, Events, Add event

Used to check the screens on a real Windows desktop (WO-FS-002 correction 3). Nothing touches Google or Gmail.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
PART_A = "--part-a" in sys.argv  # WO-FS-004 Part A: Calendar, Events and Add event only
OUT = Path(ARGS[0] if ARGS else "screenshots")
OUT.mkdir(parents=True, exist_ok=True)
DATA = Path(tempfile.mkdtemp(prefix="fs-shots-"))
os.environ["FS_DATA_DIR"] = str(DATA)
os.environ["APPDATA"] = str(DATA)  # keep this run's "last member" away from the real one

import uvicorn  # noqa: E402
from PySide6.QtCore import QDate, QPoint, QTimer  # noqa: E402
from PySide6.QtGui import QFontMetrics, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QWidget  # noqa: E402

from family_scheduler.app import dialogs  # noqa: E402
from family_scheduler.app.calview import MonthGrid, WeekStack, event_block_text, task_block_lines, whole  # noqa: E402
from family_scheduler.app.main_window import MainWindow  # noqa: E402
from family_scheduler.app.style import S1, S2, TAG_MAX_WIDTH, apply_theme, font  # noqa: E402
from family_scheduler.app.widgets import ElidedLabel, Header, Row, RowList  # noqa: E402
from family_scheduler.client.hub_client import HubClient, HubError  # noqa: E402
from family_scheduler.client.local_settings import LocalSettings  # noqa: E402
from family_scheduler.hub.api import create_app  # noqa: E402
from family_scheduler.hub.context import build_context  # noqa: E402
from family_scheduler.organizer import EventInput, TaskInput  # noqa: E402
from family_scheduler.secrets_store import StaticKeyProvider  # noqa: E402

ADMIN, ALEX, SAM, JORDAN = "admin@example.com", "alex@example.com", "sam@example.com", "jordan@example.com"
SECOND = "second@example.com"  # the second calendar account (WO-FS-005, WO-FS-010 AC-5)
DESKTOP_CLIENT = ('{"installed": {"client_id": "000000000000-sample.apps.googleusercontent.com", '
                  '"client_secret": "sample", "redirect_uris": ["http://localhost"]}}')  # a stand-in, for the screens


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def set_up(ctx: object) -> None:
    """A new hub asks for its family first (WO-FS-010 AC-4): placeholder accounts, both connected in test mode."""
    org = ctx.organizer  # type: ignore[attr-defined]
    org.set_up_family(ADMIN, SECOND)
    for account in (ADMIN, SECOND):
        org.set_account_status(account, "connected")


def seed(ctx: object) -> None:
    org = ctx.organizer  # type: ignore[attr-defined]
    gcal = ctx.fake_calendar  # type: ignore[attr-defined]
    add_event = org.add_event

    def add_any_day(who: str, inp: EventInput, **kwargs: Any) -> Any:
        # The sample dates move with today, so any of them may land on a repeat: overlaps are fine here.
        kwargs["acknowledge_conflicts"] = True
        return add_event(who, inp, **kwargs)

    org.add_event = add_any_day
    set_up(ctx)
    for m in (ALEX, SAM, JORDAN):
        org.add_member(ADMIN, m)
    today = org.today()

    def d(days: int) -> str:
        return (today + timedelta(days=days)).isoformat()

    everyone = [ADMIN, ALEX, SAM, JORDAN]
    dentist = org.add_event(ALEX, EventInput("Dentist", d(7), "16:00", "17:00", "Smile Dental, Queen St", "none",
                                             everyone))
    org.add_event(ADMIN, EventInput("Soccer pickup", d(0), "16:15", "16:45", "Riverdale Park", "none", everyone))
    org.add_event(SAM, EventInput("Swim practice", d(8), "17:30", "18:30", "Pan Am Centre", "weekly", everyone))
    org.add_event(ADMIN, EventInput("Sunday dinner", d((6 - today.weekday()) % 7 or 7), "18:00", "20:00",
                                    "14 Elm Ave", "weekly", everyone))
    org.add_event(ALEX, EventInput("Parent-teacher interview", d(7), "16:30", "17:00", "Riverdale Public School",
                                   "none", [ADMIN, ALEX]), acknowledge_conflicts=True)
    # Events across several months (WO-FS-003 deliverable 2), and a past one kept for 30 days in its own month.
    org.add_event(ADMIN, EventInput("Back-to-school night", d(-28), "18:30", "20:00", "Riverdale Public School",
                                    "none", everyone))
    org.add_event(SAM, EventInput("Winter concert", d(44), "19:00", "20:30", "Riverdale Public School", "none",
                                  everyone))
    org.add_event(ADMIN, EventInput("Ski weekend", d(74), "09:00", "17:00", "Blue Mountain", "none", everyone))
    gcal.fail_next = "insert"
    org.add_event(JORDAN, EventInput("Piano recital", d(11), "19:00", "20:30", "Royal Conservatory", "none", everyone),
                  acknowledge_conflicts=True)
    for gid, body in gcal.events.items():
        for a in body.get("attendees", []):
            if a["email"] == ALEX:
                gcal.respond(gid, ALEX, "accepted")
            if a["email"] == SAM and body["summary"] == "Dentist":
                gcal.respond(gid, SAM, "declined")
    org.refresh_rsvps(SAM)
    org.edit_event(ALEX, dentist["id"], EventInput("Dentist", d(7), "16:00", "17:00", "Smile Dental, Queen St",
                                                   "none", everyone), acknowledge_conflicts=True)
    # This week, for the Calendar (WO-FS-005, WO-FS-006): the second account, a daily repeat, an overlap, a busy day.
    monday = today - timedelta(days=today.weekday())

    def wd(n: int) -> str:
        return (monday + timedelta(days=n)).isoformat()

    org.add_event(SECOND, EventInput("Piano lesson", d(0), "16:30", "17:15", "Ms. Lee's studio", "none",
                                    [ADMIN, SECOND, SAM]), acknowledge_conflicts=True)
    org.add_event(SECOND, EventInput("Morning walk", wd(0), "07:00", "07:30", "Riverdale Park", "daily",
                                    [ADMIN, SECOND]), acknowledge_conflicts=True)
    org.add_event(ADMIN, EventInput("Book club", wd(3), "19:00", "21:00", "Library, 2nd floor", "none",
                                    [ADMIN, SECOND]), acknowledge_conflicts=True)
    org.add_event(SECOND, EventInput("Hockey practice", wd(5), "09:00", "10:30", "Pan Am Centre", "none",
                                    everyone), acknowledge_conflicts=True)
    for title, start, end in (("Grocery run", "11:00", "12:00"), ("Library pickup", "13:30", "14:00"),
                              ("Dentist check-up", "15:00", "15:45")):
        org.add_event(SECOND, EventInput(title, d(0), start, end, "Main St", "none", [ADMIN, SECOND]),
                      acknowledge_conflicts=True)
    # WO-FS-008: all-day events, today, repeating weekly this week, and one next week.
    org.add_event(ADMIN, EventInput("Pyjama day at school", d(0), "", "", "Riverdale Public School", "none",
                                    everyone, all_day=True))
    org.add_event(SECOND, EventInput("Garbage day", wd(2), "", "", "", "weekly", [ADMIN, SECOND], all_day=True))
    org.add_event(ALEX, EventInput("Field trip", d(7), "", "", "Science Centre", "none", everyone, all_day=True))
    for title, priority in (("Pack lunches", "low"), ("Sign field-trip form", "high"), ("Call Grandma", "medium"),
                            ("Water the plants", "low")):
        org.add_task(SECOND, TaskInput(title, wd(4), priority))
    org.add_task(ALEX, TaskInput("Renew passport photos", d(-3), "high"))
    org.add_task(SAM, TaskInput("Return library books", d(-1), "low"))
    org.add_task(ADMIN, TaskInput("Book winter tire swap", d(3), "high"))
    rec = org.add_task(JORDAN, TaskInput("Take out recycling", d(-6), "medium", "weekly"))
    org.complete_task(SAM, rec["id"])
    org.add_task(ALEX, TaskInput("Order school photos", d(10), "low"))
    org.add_task(SAM, TaskInput("Clean the gutters", d(14), "medium"))  # WO-FS-012: not due soon, so Medium
    on_31st = next(n for n in range(8, 80) if d(n).endswith("-31"))  # WO-FS-013: a monthly task on the 31st
    org.add_task(ALEX, TaskInput("Pay the rent", d(on_31st), "low", "monthly"))
    org.add_task(JORDAN, TaskInput("Plan the holiday party", d(20), "low"))
    bill = org.add_task(ADMIN, TaskInput("Pay hydro bill", d(-1), "medium"))
    org.complete_task(SAM, bill["id"])
    gone = org.add_task(ALEX, TaskInput("Buy new garden hose", d(20), "low"))
    org.remove_task(SAM, gone["id"])
    org.add_reminder(ADMIN, "Guest Wi-Fi password is on the fridge.")
    org.add_reminder(ALEX, "Sam is allergic to tree nuts. Tell any new babysitter.")
    org.add_reminder(JORDAN, "Garage code changed. It's in the shared notes.")
    org.add_reminder(SAM, "Bring reusable bags to the Saturday market.")
    org.add_reminder(SECOND, "Recycling goes out Wednesday night now, not Thursday morning. Blue bin for paper and "
                            "cardboard, grey bin for cans and bottles; flatten the boxes first.")  # WO-FS-007: long
    org.add_task(SECOND, TaskInput("Call the plumber about the kitchen tap", d(0), "medium"))  # due today
    # WO-FS-009: ongoing tasks (no due date), and an event in a month of each season for the month colours.
    org.add_task(ADMIN, TaskInput("Keep the first-aid kit stocked", "", "high"))
    org.add_task(ALEX, TaskInput("Tidy the garage", "", "low"))
    for month, title in ((1, "Skating party"), (4, "Spring cleanup"), (7, "Cottage weekend"),
                         (11, "Remembrance Day assembly")):
        year = today.year + (1 if (month, 15) <= (today.month, today.day) else 0)
        org.add_event(ADMIN, EventInput(title, date(year, month, 15).isoformat(), "10:00", "12:00", "", "none",
                                        everyone))
    old = org.add_reminder(ALEX, "Pick up dry cleaning before Friday.")
    org.remove_reminder(ALEX, old["id"])
    fence = org.add_task(JORDAN, TaskInput("Fix the back fence", d(12), "medium"))  # WO-FS-014: a third in Trash
    org.remove_task(JORDAN, fence["id"])
    seed_wo10(ctx, d)


def seed_wo10(ctx: Any, d: Any) -> None:
    """WO-FS-010: cars on today's events (one double-booked), events made in Google, and the grocery list."""
    org, gcal = ctx.organizer, ctx.fake_calendar
    subaru, cruze = (c["id"] for c in org.list_cars())
    listed = org.list_events()  # past ones too: the screens may be taken after some of today's events ended
    today = {e["title"]: e for e in listed["upcoming"] + listed["past"] if d(0) in (e["next_date"], e["date"])}
    for title, car in (("Soccer pickup", subaru), ("Piano lesson", cruze), ("Dentist check-up", subaru),
                       ("Grocery run", cruze)):
        org.set_event_car(ALEX, today[title]["id"], car, use_anyway=True)
    store = org.add_event(SAM, EventInput("Hardware store", d(0), "11:30", "12:15", "Main St", "none",
                                          [ADMIN, SAM]))
    org.set_event_car(SAM, store["id"], cruze, use_anyway=True)  # CR-7: double-booked with the grocery run
    org.add_event(ALEX, EventInput("Bank", d(0), "15:15", "15:30", "Main St", "none", [ADMIN, ALEX]))
    zone = {"timeZone": "America/Toronto"}
    gcal.google_add(ADMIN, {"summary": "Parent council meeting", "location": "School library",
                            "start": {"dateTime": f"{d(2)}T19:00:00", **zone},
                            "end": {"dateTime": f"{d(2)}T20:00:00", **zone},
                            "attendees": [{"email": SECOND}, {"email": "principal@example.org"}]})
    gcal.google_add("coach@example.org", {"summary": "Hockey team party", "location": "Arena hall",
                                          "start": {"dateTime": f"{d(3)}T18:00:00", **zone},
                                          "end": {"dateTime": f"{d(3)}T20:00:00", **zone},
                                          "attendees": [{"email": ADMIN}]})
    gcal.google_add(SECOND, {"summary": "Private appointment", "visibility": "private",
                             "start": {"dateTime": f"{d(1)}T10:00:00", **zone},
                             "end": {"dateTime": f"{d(1)}T11:00:00", **zone}})
    org.sync_google(ADMIN)
    for text, who, group in (("Milk (2 L)", ALEX, "food"), ("Bread", SAM, "food"), ("Bananas", JORDAN, "food"),
                             ("Coffee beans", ADMIN, "food"), ("Eggs (dozen)", ALEX, "food"),
                             ("Dish soap", SECOND, "item"), ("AA batteries", ADMIN, "item"),
                             ("Light bulbs", SAM, "item")):  # WO-FS-011: Food and Items
        org.add_grocery(who, text, category=group)
    for item in org.list_groceries()["to_buy"]:
        if item["text"] in ("Bananas", "Dish soap"):
            org.set_grocery_bought(SAM, item["id"], True)


def grab(widget: QWidget, name: str) -> None:
    QApplication.processEvents()
    widget.grab().save(str(OUT / f"{name}.png"))
    print("saved", name)


def settle() -> None:
    """Let fades (CF-4) finish so the capture shows the final look."""
    end = time.monotonic() + 0.4
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)


def find_row(rows: RowList, title: str) -> int:
    for i in range(rows.count()):
        entry = rows.row_delegate.entry(rows.model().index(i, 0))
        if isinstance(entry, Row) and entry.title.startswith(title):
            return i
    return -1


def hover(rows: RowList, title: str) -> None:
    """Show a row as the mouse would leave it: lighter, with its Edit and Remove icons (LC-4)."""
    rows.hover_row = find_row(rows, title)
    rows.hover_action = None
    rows.hover_level = 1.0
    rows.viewport().update()


def select_row(view: QWidget, title: str) -> None:
    """Select the row with this title, then put focus on a button, to show the highlight stays (SL-3)."""
    rows = view.findChild(RowList)
    if rows is None:
        return
    index = find_row(rows, title)
    item = rows.item(index) if index >= 0 else None
    if item is not None:
        rows.setCurrentItem(item)
        rows.scrollToItem(item)
    buttons = [b for b in view.findChildren(QPushButton) if b.isEnabled() and b.isVisible()]
    if buttons:
        buttons[-1].setFocus()
    settle()


def grab_over_backdrop(window: QWidget, dialog: dialogs.CardDialog, name: str,
                       prepare: Callable[[], None] | None = None) -> None:
    """MS-5: the window with its dimmed backdrop, and the dialog card where it opens, centred over it. `prepare` runs
    once the dialog is in place (e.g. to hover over something in it)."""
    dialog.open_backdrop()
    dialog.show()
    settle()
    if prepare is not None:
        prepare()
        settle()
    image = window.grab().toImage()
    painter = QPainter(image)
    offset = dialog.mapToGlobal(QPoint(0, 0)) - window.mapToGlobal(QPoint(0, 0))
    painter.drawImage(offset, dialog.grab().toImage())
    painter.end()
    image.save(str(OUT / f"{name}.png"))
    print("saved", name)
    dialog.close()
    dialog.close_backdrop()


def cut_off(window: QWidget) -> list[str]:
    """FN-5: every label, button, heading, tag and row whose text needs more room than it has."""
    problems = []
    for label in window.findChildren(QLabel):
        if not label.isVisible() or label.wordWrap() or not label.text():
            continue
        if isinstance(label, ElidedLabel):
            if QFontMetrics(label.font()).horizontalAdvance(label.full) > label.width():
                problems.append(f"shortened, full text on hover: '{label.full}'")
        elif label.sizeHint().width() > label.width() + 1:
            problems.append(f"CUT OFF: label '{label.text()[:50]}' needs {label.sizeHint().width()}, "
                            f"has {label.width()}")
    for b in window.findChildren(QPushButton):
        if b.isVisible() and b.sizeHint().width() > b.width() + 1:
            problems.append(f"CUT OFF: button '{b.text()}' needs {b.sizeHint().width()}, has {b.width()}")
    for rows in window.findChildren(RowList):
        if not rows.isVisible():
            continue
        d = rows.row_delegate
        for i in range(rows.count()):
            index = rows.model().index(i, 0)
            e = d.entry(index)
            rect = rows.visualRect(index)
            rect.setWidth(rows.viewport().width())
            if isinstance(e, Header):
                need = QFontMetrics(font(e.pt, bold=True)).horizontalAdvance(e.text) + S1
                if need > rect.width():
                    problems.append(f"CUT OFF: heading '{e.text}' needs {need}, has {rect.width()}")
            if not isinstance(e, Row):
                continue
            # The narrowest a row gets: with its Edit and Remove icons showing.
            text_rect, shown = d.layout_row(rect, e, show_actions=True)
            width = text_rect.width()
            for text, _ in shown:
                if not text.startswith("+") and QFontMetrics(d.tag_font).horizontalAdvance(text) + S2 > TAG_MAX_WIDTH:
                    kind = "shortened, full text on hover" if text.startswith("Overlaps") else "CUT OFF"
                    problems.append(f"{kind}: tag '{text}'")
            if len(shown) < len(e.tags) or any(t.startswith("+") for t, _ in shown):
                problems.append(f"tags folded into '+N', all listed on hover: '{e.title}'")
            if QFontMetrics(d.body_font).horizontalAdvance(d.fixed_part(e)) > width:
                problems.append(f"CUT OFF: '{e.subtitle}'")
            for line, f in ((e.title, d.title_font), (e.subtitle, d.body_font)):
                if line and QFontMetrics(f).horizontalAdvance(line) > width:
                    problems.append(f"shortened, full text on hover: '{line}'")
    for week in window.findChildren(WeekStack):
        if not week.isVisible():
            continue
        for b in week.canvas.layout_blocks():
            # WO-FS-008 FX-3: measure the lines the block really draws, from the same layout as the painter.
            title = b.item["title"]
            lines = event_block_text(b.rect, b.item).lines if b.kind == "event" else task_block_lines(b.rect, b.item)
            drawn = " ".join(lines)
            if whole(title, lines):
                continue
            if not drawn.endswith("…"):
                problems.append(f"CUT OFF without '…': week block '{title}' shows '{drawn}'")
            elif len(drawn.replace("…", "").replace(" ", "")) < min(10, len(title.replace(" ", ""))):  # SB-9
                problems.append(f"CUT OFF: week block '{title}' shows only '{drawn}'")
            else:
                problems.append(f"shortened with '…', full text on hover: week block '{title}' shows '{drawn}'")
    for month in window.findChildren(MonthGrid):
        if not month.isVisible():
            continue
        for cell in month._layout():  # MV-8: at least two items, or one and "+N more"
            if len(cell.bars) + (1 if cell.more else 0) < min(2, len(month.items.get(cell.day, []))):
                problems.append(f"CUT OFF: month cell {cell.day} shows fewer than two items")
    return problems


SHOTS = [("Today", "00-today", "Soccer pickup"), ("Calendar", "02-calendar-week", "Soccer pickup"),
         ("Events", "03-events", "Dentist"),
         ("Tasks", "04-tasks", "Renew passport photos"), ("Reminders", "05-reminders", "Sam is allergic"),
         ("History", "06-history", ""), ("Trash", "07-trash", "Buy new garden hose"),
         ("Family & Settings", "08-family-and-settings", "sam@example.com")]


def sample_screens(window: MainWindow, members: list[dict[str, Any]], client: HubClient) -> None:
    """WO-FS-004 Part A 2: Calendar, Events with a selected row and a hovered row, and Add event over the backdrop."""
    window.show_view("Calendar")
    grab(window, "a1-calendar")
    window.show_view("Events")
    events = window.views["Events"]
    select_row(events, "Dentist")
    rows = events.findChild(RowList)
    assert rows is not None
    hover(rows, "Swim practice")
    grab(window, "a2-events")
    rows.hover_row = -1
    dlg = dialogs.EventDialog(members, client.add_event, parent=window)
    dlg.title.setText("Haircut")
    dlg.location.setText("Main St Barbers")
    grab_over_backdrop(window, dlg, "a3-add-event")


def calendar(window: MainWindow) -> Any:
    return window.views["Calendar"]


def select_block(window: MainWindow, title: str, kind: str = "event") -> None:
    """WU-a, MV-6: select a block or bar on the Calendar (white outline)."""
    view = calendar(window)
    items = view.data["week_events"] if kind == "event" else view.data["week_tasks"]
    match = next(i for i in items if i["title"] == title)
    target = view.week if view.mode == "week" else view.month
    target.select((kind, match["id"], match["day"]))
    settle()


def settings_states(window: MainWindow, ctx: Any, client: HubClient) -> None:
    """Deliverable 4: the Calendar accounts card in each state (CB-3, CB-7, OA-3)."""
    org = ctx.organizer
    window.show_view("Family & Settings")
    settle()
    view = window.views["Family & Settings"]
    accounts = view.account_list  # type: ignore[attr-defined]
    grab(window, "08a-settings-test-mode")
    fake, ctx.fake_calendar = ctx.fake_calendar, None  # as on a real hub, for the settings card only
    org.set_account_status(ADMIN, "not_connected")
    org.set_account_status(SECOND, "not_connected")
    window.changed()
    accounts.select_id(ADMIN)
    settle()
    grab(window, "08b-settings-no-client-file")
    from family_scheduler.google_calendar import WEB_CLIENT

    grab_over_backdrop(window, dialogs.MessageDialog(window, "Home Base", WEB_CLIENT, "", "OK", danger=True,
                                                     cancel=False), "08c-settings-web-client-file-refused")
    client.set_client_file(DESKTOP_CLIENT)
    window.changed()
    accounts.select_id(SECOND)
    settle()
    grab(window, "08d-settings-not-connected")
    org.set_account_status(ADMIN, "connected")
    window.changed()
    accounts.select_id(ADMIN)
    settle()
    grab(window, "08e-settings-connected")
    org.set_account_status(SECOND, "needs_reconnect", "Google no longer accepts this account's connection.")
    window.changed()
    accounts.select_id(SECOND)
    settle()
    grab(window, "08f-settings-needs-reconnecting")
    ctx.fake_calendar = fake
    org.set_account_status(SECOND, "connected")
    window.changed()


def all_screens(window: MainWindow, members: list[dict[str, Any]], client: HubClient, ctx: Any) -> None:
    for title, name, row in SHOTS:
        window.show_view(title)
        settle()
        if title == "Calendar":
            select_block(window, row)
        elif row:
            select_row(window.views[title], row)
        if title == "Events":
            rows = window.views["Events"].findChild(RowList)
            assert rows is not None
            hover(rows, "Swim practice")
        grab(window, name)
        if title == "Today":  # the rest of the page: to-do list and reminders, the long one wrapped (TR-b)
            bar = window.views["Today"].list.verticalScrollBar()  # type: ignore[attr-defined]
            bar.setValue(bar.maximum())
            settle()
            grab(window, "00b-today-scrolled")
            bar.setValue(0)

    # WO-FS-006: the week scrolled to a busy day's end, the month view, "+N more", Replies, settings states.
    window.show_view("Calendar")
    settle()
    view = calendar(window)
    view.week.area.verticalScrollBar().setValue(view.week.area.verticalScrollBar().maximum())
    settle()
    grab(window, "02a-calendar-week-scrolled")
    view.week.area.verticalScrollBar().setValue(0)
    view.show_mode("month")
    settle()
    select_block(window, "Sign field-trip form", "task")
    grab(window, "02b-calendar-month")
    busy = max(view.month.days, key=lambda d: len(view.month.items.get(d, [])))
    grab_over_backdrop(window, view.day_dialog(busy), "02c-calendar-month-more")
    view.show_mode("week")
    settle()
    view.toggle_replies()
    settle()
    grab(window, "21-calendar-replies-panel")
    view.toggle_replies()
    settings_states(window, ctx, client)
    window.show_view("Family & Settings")  # WO-FS-008 PS-9: the Hub status line under This PC
    settle()
    for area in window.views["Family & Settings"].findChildren(QScrollArea):
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    settle()
    grab(window, "36-hub-status")

    # FN-5: nothing fixed is cut off at the smallest window size, on every view.
    window.resize(1024, 700)
    settle()
    report = [f"Window size: {window.width()} x {window.height()}"]
    checks = [(title, "week" if title == "Calendar" else "") for title, _, _ in SHOTS] + [("Calendar", "month")]
    for title, mode in checks:
        window.show_view(title)
        if mode:
            calendar(window).show_mode(mode)
        settle()
        label = f"{title} ({mode} view)" if mode else title
        report += [f"{label}: {p}" for p in cut_off(window)] or [f"{label}: nothing cut off"]
        if (title, mode) == ("Calendar", "week"):
            grab(window, "35-week-at-1024-titles-cut-with-ellipsis")  # WO-FS-008 FX-3
    calendar(window).show_mode("week")
    window.show_guide()  # WO-FS-009: the Setup guide isn't in the sidebar
    settle()
    report += [f"Setup guide: {p}" for p in cut_off(window)] or ["Setup guide: nothing cut off"]
    (OUT / "cut-off-check.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    window.resize(1180, 760)
    window.show_view("Events")
    settle()

    # Add event, then the conflict warning (EV-4), over the dimmed window.
    dlg = dialogs.EventDialog(members, client.add_event, parent=window, sender=client.calendar_sender())
    dlg.title.setText("Haircut")
    dlg.date.setDate(dlg.date.date().addDays(7))
    dlg.start.set_value("16:30")
    dlg.end.set_value("17:15")
    dlg._shown_start = "16:30"
    dlg._check_times()
    dlg.location.setText("Main St Barbers")
    grab_over_backdrop(window, dlg, "09-add-event")
    try:
        client.add_event(dlg.body_data())
    except HubError as err:
        dlg.handle_error(err)
    grab_over_backdrop(window, dlg, "10-conflict-warning")
    pickers(window, members, client)
    wo9_screens(window, client)
    wo10_screens(window, client)
    wo11_screens(window, client)
    wo12_screens(window, members, client)
    history_screens(window, client)
    wo13_screens(window, members, client)
    trash_screens(window, client)

    # EV-10 series confirmation, TR-3 single removal and the Clear all warning, as the app words them.
    data = client.events()
    dinner = next(e for e in data["upcoming"] if e["title"] == "Sunday dinner")
    notified = dialogs.invitee_list([i["email"] for i in dinner["invitees"] if i["rsvp"] != "organizer"])
    grab_over_backdrop(window, dialogs.MessageDialog(
        window, "Change the whole series?", dinner["series_note"],
        f"{notified} will get one update from Google Calendar.", "Change all occurrences", danger=False,
        cancel=True), "11-repeating-event-confirmation")
    window.show_view("Tasks")
    settle()
    grab_over_backdrop(window, dialogs.MessageDialog(
        window, "Remove task", "Remove Order school photos?", "You can restore it from Trash for 30 days.",
        "Move to Trash", danger=False, cancel=True), "12-single-removal-confirmation")
    window.show_view("Events")
    settle()
    preview = client.clear_preview("events")
    grab_over_backdrop(window, dialogs.MessageDialog(
        window, "Clear all", f"Clear all {preview['count']} events?",
        f"After 10 seconds, all {preview['count']} events are cancelled in Google Calendar and "
        f"{dialogs.invitee_list(preview['invitees'])} get cancellation emails. You can't undo that.",
        f"Clear {preview['count']} events", danger=True, cancel=True), "13-clear-all-events-confirmation")

    # Clear all tasks, with the countdown toast (CA-3, CF-3).
    window.show_view("Tasks")
    pending = client.clear_all("tasks")
    window.start_undo(pending["id"], "6 tasks cleared. {seconds} s to undo.", seconds=7)
    window.changed()
    settle()
    grab(window, "14-undo-toast")
    client.undo_clear(pending["id"])
    window.undo_timer.stop()
    window.toast.hide()

    # New for WO-FS-004: a hovered row with its actions, an empty list, a toast and the sidebar badges.
    window.changed()
    settle()
    rows = window.views["Tasks"].findChild(RowList)
    assert rows is not None
    rows.clearSelection()
    hover(rows, "Book winter tire swap")
    settle()
    grab(window, "16-hover-row-actions")
    rows.hover_row = -1

    window.show_view("Reminders")
    pending = client.clear_all("reminders")
    window.changed()
    settle()
    grab(window, "17-empty-state")
    client.undo_clear(pending["id"])
    window.changed()

    window.show_view("Calendar")
    settle()
    window.toast_message(client.send_digest()["message"])
    settle()
    grab(window, "18-toast")
    window.toast.hide()
    grab(window.sidebar, "19-sidebar-badges")


def pickers(window: MainWindow, members: list[dict[str, Any]], client: HubClient) -> None:
    """WO-FS-008: the date picker, the time wheels, the end-before-start message and All day."""
    from family_scheduler.app.pickers import DarkCalendar

    picker = DarkCalendar()
    today = QDate.currentDate()
    picker.setSelectedDate(today.addDays(3) if today.addDays(3).month() == today.month() else today.addDays(-3))
    picker.resize(picker.sizeHint().expandedTo(picker.minimumSize()))
    picker.show()
    settle()
    grab(picker, "30-date-picker")
    picker.close()

    def form(start: str, end: str) -> dialogs.EventDialog:
        dlg = dialogs.EventDialog(members, client.add_event, parent=window, sender=client.calendar_sender())
        dlg.title.setText("Haircut")
        dlg.location.setText("Main St Barbers")
        dlg.start.set_value(start)
        dlg.end.set_value(end)
        dlg._shown_start = start
        dlg._check_times()
        return dlg

    grab_over_backdrop(window, form("16:15", "17:00"), "31-time-wheels")
    grab_over_backdrop(window, form("16:15", "15:00"), "32-end-before-start")
    dlg = form("16:15", "17:00")
    dlg.title.setText("Pyjama day at school")
    dlg.all_day.setChecked(True)
    grab_over_backdrop(window, dlg, "33-all-day-on")


def trash_screens(window: MainWindow, client: HubClient) -> None:
    """WO-FS-014: Delete forever on a hovered row, Clear all and Delete ticked with their confirmations, the toast
    with Undo, and the empty Trash."""
    window.undo_timer.stop()
    window.toast.hide()
    window.show_view("Trash")
    settle()
    view = window.views["Trash"]
    rows = view.list  # type: ignore[attr-defined]
    rows.setCurrentRow(-1)
    rows.clearSelection()
    hover(rows, "Pick up dry cleaning")
    rows.hover_action = "delete_forever"  # DT-1: the red trash icon under the mouse
    rows.viewport().update()
    grab(window, "110-trash-delete-forever-hovered")
    rows.hover_row, rows.hover_action = -1, None
    rows.viewport().update()
    grab(window, "111-trash-clear-all-button")
    n = len(client.trash())
    grab_over_backdrop(window, dialogs.MessageDialog(
        window, "Clear all", "Clear all Trash?",
        f"All {n} items in Trash are deleted for good, for the whole family. You'll have 10 seconds to undo.",
        "Clear all", danger=True, cancel=True), "112-clear-all-trash-confirmation")
    view.ticked = {f"{i['type']}:{i['id']}" for i in client.trash()[:2]}  # type: ignore[attr-defined]
    view.show_rows()  # type: ignore[attr-defined]
    settle()
    grab(window, "113-trash-delete-ticked")
    grab_over_backdrop(window, dialogs.MessageDialog(
        window, "Delete ticked", "Delete 2 items forever?", "You'll have 10 seconds to undo.", "Delete forever",
        danger=True, cancel=True), "114-delete-ticked-confirmation")
    view.ticked = set()  # type: ignore[attr-defined]
    first = client.trash()[0]
    view.on_action(f"{first['type']}:{first['id']}", "delete_forever")  # type: ignore[attr-defined]
    settle()
    grab(window, "115-trash-deleted-forever-undo")
    window.undo()
    settle()
    done = client.clear_trash()
    window.start_undo(done["id"], f"Trash cleared ({done['count']} items). {{seconds}} s to undo.",
                      undo_with=client.undo_trash_delete, undone="Back in Trash.")
    window.changed()
    settle()
    grab(window, "116-trash-cleared-empty")
    window.undo()
    settle()


def wo13_screens(window: MainWindow, members: list[dict[str, Any]], client: HubClient) -> None:
    """WO-FS-013: the repeat-day lines and tooltip, the Which car? cards, the priority colours, the event pop-up, and
    deleting hidden events."""
    from PySide6.QtWidgets import QToolTip

    window.undo_timer.stop()
    window.toast.hide()  # nothing left over from the screens before
    rent = next(t for group in client.tasks().values() for t in group if t["title"] == "Pay the rent")

    def task_form(title: str, due: str, repeat: str, priority: str = "medium") -> dialogs.TaskDialog:
        dlg = dialogs.TaskDialog(client.add_task, parent=window, due=due)
        dlg.title.setText(title)
        next(b for b in dlg.priority.buttons() if b.property("key") == priority).click()
        dlg.repeat.setCurrentIndex(dlg.repeat.findData(repeat))
        return dlg

    window.show_view("Tasks")
    settle()
    grab_over_backdrop(window, task_form("Pay the rent", rent["due_date"], "monthly", "low"),
                       "90-add-task-every-month-on-the-31st")
    grab_over_backdrop(window, task_form("Leap-day birthday card", "2028-02-29", "yearly"),
                       "91-add-task-every-year-on-february-29")
    far = next(t for group in client.tasks().values() for t in group if t["title"] == "Plan the holiday party")
    for key in ("high", "medium", "low"):
        grab_over_backdrop(window, task_form("Plan the holiday party", far["due_date"], "none", key),
                           f"95-add-task-{key}-chosen")
    tasks = window.views["Tasks"]
    rows = tasks.findChild(RowList)
    assert rows is not None
    rows.setCurrentRow(-1)
    rows.clearSelection()
    rows.scrollToItem(rows.item(find_row(rows, "Book winter tire swap")), RowList.ScrollHint.PositionAtTop)
    settle()
    hover(rows, "Pay the rent")
    grab(window, "96-tasks-priority-tags")
    row = tasks.task_row(rent, "low")  # type: ignore[attr-defined]
    QToolTip.showText(window.mapToGlobal(QPoint(700, 300)), row.tooltip, window)
    settle()
    tips = [w for w in QApplication.topLevelWidgets() if w.isVisible() and w.metaObject().className() == "QTipLabel"]
    if tips:
        grab(tips[0], "92-task-tooltip-repeats-on-the-31st")
    QToolTip.hideText()
    rows.hover_row = -1
    rows.verticalScrollBar().setValue(0)
    window.show_view("Today")
    settle()
    bar = window.views["Today"].list.verticalScrollBar()  # type: ignore[attr-defined]
    bar.setValue(bar.maximum() * 3 // 4)
    settle()
    grab(window, "97-today-priority-tags")
    bar.setValue(0)

    window.show_view("Events")
    settle()
    data = client.events()
    by_title = {e["title"]: e for e in data["upcoming"] + data["past"]}
    bank = by_title["Bank"]
    free_and_busy = dialogs.WhichCarDialog(bank, client.event_cars(bank["id"]), lambda car_id, anyway: None, window)
    grab_over_backdrop(window, free_and_busy, "93-which-car-free-and-in-use")
    hardware = by_title["Hardware store"]
    grab_over_backdrop(window, dialogs.WhichCarDialog(hardware, client.event_cars(hardware["id"]),
                                                      lambda car_id, anyway: None, window),
                       "94-change-car-selected")

    dlg = dialogs.EventDialog(members, client.add_event, parent=window, sender=client.calendar_sender())
    dlg.title.setText("Soccer pickup")
    dlg.location.setText("Riverdale Park")
    dlg.start.set_value("08:30")
    dlg.end.set_value("13:15")
    dlg._shown_start = "08:30"
    dlg._check_times()
    dlg.invitees[-1].setChecked(False)
    dlg.set_cars(client.cars())
    dlg.pick_car(client.cars()[0]["id"])
    grab_over_backdrop(window, dlg, "98-add-event-am-pm-chips-and-car-cards")
    dlg = dialogs.EventDialog(members, client.add_event, parent=window, sender=client.calendar_sender())
    dlg.title.setText("Pyjama day at school")
    dlg.all_day.setChecked(True)
    dlg.set_cars(client.cars())
    grab_over_backdrop(window, dlg, "99-add-event-all-day-switch-on")

    events = window.views["Events"]
    for title in ("Sunday dinner", "Book club", "Garbage day"):
        events.hide_event(by_title[title])  # type: ignore[attr-defined]
    window.toast.hide()
    settle()
    hidden = client.hidden_events()

    def hidden_list() -> dialogs.HiddenDialog:
        return dialogs.HiddenDialog(hidden, lambda _id: None, window, delete=lambda _id: None,
                                    delete_all=lambda: None, undo_delete=lambda _id: None)

    grab_over_backdrop(window, hidden_list(), "100-hidden-list-delete-from-home-base")
    grab_over_backdrop(window, dialogs.MessageDialog(
        window, "Delete all hidden", f"Delete all {len(hidden)} hidden events from Home Base? They stay in Google "
        "Calendar.", "Nothing changes in Google Calendar and nobody is emailed. You'll have a few seconds to undo.",
        f"Delete {len(hidden)} events", danger=True, cancel=True), "101-delete-all-hidden-confirmation")
    after = hidden_list()
    after.delete_one(hidden[0])
    grab_over_backdrop(window, after, "102-hidden-deleted-undo")
    for title in ("Sunday dinner", "Book club", "Garbage day"):  # back, for anything captured later
        client.show_event(by_title[title]["id"])
    window.changed()
    settle()


def history_screens(window: MainWindow, client: HubClient) -> None:
    """1.10.1: History's Clear all and the delete button on each entry, its confirmation, and the Undo toast."""
    window.show_view("History")
    settle()
    view = window.views["History"]
    rows = view.findChild(RowList)
    assert rows is not None
    first = client.history()[0]
    hover(rows, first["label"])
    grab(window, "86-history-delete-and-clear-all")
    rows.hover_row = -1
    grab_over_backdrop(window, dialogs.MessageDialog(
        window, "Clear all", "Clear all History?",
        "Every entry is deleted, for the whole family, not just the ones shown. Nothing else changes. "
        "You'll have 10 seconds to undo.", "Clear History", danger=True, cancel=True),
        "87-clear-all-history-confirmation")
    view.delete_entry(first["id"])  # type: ignore[attr-defined]
    settle()
    grab(window, "88-history-entry-deleted-undo")
    window.undo()
    settle()


def wo12_screens(window: MainWindow, members: list[dict[str, Any]], client: HubClient) -> None:
    """WO-FS-012: the priority lists, a due-soon task's tags and tooltip, the Add task line, Today, the Calendar and
    the digest."""
    from PySide6.QtWidgets import QTextBrowser, QToolTip

    from family_scheduler.digest import build_digest

    window.show_view("Tasks")
    settle()
    tasks = window.views["Tasks"]
    rows = tasks.findChild(RowList)
    assert rows is not None
    grab(window, "80-tasks-priority-lists")
    rows.setCurrentRow(-1)  # a selected first row would pull the list back to the top
    rows.clearSelection()
    scroll = rows.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    settle()
    grab(window, "80a-tasks-medium-and-low")
    scroll.setValue(0)
    settle()
    bumped = next(t for t in client.tasks()["high"] if t["due_soon"])
    hover(rows, bumped["title"])
    row = tasks.task_row(bumped, "high")  # type: ignore[attr-defined]
    QToolTip.showText(window.mapToGlobal(QPoint(700, 300)), row.tag_tips["Due soon"], window)
    settle()
    tips = [w for w in QApplication.topLevelWidgets() if w.isVisible() and w.metaObject().className() == "QTipLabel"]
    if tips:
        grab(tips[0], "81-due-soon-tooltip")
    QToolTip.hideText()
    grab(window, "81a-tasks-due-soon-tags")
    rows.hover_row = -1
    dlg = dialogs.TaskDialog(client.add_task, parent=window, due=bumped["due_date"])
    dlg.title.setText("Change the furnace filter")
    next(b for b in dlg.priority.buttons() if b.property("key") == "low").click()
    grab_over_backdrop(window, dlg, "82-add-task-due-soon-line")
    window.show_view("Today")
    settle()
    bar = window.views["Today"].list.verticalScrollBar()  # type: ignore[attr-defined]
    bar.setValue(bar.maximum() * 3 // 4)  # the to-do list, with its Coming up group
    settle()
    grab(window, "83-today-bumped-task")
    bar.setValue(0)
    window.show_view("Calendar")
    view = calendar(window)
    view.show_mode("week")
    view.go_today()
    settle()
    grab(window, "84-calendar-bumped-task-blocks")
    subject, _text, html = build_digest(client.today(), client.member)
    (OUT / "85-digest.html").write_text(html, encoding="utf-8")
    page = QTextBrowser()
    page.setStyleSheet("QTextBrowser { background: #ffffff; color: #1a1a1a; }")  # as a mail app shows it
    page.setHtml(f"<p><b>{subject}</b></p>" + html)
    page.resize(720, 640)
    page.show()
    settle()
    page.find("High-priority tasks")  # scroll to that section
    page.verticalScrollBar().setValue(page.verticalScrollBar().value() + page.cursorRect().top() - 8)
    cursor = page.textCursor()  # no find highlight in the shot
    cursor.clearSelection()
    page.setTextCursor(cursor)
    settle()
    grab(page, "85-digest-high-priority")
    page.close()


def wo11_screens(window: MainWindow, client: HubClient) -> None:
    """WO-FS-011: Home Base, Today without its footer, Hide from app, the Hidden list, and grocery groups."""
    from PySide6.QtWidgets import QMenu

    window.show_view("Today")
    settle()
    grab(window, "70-home-base-today-no-footer")
    window.show_view("Events")
    settle()
    events = window.views["Events"]
    rows = events.findChild(RowList)
    assert rows is not None
    hover(rows, "Parent council meeting")
    grab(window, "71-event-row-hide-from-app")
    rows.hover_row = -1
    data = client.events()
    by_title = {e["title"]: e for e in data["upcoming"] + data["past"]}
    menu = QMenu(window)  # HE-1: the right-click menu on a calendar block, as the Calendar builds it
    for text in ("Edit", "Which car?", "Hide from app"):
        menu.addAction(text)
    menu.addSeparator()
    menu.addAction("Remove (cancel in Google Calendar)")
    menu.popup(window.mapToGlobal(QPoint(700, 300)))
    settle()
    grab(menu, "72-calendar-block-menu")
    menu.close()
    events.hide_event(by_title["Sunday dinner"])  # type: ignore[attr-defined]
    settle()
    grab(window, "73-hidden-toast-with-undo")
    window.toast.hide()
    events.delete_from_app(by_title["Hockey team party"])  # type: ignore[attr-defined]  # 1.9.1: a Google event
    settle()
    grab(window, "79-google-event-deleted-from-home-base")
    window.toast.hide()
    settle()
    grab(window, "74-events-hidden-button")
    grab_over_backdrop(window, dialogs.HiddenDialog(client.hidden_events(), lambda _id: None, window),
                       "75-hidden-list")
    window.show_view("Groceries")
    settle()
    grab(window, "76-groceries-food-and-items")
    groceries = window.views["Groceries"]
    glist = groceries.list  # type: ignore[attr-defined]
    hover(glist, "Bread")
    grab(window, "77-groceries-hover-grip-and-arrows")
    index = find_row(glist, "Coffee beans")
    item = glist.item(index)
    glist.setCurrentItem(item)
    glist.dragging = item.data(0x0100)
    glist.drop_line = glist.visualRect(glist.model().index(find_row(glist, "Milk (2 L)"), 0)).top()
    glist.viewport().update()
    settle()
    grab(window, "78-groceries-drag-in-progress")
    glist.dragging, glist.drop_line = None, None
    glist.hover_row = -1
    client.show_event(by_title["Sunday dinner"]["id"])  # back, for the screens taken after these
    client.undo_delete_from_app(by_title["Hockey team party"]["id"])
    window.changed()


def wo10_screens(window: MainWindow, client: HubClient) -> None:
    """WO-FS-010: Set up your family, the accounts card, the guide from settings, sync, cars and groceries."""
    window.show_view("Family & Settings")
    settle()
    grab(window, "51-calendar-accounts-card")
    settings = window.views["Family & Settings"]
    settings.show_card("cars")  # type: ignore[attr-defined]
    settle()
    grab(window, "52-cars-card")
    window.show_guide()
    settle()
    grab(window, "53-setup-guide-addresses-from-settings")
    window.show_view("Events")
    settle()
    rows = window.views["Events"].findChild(RowList)
    assert rows is not None
    select_row(window.views["Events"], "Parent council meeting")
    hover(rows, "Hockey team party")
    grab(window, "54-events-synced-from-google")
    rows.hover_row = -1
    window.show_view("Today")
    settle()
    grab(window, "55-today-cars-bar")
    data = client.events()
    events = {e["title"]: e for e in data["upcoming"] + data["past"]}
    bank = events["Bank"]
    grab_over_backdrop(window, dialogs.WhichCarDialog(bank, client.event_cars(bank["id"]),
                                                      lambda car_id, anyway: None, window), "56-which-car")
    try:
        client.set_event_car(bank["id"], next(c["id"] for c in client.cars() if c["name"] == "Subaru Crosstrek"))
    except HubError as err:  # CR-7: the warning, as the app shows it
        grab_over_backdrop(window, dialogs.MessageDialog(
            window, "Car already in use", err.detail, "Both events will show it as double-booked.", "Use anyway",
            danger=True, cancel=True), "57-double-booked-warning")
    window.show_view("Calendar")
    view = calendar(window)
    view.show_mode("week")
    view.go_today()
    settle()
    grab(window, "58-week-blocks-with-cars")
    view.toggle_replies()
    settle()
    grab(window, "59-refresh-beside-replies")
    view.toggle_replies()
    window.show_view("Groceries")
    settle()
    grab(window, "60-groceries")


def set_up_screen(app: QApplication) -> None:
    """WO-FS-010 AC-4: a fresh hub opens on Set up your family."""
    dlg = dialogs.SetupFamilyDialog(lambda body: body)
    dlg.admin.setText("admin@example.com")
    dlg.second.setText("second@example.com")
    dlg.members.setPlainText("alex@example.com\nsam@example.com")
    dlg.show()
    settle()
    grab(dlg, "50-set-up-your-family")
    dlg.close()


def wo9_screens(window: MainWindow, client: HubClient) -> None:
    """WO-FS-009: the Setup guide, No due date, ongoing tasks, and month colours in each season."""
    window.show_view("Family & Settings")
    settle()
    grab(window, "40-settings-setup-guide-button")
    window.show_guide()
    settle()
    grab(window, "41-setup-guide")
    for area in window.guide.findChildren(QScrollArea):
        bar = area.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        settle()
        grab(window, "41a-setup-guide-calendar")
        bar.setValue(bar.maximum())
        settle()
        grab(window, "41b-setup-guide-another-pc")
    window.show_view("Tasks")
    settle()
    grab(window, "42-tasks-ongoing")
    window.show_view("Today")
    settle()
    bar = window.views["Today"].list.verticalScrollBar()  # type: ignore[attr-defined]
    bar.setValue(bar.maximum())
    settle()
    grab(window, "43-today-ongoing")
    bar.setValue(0)
    dlg = dialogs.TaskDialog(client.add_task, parent=window)
    dlg.title.setText("Keep the first-aid kit stocked")
    dlg.no_due.setChecked(True)
    grab_over_backdrop(window, dlg, "44-add-task-no-due-date")
    window.show_view("Events")
    settle()
    rows = window.views["Events"].findChild(RowList)
    assert rows is not None
    for name, step in (("45-events-month-headers", 0), ("45a-events-month-headers-later", 1)):
        rows.verticalScrollBar().setValue(rows.verticalScrollBar().maximum() * step)
        settle()
        grab(window, name)
    view = calendar(window)
    window.show_view("Calendar")
    view.show_mode("month")
    today = date.today()
    for month, name in ((1, "january"), (4, "april"), (7, "july"), (10, "october"), (11, "november")):
        year = today.year + (1 if month < today.month else 0)
        view.anchor = date(year, month, 1)
        window.safe_reload(view)
        settle()
        grab(window, f"46-month-{month:02d}-{name}")
    view.show_mode("week")
    view.go_today()
    settle()
    grab(window, "47-week-header-underline")


def empty_today(app: QApplication) -> None:
    """WO-FS-007 deliverable 2: Today with every section empty, on a second, empty test hub."""
    folder = Path(tempfile.mkdtemp(prefix="fs-empty-"))
    ctx = build_context(folder, keys=StaticKeyProvider(), fake_calendar=True, fake_mail=True)
    set_up(ctx)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    client = HubClient(f"127.0.0.1:{port}", ADMIN)
    window = MainWindow(client, LocalSettings(folder / "client.json"), client.members(), lambda: None)
    window.resize(1180, 760)
    window.show()
    settle()
    grab(window, "00a-today-empty")
    window.close()
    server.should_exit = True


def main() -> None:
    port = free_port()
    ctx = build_context(DATA, keys=StaticKeyProvider(), fake_calendar=True, fake_mail=True)
    seed(ctx)
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    app = QApplication(sys.argv)
    apply_theme(app)
    client = HubClient(f"127.0.0.1:{port}", ALEX)
    members = client.members()

    if not PART_A:
        who = dialogs.WhoDialog(members, ALEX)
        who.show()
        settle()
        grab(who, "01-who-is-using-the-app")
        who.close()

    window = MainWindow(client, LocalSettings(DATA / "client.json"), members, lambda: None)
    window.resize(1180, 760)
    window.show()
    settle()
    if PART_A:
        sample_screens(window, members, client)
    else:
        all_screens(window, members, client, ctx)
        empty_today(app)
        set_up_screen(app)
        # Hub turned off (PC-8, CF-7).
        window.show_view("Events")
        server.should_exit = True
        time.sleep(1.0)
        window._poll()
        settle()
        grab(window, "15-cant-reach-the-hub")
    QTimer.singleShot(0, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
