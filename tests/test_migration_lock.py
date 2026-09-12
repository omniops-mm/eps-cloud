"""Run with EPS_TEST_POSTGRES_URL pointing to the disposable CI database."""

import os

import pytest
from sqlalchemy import create_engine

from app.migration_lock import migration_lock


@pytest.mark.skipif(not os.environ.get("EPS_TEST_POSTGRES_URL"), reason="PostgreSQL not configured")
def test_lock_excludes_another_connection_and_releases_after_error() -> None:
    engine = create_engine(os.environ["EPS_TEST_POSTGRES_URL"])
    with engine.connect() as first, engine.connect() as second:
        with pytest.raises(ValueError, match="test failure"), migration_lock(first):
            with pytest.raises(TimeoutError), migration_lock(second, timeout=0):
                pytest.fail("Concurrent migration entered the critical section")
            raise ValueError("test failure")
        with migration_lock(second, timeout=0):
            pass
    engine.dispose()
