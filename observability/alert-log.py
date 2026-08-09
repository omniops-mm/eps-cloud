"""Prints every alert Alertmanager delivers, one JSON line per alert.

Runs as its own container. The point is a place where the full pipeline
(rule fires -> Alertmanager routes -> notification delivered) is visible:
docker compose logs alert-log.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9091


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            for alert in json.loads(body).get("alerts", []):
                line = {
                    "alert": alert.get("labels", {}).get("alertname"),
                    "status": alert.get("status"),
                    "labels": alert.get("labels"),
                }
                print(json.dumps(line), flush=True)
        except json.JSONDecodeError:
            print(json.dumps({"alert": "unparseable payload"}), flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Drop the built-in per-request access line; the JSON above is the log."""


HTTPServer(("", PORT), Handler).serve_forever()
