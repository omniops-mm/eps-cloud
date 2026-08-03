"""Fill a database with a few weeks of plausible use.

A brand new install has nothing in it, which makes it hard to tell what any of
the screens are for. This gives you a system that looks lived in: streaks partly
built, trackers at various stages, tasks in every state, a couple of months of
history behind the calendar.

    uv run python seed.py

Safe to run more than once. Everything is matched by name or by date and only
ever added, never updated and never deleted, so running it against a database
you already use will fill the gaps and leave your own entries alone.

The example habits, trackers and tasks are deliberately ordinary ones. They are
placeholders to be deleted, not a suggestion of what to track.
"""

import datetime
import decimal
import random
import zoneinfo
from typing import Any

from sqlalchemy import Select, select

from app import create_app
from app.clock import current_date, utc_now
from app.config import get_settings
from app.db import db_session
from app.models import (
    CalendarEvent,
    DailyNote,
    DailyState,
    HabitLog,
    Metric,
    MetricLog,
    Streak,
    Task,
    Tracker,
    TrackerEvent,
)
from app.recompute import recompute_streak_state, recompute_tracker_state

# fixed seed: re-running gives the same history rather than a new random one
RNG = random.Random(7)

HISTORY_DAYS = 56

HABITS = [
    ("No sugar", "No sweets, no sugary drinks."),
    ("In bed before midnight", None),
    ("No phone first hour", "Nothing on a screen until an hour after waking."),
    ("Morning stretch", None),
    ("Read 20 pages", None),
]

# name, how many days before it is due again, how likely it is done on a day
TRACKERS = [
    ("Do the dishes", 4, 0.45),
    ("Practice guitar", 3, 0.35),
    ("Water the plants", 7, 0.2),
    ("Call parents", 7, 0.18),
]

METRICS = [
    ("Mood", "scale", None, 1, 5),
    ("Sleep", "numeric", "hours", None, None),
    ("Steps", "numeric", None, None, None),
]

# offsets are days from today; None means the field is not set
# name, deadline, scheduled_for, time, vital, done_offset
TASKS = [
    ("Return the library books", -7, None, None, False, None),
    ("Pay rent", -1, None, None, True, None),
    ("Prepare Monday standup notes", 0, None, None, False, None),
    ("Book the dentist", None, 0, "09:30", False, None),
    ("Gym session", None, 0, "18:00", False, None),
    ("Reply to the landlord", 1, None, None, True, None),
    ("Submit meter reading", 2, None, None, False, None),
    ("Renew library card", 3, None, None, False, None),
    ("Order a birthday present", 5, None, None, False, None),
    ("Back up the laptop", None, -1, "20:00", False, -1),
    ("Cancel the unused subscription", -3, None, None, False, -3),
    ("Take the bottles out", None, -2, None, False, -2),
    ("Fix the squeaky door", None, -5, None, False, -4),
]

# title, days from today, time, length in hours
EVENTS = [
    ("Team standup", 0, "09:00", 0.5),
    ("Lunch with Sam", 0, "12:30", 1),
    ("Quarterly review", 1, "14:00", 1.5),
    ("Football", 2, "19:00", 2),
    ("Flat viewing", 3, "17:30", 0.5),
    ("Team standup", -1, "09:00", 0.5),
    ("Dentist", -2, "11:15", 0.75),
    ("Team standup", -3, "09:00", 0.5),
]

NOTES = {
    -1: "Slow start, better afternoon. The evening walk helped.",
    -4: "Long day. Got the important thing done and left the rest.",
    -9: "Good one. Finished early and actually stopped working.",
    -16: "Travelling most of the day, not much got done and that is fine.",
}

BAD_DAYS = [-6, -13, -27]


def parse_time(value: str) -> datetime.time:
    hour, minute = value.split(":")
    return datetime.time(int(hour), int(minute))


def by_name(model: Any, name: str) -> Select[Any]:
    """Every definition table is keyed by a name here, so matching is one query."""
    return select(model).where(model.name == name)


def seed_definitions() -> tuple[list[Streak], list[Tracker], list[Metric]]:
    """Create the things being tracked, skipping any that already exist."""
    streaks = []
    for name, description in HABITS:
        row = db_session.scalar(by_name(Streak, name))
        if row is None:
            row = Streak(name=name, description=description)
            db_session.add(row)
        streaks.append(row)

    trackers = []
    for name, threshold, _ in TRACKERS:
        row = db_session.scalar(by_name(Tracker, name))
        if row is None:
            row = Tracker(name=name, threshold_days=threshold)
            db_session.add(row)
        trackers.append(row)

    metrics = []
    for name, kind, unit, low, high in METRICS:
        row = db_session.scalar(by_name(Metric, name))
        if row is None:
            row = Metric(name=name, metric_type=kind, unit=unit, scale_min=low, scale_max=high)
            db_session.add(row)
        metrics.append(row)

    db_session.flush()
    return streaks, trackers, metrics


