import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location(
    "alert_log", Path(__file__).parents[1] / "observability/alert-log.py"
)
assert spec and spec.loader
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


def test_alert_payload_validation_and_label_minimization() -> None:
    invalid: tuple[Any, ...] = (
        [],
        {},
        {"alerts": {}},
        {"alerts": [None]},
        {"alerts": [{"status": "firing", "labels": {"secret": 3}}]},
        {"alerts": [{}] * 101},
    )
    for bad in invalid:
        with pytest.raises((TypeError, ValueError)):
            receiver.parse_alerts(json.dumps(bad).encode())
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "Watchdog", "credential": "discard-me"},
                "annotations": {"message": "private detail"},
            }
        ]
    }
    assert receiver.parse_alerts(json.dumps(payload).encode()) == [
        {"event": "alert", "status": "firing", "labels": {"alertname": "Watchdog"}}
    ]


def test_http_limits_and_private_field_filtering(capsys):
    import http.client
    import threading
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", 0), receiver.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=b"", headers=()):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=7)
        try:
            connection.putrequest(method, path)
            for key, value in headers:
                connection.putheader(key, value)
            connection.endheaders(body)
            response = connection.getresponse()
            assert response.read() == b""
            return response.status
        finally:
            connection.close()

    try:
        body = json.dumps(
            {
                "alerts": [
                    {
                        "status": "resolved",
                        "labels": {"alertname": "Watchdog", "credential": "must-not-be-logged"},
                        "annotations": {"detail": "private-note"},
                    }
                ]
            }
        ).encode()
        assert request("POST", "/", body, (("Content-Length", str(len(body))),)) == 200
        assert request("GET", "/healthz") == 200
        assert request("POST", "/other") == 404
        assert request("POST", "/") == 411
        assert request("POST", "/", headers=(("Content-Length", "invalid"),)) == 400
        assert request("POST", "/", headers=(("Content-Length", "65537"),)) == 413
        assert request("POST", "/", headers=(("Transfer-Encoding", "chunked"),)) == 400
        assert (
            request("POST", "/", headers=(("Content-Length", "0"), ("Content-Length", "0"))) == 400
        )
        assert request("POST", "/", b"{", (("Content-Length", "1"),)) == 400
        output = capsys.readouterr().out
        assert "Watchdog" in output and "resolved" in output
        assert "must-not-be-logged" not in output and "private-note" not in output
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
