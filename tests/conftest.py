from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from family_scheduler.google_calendar import FakeCalendarGateway
from family_scheduler.hub.context import HubContext, build_context
from family_scheduler.mailer import RecordingMailer
from family_scheduler.organizer import EventInput, Organizer, TaskInput
from family_scheduler.secrets_store import StaticKeyProvider

ADMIN = "admin@example.com"
ALEX = "alex@example.com"
SAM = "sam@example.com"
SECOND = "second@example.com"  # the second calendar account (WO-FS-005 GA-1, WO-FS-010 AC-5)


class FakeClock:
    """Starts Tuesday Oct 27, 2026, 8:00 am Toronto time."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ctx(tmp_path: Path, clock: FakeClock) -> Iterator[HubContext]:
    context = build_context(tmp_path, keys=StaticKeyProvider(), clock=clock, fake_calendar=True, fake_mail=True)
    set_up(context)
    context.organizer.add_member(ADMIN, ALEX)
    context.organizer.add_member(ADMIN, SAM)
    yield context
    context.conn.close()


def set_up(context: HubContext) -> None:
    """A new hub has no family (WO-FS-010 AC-4): the tests set up placeholder accounts, both connected, as the
    app's first-time setup and Connect would."""
    context.organizer.set_up_family(ADMIN, SECOND)
    for account in (ADMIN, SECOND):
        context.organizer.set_account_status(account, "connected")


@pytest.fixture
def org(ctx: HubContext) -> Organizer:
    return ctx.organizer


@pytest.fixture
def gcal(ctx: HubContext) -> FakeCalendarGateway:
    assert ctx.fake_calendar is not None
    return ctx.fake_calendar


@pytest.fixture
def mail(ctx: HubContext) -> RecordingMailer:
    assert ctx.fake_mailer is not None
    return ctx.fake_mailer


def event(title: str = "Dentist", day: str = "2026-11-03", start: str = "16:00", end: str = "17:00",
          repeat: str = "none", invitees: list[str] | None = None, location: str = "Smile Dental") -> EventInput:
    return EventInput(title, day, start, end, location, repeat, invitees or [ADMIN, ALEX, SAM])


def task(title: str = "Take out recycling", due: str = "2026-10-29", priority: str = "medium",
         repeat: str = "none") -> TaskInput:
    return TaskInput(title, due, priority, repeat)
