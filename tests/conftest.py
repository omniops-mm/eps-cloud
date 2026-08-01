"""Shared test setup.

Tests run against an in-memory SQLite database rather than Postgres so the suite
needs no running container and each test starts from an empty schema. The models
declare portable column types for exactly this reason.
"""

import datetime
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, DailyState, HabitLog, Streak, Tracker, TrackerEvent

# A Monday, so any weekday-dependent assertion reads unambiguously.
START = datetime.date(2026, 1, 5)


def days(offset: int) -> datetime.date:
    """A date offset in days from the fixed start of the test timeline."""
    return START + datetime.timedelta(days=offset)


class Builder:
    """Terse helpers for putting rows in, so tests read as scenarios not setup."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def streak(self, name: str = "example streak") -> int:
        row = Streak(name=name)
        self.session.add(row)
        self.session.flush()
        return row.id

    def log(self, streak_id: int, pattern: str, start: datetime.date = START) -> None:
        """One character per consecutive day: P passed, F failed, . no entry."""
        for offset, mark in enumerate(pattern):
            if mark == ".":
                continue
            self.session.add(
                HabitLog(
                    date=start + datetime.timedelta(days=offset),
                    streak_id=streak_id,
                    passed=mark == "P",
                )
            )
        self.session.flush()

    def edit(self, streak_id: int, date: datetime.date, passed: bool) -> None:
        """Change a day that was already logged, the way a retroactive edit does."""
        entry = self.session.get(HabitLog, (date, streak_id))
        assert entry is not None
        entry.passed = passed
        self.session.flush()

    def tracker(self, name: str = "example tracker", threshold_days: int = 3) -> int:
        row = Tracker(
            name=name,
            threshold_days=threshold_days,
            visible_on_days=[0, 1, 2, 3, 4, 5, 6],
        )
        self.session.add(row)
        self.session.flush()
        return row.id

    def done(self, tracker_id: int, *dates: datetime.date, activity_done: bool = True) -> None:
        for date in dates:
            self.session.add(
                TrackerEvent(date=date, tracker_id=tracker_id, activity_done=activity_done)
            )
        self.session.flush()

    def bad_day(self, *dates: datetime.date) -> None:
        for date in dates:
            self.session.add(DailyState(date=date, bad_day=True))
        self.session.flush()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def build(session: Session) -> Builder:
    return Builder(session)
