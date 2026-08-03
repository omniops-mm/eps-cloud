"""The front page: one agenda of everything today asks of you, then the days
ahead, with a read-only summary column beside it.

The agenda regions, top to bottom:

1. Overdue tasks. Always first, never part of manual ordering.
2. The day list: calendar events, tasks and due trackers in one sequence.
   Default order is by time for anything timed, then untimed tasks (vital
   first), then trackers. "Plan the day" mode reorders tasks and trackers
   freely around the fixed calendar events.
3. Tasks finished today, crossed out.
4. Upcoming: one group per day for the next five days.

Ordering works on one shared key scale: timed items rank by their clock time,
untimed items and trackers rank in bands above that, and a manual move stores
a key between its new neighbours' keys in sort_order. Rendering just sorts by
key, so manual and default positions mix without special cases.
"""

import datetime
from typing import Any

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy import select
from werkzeug.wrappers import Response

from app.clock import current_date, local_day_start_utc, utc_now
from app.db import db_session
from app.integrations.brightsky import weather_summary
from app.models import (
    CalendarEvent,
    HabitLog,
    Metric,
    MetricLog,
    Streak,
    StreakState,
    Task,
    TaskEvent,
    Tracker,
    TrackerEvent,
    TrackerState,
)
from app.recompute import recompute_tracker_state

bp = Blueprint("dashboard", __name__)

STALE_AFTER_DAYS = 7
UPCOMING_DAYS = 5

# default-order bands for the day list's shared key scale; times land in
# 10000..11439 (10000 + minutes of the day), so the bands sit above them
BAND_TIMED = 10000
BAND_VITAL = 30000
BAND_UNTIMED = 40000
BAND_TRACKER = 50000
KEY_GAP = 100


def task_ctx(task: Task, today: datetime.date) -> dict:
    """Everything a task row needs, precomputed so the template stays dumb."""
    overdue_days = 0
    if task.deadline and task.completed_at is None and task.deadline < today:
        overdue_days = (today - task.deadline).days
    stale = (
        task.completed_at is None
        and task.last_user_interaction_at < utc_now() - datetime.timedelta(days=STALE_AFTER_DAYS)
    )
    return {
        "kind": "task",
        "task": task,
        "today": today,
        "tomorrow": today + datetime.timedelta(days=1),
        "overdue_days": overdue_days,
        "stale": stale,
    }


def tracker_status(tracker: Tracker, state: TrackerState, done_today: bool) -> dict | None:
    """One agenda row for a tracker, or None if it has nothing to say today.

    A done tracker stays visible crossed out. A due one reports how far past
    its limit it is, task-style. One coming due within two days of the last
    doing shows as a reminder. Freshly done ones stay off the list.
    """
    row: dict[str, Any] = {"kind": "tracker", "tracker": tracker, "state": state}
    if done_today:
        row["status"] = "done"
    elif state.last_done_date is None:
        row["status"] = "never"
    elif state.days_since_last_done >= tracker.threshold_days:
        row["status"] = "due"
        row["overdue_by"] = state.days_since_last_done - tracker.threshold_days
    elif state.days_since_last_done >= 2:
        row["status"] = "soon"
        row["due_in"] = tracker.threshold_days - state.days_since_last_done
    else:
        return None
    return row


def default_key(row: dict) -> int:
    """Where a row sits on the shared ordering scale when it has no manual key."""
    if row["kind"] == "event":
        t = row["event"].start_at.time()
        return BAND_TIMED + t.hour * 60 + t.minute
    if row["kind"] == "task":
        task = row["task"]
        if task.scheduled_time:
            return BAND_TIMED + task.scheduled_time.hour * 60 + task.scheduled_time.minute
        return (BAND_VITAL if task.vital else BAND_UNTIMED) + task.id
    rank = {"due": 0, "soon": 1, "never": 2, "done": 3}[row["status"]]
    return BAND_TRACKER + rank * KEY_GAP + row["tracker"].id


