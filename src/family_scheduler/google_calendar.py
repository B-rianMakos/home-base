"""Google Calendar adapter (WO-FS-001 section 4, WO-FS-002 sections 6 and 9, WO-FS-005 section 2).

`CalendarGateway` is what the Organizer uses, one per calendar account. `GoogleCalendarGateway` talks to Google;
`FakeCalendarGateway` keeps events in memory for tests and offline development.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Protocol

SCOPES = ["https://www.googleapis.com/auth/calendar.events", "openid",
          "https://www.googleapis.com/auth/userinfo.email"]

_BASE32HEX = "0123456789abcdefghijklmnopqrstuv"


class CalendarError(Exception):
    """A Google call failed. The message is shown to the member."""


class SyncTokenExpired(CalendarError):
    """Google no longer accepts a sync token (410 Gone): sync everything again from the start (GY-3)."""


class CalendarAuthError(CalendarError):
    """Google refused the account's token: it was revoked or has expired. The account needs reconnecting (GA-5)."""


def new_event_id() -> str:
    """Google accepts client-chosen ids of base32hex characters (architecture 4.3)."""
    return "".join(secrets.choice(_BASE32HEX) for _ in range(32))


class CalendarGateway(Protocol):
    def insert(self, body: dict[str, Any]) -> None: ...

    def patch(self, event_id: str, body: dict[str, Any]) -> None: ...

    def delete(self, event_id: str) -> None: ...

    def attendee_statuses(self, event_id: str) -> dict[str, str] | None:
        """Email -> Google responseStatus, or None if the event no longer exists."""
        ...

    def list_changes(self, sync_token: str | None, time_min: str) -> tuple[list[dict[str, Any]], str]:
        """WO-FS-010 GY-1, GY-3: the account's primary calendar. With no token, every event from `time_min` (RFC 3339)
        on; with a token, only what changed since, removed events included (status "cancelled"). Returns the events
        and the token for next time. Raises SyncTokenExpired when Google wants a full sync again."""
        ...


class _FakeState:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.senders: list[tuple[str, str, str]] = []  # (call, event id, account), for GS-1 and GS-4
        self.fail_next: str | None = None
        self.offline = False
        self.version = 0                              # bumped on every change, the fake's sync token
        self.changed: dict[str, int] = {}             # event id -> version of its last change
        self.removed: dict[str, dict[str, Any]] = {}  # removed events, still visible to incremental syncs


