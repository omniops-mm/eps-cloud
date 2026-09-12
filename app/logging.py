"""JSON logs with credential fields removed and exception types retained."""

import logging
import sys

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

# substrings that mark a field as sensitive, checked case-insensitively
SECRET_MARKERS = ("password", "secret", "token", "key", "credential", "database_url")


def redact_secrets(logger: WrappedLogger, method: str, event: EventDict) -> EventDict:
    """Omit free-text library diagnostics and exception details."""
    record = event.get("_record")
    if isinstance(record, logging.LogRecord):
        # Library messages can embed SQL parameters, URLs or request bodies.
        event["logger"] = record.name
        event["event"] = "library_log"
    exception = event.pop("exc_info", None)
    if exception is True:
        exception = sys.exc_info()
    if isinstance(exception, tuple) and exception[0] is not None:
        event["error_type"] = exception[0].__name__
    event.pop("stack_info", None)
    event.pop("exception", None)
    for field in event:
        name = field.lower()
        if any(marker in name for marker in SECRET_MARKERS):
            event[field] = "[redacted]"
    return event


def configure_logging() -> None:
    """Set up logging so every line on stdout is JSON. Called once at startup.

    Libraries log through the standard library rather than through structlog, so
    routing the stdlib root logger into the same pipeline is what stops a
    scheduler or a web server writing plain text into a JSON stream. It also
    means their fields get the same redaction ours do.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_secrets,
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # applied to records from libraries, which arrive without our fields
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
