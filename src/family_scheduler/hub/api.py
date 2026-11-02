"""The client–hub API (architecture section 4). Thin: each route calls one Organizer command.

No `from __future__ import annotations` here: FastAPI must see the real `Member` dependency type.
"""

import contextlib
import ipaddress
import os
import re
import socket
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from family_scheduler import __version__
from family_scheduler.google_calendar import CalendarError
from family_scheduler.hub.context import HubContext
from family_scheduler.organizer import EventInput, OrganizerError, TaskInput

_INSTALLER = re.compile(r"^(?:HomeBase|FamilyScheduler)Setup-(\d+)\.(\d+)\.(\d+)\.exe$")  # HB-2: either name

# Spelled out rather than ipaddress.is_private, which also counts documentation and reserved ranges.
_HOME_NETWORKS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
)]


def is_home_network(host: str) -> bool:
    """PC-5: only loopback and private (home network) addresses may talk to the hub."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in _HOME_NETWORKS)


_own: tuple[float, frozenset[str]] = (0.0, frozenset())


def own_addresses() -> frozenset[str]:
    """Every address of this PC (WO-FS-006 CB-2), re-read once a minute in case the network changes."""
    global _own
    if time.monotonic() - _own[0] < 60:
        return _own[1]
    found: set[str] = set()
    with contextlib.suppress(OSError):
        name = socket.gethostname()
        found.update(str(info[4][0]).split("%")[0] for info in socket.getaddrinfo(name, None))
        found.update(socket.gethostbyname_ex(name)[2])
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))  # no packet is sent; this finds the address used on the home network
        found.add(probe.getsockname()[0])
    _own = (time.monotonic(), frozenset(found))
    return _own[1]


_task: tuple[float, bool] = (0.0, False)


def started_with_windows() -> bool:
    """PS-1, PS-9: is the hub's startup task in Windows Task Scheduler? Checked once a minute."""
    global _task
    if time.monotonic() - _task[0] < 60:
        return _task[1]
    found = False
    if sys.platform == "win32":
        import subprocess

        result = subprocess.run(["schtasks", "/Query", "/TN", "Home Base Hub"], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        found = result.returncode == 0
    _task = (time.monotonic(), found)
    return found


def hub_status(ctx: HubContext) -> dict[str, Any]:
    """PS-9: what Family & Settings shows under This PC."""
    backups = sorted((ctx.data_dir / "backups").glob("family-*.db"), key=lambda p: p.stat().st_mtime)
    last = datetime.fromtimestamp(backups[-1].stat().st_mtime, UTC).isoformat(timespec="seconds") if backups else None
    return {"data_dir": str(ctx.data_dir), "test_hub": bool(os.environ.get("FS_DATA_DIR")),
            "started_with_windows": started_with_windows(), "last_backup": last}


def is_this_pc(host: str) -> bool:
    """CB-2: the app on the hub PC may reach the hub at 127.0.0.1 or at any of the PC's own addresses."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_loopback or str(addr) in own_addresses()


# --- request bodies ---------------------------------------------------------------------------

class EventBody(BaseModel):
    title: str
    date: str
    start_time: str = ""
    end_time: str = ""
    location: str = ""
    repeat: str = "none"
    invitees: list[str] = Field(default_factory=list)
    acknowledge_conflicts: bool = False
    all_day: bool = False  # WO-FS-008 AD-1

    def to_input(self) -> EventInput:
        return EventInput(self.title, self.date, self.start_time, self.end_time, self.location, self.repeat,
                          list(self.invitees), all_day=self.all_day)


class TaskBody(BaseModel):
    title: str
    due_date: str
    priority: str = "medium"
    repeat: str = "none"

    def to_input(self) -> TaskInput:
        return TaskInput(self.title, self.due_date, self.priority, self.repeat)


class ReminderBody(BaseModel):
    message: str


class EmailBody(BaseModel):
    email: str


class RestoreItem(BaseModel):
    type: str
    id: str


class RestoreBody(BaseModel):
    items: list[RestoreItem]


class SetupBody(BaseModel):
    admin: str
    second: str = ""
    members: list[str] = []


class SecondAccountBody(BaseModel):
    email: str = ""


class CarsBody(BaseModel):
    names: list[str]
    remove_anyway: bool = False


class EventCarBody(BaseModel):
    car_id: int | None = None
    use_anyway: bool = False


class GroceryBody(BaseModel):
    text: str
    category: str = "food"
    allow_duplicate: bool = False


class GroceryPlaceBody(BaseModel):
    category: str
    index: int | None = None


class GroceryMoveBody(BaseModel):
    step: int


class BoughtBody(BaseModel):
    bought: bool


class GroceryIdsBody(BaseModel):
    ids: list[str] | None = None


class GroceryRestoreBody(BaseModel):
    items: list[dict[str, Any]]


class GoogleConnectBody(BaseModel):
    token_json: str
    account_email: str  # who Google signed in
    calendar_account: str | None = None  # who the member chose to connect (GA-5)


class ClientFileBody(BaseModel):
    client_json: str


class AppPasswordBody(BaseModel):
    password: str


def create_app(ctx: HubContext, updates_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Home Base Hub", version=__version__, docs_url=None, redoc_url=None)
    org = ctx.organizer
    updates = updates_dir or ctx.data_dir / "updates"

    @app.middleware("http")
    async def home_network_only(request: Request, call_next: Any) -> Any:
        host = request.client.host if request.client else ""
        if not is_home_network(host):
            return JSONResponse({"detail": "The hub only accepts connections from the home network."},
                                status_code=403)
        return await call_next(request)

    @app.exception_handler(OrganizerError)
    async def organizer_error(_: Request, err: OrganizerError) -> JSONResponse:
        return JSONResponse({"detail": err.message, **err.extra}, status_code=err.status)

    def member(x_member: Annotated[str, Header()] = "") -> str:
        return org.require_member(x_member)

    def this_pc_only(request: Request) -> None:
        if not is_this_pc(request.client.host if request.client else ""):
            raise HTTPException(403, "Do this on the hub PC itself. Secrets never leave the hub.")

    Member = Annotated[str, Depends(member)]
    LocalOnly = Depends(this_pc_only)

    # --- status ---------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"app": "family-scheduler-hub", "version": __version__}

    @app.get("/api/changes")
    def changes() -> dict[str, int]:
        return {"seq": org.change_seq()}

    # --- members and settings -------------------------------------------------------------

    @app.get("/api/members")
    def members() -> list[dict[str, Any]]:
        return org.list_members()

    @app.post("/api/members")
    def add_member(body: EmailBody, who: Member) -> dict[str, Any]:
        return org.add_member(who, body.email)

    @app.delete("/api/members/{email}")
    def remove_member(email: str, who: Member) -> dict[str, bool]:
        org.remove_member(who, email)
        return {"ok": True}

    @app.get("/api/settings")
    def settings(request: Request, who: Member) -> dict[str, Any]:
        return {
            "admin_email": org.admin_email(),
            "google_connected": ctx.google_connected(),
            "calendar_accounts": org.calendar_accounts(),  # GA-5
            "google_mode": "fake" if ctx.fake_calendar else "google",
            "google_client_set": ctx.client_file_set(),  # OA-3
            "hub_status": hub_status(ctx),  # WO-FS-008 PS-9
            "mail_configured": ctx.mail_configured(),
            "hub_version": __version__,
            "on_hub_pc": is_this_pc(request.client.host if request.client else ""),
        }

    @app.get("/api/setup")
    def setup_state() -> dict[str, bool]:
        """WO-FS-010 AC-4: a new hub has no admin yet; the app then shows Set up your family first."""
        return {"needs_setup": org.needs_setup()}

    @app.post("/api/setup")
    def set_up(body: SetupBody) -> dict[str, Any]:
        org.set_up_family(body.admin, body.second, body.members)
        return {"ok": True, "members": org.list_members()}

    @app.put("/api/settings/second-account", dependencies=[LocalOnly])
    def set_second(body: SecondAccountBody, who: Member) -> dict[str, bool]:
        """AC-5: the second calendar account is chosen on the hub PC."""
        org.set_second_account(who, body.email)
        return {"ok": True}

    @app.put("/api/settings/admin-email")
    def set_admin(body: EmailBody, who: Member) -> dict[str, bool]:
        org.set_admin_email(who, body.email, google_connected=ctx.google_connected() and not ctx.fake_calendar)
        return {"ok": True}

    @app.post("/api/google/connect", dependencies=[LocalOnly])
    def google_connect(body: GoogleConnectBody, who: Member) -> dict[str, bool]:
        """GA-1, GA-2: only the calendar accounts, each connected on the hub PC."""
        signed_in = body.account_email.strip().lower()
        wanted = (body.calendar_account or signed_in).strip().lower()
        if signed_in != wanted:
            raise HTTPException(400, f"Google signed you in as {signed_in}. Connect again and choose {wanted} "
                                     "on Google's sign-in page.")
        ctx.connect_google(org.require_calendar_account(signed_in), body.token_json)
        return {"ok": True}

    @app.post("/api/google/disconnect", dependencies=[LocalOnly])
    def google_disconnect(body: EmailBody, who: Member) -> dict[str, Any]:
        note = ctx.disconnect_google(body.email)  # CB-5
        return {"ok": True, "note": note}

    @app.put("/api/google/client", dependencies=[LocalOnly])
    def google_client_set(body: ClientFileBody, who: Member) -> dict[str, bool]:
        """OA-2, OA-3: the Desktop app client file, chosen once on the hub PC."""
        try:
            ctx.set_client_file(body.client_json)
        except CalendarError as err:
            raise HTTPException(400, str(err)) from err
        return {"ok": True}

    @app.get("/api/google/client", dependencies=[LocalOnly])
    def google_client_get(who: Member) -> dict[str, Any]:
        client = ctx.client_file()
        if client is None:
            raise HTTPException(404, "Choose the Google client file first.")
        return {"client": client}

    @app.get("/api/calendar/sender")
    def calendar_sender(who: Member) -> dict[str, Any]:
        """GS-2, GS-3: which account would send this member's new event, or why none can."""
        try:
            return org.sender_for(who) | {"error": None}
        except OrganizerError as err:
            return {"email": None, "preferred": None, "fallback": False, "error": err.message}

    @app.put("/api/mail/app-password", dependencies=[LocalOnly])
    def app_password(body: AppPasswordBody, who: Member) -> dict[str, bool]:
        cleaned = body.password.replace(" ", "")
        if len(cleaned) != 16 or not cleaned.isalpha():
            raise HTTPException(400, "A Gmail app password is 16 letters, shown by Google in four groups of four.")
        ctx.set_app_password(cleaned)
        return {"ok": True}

    # --- Today and digest -----------------------------------------------------------------

    @app.get("/api/today")
    def today(who: Member) -> dict[str, Any]:
        return org.today_view()

    @app.get("/api/week")
    def week(who: Member, start: str | None = None, days: int = 7) -> dict[str, Any]:
        """The Calendar page: `days` days from the Monday on or before `start` (YYYY-MM-DD), or this week.
        7 for the week view; 35 or 42 for the month view (WO-FS-006)."""
        try:
            first = date.fromisoformat(start) if start else None
        except ValueError as err:
            raise HTTPException(400, "Give the week as a date, like 2026-10-26.") from err
        if not 1 <= days <= 42:
            raise HTTPException(400, "Ask for 1 to 42 days.")
        return org.week_view(first, days)

    @app.post("/api/digest/send")
    def send_digest(who: Member) -> dict[str, Any]:
        return org.send_digest(who)

    @app.post("/api/digest/test")
    def send_test_email(who: Member) -> dict[str, Any]:
        """WO-FS-007 EL-5: check the Gmail setup with a short message to the admin address."""
        return org.send_test_email(who)

    # --- events ---------------------------------------------------------------------------

    @app.get("/api/events")
    def events(who: Member) -> dict[str, Any]:
        return org.list_events()

    @app.post("/api/events/check")
    def check(body: EventBody, who: Member, exclude_id: str | None = None) -> dict[str, Any]:
        return {"conflicts": org.check_conflicts(who, body.to_input(), exclude_id)}

    @app.post("/api/events")
    def add_event(body: EventBody, who: Member) -> dict[str, Any]:
        return org.add_event(who, body.to_input(), acknowledge_conflicts=body.acknowledge_conflicts)

    @app.put("/api/events/{event_id}")
    def edit_event(event_id: str, body: EventBody, who: Member) -> dict[str, Any]:
        return org.edit_event(who, event_id, body.to_input(), acknowledge_conflicts=body.acknowledge_conflicts)

    @app.delete("/api/events/{event_id}")
    def remove_event(event_id: str, who: Member) -> dict[str, bool]:
        org.remove_event(who, event_id)
        return {"ok": True}

    @app.post("/api/events/{event_id}/retry")
    def retry(event_id: str, who: Member) -> dict[str, Any]:
        return {"event": org.retry_sync(who, event_id)}

    @app.post("/api/events/{event_id}/discard")
    def discard(event_id: str, who: Member) -> dict[str, bool]:
        org.discard_event(who, event_id)
        return {"ok": True}

    @app.post("/api/rsvp/refresh")
    def refresh(who: Member) -> dict[str, Any]:
        return org.refresh_rsvps(who)

    @app.post("/api/sync")
    def sync(who: Member) -> dict[str, Any]:
        """WO-FS-010 GY-2: Sync with Google: new, changed and removed events and replies, in one go."""
        return org.sync_google(who)

    # --- cars (WO-FS-010 section 9) --------------------------------------------------------

    @app.get("/api/cars")
    def cars(who: Member) -> list[dict[str, Any]]:
        return org.list_cars()

    @app.put("/api/cars")
    def set_cars(body: CarsBody, who: Member) -> list[dict[str, Any]]:
        return org.set_cars(who, body.names, remove_anyway=body.remove_anyway)

    @app.get("/api/events/hidden")
    def hidden_events(who: Member) -> list[dict[str, Any]]:
        """WO-FS-011 HE-7: the Hidden list."""
        return org.list_hidden()

    @app.post("/api/events/hidden/delete-all")
    def delete_all_hidden(who: Member) -> dict[str, Any]:
        """WO-FS-013 DH-6: every hidden event out of Home Base; Google Calendar isn't touched."""
        return org.delete_all_hidden(who)

    @app.post("/api/events/{event_id}/hide")
    def hide_event(event_id: str, who: Member) -> dict[str, bool]:
        """HE-1 to HE-6: out of the app only; Google Calendar isn't touched."""
        org.hide_event(who, event_id)
        return {"ok": True}

    @app.post("/api/events/{event_id}/delete-from-app")
    def delete_from_app(event_id: str, who: Member) -> dict[str, bool]:
        """1.9.1: a Google event, or (WO-FS-013) any hidden event, out of Home Base only; Google isn't touched."""
        org.delete_from_app(who, event_id)
        return {"ok": True}

    @app.post("/api/events/{event_id}/undo-delete-from-app")
    def undo_delete_from_app(event_id: str, who: Member) -> dict[str, Any]:
        return org.undo_delete_from_app(who, event_id)

    @app.post("/api/events/{event_id}/show")
    def show_event(event_id: str, who: Member) -> dict[str, Any]:
        return org.show_event(who, event_id)

    @app.get("/api/events/{event_id}/cars")
    def event_cars(event_id: str, who: Member) -> list[dict[str, Any]]:
        return org.event_cars(who, event_id)

    @app.put("/api/events/{event_id}/car")
    def set_event_car(event_id: str, body: EventCarBody, who: Member) -> dict[str, Any]:
        return org.set_event_car(who, event_id, body.car_id, use_anyway=body.use_anyway)

    # --- groceries (WO-FS-010 section 10) ----------------------------------------------------

    @app.get("/api/groceries")
    def groceries(who: Member) -> dict[str, Any]:
        return org.list_groceries()

    @app.post("/api/groceries")
    def add_grocery(body: GroceryBody, who: Member) -> dict[str, Any]:
        return org.add_grocery(who, body.text, category=body.category, allow_duplicate=body.allow_duplicate)

    @app.put("/api/groceries/{item_id}/place")
    def place_grocery(item_id: str, body: GroceryPlaceBody, who: Member) -> dict[str, bool]:
        """WO-FS-011 GG-4, GO-3: a group and a place in it (a drag, or Move to Food / Move to Items)."""
        org.place_grocery(who, item_id, body.category, body.index)
        return {"ok": True}

    @app.post("/api/groceries/{item_id}/move")
    def move_grocery(item_id: str, body: GroceryMoveBody, who: Member) -> dict[str, bool]:
        """GO-2: one place up or down."""
        org.move_grocery(who, item_id, body.step)
        return {"ok": True}

    @app.put("/api/groceries/{item_id}/bought")
    def grocery_bought(item_id: str, body: BoughtBody, who: Member) -> dict[str, bool]:
        org.set_grocery_bought(who, item_id, body.bought)
        return {"ok": True}

    @app.post("/api/groceries/remove")
    def remove_groceries(body: GroceryIdsBody, who: Member) -> dict[str, Any]:
        return {"removed": org.remove_groceries(who, body.ids)}

    @app.post("/api/groceries/restore")
    def restore_groceries(body: GroceryRestoreBody, who: Member) -> dict[str, bool]:
        org.restore_groceries(who, body.items)
        return {"ok": True}

    # --- tasks ----------------------------------------------------------------------------

    @app.get("/api/tasks")
    def tasks(who: Member) -> dict[str, Any]:
        return org.list_tasks()

    @app.post("/api/tasks")
    def add_task(body: TaskBody, who: Member) -> dict[str, Any]:
        return org.add_task(who, body.to_input())

    @app.put("/api/tasks/{task_id}")
    def edit_task(task_id: str, body: TaskBody, who: Member) -> dict[str, Any]:
        return org.edit_task(who, task_id, body.to_input())

    @app.delete("/api/tasks/{task_id}")
    def remove_task(task_id: str, who: Member) -> dict[str, bool]:
        org.remove_task(who, task_id)
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/complete")
    def complete(task_id: str, who: Member) -> dict[str, Any]:
        return org.complete_task(who, task_id)

    @app.post("/api/tasks/{task_id}/reopen")
    def reopen(task_id: str, who: Member) -> dict[str, Any]:
        return org.reopen_task(who, task_id)

    # --- reminders ------------------------------------------------------------------------

    @app.get("/api/reminders")
    def reminders(who: Member) -> list[dict[str, Any]]:
        return org.list_reminders()

    @app.post("/api/reminders")
    def add_reminder(body: ReminderBody, who: Member) -> dict[str, Any]:
        return org.add_reminder(who, body.message)

    @app.put("/api/reminders/{reminder_id}")
    def edit_reminder(reminder_id: str, body: ReminderBody, who: Member) -> dict[str, Any]:
        return org.edit_reminder(who, reminder_id, body.message)

    @app.delete("/api/reminders/{reminder_id}")
    def remove_reminder(reminder_id: str, who: Member) -> dict[str, bool]:
        org.remove_reminder(who, reminder_id)
        return {"ok": True}

    # --- Clear all, Trash, History --------------------------------------------------------

    @app.get("/api/clear/{list_name}/preview")
    def clear_preview(list_name: str, who: Member) -> dict[str, Any]:
        return org.clear_preview(list_name)

    @app.post("/api/clear/{list_name}")
    def clear_all(list_name: str, who: Member) -> dict[str, Any]:
        return org.clear_all(who, list_name)

    @app.post("/api/clear/{clear_id}/undo")
    def undo(clear_id: str, who: Member) -> dict[str, bool]:
        org.undo_clear(who, clear_id)
        return {"ok": True}

    @app.get("/api/trash")
    def trash(who: Member) -> list[dict[str, Any]]:
        return org.list_trash()

    @app.post("/api/trash/restore")
    def restore(body: RestoreBody, who: Member) -> dict[str, int]:
        return {"restored": org.restore(who, [(i.type, i.id) for i in body.items])}

    @app.post("/api/trash/delete")
    def delete_from_trash(body: RestoreBody, who: Member) -> dict[str, Any]:
        """WO-FS-014 DT-1, TK-a: one Trash item, or the ticked ones, deleted for good after the Undo time."""
        return org.delete_from_trash(who, [(i.type, i.id) for i in body.items])

    @app.post("/api/trash/clear")
    def clear_trash(who: Member) -> dict[str, Any]:
        """CT-1 to CT-4: everything in Trash."""
        return org.clear_trash(who)

    @app.post("/api/trash/undo/{batch}")
    def undo_trash_delete(batch: str, who: Member) -> dict[str, int]:
        return {"restored": org.undo_trash_delete(who, batch)}

    @app.get("/api/history")
    def history(who: Member, list_name: str | None = None, member_email: str | None = None) -> list[dict[str, Any]]:
        return org.list_history(list_name=list_name, member=member_email)

    @app.post("/api/history/clear")
    def clear_history(who: Member) -> dict[str, Any]:
        return org.clear_history(who)

    @app.delete("/api/history/{entry_id}")
    def delete_history(entry_id: int, who: Member) -> dict[str, Any]:
        return org.delete_history(who, entry_id)

    @app.post("/api/history/undo/{batch}")
    def undo_history_delete(batch: str, who: Member) -> dict[str, int]:
        return {"restored": org.undo_history_delete(who, batch)}

    # --- updates (PC-9) -------------------------------------------------------------------

    def latest_installer() -> tuple[tuple[int, ...], Path] | None:
        if not updates.exists():
            return None
        found = []
        for f in updates.iterdir():
            m = _INSTALLER.match(f.name)
            if m:
                found.append((tuple(int(x) for x in m.groups()), f))
        return max(found) if found else None

    @app.get("/api/update/latest")
    def update_latest() -> dict[str, Any]:
        latest = latest_installer()
        if latest is None:
            return {"version": __version__, "available": False}
        return {"version": ".".join(map(str, latest[0])), "available": True, "file": latest[1].name}

    @app.get("/api/update/download")
    def update_download() -> FileResponse:
        latest = latest_installer()
        if latest is None:
            raise HTTPException(404, "No installer has been placed on the hub.")
        return FileResponse(latest[1], filename=latest[1].name, media_type="application/octet-stream")

    return app
