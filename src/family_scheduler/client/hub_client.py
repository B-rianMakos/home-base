"""HTTP client for the hub API (architecture section 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

UNREACHABLE = "Can't reach the home hub. Make sure the hub PC is on."


class HubUnreachable(Exception):
    """PC-8: the hub is off, asleep or off the network."""


class HubError(Exception):
    def __init__(self, status: int, detail: str, extra: dict[str, Any]) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra


class HubClient:
    def __init__(self, address: str, member: str = "", timeout: float = 8.0) -> None:
        self.address = address
        self.member = member
        self._http = httpx.Client(base_url=f"http://{address}", timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
              timeout: float | None = None) -> Any:
        headers = {"X-Member": self.member} if self.member else {}
        try:
            response = self._http.request(method, path, json=json, params=params, headers=headers,
                                          timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
        except httpx.TransportError as err:
            raise HubUnreachable(UNREACHABLE) from err
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            detail = body.pop("detail", None) if isinstance(body, dict) else None
            if not isinstance(detail, str):
                detail = "The hub couldn't do that. Try again."
            raise HubError(response.status_code, detail, body if isinstance(body, dict) else {})
        return response.json()

    # status
    def health(self, timeout: float = 2.0) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", "/api/health", timeout=timeout)
        return result

    def changes(self) -> int:
        return int(self._call("GET", "/api/changes", timeout=3.0)["seq"])

    # members and settings
    def members(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("GET", "/api/members")
        return result

    def add_member(self, email: str) -> Any:
        return self._call("POST", "/api/members", json={"email": email})

    def remove_member(self, email: str) -> Any:
        return self._call("DELETE", f"/api/members/{email}")

    def settings(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", "/api/settings")
        return result

    def setup_state(self) -> dict[str, Any]:
        """WO-FS-010 AC-4: whether the hub still needs its family's details."""
        result: dict[str, Any] = self._call("GET", "/api/setup")
        return result

    def set_up(self, admin: str, second: str, members: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/api/setup",
                                            json={"admin": admin, "second": second, "members": members})
        return result

    def set_second_account(self, email: str) -> Any:
        return self._call("PUT", "/api/settings/second-account", json={"email": email})

    def set_admin_email(self, email: str) -> Any:
        return self._call("PUT", "/api/settings/admin-email", json={"email": email})

    def connect_google(self, token_json: str, account_email: str, calendar_account: str | None = None) -> Any:
        return self._call("POST", "/api/google/connect",
                          json={"token_json": token_json, "account_email": account_email,
                                "calendar_account": calendar_account})

    def disconnect_google(self, account: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/api/google/disconnect", json={"email": account})
        return result

    def calendar_sender(self) -> dict[str, Any]:
        """GS-3: which account sends this member's new events' invites."""
        result: dict[str, Any] = self._call("GET", "/api/calendar/sender")
        return result

    def set_app_password(self, password: str) -> Any:
        return self._call("PUT", "/api/mail/app-password", json={"password": password})

    # today and digest
    def today(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", "/api/today")
        return result

    def week(self, start: str | None = None, days: int = 7) -> dict[str, Any]:
        """The Calendar page: `days` days from the Monday `start` (YYYY-MM-DD); this week if no start."""
        params: dict[str, Any] = {"days": days}
        if start:
            params["start"] = start
        result: dict[str, Any] = self._call("GET", "/api/week", params=params)
        return result

    def set_client_file(self, client_json: str) -> Any:
        """OA-3: the Google Desktop app client file, kept on the hub."""
        return self._call("PUT", "/api/google/client", json={"client_json": client_json})

    def client_file(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", "/api/google/client")["client"]
        return result

    def send_test_email(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/api/digest/test", timeout=40.0)
        return result

    def send_digest(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/api/digest/send", timeout=40.0)
        return result

    def sync(self) -> dict[str, Any]:
        """WO-FS-010 GY-2: Sync with Google."""
        result: dict[str, Any] = self._call("POST", "/api/sync", timeout=60.0)
        return result

    # cars (WO-FS-010 section 9)
    def cars(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("GET", "/api/cars")
        return result

    def set_cars(self, names: list[str], remove_anyway: bool = False) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("PUT", "/api/cars",
                                                  json={"names": names, "remove_anyway": remove_anyway})
        return result

    def event_cars(self, event_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("GET", f"/api/events/{event_id}/cars")
        return result

    def set_event_car(self, event_id: str, car_id: int | None, use_anyway: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = self._call("PUT", f"/api/events/{event_id}/car",
                                            json={"car_id": car_id, "use_anyway": use_anyway})
        return result

    # groceries (WO-FS-010 section 10)
    def groceries(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", "/api/groceries")
        return result

    def add_grocery(self, text: str, category: str = "food", allow_duplicate: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/api/groceries", json={
            "text": text, "category": category, "allow_duplicate": allow_duplicate})
        return result

    def place_grocery(self, item_id: str, category: str, index: int | None = None) -> Any:
        return self._call("PUT", f"/api/groceries/{item_id}/place", json={"category": category, "index": index})

    def move_grocery(self, item_id: str, step: int) -> Any:
        return self._call("POST", f"/api/groceries/{item_id}/move", json={"step": step})

    # hidden events (WO-FS-011 section 3)
    def hidden_events(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("GET", "/api/events/hidden")
        return result

    def hide_event(self, event_id: str) -> Any:
        return self._call("POST", f"/api/events/{event_id}/hide")

    def delete_from_app(self, event_id: str) -> Any:
        return self._call("POST", f"/api/events/{event_id}/delete-from-app")

    def delete_all_hidden(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/api/events/hidden/delete-all")
        return result

    def undo_delete_from_app(self, event_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", f"/api/events/{event_id}/undo-delete-from-app")
        return result

    def show_event(self, event_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", f"/api/events/{event_id}/show")
        return result

    def set_grocery_bought(self, item_id: str, bought: bool) -> Any:
        return self._call("PUT", f"/api/groceries/{item_id}/bought", json={"bought": bought})

    def remove_groceries(self, ids: list[str] | None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("POST", "/api/groceries/remove", json={"ids": ids})["removed"]
        return result

    def restore_groceries(self, items: list[dict[str, Any]]) -> Any:
        return self._call("POST", "/api/groceries/restore", json={"items": items})

    # events
    def events(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", "/api/events")
        return result

    def add_event(self, body: dict[str, Any]) -> Any:
        return self._call("POST", "/api/events", json=body, timeout=40.0)

    def edit_event(self, event_id: str, body: dict[str, Any]) -> Any:
        return self._call("PUT", f"/api/events/{event_id}", json=body, timeout=40.0)

    def remove_event(self, event_id: str) -> Any:
        return self._call("DELETE", f"/api/events/{event_id}", timeout=40.0)

    def retry_event(self, event_id: str) -> Any:
        return self._call("POST", f"/api/events/{event_id}/retry", timeout=40.0)

    def discard_event(self, event_id: str) -> Any:
        return self._call("POST", f"/api/events/{event_id}/discard")

    def refresh_rsvps(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/api/rsvp/refresh", timeout=120.0)
        return result

    # tasks
    def tasks(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", "/api/tasks")
        return result

    def add_task(self, body: dict[str, Any]) -> Any:
        return self._call("POST", "/api/tasks", json=body)

    def edit_task(self, task_id: str, body: dict[str, Any]) -> Any:
        return self._call("PUT", f"/api/tasks/{task_id}", json=body)

    def remove_task(self, task_id: str) -> Any:
        return self._call("DELETE", f"/api/tasks/{task_id}")

    def complete_task(self, task_id: str) -> Any:
        return self._call("POST", f"/api/tasks/{task_id}/complete")

    def reopen_task(self, task_id: str) -> Any:
        return self._call("POST", f"/api/tasks/{task_id}/reopen")

    # reminders
    def reminders(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("GET", "/api/reminders")
        return result

    def add_reminder(self, message: str) -> Any:
        return self._call("POST", "/api/reminders", json={"message": message})

    def edit_reminder(self, reminder_id: str, message: str) -> Any:
        return self._call("PUT", f"/api/reminders/{reminder_id}", json={"message": message})

    def remove_reminder(self, reminder_id: str) -> Any:
        return self._call("DELETE", f"/api/reminders/{reminder_id}")

    # clear all, trash, history
    def clear_preview(self, list_name: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", f"/api/clear/{list_name}/preview")
        return result

    def clear_all(self, list_name: str) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", f"/api/clear/{list_name}")
        return result

    def undo_clear(self, clear_id: str) -> Any:
        return self._call("POST", f"/api/clear/{clear_id}/undo")

    def trash(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("GET", "/api/trash")
        return result

    def delete_from_trash(self, items: list[dict[str, str]]) -> Any:
        return self._call("POST", "/api/trash/delete", json={"items": items})

    def clear_trash(self) -> Any:
        return self._call("POST", "/api/trash/clear")

    def undo_trash_delete(self, batch: str) -> Any:
        return self._call("POST", f"/api/trash/undo/{batch}")

    def restore(self, items: list[dict[str, str]]) -> Any:
        return self._call("POST", "/api/trash/restore", json={"items": items})

    def delete_history(self, entry_id: int) -> Any:
        return self._call("DELETE", f"/api/history/{entry_id}")

    def clear_history(self) -> Any:
        return self._call("POST", "/api/history/clear")

    def undo_history_delete(self, batch: str) -> Any:
        return self._call("POST", f"/api/history/undo/{batch}")

    def history(self, list_name: str | None = None, member: str | None = None) -> list[dict[str, Any]]:
        params = {k: v for k, v in {"list_name": list_name, "member_email": member}.items() if v}
        result: list[dict[str, Any]] = self._call("GET", "/api/history", params=params)
        return result

    # updates
    def update_latest(self) -> dict[str, Any]:
        result: dict[str, Any] = self._call("GET", "/api/update/latest")
        return result

    def download_update(self, target: Path) -> None:
        try:
            with self._http.stream("GET", "/api/update/download", timeout=300.0) as response:
                response.raise_for_status()
                with target.open("wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
        except httpx.TransportError as err:
            raise HubUnreachable(UNREACHABLE) from err
