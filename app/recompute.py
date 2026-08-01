"""Rebuilds the derived state tables from the event tables they summarise.

Every function here reads its event table from the beginning and overwrites the
matching state row. Nothing is adjusted incrementally, so correcting a day that
was recorded wrongly last month gives exactly the same result as if it had been
recorded correctly at the time.

Call the relevant function after any insert, update or delete on an event table.

Both functions take today's date rather than reading the clock themselves. The
process runs in UTC while the user lives in some other timezone, so for an hour
or two after local midnight the two disagree about what day it is. The caller
knows which timezone applies and passes the date it means.
"""

import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DailyState, HabitLog, StreakState, TrackerEvent, TrackerState

# A streak has to reach this length before a missed day can be forgiven.
GRACE_MIN_STREAK = 7

# Once a miss has been forgiven, this many days must pass before another one is.
GRACE_COOLDOWN_DAYS = 7


def recompute_streak_state(
    session: Session,
    streak_id: int,
    today: datetime.date,
    grace_enabled: bool = True,
) -> StreakState:
    """Replay every logged day for one streak and rewrite its state row.

    A day that passed extends the streak. A day that failed resets it to zero,
    unless grace is enabled and the streak is long enough to spend it. A day
    with no entry counts as a failure, with two exceptions: a day the user
    marked as a bad day, and today, which is not over yet.
    """
    # the caller may hold unflushed event rows; without this the queries below
    # read the database as it was before those writes and miss them
    session.flush()
    logged = {
        row.date: row.passed
        for row in session.scalars(select(HabitLog).where(HabitLog.streak_id == streak_id))
    }
    bad_days = set(session.scalars(select(DailyState.date).where(DailyState.bad_day.is_(True))))

    state = session.get(StreakState, streak_id)
    if state is None:
        state = StreakState(streak_id=streak_id, current_streak=0, personal_record=0)
        session.add(state)

    current = 0
    record = state.personal_record
    last_grace: datetime.date | None = None

    day = min(logged) if logged else today
    while day <= today:
        passed = logged.get(day)
        forgiven_gap = passed is None and (day == today or day in bad_days)
        if passed:
            current += 1
            record = max(record, current)
        elif forgiven_gap:
            pass
        elif (
            grace_enabled
            and current >= GRACE_MIN_STREAK
            and (last_grace is None or (day - last_grace).days >= GRACE_COOLDOWN_DAYS)
        ):
            last_grace = day
        else:
            current = 0
        day += datetime.timedelta(days=1)

    state.current_streak = current
    # personal record never decreases, resets keep it as history
    state.personal_record = record
    state.last_grace_used_date = last_grace
    # stored without an offset, matching the UTC clock the database defaults use
    state.last_recomputed_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    session.flush()
    return state


def recompute_tracker_state(
    session: Session, tracker_id: int, today: datetime.date
) -> TrackerState:
    """Find the most recent day this activity was done and count forward from it.

    A tracker that has never been done reports zero days and no last-done date,
    so it does not read as overdue before it has been used once.
    """
    # same reason as in recompute_streak_state: see the caller's pending writes
    session.flush()
    last_done = session.scalar(
        select(func.max(TrackerEvent.date)).where(
            TrackerEvent.tracker_id == tracker_id,
            TrackerEvent.activity_done.is_(True),
        )
    )

    state = session.get(TrackerState, tracker_id)
    if state is None:
        state = TrackerState(tracker_id=tracker_id, days_since_last_done=0)
        session.add(state)

    state.last_done_date = last_done
    state.days_since_last_done = 0 if last_done is None else (today - last_done).days
    # stored without an offset, matching the UTC clock the database defaults use
    state.last_recomputed_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    session.flush()
    return state
