"""Dates and times, in the user's zone rather than the server's.

The process runs UTC inside a container and the person using it does not, so
"today" is a question about their timezone, not the machine's. Everything that
turns a moment into a calendar day goes through here. Nothing else should call
datetime.now() or date.today() directly.
"""

import datetime
import zoneinfo

from app.config import get_settings


def current_date() -> datetime.date:
    """Today in the configured timezone, not the server's UTC clock."""
    return datetime.datetime.now(zoneinfo.ZoneInfo(get_settings().tz)).date()


def utc_now() -> datetime.datetime:
    """Naive-UTC timestamp, matching what the database defaults store."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def local_day_start_utc(day: datetime.date) -> datetime.datetime:
    """Local midnight of a day, expressed in naive UTC for column comparisons."""
    zone = zoneinfo.ZoneInfo(get_settings().tz)
    midnight = datetime.datetime.combine(day, datetime.time.min, tzinfo=zone)
    return midnight.astimezone(datetime.UTC).replace(tzinfo=None)


def local_date_of(moment: datetime.datetime) -> datetime.date:
    """Which local day a stored naive-UTC timestamp actually fell on."""
    zone = zoneinfo.ZoneInfo(get_settings().tz)
    return moment.replace(tzinfo=datetime.UTC).astimezone(zone).date()
