"""One JSON log line per request, each carrying a request ID.

The ID is taken from the X-Request-ID header when a plausible one arrives,
otherwise generated. It is bound into structlog's context, so every line
logged while handling the request carries it, and it is returned in the
response header so a failed response can be matched to its log lines.
"""

import re
import time
import uuid

import structlog
from flask import Flask, Response, request

from app.metrics import UNCOUNTED_ROUTES

log = structlog.get_logger("web")

# Accepted from outside only if it looks like an ID. Anything else is
# replaced, so header content never reaches the logs unchecked.
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9-]{8,64}")


def request_id_from(header: str | None) -> str:
    """The inbound ID if it passes the pattern, otherwise a fresh one."""
    if header is not None and REQUEST_ID_PATTERN.fullmatch(header):
        return header
    return uuid.uuid4().hex


def init_app(app: Flask) -> None:
    @app.before_request
    def bind_request_id() -> None:
        # threads are reused across requests, so leftover context must go first
        structlog.contextvars.clear_contextvars()
        request_id = request_id_from(request.headers.get("X-Request-ID"))
        structlog.contextvars.bind_contextvars(request_id=request_id)
        request.request_id = request_id  # type: ignore[attr-defined]
        request.log_start = time.perf_counter()  # type: ignore[attr-defined]

    @app.after_request
    def log_request(response: Response) -> Response:
        # every response carries the ID, including probes; only the line is skipped
        response.headers["X-Request-ID"] = getattr(request, "request_id", "")
        route = request.url_rule.rule if request.url_rule else "unmatched"
        if route in UNCOUNTED_ROUTES:
            return response
        started = getattr(request, "log_start", None)
        duration_ms = None if started is None else round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "request",
            method=request.method,
            path=request.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response
