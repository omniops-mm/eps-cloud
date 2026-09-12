"""Unknown HTTP methods share one bounded Prometheus label value."""

from flask.testing import FlaskClient
from prometheus_client import REGISTRY

from tests.conftest import sample


def test_unknown_methods_share_one_counter(client: FlaskClient) -> None:
    labels = {"method": "OTHER", "route": "unmatched", "status": "405"}
    before = sample("eps_http_requests_total", **labels)
    for number in range(16):
        assert client.open("/", method=f"CUSTOM{number}").status_code == 405
    assert sample("eps_http_requests_total", **labels) - before == 16

    samples = REGISTRY.collect()
    methods = {
        entry.labels.get("method")
        for metric in samples
        for entry in metric.samples
        if entry.name == "eps_http_requests_total"
    }
    assert not any(f"CUSTOM{number}" in methods for number in range(16))

    known = {"method": "GET", "route": "/", "status": "200"}
    before_get = sample("eps_http_requests_total", **known)
    assert client.get("/").status_code == 200
    assert sample("eps_http_requests_total", **known) - before_get == 1
