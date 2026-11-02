"""WO-FS-011: Home Base (HB-1 to HB-6), hiding events (HE-1 to HE-11), grocery groups and order (GG-1 to GG-9,
GO-1 to GO-8) and the Today footer (TF-1 to TF-3)."""

from __future__ import annotations

import ast
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import ADMIN, ALEX, SAM, SECOND, FakeClock, event
from test_today import TODAY, open_app, page_text

from family_scheduler import db
from family_scheduler.digest import build_digest
from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import NeedsConfirmation, NotFound, Organizer

ROOT = Path(__file__).resolve().parents[1]
OUTSIDER = "coach@example.org"


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def timed(title: str, day: str = "2026-11-05", start: str = "18:00", end: str = "19:00", **more: Any) -> dict[str, Any]:
    return {"summary": title, "start": {"dateTime": f"{day}T{start}:00", "timeZone": "America/Toronto"},
            "end": {"dateTime": f"{day}T{end}:00", "timeZone": "America/Toronto"}, **more}


def titles(org: Organizer) -> list[str]:
    events = org.list_events()
    return sorted(e["title"] for e in events["upcoming"] + events["past"])


def find(org: Organizer, title: str) -> dict[str, Any]:
    events = org.list_events()
    return next(e for e in events["upcoming"] + events["past"] if e["title"] == title)


# --- hiding events (section 3) ---------------------------------------------------------------------------------

def test_every_kind_of_event_can_be_hidden(org: Organizer, gcal: FakeCalendarGateway) -> None:  # HE-1, HE-2
    org.add_event(ALEX, event("App event"))
    gcal.google_add(ADMIN, timed("From Google"))
    gcal.google_add(OUTSIDER, timed("Outside invitation", attendees=[{"email": ADMIN}]))
    gcal.google_add(SECOND, timed("Secret", visibility="private"))
    org.sync_google(ALEX)
    gcal.offline = True
    org.add_event(ALEX, event("Never sent", day="2026-11-06"))  # failed to reach Google
    gcal.offline = False
    org.add_event(SECOND, event("Second's event", day="2026-11-07"))
    org.set_account_status(SECOND, "needs_reconnect")  # its organizer needs reconnecting: Remove isn't possible
    kinds = ["App event", "From Google", "Outside invitation", "Busy", "Never sent", "Second's event"]
    assert all(t in titles(org) for t in kinds)
    sent = len(gcal.calls)
    for title in kinds:
        org.hide_event(SAM, find(org, title)["id"])
    assert titles(org) == []
    assert gcal.calls[sent:] == []  # HE-3: nothing changes in Google Calendar, so no email


def test_hidden_events_disappear_everywhere(org: Organizer, clock: FakeClock) -> None:  # HE-4
    soccer = org.add_event(ALEX, event("Soccer", day=TODAY, start="16:00", end="17:00"))
    org.add_event(ALEX, event("Dentist", day=TODAY, start="16:30", end="17:30"), acknowledge_conflicts=True)
    cruze = org.list_cars()[1]["id"]
    org.set_event_car(ALEX, soccer["id"], cruze)
    org.hide_event(ALEX, soccer["id"])
    view = org.today_view()
    assert [e["title"] for e in view["events"]] == ["Dentist"]  # Today, the badge and the digest
    assert view["cars_today"] == []  # its car isn't counted
    assert find(org, "Dentist")["conflicts_with"] == []  # no overlap warning
    week = org.week_view(date(2026, 10, 26), 7)["week_events"]
    assert [e["title"] for e in week] == ["Dentist"]
    assert "Soccer" not in build_digest(view, ALEX)[1]
    late = org.add_event(SAM, event("Late", day=TODAY, start="16:15", end="16:45"), acknowledge_conflicts=True)
    org.set_event_car(SAM, late["id"], cruze)  # the hidden event's car no longer clashes
    assert not org.get_event(late["id"])["car_double_booked"]


def test_a_repeating_event_hides_the_whole_series(org: Organizer) -> None:  # HE-5
    walk = org.add_event(ALEX, event("Walk", day="2026-10-26", repeat="daily"))
    org.hide_event(ALEX, walk["id"])
    assert not [e for e in org.week_view(date(2026, 10, 26), 35)["week_events"] if e["title"] == "Walk"]


def test_undo_and_show_again(org: Organizer) -> None:  # HE-3, HE-7, HE-8
    e = org.add_event(ALEX, event("Sunday dinner"))
    org.hide_event(ALEX, e["id"])
    assert org.list_events()["hidden_count"] == 1
    assert [h["title"] for h in org.list_hidden()] == ["Sunday dinner"]
    org.show_event(SAM, e["id"])
    assert titles(org) == ["Sunday dinner"] and org.list_events()["hidden_count"] == 0
    history = [(h["action"], h["after"]) for h in org.list_history() if h["label"] == "Sunday dinner"]
    assert history[:2] == [("restored", {"from": "hidden"}), ("removed", {"to": "hidden"})]


