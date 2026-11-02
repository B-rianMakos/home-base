"""Demo mode (WO-FS-015 RN-3): a made-up family, so Home Base can be tried and screenshotted without any setup.

    python -m family_scheduler.hub demo

runs a hub in test mode (events stay on the hub, nobody is invited, no email is sent) with its data in
%TEMP%\\home-base-demo (or FS_DATA_DIR), and fills it with the people, events, tasks, reminders, cars and groceries
below the first time. Every name and address here is made up.
"""

from __future__ import annotations

from datetime import date, timedelta

from family_scheduler.organizer import EventInput, Organizer, TaskInput

ADMIN = "riley@example.com"
SECOND = "family@example.com"  # the second calendar account
MEMBERS = ("alex@example.com", "sam@example.com", "jordan@example.com")


def seed(org: Organizer, today: date) -> bool:
    """Fill an empty hub with the demo family. Returns False, changing nothing, if it already has a family."""
    if not org.needs_setup():
        return False
    org.set_up_family(ADMIN, SECOND)
    for account in (ADMIN, SECOND):
        org.set_account_status(account, "connected")  # test mode: both "connected", nothing reaches Google
    for email in MEMBERS:
        org.add_member(ADMIN, email)
    alex, sam, jordan = MEMBERS
    everyone = [ADMIN, *MEMBERS]

    def day(n: int) -> str:
        return (today + timedelta(days=n)).isoformat()

    events = [
        EventInput("Soccer practice", day(0), "16:15", "17:30", "Community field", "weekly", [ADMIN, sam]),
        EventInput("Dentist check-up", day(1), "15:00", "15:45", "Smile Dental", "none", [alex, ADMIN]),
        EventInput("Piano lesson", day(2), "16:30", "17:15", "Music school", "weekly", [jordan]),
        EventInput("Family dinner", day(3), "18:00", "20:00", "", "none", everyone),
        EventInput("Library day", day(4), "", "", "", "none", everyone, all_day=True),
        EventInput("Book club", day(6), "19:00", "21:00", "Corner café", "monthly", [ADMIN]),
    ]
    added = [org.add_event(ADMIN, e, acknowledge_conflicts=True) for e in events]
    org.set_cars(ADMIN, ["Blue hatchback", "Grey minivan"])
    cars = org.list_cars()
    org.set_event_car(ADMIN, added[0]["id"], cars[0]["id"])
    org.set_event_car(ADMIN, added[1]["id"], cars[1]["id"])
    for who, inp in [
        (alex, TaskInput("Sign the field-trip form", day(-1), "high")),
        (sam, TaskInput("Take out recycling", day(1), "medium", "weekly")),
        (ADMIN, TaskInput("Book winter tire swap", day(9), "high")),
        (jordan, TaskInput("Clean the gutters", day(14), "medium")),
        (alex, TaskInput("Pay the rent", day(20), "low", "monthly")),
        (ADMIN, TaskInput("Keep the first-aid kit stocked", "", "low")),
    ]:
        org.add_task(who, inp)
    gone = org.add_task(sam, TaskInput("Buy a new garden hose", day(10), "low"))
    org.remove_task(sam, gone["id"])  # something in Trash
    for who, text in [(ADMIN, "Guest Wi-Fi password is on the fridge."),
                      (alex, "Bring reusable bags to the Saturday market."),
                      (jordan, "Sam is allergic to tree nuts. Tell any new babysitter.")]:
        org.add_reminder(who, text)
    for who, text, category in [(sam, "Milk", "food"), (ADMIN, "Bread", "food"), (alex, "Apples", "food"),
                                (jordan, "Dish soap", "item"), (ADMIN, "Light bulbs", "item")]:
        org.add_grocery(who, text, category=category)
    return True
