"""The worker process: runs each job on its cadence, and otherwise waits.

Times here are wall-clock times for whoever uses the app, not for the server.
The scheduler is built with the configured zone, so "03:00" survives the
container running UTC and survives daylight saving moving the offset underneath
it. Cadences that the settings page owns are read once at startup, so a change
applies on the next restart.

The jobs live in worker.jobs and know nothing about scheduling. That separation
means an external scheduler can call the same functions directly, without this
process running at all.
"""

import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.clock import zone
from app.logging import configure_logging
from app.preferences import read
from worker import jobs

log = structlog.get_logger("worker")

# no setting owns this one; it runs when nobody is using the app
AUDIT_CLEANUP_TIME = datetime.time(3, 0)
DEFAULT_WEATHER_FETCH_TIME = datetime.time(6, 0)


def weather_fetch_time() -> datetime.time:
    """When to warm the forecast, from the settings row if it has been saved."""
    with jobs.session_scope() as session:
        prefs = read(session)
        return prefs.weather_fetch_time if prefs else DEFAULT_WEATHER_FETCH_TIME


def build_scheduler() -> BlockingScheduler:
    """Wire every job to its trigger. Separate from main so a test can inspect it."""
    scheduler = BlockingScheduler(timezone=zone())

    at_weather = weather_fetch_time()
    scheduler.add_job(
        jobs.run,
        CronTrigger(hour=at_weather.hour, minute=at_weather.minute),
        args=["refresh-weather"],
        id="refresh-weather",
        # a missed run is pointless to catch up on: the next one fetches the
        # same days anyway, and firing several at once helps nobody
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        jobs.run,
        CronTrigger(hour=AUDIT_CLEANUP_TIME.hour, minute=AUDIT_CLEANUP_TIME.minute),
        args=["cleanup-audit-log"],
        id="cleanup-audit-log",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler


def main() -> None:
    configure_logging()
    scheduler = build_scheduler()
    for job in scheduler.get_jobs():
        log.info("job scheduled", job=job.id, trigger=str(job.trigger))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        # container stop: let the process end quietly rather than as a crash
        log.info("worker stopping")


if __name__ == "__main__":
    main()
