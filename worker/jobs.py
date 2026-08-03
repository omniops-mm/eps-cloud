"""Background jobs, and a way to run exactly one of them.

Every job is a function that takes a database session and returns how many rows
it touched. That shape means the scheduler, a test and the command line can all
call the same function without knowing about each other, and it gives every run
something worth logging.

Run one directly, which is also how an external scheduler would invoke a single
job without this project's own scheduler being involved:

    python -m worker.jobs cleanup-audit-log
"""

import argparse
import contextlib
import datetime
import sys
from collections.abc import Callable, Iterator
from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, delete
from sqlalchemy.orm import Session

from app import db
from app.clock import current_date, utc_now
from app.integrations.brightsky import weather_summary
from app.logging import configure_logging
from app.models import EditLog

log = structlog.get_logger("worker")

# how long an edit stays in the audit trail
AUDIT_RETENTION_DAYS = 180

# how far ahead the forecast is warmed, so opening the app never waits on a fetch
WEATHER_LOOKAHEAD_DAYS = 7


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """A bound session that commits on success and rolls back on failure.

    The worker has no request cycle to hang this on, so each job gets its own
    session and gives the connection straight back. Binding here rather than at
    import means a job can be run without a database being reachable at the
    moment the module loads.
    """
    # reached through the module rather than imported by name, so that whatever
    # binds the engine elsewhere is what this uses too
    #
    # remove() first: configure() cannot change a session that already exists,
    # so a scope that inherited one would quietly run against the wrong bind
    db.db_session.remove()
    db.db_session.configure(bind=db.get_engine())
    session = db.db_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        db.db_session.remove()


def cleanup_audit_log(session: Session) -> int:
    """Drop audit rows past the retention window.

    Issued as one DELETE rather than loading the rows and deleting them one at a
    time, because nothing here needs the rows themselves and a year of edits is
    a lot of objects to build just to throw away.
    """
    cutoff = utc_now() - datetime.timedelta(days=AUDIT_RETENTION_DAYS)
    # execute() is typed as returning a plain Result, which has no row count.
    # A DELETE always produces a CursorResult, which does.
    result = cast(
        "CursorResult[Any]", session.execute(delete(EditLog).where(EditLog.timestamp < cutoff))
    )
    return int(result.rowcount)


def refresh_weather(session: Session) -> int:
    """Warm the forecast cache for today and the days ahead.

    The dashboard fetches a missing day itself, so this job is not what makes
    weather work. It is what stops the first person to open the app each morning
    paying for the network call. Returns how many days ended up cached, so a run
    that quietly achieved nothing is visible in the logs.
    """
    today = current_date()
    warmed = 0
    for offset in range(WEATHER_LOOKAHEAD_DAYS + 1):
        if weather_summary(session, today + datetime.timedelta(days=offset)) is not None:
            warmed += 1
    return warmed


JOBS: dict[str, Callable[[Session], int]] = {
    "cleanup-audit-log": cleanup_audit_log,
    "refresh-weather": refresh_weather,
}


def run(name: str) -> int:
    """Run one job by name in its own session, and say what it did."""
    job = JOBS[name]
    with session_scope() as session:
        touched = job(session)
    log.info("job finished", job=name, rows=touched)
    return touched


def main(argv: list[str] | None = None) -> int:
    """Command line entry point. Returns a process exit code."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Run one background job.")
    parser.add_argument("job", choices=sorted(JOBS), help="which job to run")
    args = parser.parse_args(argv)
    try:
        run(args.job)
    except Exception:
        # a failed job should end the process with a non-zero code, so whatever
        # invoked it can tell, but it must not take the traceback to stdout
        log.exception("job failed", job=args.job)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
