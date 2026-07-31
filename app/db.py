"""Engine + session wiring. One engine per process, one session per request."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None

# unbound at import on purpose, tests bind their own sqlite engine
db_session = scoped_session(sessionmaker(autoflush=False))


def get_engine() -> Engine:
    """Lazy build the process-wide engine from Settings."""
    global _engine
    if _engine is None:
        # pool_pre_ping: swap out dead pooled connections instead of erroring
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
    return _engine


def init_app(app) -> None:
    """Bind the session factory and register per-request cleanup."""
    db_session.configure(bind=get_engine())

    @app.teardown_appcontext
    def remove_session(exc: BaseException | None) -> None:
        # after every request, error or not: rollback + connection back to pool
        db_session.remove()