class FakeCalendarGateway:
    """In-memory calendar shared by both calendar accounts. `fail_next` makes the next call raise, to test error
    paths; `offline` makes every call fail, like a hub without internet. `as_account` is one account's view."""

    def __init__(self, state: _FakeState | None = None, account: str = "") -> None:
        self._s = state or _FakeState()
        self.account = account

    def as_account(self, email: str) -> FakeCalendarGateway:
        return FakeCalendarGateway(self._s, email)

    # Shared state, readable and settable from tests through any view.
    @property
    def events(self) -> dict[str, dict[str, Any]]:
        return self._s.events

    @property
    def calls(self) -> list[tuple[str, str]]:
        return self._s.calls

    @property
    def senders(self) -> list[tuple[str, str, str]]:
        return self._s.senders

    @property
    def fail_next(self) -> str | None:
        return self._s.fail_next

    @fail_next.setter
    def fail_next(self, value: str | None) -> None:
        self._s.fail_next = value

    @property
    def offline(self) -> bool:
        return self._s.offline

    @offline.setter
    def offline(self, value: bool) -> None:
        self._s.offline = value

    def _maybe_fail(self, op: str) -> None:
        if self._s.offline:
            raise CalendarError("Couldn't reach Google Calendar. Check the hub's internet connection and try again.")
        if self._s.fail_next is not None and self._s.fail_next in (op, "any"):
            self._s.fail_next = None
            raise CalendarError("Google Calendar didn't respond. Try again.")

    def _record(self, op: str, event_id: str) -> None:
        self._s.calls.append((op, event_id))
        self._s.senders.append((op, event_id, self.account))

    def _touch(self, event_id: str) -> None:
        self._s.version += 1
        self._s.changed[event_id] = self._s.version

    def _check_organizer(self, event_id: str) -> None:
        """Google only lets the organizer change an event or see all its replies (GS-4)."""
        owner = self._s.events.get(event_id, {}).get("organizer")
        if self.account and owner and owner != self.account:
            raise CalendarError("Shared properties can only be changed by the organizer of the event.")

    def insert(self, body: dict[str, Any]) -> None:
        self._maybe_fail("insert")
        self._record("insert", body["id"])
        if body["id"] in self._s.events:
            return  # 409 already exists: treated as success (idempotent create)
        stored = json.loads(json.dumps(body))
        stored["organizer"] = self.account
        for attendee in stored.get("attendees", []):
            attendee.setdefault("responseStatus", "needsAction")
        self._s.events[body["id"]] = stored
        self._touch(body["id"])

    def patch(self, event_id: str, body: dict[str, Any]) -> None:
        self._maybe_fail("patch")
        self._check_organizer(event_id)
        self._record("patch", event_id)
        if event_id not in self._s.events:
            raise CalendarError("That event no longer exists in Google Calendar.")
        current = self._s.events[event_id]
        old_status = {a["email"]: a.get("responseStatus", "needsAction") for a in current.get("attendees", [])}
        current.update(json.loads(json.dumps(body)))
        for attendee in current.get("attendees", []):
            attendee["responseStatus"] = old_status.get(attendee["email"], "needsAction")
        self._touch(event_id)

    def delete(self, event_id: str) -> None:
        self._maybe_fail("delete")
        self._check_organizer(event_id)
        self._record("delete", event_id)
        gone = self._s.events.pop(event_id, None)
        if gone is not None:
            self._s.removed[event_id] = gone
            self._touch(event_id)

    def attendee_statuses(self, event_id: str) -> dict[str, str] | None:
        self._maybe_fail("get")
        self._check_organizer(event_id)
        self._record("get", event_id)
        event = self._s.events.get(event_id)
        if event is None:
            return None
        return {a["email"]: a.get("responseStatus", "needsAction") for a in event.get("attendees", [])}

    def respond(self, event_id: str, email: str, status: str) -> None:
        """Test helper: an invitee answers in Google Calendar."""
        for attendee in self._s.events[event_id].get("attendees", []):
            if attendee["email"] == email:
                attendee["responseStatus"] = status
        self._touch(event_id)

    # --- changes made in Google Calendar itself (test helpers for WO-FS-010 section 8) ---------------------

    def google_add(self, organizer: str, body: dict[str, Any]) -> str:
        """Someone adds an event in Google Calendar (on a phone, say). `organizer` may be outside the family."""
        stored = json.loads(json.dumps(body))
        stored.setdefault("id", new_event_id())
        stored["organizer"] = organizer
        stored.setdefault("htmlLink", f"https://calendar.google.com/event?eid={stored['id']}")
        for attendee in stored.get("attendees", []):
            attendee.setdefault("responseStatus", "needsAction")
        self._s.events[stored["id"]] = stored
        self._touch(stored["id"])
        return str(stored["id"])

    def google_change(self, event_id: str, **fields: Any) -> None:
        """Someone changes an event in Google Calendar."""
        self._s.events[event_id].update(json.loads(json.dumps(fields)))
        self._touch(event_id)

    def google_remove(self, event_id: str) -> None:
        """Someone removes an event in Google Calendar."""
        self._s.removed[event_id] = self._s.events.pop(event_id)
        self._touch(event_id)

    def _sees(self, event: dict[str, Any]) -> bool:
        """An account's primary calendar holds the events it organizes and the ones it's invited to."""
        return not self.account or event.get("organizer") == self.account or \
            any(a["email"] == self.account for a in event.get("attendees", []))

    def list_changes(self, sync_token: str | None, time_min: str) -> tuple[list[dict[str, Any]], str]:
        self._maybe_fail("list")
        self._record("list", self.account)
        if sync_token == "expired":
            raise SyncTokenExpired("Google wants a full sync.")
        since = int(sync_token) if sync_token else -1
        items = []
        for event_id, version in self._s.changed.items():
            if version <= since:
                continue
            if event_id in self._s.events:
                event = self._s.events[event_id]
                if self._sees(event):
                    items.append({"status": "confirmed", **json.loads(json.dumps(event)),
                                  "organizer": {"email": event.get("organizer", "")}})
            elif since >= 0 and event_id in self._s.removed and self._sees(self._s.removed[event_id]):
                items.append({"id": event_id, "status": "cancelled"})
        return items, str(self._s.version)