def test_sync_doesnt_bring_it_back(org: Organizer, gcal: FakeCalendarGateway) -> None:  # HE-6
    gid = gcal.google_add(ADMIN, timed("Book club"))
    org.sync_google(ALEX)
    org.hide_event(ALEX, find(org, "Book club")["id"])
    org.sync_google(ALEX)
    assert titles(org) == []
    gcal.google_change(gid, summary="Book club (moved)")  # changed in Google: still hidden, but up to date
    org.sync_google(ALEX)
    hidden = org.list_hidden()
    assert titles(org) == [] and [h["title"] for h in hidden] == ["Book club (moved)"]
    gcal.google_remove(gid)  # removed in Google: removed from the app as usual
    org.sync_google(ALEX)
    assert org.list_hidden() == []


def test_a_hidden_event_is_never_sent_to_google_later(org: Organizer, gcal: FakeCalendarGateway,
                                                     clock: FakeClock) -> None:  # HE-3
    gcal.offline = True
    e = org.add_event(ALEX, event("Never sent"))
    org.hide_event(ALEX, e["id"])
    gcal.offline = False
    clock.advance(minutes=10)
    assert org.retry_due() == 0 and not gcal.events


def test_hidden_past_events_are_cleared_after_30_days(org: Organizer, clock: FakeClock) -> None:  # HE-10
    e = org.add_event(ALEX, event("Old", day=TODAY, start="09:00", end="09:30"))
    org.hide_event(ALEX, e["id"])
    clock.advance(days=31)
    org.purge()
    assert org.list_hidden() == []
    with pytest.raises(NotFound):
        org.show_event(ALEX, e["id"])


def test_hide_over_the_api(ctx: HubContext) -> None:  # HE-1, HE-7
    from fastapi.testclient import TestClient

    from family_scheduler.hub.api import create_app

    web = TestClient(create_app(ctx), client=("127.0.0.1", 50000))
    h = {"X-Member": ALEX}
    e = ctx.organizer.add_event(ALEX, event("Swim"))
    assert web.post(f"/api/events/{e['id']}/hide", headers=h).status_code == 200
    assert [x["title"] for x in web.get("/api/events/hidden", headers=h).json()] == ["Swim"]
    assert web.post(f"/api/events/{e['id']}/show", headers=h).json()["title"] == "Swim"


# --- grocery groups (section 4.1) --------------------------------------------------------------------------------

def group(org: Organizer, name: str, part: str = "to_buy") -> list[str]:
    return [i["text"] for i in org.list_groceries()["groups"][name][part]]


def test_food_and_items_groups(org: Organizer) -> None:  # GG-1, GG-3, GG-7, GG-8
    org.add_grocery(ALEX, "Milk")
    org.add_grocery(ALEX, "Batteries", category="item")
    org.add_grocery(ALEX, "Bread", category="food")
    assert group(org, "food") == ["Milk", "Bread"] and group(org, "item") == ["Batteries"]
    with pytest.raises(NeedsConfirmation):
        org.add_grocery(SAM, "milk", category="food")
    org.add_grocery(SAM, "Milk", category="item")  # the duplicate check is per group
    assert len(org.list_groceries()["to_buy"]) == 4 and org.today_view()["groceries_to_buy"] == 4


def test_moving_between_groups_and_bought_items(org: Organizer) -> None:  # GG-4, GG-5
    milk = org.add_grocery(ALEX, "Milk")
    soap = org.add_grocery(ALEX, "Dish soap")
    org.add_grocery(ALEX, "Bulbs", category="item")
    org.place_grocery(ALEX, soap["id"], "item")  # Move to Items: the bottom of the other group
    assert group(org, "food") == ["Milk"] and group(org, "item") == ["Bulbs", "Dish soap"]
    org.set_grocery_bought(ALEX, milk["id"], True)
    assert group(org, "food") == [] and group(org, "food", "bought") == ["Milk"]  # inside its own group
    org.add_grocery(ALEX, "Eggs")
    org.set_grocery_bought(ALEX, milk["id"], False)
    assert group(org, "food") == ["Eggs", "Milk"]  # unticked: back at the bottom of its group
    org.set_grocery_bought(ALEX, milk["id"], True)
    org.remove_groceries(ALEX, None)  # Clear bought: both groups
    assert group(org, "food", "bought") == [] and group(org, "food") == ["Eggs"]


