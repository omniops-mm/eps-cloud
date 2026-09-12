"""The traffic generator writes through the same browser protection as a user."""

import http.cookiejar
import threading
import urllib.request

from flask.testing import FlaskClient
from werkzeug.serving import make_server

from scripts.traffic import hit


def test_traffic_obtains_cookie_and_csrf_token(client: FlaskClient) -> None:
    server = make_server("127.0.0.1", 0, client.application)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    try:
        assert (
            hit(opener, f"http://127.0.0.1:{server.server_port}", "POST", "/settings/grace") == 200
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