def effective_key(row: dict) -> int:
    if row["kind"] == "task" and row["task"].sort_order is not None:
        return row["task"].sort_order
    if row["kind"] == "tracker" and row["tracker"].sort_order is not None:
        return row["tracker"].sort_order
    return default_key(row)


def day_list(today: datetime.date) -> list[dict]:
    """Today's sortable sequence: events, today-tasks and trackers, by key."""
    tasks = db_session.scalars(
        select(Task).where(Task.archived_at.is_(None), Task.completed_at.is_(None))
    ).all()
    rows: list[dict] = []
    for t in tasks:
        if t.remind_after and t.remind_after > today:
            continue
        ctx = task_ctx(t, today)
        task_day = t.scheduled_for or t.deadline
        if not ctx["overdue_days"] and (task_day == today or t.vital):
            rows.append(ctx)

    events = db_session.scalars(select(CalendarEvent).where(CalendarEvent.date == today)).all()
    rows.extend({"kind": "event", "event": e} for e in events)

    states = {s.tracker_id: s for s in db_session.scalars(select(TrackerState))}
    done_today = {
        e.tracker_id
        for e in db_session.scalars(select(TrackerEvent).where(TrackerEvent.date == today))
        if e.activity_done
    }
    for tracker in db_session.scalars(select(Tracker).where(Tracker.archived_at.is_(None))):
        state = states.get(tracker.id)
        if state is None or today.weekday() not in tracker.visible_on_days:
            continue
        row = tracker_status(tracker, state, tracker.id in done_today)
        if row is not None:
            rows.append(row)

    rows.sort(key=effective_key)
    return rows


def build_agenda(today: datetime.date) -> dict:
    """Overdue block, the day list, finished tasks, and the upcoming groups."""
    tasks = db_session.scalars(
        select(Task).where(Task.archived_at.is_(None), Task.completed_at.is_(None))
    ).all()

    overdue: list[dict] = []
    upcoming: dict[datetime.date, list[dict]] = {}
    for t in tasks:
        if t.remind_after and t.remind_after > today:
            continue
        ctx = task_ctx(t, today)
        task_day = t.scheduled_for or t.deadline
        if ctx["overdue_days"]:
            overdue.append(ctx)
        elif (
            not t.vital
            and task_day
            and today < task_day <= today + datetime.timedelta(days=UPCOMING_DAYS)
        ):
            upcoming.setdefault(task_day, []).append(ctx)
    overdue.sort(key=lambda r: -r["overdue_days"])

    finished = [
        task_ctx(t, today)
        for t in db_session.scalars(
            select(Task).where(
                Task.archived_at.is_(None),
                Task.completed_at.isnot(None),
                Task.completed_at >= local_day_start_utc(today),
            )
        )
    ]

    upcoming_groups = [
        {
            "date": day,
            "label": "Tomorrow"
            if day == today + datetime.timedelta(days=1)
            else day.strftime("%A %d.%m"),
            "rows": rows,
        }
        for day, rows in sorted(upcoming.items())
    ]

    return {
        "overdue": overdue,
        "day": day_list(today),
        "finished": finished,
        "upcoming": upcoming_groups,
    }


def snapshot(today: datetime.date) -> dict:
    """Read-only side column: streaks, tracker schedules, latest metrics."""
    streaks = db_session.scalars(
        select(Streak).where(Streak.archived_at.is_(None)).order_by(Streak.id)
    ).all()
    states = {s.streak_id: s for s in db_session.scalars(select(StreakState))}
    logged_today = {
        e.streak_id for e in db_session.scalars(select(HabitLog).where(HabitLog.date == today))
    }
    streak_rows = [
        {"streak": s, "state": states.get(s.id), "logged": s.id in logged_today} for s in streaks
    ]

    # every active tracker with its full schedule, whatever today's weekday
    tstates = {s.tracker_id: s for s in db_session.scalars(select(TrackerState))}
    tracker_rows = []
    for t in db_session.scalars(
        select(Tracker).where(Tracker.archived_at.is_(None)).order_by(Tracker.id)
    ):
        state = tstates.get(t.id)
        info: dict[str, Any] = {"tracker": t, "state": state}
        if state and state.last_done_date:
            info["overdue_by"] = state.days_since_last_done - t.threshold_days
        tracker_rows.append(info)

    metrics = db_session.scalars(
        select(Metric).where(Metric.archived_at.is_(None)).order_by(Metric.id)
    ).all()
    metric_rows = []
    for m in metrics:
        latest = db_session.scalars(
            select(MetricLog).where(MetricLog.metric_id == m.id).order_by(MetricLog.date.desc())
        ).first()
        metric_rows.append({"metric": m, "latest": latest})

    return {"streaks": streak_rows, "trackers": tracker_rows, "metrics": metric_rows}


