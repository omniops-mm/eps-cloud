"""Route-level tests for the dashboard, the calendar and the day view.

These cover the rules that are easy to break silently later: which tasks belong
to a day, how far ahead the dashboard looks and how much of each day it shows,
whether a day view knows it is not today, and that a tracker can be corrected
after the fact. The recompute suite covers the counting itself.
"""

import datetime

import pytest
from flask.testing import FlaskClient

from app.clock import current_date, local_day_start_utc, utc_now, zone_name
from app.db import db_session
from app.models import StreakState, Task, TrackerEvent
from app.preferences import grace_enabled, read
from app.routes.dashboard import build_agenda
from app.routes.journal import day_items

from .conftest import Builder


def add_task(**kwargs: object) -> Task:
    task = Task(**kwargs)  # type: ignore[arg-type]
    db_session.add(task)
    db_session.flush()
    return task


class TestUpcomingDays:
    """The preview of the days ahead: how far it reaches, and how much it shows."""

    def group_for(self, day: datetime.date) -> dict:
        groups = build_agenda(current_date())["upcoming"]
        return next(group for group in groups if group["date"] == day)

    def test_a_day_shows_three_tasks_at_most(self, client: FlaskClient) -> None:
        day = current_date() + datetime.timedelta(days=3)
        for n in range(5):
            add_task(name=f"task {n}", scheduled_for=day)

        group = self.group_for(day)
        assert len(group["rows"]) == 3
        assert group["hidden"] == 2

    def test_a_short_day_hides_nothing(self, client: FlaskClient) -> None:
        day = current_date() + datetime.timedelta(days=3)
        add_task(name="only one", scheduled_for=day)
        assert self.group_for(day)["hidden"] == 0

    def test_the_shown_tasks_are_that_days_first_ones(self, client: FlaskClient) -> None:
        """Timed tasks outrank untimed ones, so the preview is not arbitrary."""
        day = current_date() + datetime.timedelta(days=2)
        add_task(name="afternoon", scheduled_for=day, scheduled_time=datetime.time(16, 0))
        add_task(name="morning", scheduled_for=day, scheduled_time=datetime.time(8, 0))
        add_task(name="whenever", scheduled_for=day)
        add_task(name="also whenever", scheduled_for=day)

        names = [row["task"].name for row in self.group_for(day)["rows"]]
        assert names == ["morning", "afternoon", "whenever"]

    def test_the_window_stops_after_a_week(self, client: FlaskClient) -> None:
        """Seven days is the promise, so the number is spelled out here."""
        today = current_date()
        seventh = today + datetime.timedelta(days=7)
        eighth = today + datetime.timedelta(days=8)
        add_task(name="just inside", scheduled_for=seventh)
        add_task(name="just outside", scheduled_for=eighth)

        days = [group["date"] for group in build_agenda(today)["upcoming"]]
        assert seventh in days
        assert eighth not in days

    def test_a_trimmed_day_says_how_many_it_is_holding_back(self, client: FlaskClient) -> None:
        day = current_date() + datetime.timedelta(days=2)
        for n in range(5):
            add_task(name=f"task {n}", scheduled_for=day)
        db_session.commit()

        assert "+2 more" in client.get("/").get_data(as_text=True)

    def test_each_heading_links_to_that_days_page(self, client: FlaskClient) -> None:
        """The same destination the calendar cells use."""
        day = current_date() + datetime.timedelta(days=2)
        add_task(name="ahead", scheduled_for=day)
        db_session.commit()

        body = client.get("/").get_data(as_text=True)
        assert f'href="/journal/{day.isoformat()}"' in body


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
        """Any day but today offers exactly one way back, the inline link."""
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


class TestPreferences:
    def test_page_renders_on_an_install_with_no_saved_settings(self, client: FlaskClient) -> None:
        """A fresh database has no settings row; the page must still work."""
        assert read(db_session()) is None
        response = client.get("/settings/")
        assert response.status_code == 200
        assert read(db_session()) is not None

    def test_grace_toggle_persists_and_reverses(self, client: FlaskClient) -> None:
        client.get("/settings/")
        before = grace_enabled(db_session())
        client.post("/settings/grace")
        assert grace_enabled(db_session()) is not before
        client.post("/settings/grace")
        assert grace_enabled(db_session()) is before

    def earn_forgiveness(self, web: Builder) -> tuple[int, str]:
        """A streak long enough to absorb a miss, ending yesterday."""
        streak_id = web.streak()
        today = current_date()
        web.log(streak_id, "PPPPPPPP", start=today - datetime.timedelta(days=8))
        return streak_id, f"/journal/{today.isoformat()}/habit/{streak_id}/fail"

    def test_missing_a_day_is_forgiven_while_grace_is_on(
        self, client: FlaskClient, web: Builder
    ) -> None:
        streak_id, mark_missed = self.earn_forgiveness(web)
        client.get("/settings/")

        assert client.post(mark_missed).status_code == 200
        state = db_session.get(StreakState, streak_id)
        assert state is not None
        assert state.current_streak > 0

    def test_missing_a_day_resets_once_grace_is_off(
        self, client: FlaskClient, web: Builder
    ) -> None:
        """Goes through the route, so it fails if the setting stops being read."""
        streak_id, mark_missed = self.earn_forgiveness(web)
        client.get("/settings/")
        client.post("/settings/grace")

        assert client.post(mark_missed).status_code == 200
        state = db_session.get(StreakState, streak_id)
        assert state is not None
        assert state.current_streak == 0

    @pytest.mark.parametrize(
        ("field", "value"),
        [("lat", "999"), ("lat", "north"), ("lon", "-500"), ("lon", "")],
    )
    def test_impossible_coordinates_are_refused(
        self, client: FlaskClient, field: str, value: str
    ) -> None:
        client.get("/settings/")
        form = {"lat": "52.52", "lon": "13.405"} | {field: value}
        assert client.post("/settings/weather", data=form).status_code == 400

    def test_valid_location_is_saved(self, client: FlaskClient) -> None:
        client.get("/settings/")
        response = client.post("/settings/weather", data={"lat": "48.2", "lon": "16.37"})
        assert response.status_code == 302
        prefs = read(db_session())
        assert prefs is not None
        assert float(prefs.weather_location_lat) == pytest.approx(48.2)

    def test_saved_timezone_is_what_the_clock_uses(self, client: FlaskClient) -> None:
        """Proves the setting reaches the clock, not just the column."""
        client.get("/settings/")
        assert client.post("/settings/timezone", data={"timezone": "Pacific/Auckland"}).status_code
        assert zone_name() == "Pacific/Auckland"

    def test_timezone_moves_when_a_day_starts(self, client: FlaskClient) -> None:
        """Two zones put local midnight at different moments in UTC."""
        day = datetime.date(2026, 6, 1)
        client.get("/settings/")

        client.post("/settings/timezone", data={"timezone": "Europe/Berlin"})
        berlin = local_day_start_utc(day)

        client.post("/settings/timezone", data={"timezone": "Pacific/Auckland"})
        auckland = local_day_start_utc(day)

        assert berlin != auckland

    def test_an_unusable_saved_zone_is_refused(self, client: FlaskClient) -> None:
        client.get("/settings/")
        assert (
            client.post("/settings/timezone", data={"timezone": "Mars/Olympus"}).status_code == 400
        )

    def test_unparseable_fetch_time_is_refused(self, client: FlaskClient) -> None:
        client.get("/settings/")
        form = {"calendar_fetch_time": "25:99", "weather_fetch_time": "06:00"}
        assert client.post("/settings/fetch-times", data=form).status_code == 400


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
