"""Structured logging: one JSON object per line on stdout.

Containers treat stdout as the log stream, so no files and no rotation here;
whatever runs the container collects the lines. JSON because log collectors
parse it without regex guesswork.

Any logged field whose name suggests a credential is replaced with [redacted]
before the line is written. That makes "accidentally logged the database URL"
a non-event.
"""

import logging
import sys

import structlog
from structlog.typing import EventDict, WrappedLogger

# substrings that mark a field as sensitive, checked case-insensitively
SECRET_MARKERS = ("password", "secret", "token", "key", "credential", "database_url")


def redact_secrets(logger: WrappedLogger, method: str, event: EventDict) -> EventDict:
    """structlog processor: blank out any field that looks like a credential."""
    for field in event:
        name = field.lower()
        if any(marker in name for marker in SECRET_MARKERS):
            event[field] = "[redacted]"
    return event


def configure_logging() -> None:
    """Set up structlog and the stdlib root logger. Called once at startup."""
    logging.basicConfig(stream=sys.stdout, format="%(message)s", level=logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