class GoogleCalendarGateway:
    """The real thing: Calendar API v3 on one calendar account's primary calendar, invites sent by Google."""

    def __init__(self, token_json: str) -> None:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        info = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(info, SCOPES)  # type: ignore[no-untyped-call]
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def _run(self, request: Any, ok_statuses: tuple[int, ...] = ()) -> Any:
        from google.auth.exceptions import RefreshError
        from googleapiclient.errors import HttpError

        try:
            return request.execute(num_retries=3)
        except HttpError as err:
            if err.resp.status in ok_statuses:
                return None
            if err.resp.status == 401:
                raise CalendarAuthError(_explain(401)) from err
            raise CalendarError(_explain(err.resp.status)) from err
        except RefreshError as err:  # the account's access was revoked or has expired
            raise CalendarAuthError(_explain(401)) from err
        except Exception as err:  # network down, DNS
            raise CalendarError(
                "Couldn't reach Google Calendar. Check the hub's internet connection and try again."
            ) from err

    def insert(self, body: dict[str, Any]) -> None:
        events = self._service.events()
        self._run(events.insert(calendarId="primary", body=body, sendUpdates="all"), ok_statuses=(409,))

    def patch(self, event_id: str, body: dict[str, Any]) -> None:
        events = self._service.events()
        if "attendees" in body:
            # Patching replaces the attendee list; carry over answers so RSVPs aren't reset.
            current = self.attendee_statuses(event_id) or {}
            for attendee in body["attendees"]:
                if attendee["email"] in current:
                    attendee["responseStatus"] = current[attendee["email"]]
        self._run(events.patch(calendarId="primary", eventId=event_id, body=body, sendUpdates="all"))

    def delete(self, event_id: str) -> None:
        events = self._service.events()
        self._run(events.delete(calendarId="primary", eventId=event_id, sendUpdates="all"),
                  ok_statuses=(404, 410))

    def list_changes(self, sync_token: str | None, time_min: str) -> tuple[list[dict[str, Any]], str]:
        """GY-1, GY-3: events.list on the primary calendar, page by page, with Google's sync token."""
        from googleapiclient.errors import HttpError

        events = self._service.events()
        items: list[dict[str, Any]] = []
        page: str | None = None
        while True:
            args: dict[str, Any] = {"calendarId": "primary", "showDeleted": True, "maxResults": 250}
            if sync_token:
                args["syncToken"] = sync_token
            else:
                args["timeMin"] = time_min
            if page:
                args["pageToken"] = page
            try:
                result = events.list(**args).execute(num_retries=3)
            except HttpError as err:
                if err.resp.status == 410:
                    raise SyncTokenExpired("Google wants a full sync.") from err
                result = self._run(events.list(**args))  # the usual explanations and sign-in errors
            items.extend(result.get("items", []))
            page = result.get("nextPageToken")
            if not page:
                return items, str(result.get("nextSyncToken", ""))

    def attendee_statuses(self, event_id: str) -> dict[str, str] | None:
        events = self._service.events()
        result = self._run(events.get(calendarId="primary", eventId=event_id), ok_statuses=(404, 410))
        if result is None or result.get("status") == "cancelled":
            return None
        return {a["email"].lower(): a.get("responseStatus", "needsAction") for a in result.get("attendees", [])}


def _explain(status: int) -> str:
    if status == 401:
        return "Google no longer accepts this account's connection. Reconnect it on the hub PC under Family & Settings."
    if status == 403:
        return "Google refused the request. Only the account that created an event can change it."
    if status == 404:
        return "That event no longer exists in Google Calendar."
    if status == 429:
        return "Google is busy. Wait a minute and try again."
    return "Google Calendar didn't respond. Try again."


