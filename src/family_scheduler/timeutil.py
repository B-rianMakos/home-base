"""Time helpers. Everything is stored in UTC and shown in America/Toronto (NF-5, PC-10)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

HOME_TZ_NAME = "America/Toronto"
HOME_TZ = ZoneInfo(HOME_TZ_NAME)

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """UTC ISO text with a fixed shape, so stored values sort correctly as strings."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def local_today(now: datetime) -> date:
    return now.astimezone(HOME_TZ).date()


def local_to_utc(day: date, wall: time, tz_name: str = HOME_TZ_NAME) -> datetime:
    """Wall-clock time on a day in a zone, as UTC. Handles daylight saving changes."""
    return datetime.combine(day, wall, tzinfo=ZoneInfo(tz_name)).astimezone(UTC)


def parse_hhmm(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(HOME_TZ)


def days_ago(now: datetime, days: int) -> str:
    return iso(now - timedelta(days=days))
