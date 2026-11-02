# Home Base

Home Base is a free family organizer for Windows. Every PC in the house shares the same events, tasks, reminders,
cars and groceries over home Wi-Fi. There are no accounts and no paid services, and the data stays at home.

**[See how Home Base works, with screenshots and diagrams →](https://b-rianmakos.github.io/home-base/)**

![Today](docs/screens/today.png)

## What it does

- **Events.** Each event has a time, place, guest list and car. Events go out as Google Calendar invites, and the
  app shows who accepted, declined or hasn't replied yet.
- **Clash warnings.** The app warns you before an event overlaps another one or double-books a car.
- **Tasks by priority.** High, Medium and Low lists, plus Overdue at the top. A task due within 7 days counts as
  high priority, and tasks can be ongoing with no due date.
- **Repeating items.** Events and tasks can repeat every day, week, month or year.
- **Reminders.** Short notes for the whole family, like the guest Wi-Fi password.
- **Cars.** Pick the car an event needs and see at a glance which cars are free today.
- **Groceries.** A shared shopping list, split into Food and Items.
- **Undo, Trash and History.** Clear all can be undone for 10 seconds, removed items wait 30 days in Trash, and
  History shows who changed what.
- **Digest email.** Email yourself a summary of the day, only when you ask for one.
- **Calendar.** A week view and a month view of events and tasks.
- **Live on every PC.** A change on one PC shows on the others within seconds.

| | |
|---|---|
| ![Calendar](docs/screens/calendar.png) | ![Tasks](docs/screens/tasks.png) |
| ![Add event](docs/screens/add-event.png) | ![Which car?](docs/screens/which-car.png) |

The screenshots use demo mode's made-up family.

## How it works

One PC in the house is the **home hub**. It stays on and keeps everything in a small SQLite database. Every other
PC runs the Home Base app, finds the hub on the home network by itself, and connects over Wi-Fi. When you open
the app, you pick your name and start, with no password. Google Calendar and Gmail are optional and use the
family's own free accounts.

Built with Python, PySide6 and SQLite.

## Set it up

You need Windows 10 or 11 on every PC, all on the same home Wi-Fi (set to **Private** in Windows), and one PC
that can stay on.

1. **Get the installer.** Build `HomeBaseSetup-X.Y.Z.exe` with `tools\build_release.py` (see [SETUP.md](SETUP.md)).
2. **Set up the hub.** On the PC that stays on, run the installer and choose **This PC is the home hub**. It
   starts by itself with Windows from then on.
3. **Add your family.** Open Home Base. The first time, it asks for the admin's Gmail address and the other
   family members' addresses.
4. **Add the other PCs.** On every other PC, run the same installer and choose **Connect to the home hub**. The
   app finds the hub by itself.
5. **Pick your name and go.**
6. **Optional: Google Calendar invites and the digest email.** On the hub PC, open **Family & Settings →
   Setup guide** and follow the steps. It takes about 20 minutes, once.

**Just want to look around first?** Demo mode runs the app with a made-up family, and invites no one:

```powershell
git clone https://github.com/B-rianMakos/home-base.git home-base
cd home-base
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m family_scheduler.hub demo
```

Then, in a second terminal in the same folder:

```powershell
$env:FS_HUB = "127.0.0.1"
.venv\Scripts\python -m family_scheduler.app
```

Full details, including running from source, Google setup, moving the hub and troubleshooting, are in
**[SETUP.md](SETUP.md)**.

## Licence

MIT (see `LICENSE`). Bundled third-party parts keep their own licences: `THIRD-PARTY-LICENSES.md`.
