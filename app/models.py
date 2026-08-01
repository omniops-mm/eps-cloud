"""All EPS tables. One pattern throughout: *_log tables are the append-only
source of truth, *_state tables are derived caches rebuilt by the recompute
functions. Never write to a _state table by hand. Reasoning: docs/decisions.md,
entry 3."""

import datetime
import decimal

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, Text, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# portable types: real ARRAY/JSONB on postgres, JSON text on sqlite (tests only)
SmallIntArray = JSON().with_variant(postgresql.ARRAY(SmallInteger()), "postgresql")
JSONBType = JSON().with_variant(postgresql.JSONB(), "postgresql")
TextArray = JSON().with_variant(postgresql.ARRAY(Text()), "postgresql")


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


class Tracker(Base):
    __tablename__ = "trackers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    threshold_days: Mapped[int]
    visible_on_days: Mapped[list[int]] = mapped_column(
        SmallIntArray, default=lambda: [0, 1, 2, 3, 4, 5, 6]
    )
    archived_at: Mapped[datetime.datetime | None]
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class TrackerEvent(Base):
    __tablename__ = "tracker_events"

    date: Mapped[datetime.date] = mapped_column(primary_key=True)
    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id"), primary_key=True)
    activity_done: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    edited_at: Mapped[datetime.datetime | None]


class TrackerState(Base):
    __tablename__ = "tracker_state"

    tracker_id: Mapped[int] = mapped_column(ForeignKey("trackers.id"), primary_key=True)
    days_since_last_done: Mapped[int] = mapped_column(default=0)
    last_done_date: Mapped[datetime.date | None]
    last_recomputed_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "deadline IS NOT NULL OR scheduled_for IS NOT NULL OR remind_after IS NOT NULL",
            name="task_has_temporal_context",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[datetime.date | None]
    scheduled_for: Mapped[datetime.date | None]
    vital: Mapped[bool] = mapped_column(default=False)
    last_user_interaction_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    remind_after: Mapped[datetime.date | None]
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime.datetime | None]
    archived_at: Mapped[datetime.datetime | None]


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('created','completed','uncompleted','edited','snoozed','archived','unarchived')",
            name="task_event_action_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    action: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    payload: Mapped[dict | None] = mapped_column(JSONBType)


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = (
        CheckConstraint("metric_type IN ('scale', 'numeric')", name="metric_type_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    metric_type: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    scale_min: Mapped[int | None] = mapped_column(SmallInteger)
    scale_max: Mapped[int | None] = mapped_column(SmallInteger)
    archived_at: Mapped[datetime.datetime | None]
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class MetricLog(Base):
    __tablename__ = "metric_log"

    date: Mapped[datetime.date] = mapped_column(primary_key=True)
    metric_id: Mapped[int] = mapped_column(ForeignKey("metrics.id"), primary_key=True)
    value: Mapped[decimal.Decimal]
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    edited_at: Mapped[datetime.datetime | None]


class DailyState(Base):
    __tablename__ = "daily_state"

    date: Mapped[datetime.date] = mapped_column(primary_key=True)
    bad_day: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    edited_at: Mapped[datetime.datetime | None]


class DailyNote(Base):
    __tablename__ = "daily_notes"

    date: Mapped[datetime.date] = mapped_column(primary_key=True)
    note_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    edited_at: Mapped[datetime.datetime | None]


class OAuthCredential(Base):
    __tablename__ = "oauth_credentials"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    refresh_token: Mapped[str] = mapped_column(Text)
    access_token: Mapped[str | None] = mapped_column(Text)
    access_token_expires_at: Mapped[datetime.datetime | None]
    scopes: Mapped[list[str] | None] = mapped_column(TextArray)
    connected_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    last_refreshed_at: Mapped[datetime.datetime | None]


class CalendarEvent(Base):
    __tablename__ = "calendar_cache"
    __table_args__ = (Index("idx_calendar_date", "date"),)

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool]
    raw_payload: Mapped[dict | None] = mapped_column(JSONBType)
    fetched_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    date: Mapped[datetime.date]


class WeatherCache(Base):
    __tablename__ = "weather_cache"

    date: Mapped[datetime.date] = mapped_column(primary_key=True)
    raw_payload: Mapped[dict] = mapped_column(JSONBType)
    fetched_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


class UserSettings(Base):
    __tablename__ = "settings"
    __table_args__ = (CheckConstraint("id = 1", name="settings_single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    timezone: Mapped[str] = mapped_column(Text, default="Europe/Berlin")
    calendar_fetch_time: Mapped[datetime.time] = mapped_column(default=datetime.time(6, 0))
    weather_fetch_time: Mapped[datetime.time] = mapped_column(default=datetime.time(6, 0))
    weather_location_lat: Mapped[decimal.Decimal] = mapped_column(
        default=decimal.Decimal("52.5200")
    )
    weather_location_lon: Mapped[decimal.Decimal] = mapped_column(
        default=decimal.Decimal("13.4050")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    edited_at: Mapped[datetime.datetime | None]


class EditLog(Base):
    __tablename__ = "edit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    table_name: Mapped[str] = mapped_column(Text)
    row_id: Mapped[str] = mapped_column(Text)
    field: Mapped[str] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason_optional: Mapped[str | None] = mapped_column(Text)
