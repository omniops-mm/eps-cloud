"""Daily journal: the write surface for one day.

GET renders the day's page. Each POST endpoint records one interaction and
returns only the re-rendered row, which htmx swaps into place, so the page
never fully reloads. The exception is the bad-day toggle: it changes how every
streak on the page is counted, so it redirects to a full re-render.

Every write goes through the recompute functions in app.recompute; nothing
here touches a state table directly. Writes to past days are audited via
app.audit.
"""

import datetime
import decimal
import zoneinfo

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy import select
from werkzeug.wrappers import Response

from app.audit import maybe_audit
from app.config import get_settings
from app.db import db_session
from app.models import (
    DailyNote,
    DailyState,
    HabitLog,
    Metric,
    MetricLog,
    Streak,
    StreakState,
    Tracker,
    TrackerEvent,
    TrackerState,
)
from app.recompute import recompute_streak_state

bp = Blueprint("journal", __name__, url_prefix="/journal")


def current_date() -> datetime.date:
    """Today in the configured timezone, not the server's UTC clock."""
    return datetime.datetime.now(zoneinfo.ZoneInfo(get_settings().tz)).date()


def utc_now() -> datetime.datetime:
    """Naive-UTC timestamp, matching what the database defaults store."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def parse_date(value: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        abort(404)


def habit_rows(day: datetime.date) -> list[dict]:
    """One dict per active streak: the streak, its state, and that day's entry."""
    streaks = db_session.scalars(
        select(Streak).where(Streak.archived_at.is_(None)).order_by(Streak.id)
    ).all()
    entries = {
        e.streak_id: e for e in db_session.scalars(select(HabitLog).where(HabitLog.date == day))
    }
    states = {s.streak_id: s for s in db_session.scalars(select(StreakState))}
    return [{"streak": s, "state": states.get(s.id), "entry": entries.get(s.id)} for s in streaks]


def tracker_rows(day: datetime.date) -> list[dict]:
    """Active trackers that are visible on this day's weekday."""
    trackers = db_session.scalars(
        select(Tracker).where(Tracker.archived_at.is_(None)).order_by(Tracker.id)
    ).all()
    events = {
        e.tracker_id: e
        for e in db_session.scalars(select(TrackerEvent).where(TrackerEvent.date == day))
    }
    states = {s.tracker_id: s for s in db_session.scalars(select(TrackerState))}
    return [
        {"tracker": t, "state": states.get(t.id), "event": events.get(t.id)}
        for t in trackers
        if day.weekday() in t.visible_on_days
    ]


def metric_rows(day: datetime.date) -> list[dict]:
    """Active metrics with that day's logged value, if any."""
    metrics = db_session.scalars(
        select(Metric).where(Metric.archived_at.is_(None)).order_by(Metric.id)
    ).all()
    entries = {
        e.metric_id: e for e in db_session.scalars(select(MetricLog).where(MetricLog.date == day))
    }
    return [{"metric": m, "entry": entries.get(m.id)} for m in metrics]


@bp.get("/")
def today() -> Response:
    return redirect(url_for("journal.day", date=current_date().isoformat()))


@bp.get("/<date>")
def day(date: str) -> str:
    day = parse_date(date)
    daily = db_session.get(DailyState, day)
    note = db_session.get(DailyNote, day)
    return render_template(
        "journal.html",
        day=day,
        today=current_date(),
        rows=habit_rows(day),
        trackers=tracker_rows(day),
        metrics=metric_rows(day),
        bad_day=daily.bad_day if daily else False,
        note_text=note.note_text if note else "",
    )