def seed_history(
    streaks: list[Streak], trackers: list[Tracker], metrics: list[Metric], today: datetime.date
) -> None:
    """Fill the last stretch of days with entries, leaving existing ones alone."""
    for offset in range(HISTORY_DAYS, 0, -1):
        day = today - datetime.timedelta(days=offset)

        for streak in streaks:
            if db_session.get(HabitLog, (day, streak.id)) is not None:
                continue
            # mostly kept, with the odd miss, so streaks and grace both show up
            db_session.add(HabitLog(date=day, streak_id=streak.id, passed=RNG.random() > 0.16))

        for tracker, (_, _, chance) in zip(trackers, TRACKERS, strict=True):
            if db_session.get(TrackerEvent, (day, tracker.id)) is not None:
                continue
            if RNG.random() < chance:
                db_session.add(TrackerEvent(date=day, tracker_id=tracker.id, activity_done=True))

        for metric in metrics:
            if db_session.get(MetricLog, (day, metric.id)) is not None:
                continue
            value: decimal.Decimal
            if metric.metric_type == "scale":
                value = decimal.Decimal(RNG.randint(metric.scale_min or 1, metric.scale_max or 5))
            elif metric.name == "Sleep":
                value = decimal.Decimal(str(round(RNG.uniform(5.5, 8.5), 1)))
            else:
                value = decimal.Decimal(RNG.randrange(3000, 14000, 250))
            db_session.add(MetricLog(date=day, metric_id=metric.id, value=value))

    for offset, text in NOTES.items():
        day = today + datetime.timedelta(days=offset)
        if db_session.get(DailyNote, day) is None:
            db_session.add(DailyNote(date=day, note_text=text))

    for offset in BAD_DAYS:
        day = today + datetime.timedelta(days=offset)
        if db_session.get(DailyState, day) is None:
            db_session.add(DailyState(date=day, bad_day=True))


def seed_tasks(today: datetime.date) -> None:
    """One task per state: overdue, due today, timed, upcoming, already done."""
    for name, deadline, scheduled, time_of_day, vital, done in TASKS:
        if db_session.scalar(by_name(Task, name)) is not None:
            continue
        db_session.add(
            Task(
                name=name,
                deadline=today + datetime.timedelta(days=deadline)
                if deadline is not None
                else None,
                scheduled_for=(
                    today + datetime.timedelta(days=scheduled) if scheduled is not None else None
                ),
                scheduled_time=parse_time(time_of_day) if time_of_day else None,
                vital=vital,
                created_at=utc_now() - datetime.timedelta(days=HISTORY_DAYS),
                last_user_interaction_at=utc_now() - datetime.timedelta(days=2),
                completed_at=(
                    utc_now() + datetime.timedelta(days=done) if done is not None else None
                ),
            )
        )


def seed_events(today: datetime.date) -> None:
    """Calendar entries, as if a sync had already run."""
    zone = zoneinfo.ZoneInfo(get_settings().tz)
    for title, offset, time_of_day, hours in EVENTS:
        day = today + datetime.timedelta(days=offset)
        event_id = f"seed-{day.isoformat()}-{title.lower().replace(' ', '-')}"
        if db_session.get(CalendarEvent, event_id) is not None:
            continue
        start = datetime.datetime.combine(day, parse_time(time_of_day), tzinfo=zone)
        db_session.add(
            CalendarEvent(
                event_id=event_id,
                source="seed",
                title=title,
                start_at=start,
                end_at=start + datetime.timedelta(hours=hours),
                all_day=False,
                date=day,
                fetched_at=utc_now().replace(tzinfo=datetime.UTC),
            )
        )


def main() -> None:
    app = create_app()
    with app.app_context():
        today = current_date()
        streaks, trackers, metrics = seed_definitions()
        seed_history(streaks, trackers, metrics, today)
        seed_tasks(today)
        seed_events(today)
        db_session.flush()

        # the caches are derived, so rebuild them rather than writing them here
        session = db_session()
        for streak in streaks:
            recompute_streak_state(session, streak.id, today=today)
        for tracker in trackers:
            recompute_tracker_state(session, tracker.id, today=today)

        db_session.commit()
        print(f"seeded up to {today.isoformat()}")


if __name__ == "__main__":
    main()
