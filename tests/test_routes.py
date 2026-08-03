"""Route-level tests for the calendar and the day view.

These cover the rules that are easy to break silently later: which tasks belong
to a day, whether a day view knows it is not today, and that a tracker can be
corrected after the fact. The recompute suite covers the counting itself.
"""

import datetime

import pytest
from flask.testing import FlaskClient

from app.clock import current_date, utc_now
from app.db import db_session
from app.models import Task, TrackerEvent
from app.routes.journal import day_items

from .conftest import Builder


def add_task(**kwargs: object) -> Task:
    task = Task(**kwargs)  # type: ignore[arg-type]
    db_session.add(task)
    db_session.flush()
    return task


class TestCalendar:
    def test_month_renders(self, client: FlaskClient) -> None:
        assert client.get("/calendar/2026/8").status_code == 200

    def test_today_redirects_to_this_month(self, client: FlaskClient) -> None:
        response = client.get("/calendar/")
        assert response.status_code == 302
        today = current_date()
        assert response.headers["Location"].endswith(f"/calendar/{today.year}/{today.month}")

    @pytest.mark.parametrize("path", ["/calendar/2026/0", "/calendar/2026/13", "/calendar/1969/5"])
    def test_out_of_range_is_not_found(self, client: FlaskClient, path: str) -> None:
        assert client.get(path).status_code == 404

    def test_grid_covers_whole_weeks(self, client: FlaskClient) -> None:
        """Every rendered month is a whole number of Monday-to-Sunday weeks."""
        body = client.get("/calendar/2026/8").get_data(as_text=True)
        assert body.count('href="/journal/') % 7 == 0


class TestDayBelonging:
    """Which tasks a given day owns. Everything here goes through day_items."""

    def test_task_finished_earlier_is_gone(self, client: FlaskClient) -> None:
        today = current_date()
        add_task(
            name="done last week",
            deadline=today - datetime.timedelta(days=10),
            completed_at=utc_now() - datetime.timedelta(days=8),
        )
        assert day_items(today) == []

    def test_task_finished_later_still_shows_as_open(self, client: FlaskClient) -> None:
        """A task closed next week was open on the day you are looking at."""
        today = current_date()
        earlier = today - datetime.timedelta(days=3)
        add_task(
            name="closed later",
            deadline=today,
            created_at=utc_now() - datetime.timedelta(days=10),
            completed_at=utc_now(),
        )
        rows = day_items(earlier)
        assert [row["done_here"] for row in rows] == [False]
        assert day_items(today)[0]["done_here"] is True

    def test_scheduled_task_belongs_to_its_day_only(self, client: FlaskClient) -> None:
        today = current_date()
        add_task(name="one off", scheduled_for=today)
        assert len(day_items(today)) == 1
        assert day_items(today - datetime.timedelta(days=1)) == []

    def test_reminder_hides_a_task_until_its_date(self, client: FlaskClient) -> None:
        today = current_date()
        add_task(
            name="not yet",
            scheduled_for=today,
            remind_after=today + datetime.timedelta(days=2),
        )
        assert day_items(today) == []


class TestDayView:
    def test_today_has_no_tasks_card(self, client: FlaskClient) -> None:
        """Today's journal is the wind-down surface; the dashboard owns tasks."""
        response = client.get(f"/journal/{current_date().isoformat()}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # absence assertions need proof the page rendered, or an error body passes
        assert 'card-title">Habits' in body
        assert 'card-title">Tasks' not in body

    def test_other_days_have_a_tasks_card(self, client: FlaskClient) -> None:
        past = (current_date() - datetime.timedelta(days=4)).isoformat()
        body = client.get(f"/journal/{past}").get_data(as_text=True)
        assert 'card-title">Tasks' in body

    def test_other_days_drop_the_dashboard_pill(self, client: FlaskClient) -> None:
        """The "back to today" link is already the way home, so the pill goes."""
        past = (current_date() - datetime.timedelta(days=4)).isoformat()
        response = client.get(f"/journal/{past}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert 'class="back-to-today" href="/"' in body
        assert ">Dashboard<" not in body

    def test_a_future_day_is_not_called_a_past_one(self, client: FlaskClient) -> None:
        ahead = (current_date() + datetime.timedelta(days=9)).isoformat()
        body = client.get(f"/journal/{ahead}").get_data(as_text=True)
        assert "viewing a future day" in body

    def test_unparseable_date_is_not_found(self, client: FlaskClient) -> None:
        assert client.get("/journal/not-a-date").status_code == 404


class TestTrackerCorrection:
    def test_marking_a_past_day_is_reversible(self, client: FlaskClient, web: Builder) -> None:
        """The agenda can only toggle today, so past days are corrected here."""
        tracker_id = web.tracker()
        day = current_date() - datetime.timedelta(days=5)
        path = f"/journal/{day.isoformat()}/tracker/{tracker_id}"

        assert client.post(path).status_code == 200
        assert db_session.get(TrackerEvent, (day, tracker_id)) is not None

        assert client.post(path).status_code == 200
        assert db_session.get(TrackerEvent, (day, tracker_id)) is None

    def test_unknown_tracker_is_not_found(self, client: FlaskClient) -> None:
        day = current_date().isoformat()
        assert client.post(f"/journal/{day}/tracker/999").status_code == 404
