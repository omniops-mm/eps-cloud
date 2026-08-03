"""Preferences: the handful of global choices that are not per-day data.

Habits, trackers, metrics and tasks are managed where they are used, so this
page is only the settings that have nowhere else to live.

The timezone is shown here but not editable. It is deployment configuration read
from the environment, because it decides which calendar day every entry belongs
to and so must not depend on a query that can fail.
"""

import datetime
import decimal
import zoneinfo

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app.clock import forget_zone, utc_now, zone_name
from app.db import db_session
from app.preferences import ensure

bp = Blueprint("settings", __name__, url_prefix="/settings")

LAT_RANGE = (decimal.Decimal(-90), decimal.Decimal(90))
LON_RANGE = (decimal.Decimal(-180), decimal.Decimal(180))


def parse_coordinate(raw: str, low: decimal.Decimal, high: decimal.Decimal) -> decimal.Decimal:
    try:
        value = decimal.Decimal(raw.strip())
    except (decimal.InvalidOperation, AttributeError):
        abort(400)
    if not low <= value <= high:
        abort(400)
    return value


def parse_clock_time(raw: str) -> datetime.time:
    try:
        return datetime.time.fromisoformat(raw)
    except (ValueError, AttributeError):
        abort(400)


@bp.get("/")
def index() -> str:
    """Render the preferences, creating the row on a first-ever visit.

    This is the one read path allowed to write. The alternative was repeating
    every column default in here to render an install that has saved nothing,
    which would put the defaults in two places and let them drift.
    """
    prefs = ensure(db_session())
    db_session.commit()
    return render_template(
        "settings.html",
        prefs=prefs,
        # what this process is actually using, which differs from the saved
        # value until a restart picks the change up
        active_zone=zone_name(),
        zones=sorted(zoneinfo.available_timezones()),
    )


@bp.post("/grace")
def toggle_grace() -> str:
    """Flip streak forgiveness. Returns the card so the page does not reload.

    Existing streak counts are not recomputed here. They settle on the new rule
    the next time each streak is touched, which keeps one toggle from rewriting
    every number on every page at once.
    """
    prefs = ensure(db_session())
    prefs.grace_enabled = not prefs.grace_enabled
    prefs.edited_at = utc_now()
    db_session.commit()
    return render_template("fragments/grace_card.html", prefs=prefs)


@bp.post("/weather")
def save_weather() -> Response:
    prefs = ensure(db_session())
    prefs.weather_location_lat = parse_coordinate(request.form.get("lat", ""), *LAT_RANGE)
    prefs.weather_location_lon = parse_coordinate(request.form.get("lon", ""), *LON_RANGE)
    prefs.edited_at = utc_now()
    db_session.commit()
    return redirect(url_for("settings.index", saved="weather"))


@bp.post("/timezone")
def save_timezone() -> Response:
    """Save the zone. It applies on restart, which the page says out loud.

    Validated against the zones this Python actually has rather than a pattern,
    so an unusable name cannot be stored.
    """
    chosen = request.form.get("timezone", "")
    if chosen not in zoneinfo.available_timezones():
        abort(400)
    prefs = ensure(db_session())
    prefs.timezone = chosen
    prefs.edited_at = utc_now()
    db_session.commit()
    forget_zone()
    return redirect(url_for("settings.index", saved="timezone"))


@bp.post("/fetch-times")
def save_fetch_times() -> Response:
    prefs = ensure(db_session())
    prefs.calendar_fetch_time = parse_clock_time(request.form.get("calendar_fetch_time", ""))
    prefs.weather_fetch_time = parse_clock_time(request.form.get("weather_fetch_time", ""))
    prefs.edited_at = utc_now()
    db_session.commit()
    return redirect(url_for("settings.index", saved="times"))
