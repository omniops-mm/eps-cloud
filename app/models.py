"""Database tables, straight from the spec (section 4).

Model C throughout: *_log tables are the truth, *_state tables are
derived caches rebuilt by recompute. Never write to a _state table
by hand.
"""

import datetime

from sqlalchemy import ForeignKey, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Streak(Base):
    __tablename__ = "streaks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime.datetime | None]
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class HabitLog(Base):
    __tablename__ = "habit_log"

    date: Mapped[datetime.date] = mapped_column(primary_key=True)
    streak_id: Mapped[int] = mapped_column(ForeignKey("streaks.id"), primary_key=True)
    passed: Mapped[bool]
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    edited_at: Mapped[datetime.datetime | None]


class StreakState(Base):
    __tablename__ = "streak_state"

    streak_id: Mapped[int] = mapped_column(ForeignKey("streaks.id"), primary_key=True)
    current_streak: Mapped[int] = mapped_column(default=0)
    personal_record: Mapped[int] = mapped_column(default=0)
    last_grace_used_date: Mapped[datetime.date | None]
    last_recomputed_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
