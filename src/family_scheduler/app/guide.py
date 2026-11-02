"""The Setup guide's text and its live statuses (WO-FS-009 section 2). Plain data, no Qt.

SG-8: every word of the guide is here, so it can be changed without touching the page. It says the same as the
README's "Setting up the digest email", "Google setup → Part 2" and "Setting up the house". Change both together.
In the text, **bold** is bold, and a web address starting with https:// becomes a link with a copy button (SG-6).
WO-FS-010 TX-1: no address is written here. {admin} and {second} are filled in from the hub's settings when the page
opens (fill()), or read "the admin account" and "the second account" until they're set.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

URL = re.compile(r"https://[^\s)]+[^\s).,]")


@dataclass
class Section:
    key: str                      # "digest", "calendar" or "pc": also names the Family & Settings card it goes to
    title: str
    intro: str
    steps: list[tuple[str, list[str]]]  # (sub-heading, numbered steps)
    go_to: str                    # SG-7: the button that returns to the matching card
    table: list[tuple[str, str]] = field(default_factory=list)  # (problem, what to do)
    after: str = ""               # a closing line under the steps


SECTIONS = [
    Section(
        "digest", "Digest email",
        "The digest is the summary email from **Send me my digest** on Today. Gmail sends it from "
        "{admin} with an **app password**. No website or Google Cloud setup is needed. About 5 minutes.",
        [
            ("Turn on 2-Step Verification", [
                "Sign in to Google as {admin}.",
                "Open **Google Account → Security**: https://myaccount.google.com/security",
                "Under \"How you sign in to Google\", turn on **2-Step Verification** and follow the steps (a phone "
                "or an authenticator app).",
            ]),
            ("Create an app password", [
                "Go to https://myaccount.google.com/apppasswords (you may be asked to sign in again).",
                "Type the name **Home Base** and click **Create**.",
                "Google shows a 16-letter password **once**. Copy it. Spaces don't matter.",
            ]),
            ("Enter it here, on the hub PC", [
                "In **Family & Settings → Digest email**, paste the 16 letters and click **Save**. The card says "
                "\"Ready\". The password is kept encrypted on the hub and is never shown again.",
                "Click **Send test email**. A short message reaches {admin} within a minute.",
            ]),
            ("Send a first digest", [
                "On **Today**, click **Send me my digest**. It goes to whoever is using the app.",
                "Clicking it again within a minute doesn't send a second one.",
            ]),
        ],
        "Go to Digest email",
        [
            ("\"App passwords\" isn't there", "Turn on 2-Step Verification with a phone or authenticator app first. "
             "Accounts that sign in only with security keys, or use Advanced Protection, don't get app passwords."),
            ("\"The Gmail sign-in failed\"", "The app password was copied wrong or removed. Create a new one and save "
             "it again."),
            ("The email went to Spam", "Open it in Gmail and click **Not spam** once."),
            ("\"Daily digest limit reached\"", "20 emails a day, digests and test emails together. The summary is "
             "still on Today; sending works again tomorrow."),
            ("The password must be stopped", "Delete \"Home Base\" (or the older \"Family Scheduler\") at "
             "https://myaccount.google.com/apppasswords. Nothing is sent until a new one is saved."),
        ],
        "Keep the app password private: anyone who has it can send email from {admin}.",
    ),
    Section(
        "calendar", "Google Calendar",
        "Each calendar account's events go out as invites from that account; everyone else's go out from {admin}. "
        "Connect each account once, in the app on the hub PC.",
        [
            ("Check the client file", [
                "You need the Google client file (a .json file) from the Google Cloud setup in the README. It must "
                "be a **Desktop app** file: opened in Notepad, it starts with {\"installed\". A file that starts with "
                "{\"web\" won't work.",
            ]),
            ("Connect each account", [
                "Open Home Base **on the hub PC**: Connect only works there.",
                "In **Family & Settings → Calendar accounts**, click **Choose Google client file** and pick the "
                ".json file. The card says \"Google client file: set up\".",
                "Select {admin} and click **Connect**. The browser opens Google's sign-in.",
                "Sign in. Google says \"Google hasn't verified this app\": click **Advanced → Go to Home Base "
                "(unsafe)**, then **Continue**, and allow access to the calendar.",
                "The browser says \"Home Base is connected\", and the row says **Connected**.",
                "If there's a second account, select {second}, click **Connect**, choose **Use another account** and "
                "sign in as that person. Approve the same screens.",
            ]),
            ("Show the name Home Base on Google's sign-in page", [  # WO-FS-011 HB-6
                "Google's sign-in page shows the name set in the Google Cloud project. Only the admin can change it.",
                "In https://console.cloud.google.com, open **Google Auth Platform → Branding**.",
                "Set **App name** to **Home Base** and click **Save**. Both calendar accounts stay connected.",
            ]),
            ("If an account says \"Needs reconnecting\"", [
                "Google stopped accepting its sign-in: its password changed, access was removed in its Google "
                "Account, or the consent screen is in Testing mode, which signs accounts out every 7 days. Today "
                "shows a red notice when this happens.",
                "Select the account and click **Reconnect**, on the hub PC.",
            ]),
        ],
        "Go to Calendar accounts",
        [
            ("Connect is greyed out", "The line under the buttons says why: open the app on the hub PC, choose the "
             "client file first, or select an account."),
            ("\"This file is for a web application\"", "Create an OAuth client of type **Desktop app** in Google "
             "Cloud and download that file instead."),
            ("\"The browser didn't return to the app\"", "A VPN or firewall blocked the sign-in. Turn it off for a "
             "moment and click Connect again."),
        ],
    ),
    Section(
        "pc", "Adding another PC",
        "Every PC in the house runs the same app and connects to this home hub over the home Wi-Fi.",
        [
            ("Install on the other PC", [
                "Copy **HomeBaseSetup** to the other PC (a USB stick works) and run it.",
                "Windows may say \"Windows protected your PC\", because the installer isn't signed. Click **More "
                "info → Run anyway**.",
                "When asked which PC this is, choose **Connect to the home hub**.",
                "Open Home Base from the Desktop shortcut. It finds the hub by itself.",
            ]),
            ("If it can't find the hub", [
                "Type this PC's hub address (shown above, and in **Family & Settings → This PC**).",
                "On this PC, make sure the Wi-Fi is a **Private** network (Settings → Network → Wi-Fi).",
            ]),
        ],
        "Go to This PC",
    ),
]


def status(key: str, settings: dict[str, Any], address: str) -> tuple[str, bool | None]:
    """SG-5: a card's status from the hub's settings: (text, ready). ready is None when there's nothing to set up."""
    if key == "digest":
        return ("Ready", True) if settings.get("mail_configured") else ("Not set up", False)
    if key == "calendar":
        if settings.get("google_mode") == "fake":
            return "Test mode: Google isn't used", None
        accounts = settings.get("calendar_accounts", [])
        connected = sum(a["status"] == "connected" for a in accounts)
        reconnect = [a["email"] for a in accounts if a["status"] == "needs_reconnect"]
        if accounts and connected == len(accounts):
            text, ready = ("Both accounts connected" if len(accounts) == 2 else "All accounts connected"), True
        elif connected:
            text, ready = f"{connected} of {len(accounts)} connected", False
        else:
            text, ready = "Not set up", False
        if reconnect:
            text += " · " + ", ".join(reconnect) + " needs reconnecting"
        return text, ready
    return f"This PC's hub address: {address}", None


def fill(text: str, settings: dict[str, Any]) -> str:
    """TX-1: the guide's addresses come from the hub's settings, never from the code."""
    accounts = [a["email"] for a in settings.get("calendar_accounts", [])]
    admin = settings.get("admin_email") or "the admin account"
    second = accounts[1] if len(accounts) > 1 else "the second account"
    return text.replace("{admin}", admin).replace("{second}", second)


def links(text: str) -> list[str]:
    """SG-6: the web addresses in a step."""
    return URL.findall(text)


def to_html(text: str) -> str:
    """A step as rich text: **bold**, and web addresses as links that open in the browser (SG-6)."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return URL.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0).removeprefix("https://")}</a>', out)
