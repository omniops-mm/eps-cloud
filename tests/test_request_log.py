"""Request IDs and the per-request log line."""

import logging

import pytest
from flask.testing import FlaskClient

from app.request_log import request_id_from


class TestRequestId:
    def test_every_response_carries_an_id(self, client: FlaskClient) -> None:
        first = client.get("/").headers["X-Request-ID"]
        second = client.get("/").headers["X-Request-ID"]
        assert first and second and first != second

    def test_a_plausible_inbound_id_is_kept(self, client: FlaskClient) -> None:
        sent = "abc-123-DEF-456"
        assert client.get("/", headers={"X-Request-ID": sent}).headers["X-Request-ID"] == sent

    def test_an_implausible_inbound_id_is_replaced(self, client: FlaskClient) -> None:
        sent = 'x_{"forged": "entry"}'
        got = client.get("/", headers={"X-Request-ID": sent}).headers["X-Request-ID"]
        assert got != sent

    def test_the_pattern_rejects_the_edges(self) -> None:
        assert request_id_from("a" * 7) != "a" * 7  # too short
        assert request_id_from("a" * 65) != "a" * 65  # too long
        assert request_id_from(None)  # absent still yields one
        assert request_id_from("a" * 12) == "a" * 12  # plausible is kept


class TestRequestLogLine:
    def test_a_request_writes_one_line_and_a_probe_writes_none(
        self, client: FlaskClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="web"):
            client.get("/")
            client.get("/healthz")

        # the structlog pipeline hands stdlib a dict as the record message
        events = [r.msg for r in caplog.records if isinstance(r.msg, dict)]
        requests = [e for e in events if e.get("event") == "request"]
        assert len(requests) == 1
        assert requests[0]["path"] == "/"
        assert requests[0]["status"] == 200
        assert requests[0]["request_id"]