@bp.get("/")
def index() -> str:
    today = current_date()
    return render_template(
        "dashboard.html",
        today=today,
        agenda=build_agenda(today),
        snap=snapshot(today),
        plan=request.args.get("plan") == "1",
        weather=weather_summary(db_session(), today),
    )


def render_agenda(plan: bool) -> str:
    today = current_date()
    return render_template(
        "fragments/agenda.html", agenda=build_agenda(today), today=today, plan=plan
    )


def record_event(task: Task, action: str, payload: dict | None = None) -> None:
    db_session.add(TaskEvent(task_id=task.id, action=action, payload=payload))
    task.last_user_interaction_at = utc_now()


def parse_optional_time(raw: str) -> datetime.time | None:
    if not raw:
        return None
    try:
        return datetime.time.fromisoformat(raw)
    except ValueError:
        abort(400)


@bp.post("/tasks/add")
def add_task() -> Response:
    name = request.form.get("name", "").strip()
    if not name:
        abort(400)
    try:
        date = datetime.date.fromisoformat(request.form.get("date", ""))
    except ValueError:
        abort(400)

    # "due" makes the date a deadline, "planned" a scheduled day
    kind = request.form.get("kind", "planned")
    task = Task(
        name=name,
        deadline=date if kind == "due" else None,
        scheduled_for=date if kind == "planned" else None,
        scheduled_time=parse_optional_time(request.form.get("time", "")),
        vital=request.form.get("vital") == "on",
    )
    db_session.add(task)
    db_session.flush()
    record_event(task, "created")
    db_session.commit()
    return redirect(url_for("dashboard.index"))


@bp.post("/tasks/<int:task_id>/toggle")
def toggle_task(task_id: int) -> str:
    task = db_session.get(Task, task_id)
    if task is None:
        abort(404)
    if task.completed_at is None:
        task.completed_at = utc_now()
        record_event(task, "completed")
    else:
        task.completed_at = None
        record_event(task, "uncompleted")
    db_session.commit()
    ctx = task_ctx(task, current_date())
    ctx["plan"] = request.args.get("plan") == "1"
    return render_template("fragments/task_row.html", **ctx)


@bp.post("/tasks/<int:task_id>/vital")
def toggle_vital(task_id: int) -> str:
    task = db_session.get(Task, task_id)
    if task is None:
        abort(404)
    task.vital = not task.vital
    record_event(task, "edited", {"field": "vital", "new": task.vital})
    db_session.commit()
    ctx = task_ctx(task, current_date())
    ctx["plan"] = request.args.get("plan") == "1"
    return render_template("fragments/task_row.html", **ctx)


@bp.post("/tasks/<int:task_id>/reschedule")
def reschedule_task(task_id: int) -> str:
    """Give the task a new date, as a planned day or as a deadline.

    The chosen kind replaces the task's temporal context: rescheduling a
    deadline task as scheduled clears the deadline, and the other way round.
    One date, one meaning.
    """
    task = db_session.get(Task, task_id)
    if task is None:
        abort(404)
    kind = request.form.get("kind", "planned")
    try:
        date = datetime.date.fromisoformat(request.form.get("date", ""))
    except ValueError:
        abort(400)
    old = {
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "scheduled_for": task.scheduled_for.isoformat() if task.scheduled_for else None,
    }
    task.deadline = date if kind == "due" else None
    task.scheduled_for = date if kind == "planned" else None
    task.scheduled_time = parse_optional_time(request.form.get("time", ""))
    record_event(
        task,
        "edited",
        {"field": "reschedule", "old": old, "new": {"kind": kind, "date": date.isoformat()}},
    )
    db_session.commit()
    ctx = task_ctx(task, current_date())
    ctx["plan"] = request.args.get("plan") == "1"
    return render_template("fragments/task_row.html", **ctx)


