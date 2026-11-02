"""WO-FS-013 section 5: the Which car? pop-up as large button cards, and the same cards in the event form (WC-1 to
WC-10)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from family_scheduler.client.hub_client import HubError

EVENT = {"title": "Soccer pickup", "date": "2026-11-03", "start_time": "16:15", "end_time": "16:45"}
DENTIST = {"title": "Dentist check-up", "start_time": "16:00", "end_time": "16:45"}


@pytest.fixture
def qt_app() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def car(n: int, selected: bool = False, in_use: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"id": n, "name": f"Car {n}", "selected": selected, "in_use": in_use or []}


def test_free_in_use_and_double_booked_cards(qt_app: Any) -> None:  # WC-1, WC-2
    from family_scheduler.app.dialogs import CarCard

    free = CarCard(car(1))
    assert (free.pill.text(), free.pill.property("level")) == ("Free", "free")
    assert free.detail.isHidden() and free.clash.isHidden() and free.check.isHidden()
    busy = CarCard(car(2, in_use=[DENTIST]))
    assert (busy.pill.text(), busy.pill.property("level")) == ("In use", "busy")
    assert busy.detail.text() == "Dentist check-up, 4:00 pm–4:45 pm" and not busy.detail.isHidden()
    assert busy.clash.text() == "Will be double-booked" and not busy.clash.isHidden()
    both = CarCard(car(3, selected=True, in_use=[DENTIST, DENTIST, DENTIST]))
    assert both.clash.text() == "Double-booked" and both.detail.text().endswith("and 1 more")
    assert not both.check.isHidden() and both.property("selected") is True  # WC-4
    assert free.objectName() == "CarCard" and free.cursor().shape().name == "PointingHandCursor"  # WC-3


def test_the_pop_up(qt_app: Any) -> None:  # WC-4 to WC-6
    from family_scheduler.app.dialogs import WhichCarDialog

    picked: list[tuple[int | None, bool]] = []
    first = WhichCarDialog(EVENT, [car(1), car(2)], lambda c, a: picked.append((c, a)))
    assert first.windowTitle() == "Which car?" and None not in first.buttons  # No car needed only with a car
    assert first.card.property("accent") == "event" and "Soccer pickup" in first.findChild(type(first.title_label),
                                                                                             "CarEvent").text()
    change = WhichCarDialog(EVENT, [car(1, selected=True), car(2)], lambda c, a: picked.append((c, a)))
    assert change.windowTitle() == "Change car" and change.title_label.text() == "Change car"
    change.buttons[None].click()
    assert picked == [(None, False)]
    first.cards[1].click()
    assert picked[-1] == (2, False)


def test_keyboard(qt_app: Any) -> None:  # WC-3
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from family_scheduler.app.dialogs import WhichCarDialog

    picked: list[int | None] = []
    dlg = WhichCarDialog(EVENT, [car(1), car(2, selected=True), car(3)], lambda c, a: picked.append(c))
    dlg.show()
    dlg.activateWindow()
    qt_app.processEvents()

    def key(k: Qt.Key) -> None:
        dlg.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))
        qt_app.processEvents()

    def focused() -> int:
        return next(n for n, c in enumerate(dlg.cards) if c.hasFocus())

    key(Qt.Key.Key_Down)
    assert focused() == 1  # the first arrow lands on the chosen car
    key(Qt.Key.Key_Down)
    key(Qt.Key.Key_Down)
    assert focused() == 2  # stops at the last
    key(Qt.Key.Key_Up)
    key(Qt.Key.Key_Return)
    assert picked == [2]
    dlg.close()


@pytest.mark.parametrize(("count", "scrolls"), [(1, False), (4, False), (10, True)])
def test_one_to_ten_cars(qt_app: Any, count: int, scrolls: bool) -> None:  # WC-10
    from family_scheduler.app.dialogs import WhichCarDialog

    dlg = WhichCarDialog(EVENT, [car(n) for n in range(1, count + 1)], lambda c, a: None)
    assert len(dlg.cards) == count and (dlg.card_scroll is not None) == scrolls
    if dlg.card_scroll is not None:
        assert dlg.card_scroll.height() < sum(c.sizeHint().height() for c in dlg.cards)


def test_in_use_asks_use_anyway_or_pick_another(qt_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # WC-8
    from family_scheduler.app import dialogs

    asked: list[dialogs.MessageDialog] = []

    def exec_(self: dialogs.MessageDialog) -> int:
        asked.append(self)
        return 0  # Pick another

    monkeypatch.setattr(dialogs.MessageDialog, "exec", exec_)

    def choose(car_id: int | None, anyway: bool) -> None:
        raise HubError(409, "The Car 2 is already needed for Dentist check-up, 4:00–4:45 pm.", {})

    dlg = dialogs.WhichCarDialog(EVENT, [car(1), car(2, in_use=[DENTIST])], choose)
    dlg.cards[1].click()
    texts = [b.text() for b in asked[0].findChildren(dialogs.QPushButton)]
    assert "Use anyway" in texts and "Pick another" in texts
    assert asked[0].card.property("accent") == "event"
    assert dlg.result() == 0  # still open: pick another


def test_the_event_form_uses_compact_cards(qt_app: Any) -> None:  # WC-9
    from family_scheduler.app.dialogs import CarCard, EventDialog

    dlg = EventDialog([{"email": "alex@example.com"}], lambda body: body)
    dlg.set_cars([{"id": 1, "name": "Chevrolet Cruze"}, {"id": 2, "name": "Subaru Crosstrek"}])
    cards = [w for w in dlg.findChildren(CarCard) if not w.isHidden()]
    assert [c.car["name"] for c in cards] == ["No car", "Chevrolet Cruze", "Subaru Crosstrek"]
    assert all(c.property("compact") is True for c in cards) and all(c.pill.isHidden() for c in cards)
    assert [c.car["name"] for c in dlg.chosen_car_cards()] == ["No car"]
    cards[2].click()
    assert dlg.car_id == 2 and dlg.car_changed
    assert [c.car["name"] for c in dlg.chosen_car_cards()] == ["Subaru Crosstrek"]
    status = [car(1) | {"name": "Chevrolet Cruze"}, car(2, in_use=[DENTIST]) | {"name": "Subaru Crosstrek"}]
    edit = EventDialog([{"email": "alex@example.com"}], lambda body: body,
                       existing={"title": "Bank", "date": "2026-11-03", "start_time": "16:15", "end_time": "16:45",
                                 "location": "", "repeat": "none", "invitees": [], "organizer_email": "a@b.c",
                                 "car": {"id": 1}, "read_only": True})
    edit.set_cars([{"id": 1, "name": "Chevrolet Cruze"}, {"id": 2, "name": "Subaru Crosstrek"}], lambda: status)
    shown = [w for w in edit.findChildren(CarCard) if not w.isHidden()]
    assert [c.pill.text() for c in shown[1:]] == ["Free", "In use"]  # an existing event's time is known
    assert not any(c.isEnabled() for c in shown)  # read-only events can't change car (CR-10)


def test_a_hovered_card_is_lighter(qt_app: Any) -> None:  # WC-3
    from PySide6.QtTest import QTest

    from family_scheduler.app import style
    from family_scheduler.app.dialogs import WhichCarDialog

    style.apply_theme(qt_app)
    dlg = WhichCarDialog(EVENT, [car(1), car(2)], lambda c, a: None)
    dlg.show()
    qt_app.processEvents()
    for other in qt_app.topLevelWidgets():  # only this pop-up on screen, so the simulated mouse reaches it
        if other is not dlg and other.isVisible():
            other.hide()
    hovered, plain = dlg.cards
    QTest.mouseMove(hovered, hovered.rect().center())
    qt_app.processEvents()

    def surface(card: Any) -> str:
        image = card.grab().toImage()
        return str(image.pixelColor(image.width() // 2, 4).name()).upper()

    assert hovered.underMouse()
    assert (surface(hovered), surface(plain)) == (style.CARD_BUTTON_HOVER, style.RAISED_HOVER)
    dlg.close()
