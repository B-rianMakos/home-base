"""Digest email through the admin's own Gmail (FR-4)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol

GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 465


class MailError(Exception):
    """Sending failed. The message tells the member what to do."""


class Mailer(Protocol):
    def send(self, to: str, subject: str, text: str, html: str) -> None: ...


class GmailMailer:
    def __init__(self, account: str, app_password: str, timeout: float = 20.0) -> None:
        self._account = account
        self._password = app_password.replace(" ", "")
        self._timeout = timeout

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        msg = EmailMessage()
        msg["From"] = f"Home Base <{self._account}>"
        msg["To"] = to  # always exactly one recipient (FR-8)
        msg["Subject"] = subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP_SSL(GMAIL_HOST, GMAIL_PORT, timeout=self._timeout) as smtp:
                smtp.login(self._account, self._password)
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as err:
            raise MailError(
                "The Gmail sign-in failed: Gmail didn't accept the app password. On the hub PC, open Family & "
                "settings, Digest email, and check the app password (or create a new one and save it)."
            ) from err
        except smtplib.SMTPRecipientsRefused as err:
            raise MailError(f"Gmail refused the address {to}. Check it in Family & Settings.") from err
        except smtplib.SMTPDataError as err:
            if err.smtp_code == 550 or err.smtp_code == 421:
                raise MailError(
                    "Gmail is limiting sends from this account right now. "
                    "Try again later; the digest is still on the Today screen."
                ) from err
            raise MailError("Gmail refused the email. Try again later.") from err
        except (TimeoutError, smtplib.SMTPException, OSError) as err:
            raise MailError(
                "Couldn't reach Gmail. Check the hub's internet connection and try again."
            ) from err


class RecordingMailer:
    """Keeps sent mail in memory, for tests and the offline test hub."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.fail_with: str | None = None

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        if self.fail_with:
            message, self.fail_with = self.fail_with, None
            raise MailError(message)
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html})