def test_existing_items_go_into_food_in_their_order() -> None:  # GG-9, GO-8
    conn = db.connect(":memory:")
    conn.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY)")
    for number, sql in db._migration_files()[:5]:  # the database as 1.8.0 left it
        for statement in db._split_sql(sql):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_migration (version) VALUES (?)", (number,))
    conn.execute("INSERT INTO family_member (email, created_at) VALUES (?, 'n')", (ALEX,))
    for n, text in enumerate(("Milk", "Soap", "Bread")):
        conn.execute("INSERT INTO grocery_item (id, text, added_by, added_at) VALUES (?, ?, ?, ?)",
                     (f"g{n}", text, ALEX, f"2026-10-3{n}T00:00:00+00:00"))
    conn.execute("INSERT INTO event (id, title, date, start_time, end_time, gcal_event_id, sync_status, created_by, "
                 "created_at, updated_at, starts_at, ends_at) VALUES ('e1', 'Dentist', '2026-11-03', '16:00', "
                 "'17:00', 'abcdef12345', 'synced', ?, 'n', 'n', 'n', 'n')", (ALEX,))
    conn.commit()
    assert db.migrate(conn) == 10
    rows = [tuple(r) for r in conn.execute("SELECT text, category, position FROM grocery_item ORDER BY position")]
    assert rows == [("Milk", "food", 1), ("Soap", "food", 2), ("Bread", "food", 3)]
    assert conn.execute("SELECT hidden FROM event WHERE id = 'e1'").fetchone()[0] == 0  # every event stays shown


# --- ordering (section 4.2) ------------------------------------------------------------------------------------

def test_up_down_first_and_last(org: Organizer) -> None:  # GO-1, GO-2, GO-4
    ids = {t: org.add_grocery(ALEX, t)["id"] for t in ("Milk", "Bread", "Eggs")}
    org.move_grocery(ALEX, ids["Eggs"], -1)
    assert group(org, "food") == ["Milk", "Eggs", "Bread"]
    org.move_grocery(ALEX, ids["Milk"], -1)  # already first: stays
    org.move_grocery(ALEX, ids["Bread"], 1)  # already last: stays
    assert group(org, "food") == ["Milk", "Eggs", "Bread"]
    org.move_grocery(ALEX, ids["Milk"], 1)
    assert group(org, "food") == ["Eggs", "Milk", "Bread"]


def test_dragging_within_and_across_groups(org: Organizer) -> None:  # GO-3
    ids = {t: org.add_grocery(ALEX, t)["id"] for t in ("Milk", "Bread", "Eggs")}
    org.add_grocery(ALEX, "Bulbs", category="item")
    org.place_grocery(ALEX, ids["Eggs"], "food", 0)
    assert group(org, "food") == ["Eggs", "Milk", "Bread"]
    org.place_grocery(ALEX, ids["Milk"], "item", 0)  # dropped in the other group
    assert group(org, "food") == ["Eggs", "Bread"] and group(org, "item") == ["Milk", "Bulbs"]


def test_bought_items_arent_reordered(org: Organizer) -> None:  # GO-6
    milk = org.add_grocery(ALEX, "Milk")
    org.set_grocery_bought(ALEX, milk["id"], True)
    with pytest.raises(NotFound):
        org.move_grocery(ALEX, milk["id"], -1)


def test_the_order_is_shared(ctx: HubContext) -> None:  # GO-5
    from fastapi.testclient import TestClient

    from family_scheduler.hub.api import create_app

    one = TestClient(create_app(ctx), client=("127.0.0.1", 50000))
    two = TestClient(create_app(ctx), client=("192.168.1.20", 50000))
    ids = [one.post("/api/groceries", json={"text": t}, headers={"X-Member": ALEX}).json()["id"]
           for t in ("Milk", "Bread")]
    one.post(f"/api/groceries/{ids[1]}/move", json={"step": -1}, headers={"X-Member": ALEX})
    seen = two.get("/api/groceries", headers={"X-Member": SAM}).json()["groups"]["food"]["to_buy"]
    assert [i["text"] for i in seen] == ["Bread", "Milk"]  # every PC sees the same order
    assert not any(h["label"] in ("Milk", "Bread") for h in ctx.organizer.list_history())  # GO-7


