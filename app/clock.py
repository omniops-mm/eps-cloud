"""Dates and times, in the user's zone rather than the server's.

The process runs UTC inside a container and the person using it does not, so
"today" is a question about their timezone, not the machine's. Everything that
turns a moment into a calendar day goes through here. Nothing else should call
datetime.now() or date.today() directly.

The zone is read from the saved settings once per process and then cached. Never
per call: a request that picked up a change halfway through would file two of
its own writes under different days. Changing it takes effect on restart, which
the settings page says.
"""

import datetime
import zoneinfo

from app.config import get_settings
from app.db import db_session
from app.models import UserSettings

_zone_name: str | None = None


def _saved_zone() -> str | None:
    """The zone from the settings row, or None if it cannot be read or used."""
    try:
        row = db_session.get(UserSettings, 1)
    except Exception:  # noqa: BLE001  # no database yet is a normal startup state
        # a failed read leaves a useless session in the registry, and a later
        # configure() cannot replace one that already exists
        db_session.remove()
        return None
    if row is None:
        return None
    if row.timezone not in zoneinfo.available_timezones():
        return None
    return row.timezone


def zone_name() -> str:
    """The IANA zone this process uses, read once and kept.

    Falls back to the environment when nothing is saved yet or the database is
    not up. That fallback is deliberately not cached, so the saved value is
    picked up as soon as it can be read rather than being missed for the life of
    the process.
    """
    global _zone_name
    if _zone_name is not None:
        return _zone_name
    saved = _saved_zone()
    if saved is None:
        return get_settings().tz
    _zone_name = saved
    return _zone_name


def forget_zone() -> None:
    """Drop the cached zone. For tests, and after the settings page saves one."""
    global _zone_name
    _zone_name = None


def zone() -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(zone_name())


def current_date() -> datetime.date:
    """Today in the configured timezone, not the server's UTC clock."""
    return datetime.datetime.now(zone()).date()


def utc_now() -> datetime.datetime:
    """Naive-UTC timestamp, matching what the database defaults store."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def local_day_start_utc(day: datetime.date) -> datetime.datetime:
    """Local midnight of a day, expressed in naive UTC for column comparisons."""
    midnight = datetime.datetime.combine(day, datetime.time.min, tzinfo=zone())
    return midnight.astimezone(datetime.UTC).replace(tzinfo=None)


def local_date_of(moment: datetime.datetime) -> datetime.date:
    """Which local day a stored naive-UTC timestamp actually fell on."""
    return moment.replace(tzinfo=datetime.UTC).astimezone(zone()).date()