@bp.post("/tasks/<int:task_id>/keep")
def keep_task(task_id: int) -> str:
    """Stale-menu action: still wanted, which resets the staleness clock."""
    task = db_session.get(Task, task_id)
    if task is None:
        abort(404)
    record_event(task, "edited", {"field": "kept"})
    db_session.commit()
    ctx = task_ctx(task, current_date())
    ctx["plan"] = request.args.get("plan") == "1"
    return render_template("fragments/task_row.html", **ctx)


@bp.post("/tasks/<int:task_id>/archive")
def archive_task(task_id: int) -> str:
    task = db_session.get(Task, task_id)
    if task is None:
        abort(404)
    task.archived_at = utc_now()
    record_event(task, "archived")
    db_session.commit()
    # the button uses hx-swap="delete"; the row disappears, no body needed
    return ""


@bp.post("/tasks/<int:task_id>/delete")
def delete_task(task_id: int) -> str:
    task = db_session.get(Task, task_id)
    if task is None:
        abort(404)
    # task_events reference the task, so they go first
    for event in db_session.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id)):
        db_session.delete(event)
    db_session.delete(task)
    db_session.commit()
    return ""


@bp.post("/trackers/<int:tracker_id>/toggle")
def toggle_tracker(tracker_id: int) -> str:
    """Mark a tracker done for today, or take it back.

    Returns the agenda plus the sidebar tracker card as an out-of-band swap,
    so both views of the same state change together.
    """
    tracker = db_session.get(Tracker, tracker_id)
    if tracker is None:
        abort(404)
    today = current_date()
    event = db_session.get(TrackerEvent, (today, tracker_id))
    if event is None:
        db_session.add(TrackerEvent(date=today, tracker_id=tracker_id, activity_done=True))
    else:
        db_session.delete(event)
    recompute_tracker_state(db_session(), tracker_id, today=today)
    db_session.commit()
    card = render_template(
        "fragments/tracker_card.html", trackers=snapshot(today)["trackers"], oob=True
    )
    return render_agenda(plan=request.args.get("plan") == "1") + card


@bp.post("/trackers/<int:tracker_id>/edit")
def edit_tracker(tracker_id: int) -> str:
    """Adjust a tracker's cadence and weekdays from its agenda menu."""
    tracker = db_session.get(Tracker, tracker_id)
    if tracker is None:
        abort(404)
    threshold_raw = request.form.get("threshold_days", "")
    if not threshold_raw.isdigit() or int(threshold_raw) < 1:
        abort(400)
    days = sorted(int(d) for d in request.form.getlist("visible_on_days"))
    tracker.threshold_days = int(threshold_raw)
    tracker.visible_on_days = days if days else [0, 1, 2, 3, 4, 5, 6]
    db_session.commit()
    card = render_template(
        "fragments/tracker_card.html", trackers=snapshot(current_date())["trackers"], oob=True
    )
    return render_agenda(plan=request.args.get("plan") == "1") + card


@bp.post("/trackers/<int:tracker_id>/delete")
def delete_tracker(tracker_id: int) -> str:
    """Delete a tracker with its whole history. Guarded by a confirm dialog."""
    tracker = db_session.get(Tracker, tracker_id)
    if tracker is None:
        abort(404)
    # children first: events and the state row reference the tracker
    for event in db_session.scalars(
        select(TrackerEvent).where(TrackerEvent.tracker_id == tracker_id)
    ):
        db_session.delete(event)
    state = db_session.get(TrackerState, tracker_id)
    if state is not None:
        db_session.delete(state)
    db_session.delete(tracker)
    db_session.commit()
    card = render_template(
        "fragments/tracker_card.html", trackers=snapshot(current_date())["trackers"], oob=True
    )
    return render_agenda(plan=request.args.get("plan") == "1") + card