# --- the Google client file and signing in (WO-FS-006 OA-2, OA-3, CB-4 to CB-6) -----------------------------

WEB_CLIENT = ("This file is for a web application. In Google Cloud, create an OAuth client of type Desktop app and "
              "download that file instead.")
NOT_A_CLIENT = ("This isn't a Google client file. In Google Cloud, open Google Auth Platform, Clients, and download "
                "the JSON of the Desktop app client.")
SIGN_IN_TIMEOUT = 300  # seconds to finish signing in, so the app is never stuck waiting


class SignInError(Exception):
    """Signing in to Google didn't finish. The message says what to do (CB-6)."""


def check_client_file(text: str) -> dict[str, Any]:
    """OA-2: a Desktop app client file starts with {"installed": …}; a web one is refused with a clear message."""
    try:
        data = json.loads(text)
    except ValueError as err:
        raise CalendarError(NOT_A_CLIENT) from err
    if not isinstance(data, dict):
        raise CalendarError(NOT_A_CLIENT)
    if "web" in data:
        raise CalendarError(WEB_CLIENT)
    installed = data.get("installed")
    if not isinstance(installed, dict) or not all(installed.get(k) for k in ("client_id", "client_secret")):
        raise CalendarError(NOT_A_CLIENT)
    return data


def revoke_token(token_json: str) -> None:
    """CB-5: tell Google to forget the account's access, so the hub's copy is useless even if found later."""
    import httpx

    info = json.loads(token_json)
    token = info.get("refresh_token") or info.get("token")
    if not token:
        return
    try:
        response = httpx.post("https://oauth2.googleapis.com/revoke", data={"token": token}, timeout=15)
    except httpx.HTTPError as err:
        raise CalendarError("Couldn't reach Google to revoke the access. It was still removed from the hub.") from err
    if response.status_code not in (200, 400):  # 400: already revoked or expired
        raise CalendarError("Google didn't confirm the access was revoked. It was still removed from the hub.")


def run_admin_sign_in(client_config: dict[str, Any], account: str | None = None) -> tuple[str, str]:
    """Installed-app sign-in in the browser, on the hub PC. Returns (token JSON, account email).

    Google sends the browser back to a listener on this PC (http://localhost on a free port), so no website is
    needed (WO-FS-006 section 3.1). `account` pre-selects the Google account to sign in with (GA-2).
    """
    import logging

    from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
    from googleapiclient.discovery import build
    from oauthlib.oauth2.rfc6749.errors import AccessDeniedError, OAuth2Error  # type: ignore[import-untyped]

    log = logging.getLogger(__name__)
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    extra = {"login_hint": account} if account else {}
    try:
        creds = flow.run_local_server(host="localhost", bind_addr="127.0.0.1", port=0, prompt="consent",
                                      access_type="offline", timeout_seconds=SIGN_IN_TIMEOUT,
                                      authorization_prompt_message=None,
                                      success_message="Home Base is connected. You can close this tab.",
                                      **extra)
        info = build("oauth2", "v2", credentials=creds, cache_discovery=False).userinfo().get().execute()
    except AccessDeniedError as err:
        log.info("Google sign-in cancelled: %s", err)
        raise SignInError("Google sign-in was cancelled. Click Connect to try again.") from err
    except WSGITimeoutError as err:
        log.warning("Google sign-in timed out: %s", err)
        raise SignInError("The browser didn't return to the app within 5 minutes. Check that nothing is blocking "
                          "localhost (a firewall or VPN), then click Connect again.") from err
    except OSError as err:
        log.exception("Couldn't open the sign-in listener")
        raise SignInError("The app couldn't listen for Google's reply on this PC. Check that nothing is blocking "
                          "localhost, then try again.") from err
    except OAuth2Error as err:
        log.exception("Google sign-in failed")
        raise SignInError(f"Google refused the sign-in ({err.error}). Check the client file is the Desktop app "
                          "one, then try again.") from err
    except Exception as err:  # noqa: BLE001 - every other failure gets a plain message; details go to the log
        log.exception("Google sign-in failed")
        raise SignInError(f"Google sign-in didn't finish: {err}") from err
    return creds.to_json(), str(info["email"]).lower()
