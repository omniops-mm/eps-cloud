"""Background job tests.

Each job is checked against a scratch database, and the weather one against a
stubbed forecast rather than the real service, so the suite stays offline and
does not depend on a third party being up.
"""

import datetime

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select

from app.clock import current_date, utc_now
from app.db import db_session
from app.models import EditLog, UserSettings, WeatherCache
from worker import jobs


def add_edit(age_days: int) -> None:
    """One audit row, backdated. The column defaults to now, so it is set here."""
    db_session.add(
        EditLog(
            timestamp=utc_now() - datetime.timedelta(days=age_days),
            table_name="habit_log",
            row_id="example",
            field="passed",
            old_value="True",
            new_value="False",
        )
    )
    db_session.flush()


class TestAuditCleanup:
    def test_old_rows_go_and_recent_ones_stay(self, client: FlaskClient) -> None:
        add_edit(age_days=jobs.AUDIT_RETENTION_DAYS + 5)
        add_edit(age_days=jobs.AUDIT_RETENTION_DAYS + 1)
        add_edit(age_days=10)

        removed = jobs.cleanup_audit_log(db_session())

        assert removed == 2
        surviving = db_session.scalars(select(EditLog)).all()
        assert len(surviving) == 1

    def test_a_row_exactly_at_the_limit_survives(self, client: FlaskClient) -> None:
        """The boundary is "older than", so the cutoff day itself is kept."""
        add_edit(age_days=jobs.AUDIT_RETENTION_DAYS - 1)
        assert jobs.cleanup_audit_log(db_session()) == 0

    def test_nothing_to_do_is_not_an_error(self, client: FlaskClient) -> None:
        assert jobs.cleanup_audit_log(db_session()) == 0


class TestWeatherRefresh:
    @pytest.fixture
    def stub_forecast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stand in for the weather service, so the suite never leaves the machine."""
        monkeypatch.setattr(
            "app.integrations.brightsky.fetch_forecast",
            lambda lat, lon, day: {
                "weather": [
                    {"temperature": 12.0, "precipitation_probability": 10},
                    {"temperature": 19.4, "precipitation_probability": 40},
                ]
            },
        )

    def test_warms_today_and_the_days_ahead(self, client: FlaskClient, stub_forecast: None) -> None:
        db_session.add(UserSettings(id=1))
        db_session.flush()

        warmed = jobs.refresh_weather(db_session())

        assert warmed == jobs.WEATHER_LOOKAHEAD_DAYS + 1
        assert len(db_session.scalars(select(WeatherCache)).all()) == warmed

    def test_running_twice_does_not_refetch(self, client: FlaskClient, stub_forecast: None) -> None:
        """Cached days are left alone, so repeating the job costs nothing."""
        db_session.add(UserSettings(id=1))
        db_session.flush()
        jobs.refresh_weather(db_session())
        before = db_session.get(WeatherCache, current_date())
        assert before is not None
        stamp = before.fetched_at

        jobs.refresh_weather(db_session())

        after = db_session.get(WeatherCache, current_date())
        assert after is not None
        assert after.fetched_at == stamp

    def test_no_saved_location_means_no_weather(self, client: FlaskClient) -> None:
        """Without a location there is nothing to ask for, and that is not a crash."""
        assert jobs.refresh_weather(db_session()) == 0


class TestDispatcher:
    def test_every_registered_job_is_runnable_by_name(self, client: FlaskClient) -> None:
        for name in jobs.JOBS:
            assert callable(jobs.JOBS[name])

    def test_unknown_name_is_rejected(self, client: FlaskClient) -> None:
        with pytest.raises(KeyError):
            jobs.run("no-such-job")

    def test_cli_refuses_an_unknown_job(self) -> None:
        """argparse rejects it before any of our code runs, and exits non-zero."""
        with pytest.raises(SystemExit) as exit_info:
            jobs.main(["no-such-job"])
        assert exit_info.value.code != 0

    def test_cli_reports_failure_as_an_exit_code(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A job that raises must not take the whole process down silently."""

        def explode(session: object) -> int:
            raise RuntimeError("job blew up")

        monkeypatch.setitem(jobs.JOBS, "cleanup-audit-log", explode)
        assert jobs.main(["cleanup-audit-log"]) == 1

    def test_cli_returns_zero_when_a_job_succeeds(self, client: FlaskClient) -> None:
        assert jobs.main(["cleanup-audit-log"]) == 0

    def test_a_scope_does_not_inherit_an_unbound_session(self, client: FlaskClient) -> None:
        """Reading settings before any bind exists leaves a dead session behind.

        A scoped session that already exists cannot be re-pointed by configure,
        so a scope that inherited one would run against nothing at all.
        """
        db_session.remove()
        db_session.configure(bind=None)
        db_session()

        with jobs.session_scope() as session:
            # any query at all proves the scope replaced the dead session
            assert session.get(UserSettings, 1) is None