@bp.post("/agenda/move")
def move_item() -> str:
    """Move a task or tracker within the day list.

    The new position becomes a key between the new neighbours' keys, so fixed
    calendar events need no bookkeeping: everything just sorts.
    """
    key = request.form.get("item", "")
    direction = request.form.get("dir", "")
    if direction not in ("top", "up", "down", "bottom") or len(key) < 2:
        abort(400)

    today = current_date()
    rows = day_list(today)
    keys = [
        ("t" + str(r["task"].id))
        if r["kind"] == "task"
        else ("k" + str(r["tracker"].id))
        if r["kind"] == "tracker"
        else "e"
        for r in rows
    ]
    if key not in keys:
        abort(404)
    index = keys.index(key)
    ordered = [effective_key(r) for r in rows]

    if direction == "top":
        new_key = ordered[0] - KEY_GAP
    elif direction == "bottom":
        new_key = ordered[-1] + KEY_GAP
    elif direction == "up":
        if index == 0:
            new_key = ordered[0]
        elif index == 1:
            new_key = ordered[0] - KEY_GAP
        else:
            new_key = (ordered[index - 2] + ordered[index - 1]) // 2
    else:
        if index == len(rows) - 1:
            new_key = ordered[-1]
        elif index == len(rows) - 2:
            new_key = ordered[-1] + KEY_GAP
        else:
            new_key = (ordered[index + 1] + ordered[index + 2]) // 2
    # repeated midpoints can exhaust the gap between two keys; if that ever
    # bites, Reset order clears the manual keys and heals the scale

    row = rows[index]
    if row["kind"] == "task":
        row["task"].sort_order = new_key
    else:
        row["tracker"].sort_order = new_key
    db_session.commit()
    return render_agenda(plan=True)


@bp.post("/agenda/reset")
def reset_order() -> str:
    """Back to the default ordering: clear every manual position."""
    for task in db_session.scalars(select(Task).where(Task.sort_order.isnot(None))):
        task.sort_order = None
    for tracker in db_session.scalars(select(Tracker).where(Tracker.sort_order.isnot(None))):
        tracker.sort_order = None
    db_session.commit()
    return render_agenda(plan=True)


@bp.post("/add/streak")
def add_streak() -> Response:
    name = request.form.get("name", "").strip()
    if not name:
        abort(400)
    db_session.add(Streak(name=name, description=request.form.get("description") or None))
    db_session.commit()
    return redirect(url_for("dashboard.index"))


@bp.post("/add/tracker")
def add_tracker() -> Response:
    name = request.form.get("name", "").strip()
    threshold_raw = request.form.get("threshold_days", "")
    if not name or not threshold_raw.isdigit() or int(threshold_raw) < 1:
        abort(400)
    days = [int(d) for d in request.form.getlist("visible_on_days")]
    tracker = Tracker(
        name=name,
        threshold_days=int(threshold_raw),
        visible_on_days=sorted(days) if days else [0, 1, 2, 3, 4, 5, 6],
    )
    db_session.add(tracker)
    db_session.flush()
    # without its state row the tracker would never reach the agenda
    recompute_tracker_state(db_session(), tracker.id, today=current_date())
    db_session.commit()
    return redirect(url_for("dashboard.index"))


@bp.post("/add/metric")
def add_metric() -> Response:
    name = request.form.get("name", "").strip()
    metric_type = request.form.get("metric_type", "")
    if not name or metric_type not in ("scale", "numeric"):
        abort(400)
    metric = Metric(name=name, metric_type=metric_type)
    if metric_type == "scale":
        metric.scale_min, metric.scale_max = 1, 5
    else:
        metric.unit = request.form.get("unit") or None
    db_session.add(metric)
    db_session.commit()
    return redirect(url_for("dashboard.index"))
