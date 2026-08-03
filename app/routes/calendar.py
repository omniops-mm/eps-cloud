"""Month grid. Each cell links into that day's journal, which is how a past day
gets filled in or corrected after the fact."""

import calendar as calendar_module
import dataclasses
import datetime

from flask import Blueprint, abort, redirect, render_template, url_for
from sqlalchemy import select
from werkzeug.wrappers import Response

from app.clock import current_date
from app.db import db_session
from app.models import DailyNote, DailyState, HabitLog

bp = Blueprint("calendar", __name__, url_prefix="/calendar")

# Weeks start Monday. date.weekday() already counts from Monday and the tracker
# visibility rules rely on that, so following it keeps one convention in the app.
GRID = calendar_module.Calendar(firstweekday=0)


@dataclasses.dataclass
class DaySummary:
    """What one cell has to show. Absent from the map means nothing happened."""

    passed: int = 0
    failed: int = 0
    bad_day: bool = False
    has_note: bool = False


def summarise(first: datetime.date, last: datetime.date) -> dict[datetime.date, DaySummary]:
    """Everything the grid needs, in three queries rather than one per cell.

    The range covers the whole grid including the spillover days from the
    neighbouring months, so those cells are not silently blank.
    """
    days: dict[datetime.date, DaySummary] = {}

    def entry(day: datetime.date) -> DaySummary:
        return days.setdefault(day, DaySummary())

    for log in db_session.scalars(select(HabitLog).where(HabitLog.date.between(first, last))):
        row = entry(log.date)
        if log.passed:
            row.passed += 1
        else:
            row.failed += 1

    for state in db_session.scalars(
        select(DailyState).where(DailyState.date.between(first, last), DailyState.bad_day.is_(True))
    ):
        entry(state.date).bad_day = True

    for note in db_session.scalars(select(DailyNote).where(DailyNote.date.between(first, last))):
        if note.note_text.strip():
            entry(note.date).has_note = True

    return days


def step_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Neighbouring month, rolling the year over. Months are 1-based, so shift
    to 0-based for the arithmetic and back again."""
    shifted_year, zero_based_month = divmod((year * 12 + month - 1) + delta, 12)
    return shifted_year, zero_based_month + 1


@bp.get("/")
def this_month() -> Response:
    today = current_date()
    return redirect(url_for("calendar.month", year=today.year, month=today.month))


@bp.get("/<int:year>/<int:month>")
def month(year: int, month: int) -> str:
    if not 1 <= month <= 12 or not 1970 <= year <= 2999:
        abort(404)

    weeks = GRID.monthdatescalendar(year, month)
    days = summarise(weeks[0][0], weeks[-1][-1])
    today = current_date()
    prev_year, prev_month = step_month(year, month, -1)
    next_year, next_month = step_month(year, month, 1)

    return render_template(
        "calendar.html",
        weeks=weeks,
        days=days,
        month=month,
        year=year,
        month_name=calendar_module.month_name[month],
        today=today,
        is_current_month=(year, month) == (today.year, today.month),
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
    )
