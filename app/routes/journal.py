"""Daily journal: the write surface for one day.

GET renders the day's page. POST endpoints record one interaction each and
return only the re-rendered row, which htmx swaps into place, so the page
never fully reloads. Every write goes through the recompute functions in
app.recompute; nothing here touches a state table directly.
"""

import datetime
import zoneinfo

from flask import Blueprint, abort, redirect, render_template, url_for
from sqlalchemy import select
from werkzeug.wrappers import Response

from app.config import get_settings
from app.db import db_session
from app.models import HabitLog, Streak, StreakState
from app.recompute import recompute_streak_state

bp = Blueprint("journal", __name__, url_prefix="/journal")


def current_date() -> datetime.date:
    """Today in the configured timezone, not the server's UTC clock."""
    return datetime.datetime.now(zoneinfo.ZoneInfo(get_settings().tz)).date()


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


@bp.get("/")
def today() -> Response:
    return redirect(url_for("journal.day", date=current_date().isoformat()))


@bp.get("/<date>")
def day(date: str) -> str:
    day = parse_date(date)
    return render_template(
        "journal.html",
        day=day,
        today=current_date(),
        rows=habit_rows(day),
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
    if entry is None:
        entry = HabitLog(date=day, streak_id=streak_id, passed=passed)
        db_session.add(entry)
    elif entry.passed == passed:
        db_session.delete(entry)
        entry = None
    else:
        entry.passed = passed
        entry.edited_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    # db_session is a proxy; calling it returns the real Session underneath
    state = recompute_streak_state(db_session(), streak_id, today=current_date())
    db_session.commit()

    return render_template(
        "fragments/habit_row.html",
        streak=streak,
        state=state,
        entry=entry,
        day=day,
    )