@bp.post("/<date>/habit/<int:streak_id>/<mark>")
def mark_habit(date: str, streak_id: int, mark: str) -> str:
    """Record a pass or fail for one streak on one day.

    Pressing the already-active mark removes the entry, so every state is
    reachable: pass, fail, and no entry at all.
    """
    day = parse_date(date)
    if mark not in ("pass", "fail"):
        abort(404)
    streak = db_session.get(Streak, streak_id)
    if streak is None:
        abort(404)

    passed = mark == "pass"
    entry = db_session.get(HabitLog, (day, streak_id))
    row_id = f"{day.isoformat()}:{streak_id}"
    if entry is None:
        entry = HabitLog(date=day, streak_id=streak_id, passed=passed)
        db_session.add(entry)
        old: bool | None = None
        new: bool | None = passed
    elif entry.passed == passed:
        db_session.delete(entry)
        old, new = entry.passed, None
        entry = None
    else:
        old, new = entry.passed, passed
        entry.passed = passed
        entry.edited_at = utc_now()

    session = db_session()
    maybe_audit(
        session,
        table="habit_log",
        row_id=row_id,
        field="passed",
        old=old,
        new=new,
        entry_date=day,
        today=current_date(),
    )
    state = recompute_streak_state(session, streak_id, today=current_date())
    db_session.commit()

    return render_template(
        "fragments/habit_row.html", streak=streak, state=state, entry=entry, day=day
    )


@bp.post("/<date>/metric/<int:metric_id>")
def save_metric(date: str, metric_id: int) -> str:
    """Save, change or clear one metric value for one day."""
    day = parse_date(date)
    metric = db_session.get(Metric, metric_id)
    if metric is None:
        abort(404)

    raw = request.form.get("value", "").strip()
    value: decimal.Decimal | None
    if raw == "":
        value = None
    else:
        try:
            value = decimal.Decimal(raw)
        except decimal.InvalidOperation:
            abort(400)
        if metric.metric_type == "scale" and not (
            metric.scale_min <= value <= metric.scale_max  # type: ignore[operator]
        ):
            abort(400)

    entry = db_session.get(MetricLog, (day, metric_id))
    row_id = f"{day.isoformat()}:{metric_id}"
    old = entry.value if entry else None

    if entry is None and value is not None:
        entry = MetricLog(date=day, metric_id=metric_id, value=value)
        db_session.add(entry)
    elif entry is not None and value is None:
        db_session.delete(entry)
        entry = None
    elif entry is not None and value is not None:
        # tapping the already-selected scale value clears it, like the habits
        if metric.metric_type == "scale" and entry.value == value:
            db_session.delete(entry)
            entry = None
            value = None
        else:
            entry.value = value
            entry.edited_at = utc_now()

    maybe_audit(
        db_session(),
        table="metric_log",
        row_id=row_id,
        field="value",
        old=old,
        new=value,
        entry_date=day,
        today=current_date(),
    )
    db_session.commit()

    return render_template("fragments/metric_row.html", metric=metric, entry=entry, day=day)


@bp.post("/<date>/badday")
def toggle_bad_day(date: str) -> Response:
    """Flip the bad-day flag, then re-render the whole page.

    A bad day changes how every streak is counted, so a fragment swap is not
    enough; every count on the page may move.
    """
    day = parse_date(date)
    daily = db_session.get(DailyState, day)
    if daily is None:
        daily = DailyState(date=day, bad_day=True)
        db_session.add(daily)
        old: bool | None = None
    else:
        old = daily.bad_day
        daily.bad_day = not daily.bad_day
        daily.edited_at = utc_now()

    session = db_session()
    maybe_audit(
        session,
        table="daily_state",
        row_id=day.isoformat(),
        field="bad_day",
        old=old,
        new=daily.bad_day,
        entry_date=day,
        today=current_date(),
    )
    for streak_id in db_session.scalars(select(Streak.id).where(Streak.archived_at.is_(None))):
        recompute_streak_state(session, streak_id, today=current_date())
    db_session.commit()

    return redirect(url_for("journal.day", date=day.isoformat()))


@bp.post("/<date>/note")
def save_note(date: str) -> str:
    """Autosaved journal text. Exempt from the audit log."""
    day = parse_date(date)
    text = request.form.get("note_text", "")

    note = db_session.get(DailyNote, day)
    if note is None:
        note = DailyNote(date=day, note_text=text)
        db_session.add(note)
    else:
        note.note_text = text
        note.edited_at = utc_now()
    db_session.commit()

    return "saved"
