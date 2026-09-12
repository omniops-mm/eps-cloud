"""Private path segments, library messages and exceptions stay out of stdout."""

import json
import logging

import pytest
import structlog
from flask.testing import FlaskClient

from app.logging import configure_logging


def test_request_and_exception_logs_omit_private_values(
    client: FlaskClient, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    marker = "private-log-marker"

    @client.application.get("/failure/<value>")
    def fail(value: str) -> str:
        raise ValueError(value)

    assert client.get(f"/failure/{marker}?token={marker}").status_code == 500
    assert client.get(f"/missing/{marker}").status_code == 404
    try:
        raise RuntimeError(marker)
    except RuntimeError:
        structlog.get_logger("worker").exception("job failed", password=marker)
    logging.getLogger("library").warning("request failed: %s", marker)
    output = capsys.readouterr().out
    assert marker not in output
    events = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert any(e.get("error_type") == "ValueError" for e in events)
    assert any(e.get("error_type") == "RuntimeError" for e in events)
    assert any(e.get("path") == "/failure/<value>" for e in events)
    assert any(e.get("path") == "unmatched" for e in events)
