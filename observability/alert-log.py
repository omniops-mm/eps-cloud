"""Receive bounded Alertmanager notifications and log selected labels as JSON."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_BODY = 65536
LABELS = {"alertname", "severity", "namespace", "pod", "cronjob", "job"}


def parse_alerts(body: bytes) -> list[dict[str, object]]:
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise TypeError("Expected an alerts array")
    if len(payload["alerts"]) > 100:
        raise ValueError("Too many alerts")
    result = []
    for alert in payload["alerts"]:
        if not isinstance(alert, dict) or alert.get("status") not in {"firing", "resolved"}:
            raise ValueError("Invalid alert")
        labels = alert.get("labels")
        if not isinstance(labels, dict) or any(not isinstance(v, str) for v in labels.values()):
            raise ValueError("Invalid labels")
        result.append(
            {
                "event": "alert",
                "status": alert["status"],
                "labels": {k: v[:256] for k, v in labels.items() if k in LABELS},
            }
        )
    return result


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def respond(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self.respond(200 if self.path == "/healthz" else 404)

    def do_POST(self) -> None:
        if self.path != "/":
            self.respond(404)
            return
        if self.headers.get("Transfer-Encoding"):
            self.respond(400)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            self.respond(400)
            return
        length = lengths[0] if lengths else None
        if length is None:
            self.respond(411)
            return
        try:
            size = int(length)
        except ValueError:
            self.respond(400)
            return
        if size < 0 or size > MAX_BODY:
            self.respond(413)
            return
        try:
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError("Incomplete body")
            lines = parse_alerts(body)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            self.respond(400)
            return
        except TimeoutError:
            self.respond(408)
            return
        for line in lines:
            print(json.dumps(line), flush=True)
        self.respond(200)

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9091), Handler).serve_forever()
