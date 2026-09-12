"""Serialize PostgreSQL migrations across pods and operator commands."""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection

LOCK_ID = 45505304


@contextmanager
def migration_lock(connection: Connection, timeout: float = 120) -> Iterator[None]:
    if connection.dialect.name != "postgresql":
        yield
        return
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while not acquired:
            acquired = bool(
                connection.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": LOCK_ID})
            )
            # End SQLAlchemy's implicit transaction before Alembic starts its own.
            connection.commit()
            if not acquired:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another migration holds the database lock")
                time.sleep(0.2)
        yield
    finally:
        if acquired:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
            connection.commit()
