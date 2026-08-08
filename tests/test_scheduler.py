"""Worker startup behaviour."""

import pytest
from flask.testing import FlaskClient

from worker import jobs, scheduler


class TestStartupRuns:
    def test_every_job_runs_once(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran: list[str] = []

        def record(name: str) -> int:
            ran.append(name)
            return 0

        monkeypatch.setattr("worker.jobs.run", record)

        scheduler.run_every_job_once()

        assert sorted(ran) == sorted(jobs.JOBS)

    def test_one_failure_does_not_stop_the_others(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken job at startup must not cost the process its schedule."""
        ran: list[str] = []

        def flaky(name: str) -> int:
            ran.append(name)
            if name == min(jobs.JOBS):
                raise RuntimeError("job blew up")
            return 0

        monkeypatch.setattr("worker.jobs.run", flaky)

        scheduler.run_every_job_once()

        assert sorted(ran) == sorted(jobs.JOBS)
