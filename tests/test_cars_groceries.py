"""WO-FS-010 sections 9 and 10: cars (CR-1 to CR-13, CB-a to CB-j) and groceries (GR-1 to GR-11)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import ADMIN, ALEX, SAM, FakeClock, event

from family_scheduler.app import carbar
from family_scheduler.digest import build_digest
from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.organizer import EventInput, NeedsConfirmation, Organizer, OrganizerError

TODAY = "2026-10-27"  # the test clock: Tuesday Oct 27, 2026, 8:00 am Toronto time
SUBARU, CRUZE = "Subaru Crosstrek", "Chevrolet Cruze"


def car_id(org: Organizer, name: str) -> int:
    return next(c["id"] for c in org.list_cars() if c["name"] == name)


def use(car: int, start: int, end: int, title: str = "x", name: str = "") -> dict[str, Any]:
    return {"car_id": car, "car": name or f"Car {car}", "event_id": f"{title}{start}", "title": title,
            "start": start, "end": end, "all_day": False}


# --- the Cars card (CR-1 to CR-3) ------------------------------------------------------------------------------

def test_the_family_starts_with_two_named_cars(org: Organizer) -> None:  # CR-1, CR-13
    assert [c["name"] for c in org.list_cars()] == [SUBARU, CRUZE]


def test_one_to_ten_cars_with_different_names(org: Organizer) -> None:  # CR-1
    org.set_cars(ALEX, [SUBARU, CRUZE, ""])
    assert [c["name"] for c in org.list_cars()] == [SUBARU, CRUZE, "Car 3"]  # a new car's default name
    for bad in ([], ["x"] * 11):
        with pytest.raises(OrganizerError, match="between 1 and 10"):
            org.set_cars(ALEX, bad)
    with pytest.raises(OrganizerError, match="different name"):
        org.set_cars(ALEX, [SUBARU, SUBARU.upper()])
    with pytest.raises(OrganizerError, match="30 characters"):
        org.set_cars(ALEX, ["x" * 31])


def test_renaming_updates_everywhere(org: Organizer) -> None:  # CR-2
    e = org.add_event(ALEX, event("Dentist"))
    org.set_event_car(ALEX, e["id"], car_id(org, CRUZE))
    org.set_cars(ALEX, [CRUZE, SUBARU])  # swapped names: no clash on the way
    assert org.get_event(e["id"])["car"]["name"] == SUBARU
    assert any(h["item_type"] == "car" and h["action"] == "edited" for h in org.list_history())  # CR-3


def test_lowering_the_number_asks_when_a_removed_car_is_needed(org: Organizer) -> None:  # CR-3
    for n in range(3):
        e = org.add_event(ALEX, event(f"Hockey {n}", day=f"2026-11-0{n + 3}"))
        org.set_event_car(ALEX, e["id"], car_id(org, CRUZE))
    with pytest.raises(NeedsConfirmation, match="Chevrolet Cruze is needed for 3 upcoming events"):
        org.set_cars(ALEX, [SUBARU])
    assert len(org.list_cars()) == 2  # nothing changed
    org.set_cars(ALEX, [SUBARU], remove_anyway=True)
    assert [c["name"] for c in org.list_cars()] == [SUBARU]
    assert all(e["car"] is None for e in org.list_events()["upcoming"])  # cleared from those events


# --- choosing a car (CR-4 to CR-12) ---------------------------------------------------------------------------

def test_which_car_shows_free_and_in_use(org: Organizer) -> None:  # CR-5, CR-6
    soccer = org.add_event(ALEX, event("Soccer pickup", day="2026-11-03", start="16:15", end="16:45"))
    dentist = org.add_event(ALEX, event("Dentist", day="2026-11-03", start="16:30", end="17:30"),
                            acknowledge_conflicts=True)
    org.set_event_car(ALEX, soccer["id"], car_id(org, CRUZE))
    cars = {c["name"]: c for c in org.event_cars(ALEX, dentist["id"])}
    assert cars[SUBARU]["in_use"] == [] and not cars[SUBARU]["selected"]
    assert [u["title"] for u in cars[CRUZE]["in_use"]] == ["Soccer pickup"]
    assert cars[CRUZE]["in_use"][0]["start_time"] == "16:15"
    assert [c["selected"] for c in org.event_cars(ALEX, soccer["id"])] == [False, True]
    org.set_event_car(ALEX, soccer["id"], None)  # No car needed
    assert org.get_event(soccer["id"])["car"] is None


def test_double_booking_warns_then_flags_both(org: Organizer) -> None:  # CR-7
    book = org.add_event(ALEX, event("Book club", day="2026-11-03", start="19:00", end="21:00"))
    late = org.add_event(SAM, event("Late pickup", day="2026-11-03", start="20:00", end="20:30"),
                         acknowledge_conflicts=True)
    cruze = car_id(org, CRUZE)
    org.set_event_car(ALEX, book["id"], cruze)
    with pytest.raises(NeedsConfirmation, match="The Chevrolet Cruze is already needed for Book club, "
                                                "7:00 pm–9:00 pm"):
        org.set_event_car(SAM, late["id"], cruze)
    org.set_event_car(SAM, late["id"], cruze, use_anyway=True)
    assert org.get_event(book["id"])["car_double_booked"] and org.get_event(late["id"])["car_double_booked"]
    assert org.get_event(late["id"])["car_chosen_by"] == SAM  # CR-9
    org.set_event_car(SAM, late["id"], car_id(org, SUBARU))
    assert not org.get_event(book["id"])["car_double_booked"]


def test_all_day_and_repeating_events_hold_their_car(org: Organizer) -> None:  # CR-11
    trip = org.add_event(ALEX, EventInput("Field trip", "2026-11-03", "", "", "", "none", [ADMIN], all_day=True))
    swim = org.add_event(ALEX, event("Swim", day="2026-10-27", start="09:00", end="10:00", repeat="weekly"))
    org.set_event_car(ALEX, trip["id"], car_id(org, SUBARU))
    org.set_event_car(ALEX, swim["id"], car_id(org, CRUZE))
    lunch = org.add_event(ALEX, event("Lunch", day="2026-11-03", start="12:00", end="13:00"))
    assert [u["title"] for u in org.event_cars(ALEX, lunch["id"])[0]["in_use"]] == ["Field trip"]  # all day
    later = org.add_event(ALEX, event("Bank", day="2026-11-10", start="09:30", end="09:45"),
                          acknowledge_conflicts=True)
    assert [u["title"] for u in org.event_cars(ALEX, later["id"])[1]["in_use"]] == ["Swim"]  # the whole series


def test_car_choices_stay_in_the_app(org: Organizer, gcal: FakeCalendarGateway) -> None:  # CR-10, CR-12
    e = org.add_event(ALEX, event("Dentist"))
    sent = len(gcal.calls)
    org.set_event_car(ALEX, e["id"], car_id(org, SUBARU))
    assert gcal.calls[sent:] == []  # nothing goes to Google, so no email
    gid = gcal.google_add("coach@example.org", {"summary": "Team party", "attendees": [{"email": ADMIN}],
                                                "start": {"dateTime": "2026-11-05T18:00:00"},
                                                "end": {"dateTime": "2026-11-05T19:00:00"}})
    org.sync_google(ALEX)
    party = next(x for x in org.list_events()["upcoming"] if x["title"] == "Team party")
    assert gid and party["read_only"]
    with pytest.raises(OrganizerError, match="only be changed in Google Calendar"):
        org.set_event_car(ALEX, party["id"], car_id(org, SUBARU))
    history = [h for h in org.list_history() if h["label"] == "Dentist" and h["action"] == "edited"]
    assert history[0]["after"] == {"car": SUBARU}


# --- the Cars today bar (CB-a to CB-j) ------------------------------------------------------------------------

def test_the_bar_covers_midnight_to_midnight() -> None:  # CB-b
    parts = carbar.parts([], 2)
    assert [(p.start, p.end, p.level) for p in parts] == [(0, 1440, carbar.FREE)]
    assert [carbar.hour_label(h * 60) for h in (0, 6, 12, 18, 24)] == ["12 am", "6 am", "12 pm", "6 pm", "12 am"]


def test_green_yellow_red_for_two_cars() -> None:  # CB-c, CB-d
    uses = [use(1, 13 * 60, 15 * 60, "Dentist", "Subaru"), use(2, 14 * 60, 16 * 60, "Hockey", "Cruze")]
    parts = carbar.parts(uses, 2)
    assert [(p.start // 60, p.end // 60, p.level) for p in parts] == [
        (0, 13, carbar.FREE), (13, 14, carbar.SOME), (14, 15, carbar.ALL), (15, 16, carbar.SOME),
        (16, 24, carbar.FREE)]
    assert carbar.describe(parts[2]) == "2:00 pm–3:00 pm · Cruze: Hockey · Subaru: Dentist"  # CB-e


def test_one_car_is_green_or_red_and_three_cars_use_yellow() -> None:  # CB-c
    assert {p.level for p in carbar.parts([use(1, 60, 120)], 1)} == {carbar.FREE, carbar.ALL}
    three = carbar.parts([use(1, 60, 180), use(2, 120, 180)], 3)
    assert [p.level for p in three] == [carbar.FREE, carbar.SOME, carbar.SOME, carbar.FREE]
    assert carbar.parts([use(1, 60, 180), use(2, 60, 180), use(3, 60, 180)], 3)[1].level == carbar.ALL


def test_a_double_booked_car_is_red() -> None:  # CB-c
    parts = carbar.parts([use(1, 60, 120, "a"), use(1, 90, 150, "b")], 2)
    assert [(p.start, p.level) for p in parts] == [(0, carbar.FREE), (60, carbar.SOME), (90, carbar.ALL),
                                                   (120, carbar.SOME), (150, carbar.FREE)]


def test_legend_and_summary_follow_the_number_of_cars() -> None:  # CB-g, CB-h
    assert [t for _, t in carbar.legend(2)] == ["Both cars free", "One car in use", "Both cars in use"]
    assert [t for _, t in carbar.legend(1)] == ["The car is free", "The car is in use"]
    assert carbar.summary([], 2) == "Both cars are free today"
    assert carbar.summary([use(1, 0, 60)], 2) == "1 of 2 cars needed today"


def test_todays_car_uses_and_the_digest_line(org: Organizer) -> None:  # CB-a, CB-j
    e = org.add_event(ALEX, event("Dentist", day=TODAY, start="13:00", end="15:00"))
    org.set_event_car(ALEX, e["id"], car_id(org, SUBARU))
    trip = org.add_event(ALEX, EventInput("Field trip", TODAY, "", "", "", "none", [ADMIN], all_day=True))
    org.set_event_car(ALEX, trip["id"], car_id(org, CRUZE))
    view = org.today_view()
    assert [(u["car"], u["title"], u["start"], u["end"]) for u in view["cars_today"]] == [
        (CRUZE, "Field trip", 0, 1440), (SUBARU, "Dentist", 13 * 60, 15 * 60)]
    _, text, _ = build_digest(view, ALEX)
    assert "Subaru Crosstrek: 1:00 pm–3:00 pm Dentist" in text and "Chevrolet Cruze: all day Field trip" in text


# --- groceries (GR-1 to GR-11) --------------------------------------------------------------------------------

def test_add_tick_untick_in_order(org: Organizer) -> None:  # GR-2, GR-3, GR-6
    milk = org.add_grocery(ALEX, "Milk (2 L)")
    org.add_grocery(SAM, "Bread")
    with pytest.raises(NeedsConfirmation, match="already on the list"):
        org.add_grocery(SAM, "milk (2 l)")
    org.add_grocery(SAM, "Milk (2 L)", allow_duplicate=True)  # allowed after the warning
    org.set_grocery_bought(SAM, milk["id"], True)
    lists = org.list_groceries()
    assert [i["text"] for i in lists["to_buy"]] == ["Bread", "Milk (2 L)"]
    assert [i["text"] for i in lists["bought"]] == ["Milk (2 L)"] and lists["bought"][0]["added_by"] == ALEX
    org.set_grocery_bought(SAM, milk["id"], False)
    assert len(org.list_groceries()["to_buy"]) == 3
    with pytest.raises(OrganizerError, match="up to 100"):
        org.add_grocery(ALEX, "x" * 101)


def test_remove_and_clear_bought_with_undo(org: Organizer) -> None:  # GR-4, GR-5
    eggs = org.add_grocery(ALEX, "Eggs")
    jam = org.add_grocery(ALEX, "Jam")
    org.set_grocery_bought(ALEX, jam["id"], True)
    removed = org.remove_groceries(ALEX, [eggs["id"]])
    assert org.list_groceries()["to_buy"] == []
    org.restore_groceries(ALEX, removed)
    assert [i["text"] for i in org.list_groceries()["to_buy"]] == ["Eggs"]
    cleared = org.remove_groceries(ALEX, None)  # Clear bought
    assert [i["text"] for i in cleared] == ["Jam"] and org.list_groceries()["bought"] == []
    org.restore_groceries(ALEX, cleared)
    assert [i["text"] for i in org.list_groceries()["bought"]] == ["Jam"]
    assert org.list_trash() == []  # never to Trash


def test_not_in_history_badge_counts_and_retention(org: Organizer, clock: FakeClock) -> None:  # GR-7, GR-8
    old = org.add_grocery(ALEX, "Flour")
    org.add_grocery(ALEX, "Rice")
    org.set_grocery_bought(ALEX, old["id"], True)
    assert org.list_history() == [h for h in org.list_history() if h["item_type"] != "grocery"]
    assert not any("Flour" in str(h) for h in org.list_history())
    assert org.today_view()["groceries_to_buy"] == 1
    clock.advance(days=31)
    org.purge()
    assert [i["text"] for i in org.list_groceries()["to_buy"]] == ["Rice"] and org.list_groceries()["bought"] == []


def test_groceries_arent_in_the_digest(org: Organizer) -> None:  # GR-9
    org.add_grocery(ALEX, "Bananas")
    _, text, html = build_digest(org.today_view(), ALEX)
    assert "Bananas" not in text and "Bananas" not in html