def test_the_groceries_page(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock, tmp_path: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:  # GG-1 to GG-6, GO-2 to GO-4
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("Groceries")
    view = window.current()
    view.choose_group("item")
    view.entry.setText("Batteries")
    view.add_new()
    view.choose_group("food")
    for text in ("Milk", "Bread"):
        view.entry.setText(text)
        view.add_new()
    assert window.settings.get("grocery_group") == "food"  # GG-3: remembers the last choice
    assert page_text(view) == ["# Food", "Milk", "Bread", "# Items", "Batteries"]
    rows = {e.title: e for e in (view.list.row_delegate.entry(view.list.model().index(i, 0))
                                 for i in range(view.list.count())) if hasattr(e, "disabled")}
    assert "up" in rows["Milk"].disabled and "down" in rows["Bread"].disabled  # GO-4
    assert rows["Milk"].accent != rows["Batteries"].accent  # green and blue
    view.list.select_id(rows["Bread"].id)
    view.list.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier))
    assert page_text(view)[:3] == ["# Food", "Bread", "Milk"]  # Alt+Up
    view.dropped(rows["Milk"].id, "item", 0)  # a drag into Items
    assert page_text(view) == ["# Food", "Bread", "# Items", "Milk", "Batteries"]
    view.on_action(rows["Bread"].id, "to_item")
    assert page_text(view)[:2] == ["# Food", "- No food on the list."]  # GG-6


# --- Home Base (section 2) and the Today footer (section 5) ----------------------------------------------------

# The guide's note about the older app-password name, the package docstring, and the network discovery name, which
# is kept so PCs not yet updated still find the hub (HB-4).
ALLOWED_OLD_NAME = ('older \\"Family Scheduler\\"', "formerly Family Scheduler", "Family Scheduler Hub on ")


def test_no_family_scheduler_left_in_what_a_person_sees() -> None:  # HB-1
    found = []
    for path in (ROOT / "src" / "family_scheduler").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            text = node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""
            if "Family Scheduler" in text and not any(a.replace("\\", "") in text for a in ALLOWED_OLD_NAME):
                found.append(f"{path.name}: {text[:60]}")
    assert found == []
    for name in ("docs/index.html", "docs/privacy.html", "packaging/install-notes.txt", "packaging/hub-task.xml"):
        assert "Family Scheduler" not in (ROOT / name).read_text(encoding="utf-8"), name
    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "AppName=Home Base" in iss and "OutputBaseFilename=HomeBaseSetup-" in iss
    assert 'Name: "{autodesktop}\\Home Base"; Filename: "{app}\\HomeBase.exe"' in iss
    assert "AppId={{6F3C2A8E-5B1D-4C7A-9E2F-FA11C5CEDB01}" in iss  # HB-3: the same AppId, so it upgrades in place
    assert 'Type: files; Name: "{autodesktop}\\Family Scheduler.lnk"' in iss  # the old shortcut goes


def test_the_digest_says_home_base(org: Organizer, mail: Any) -> None:  # HB-1
    org.send_digest(ALEX)
    sent = mail.sent[-1]
    assert sent["subject"].startswith("Home Base: your digest for Tuesday, October 27")
    assert 'msg["From"] = f"Home Base <' in (ROOT / "src/family_scheduler/mailer.py").read_text(encoding="utf-8")


def test_the_window_sidebar_and_today(qt_app: Any, ctx: HubContext, clock: FakeClock, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:  # HB-1, TF-1 to TF-3
    from PySide6.QtWidgets import QLabel

    window = open_app(ctx, tmp_path, monkeypatch, clock)
    assert window.windowTitle() == "Home Base"
    assert "Home Base" in [label.text() for label in window.sidebar.findChildren(QLabel)]
    today = window.views["Today"]
    texts = [label.text() for label in today.findChildren(QLabel)]
    assert not any(t.startswith("The digest email covers") for t in texts)  # TF-1: the footer is gone
    assert "The digest email covers" in today.digest_button.toolTip()  # TF-2: it's in the button's tooltip


def test_the_events_page_hide_and_hidden_list(qt_app: Any, ctx: HubContext, org: Organizer, clock: FakeClock,
                                              tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # HE-1, HE-7
    from family_scheduler.app.widgets import ACTION_TIPS

    e = org.add_event(ALEX, event("Swim"))
    window = open_app(ctx, tmp_path, monkeypatch, clock)
    window.show_view("Events")
    view = window.current()
    assert view.hidden_b.isHidden()
    row = view._row(org.get_event(e["id"]), "2026-11-03", False)
    assert "hide" in row.actions and "cancel" in row.actions
    assert ACTION_TIPS["hide"] == "Hide from Home Base. It stays in Google Calendar."
    assert ACTION_TIPS["cancel"].startswith("Remove: cancel this event in Google Calendar.")  # HE-9
    view.hide_event(org.get_event(e["id"]))
    assert view.hidden_b.isVisible() is not None and view.hidden_b.text() == "Hidden (1)"
    from family_scheduler.app.dialogs import HiddenDialog

    dlg = HiddenDialog(window.client.hidden_events(), window.client.show_event)
    dlg.show_again(window.client.hidden_events()[0])
    assert dlg.shown == ["Swim"] and titles(org) == ["Swim"]
