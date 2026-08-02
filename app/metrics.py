"""Prometheus metrics: request counts and latencies, served at /metrics.

The counters live in this process's memory. Prometheus scrapes the endpoint on
an interval and does the storage and math; the app only counts.
"""

import time

from flask import Flask, Response, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter(
    "eps_http_requests_total",
    "HTTP requests handled, by method, route pattern and status code.",
    ["method", "route", "status"],
)

LATENCY = Histogram(
    "eps_http_request_duration_seconds",
    "Time spent handling a request, by route pattern.",
    ["route"],
)


def init_app(app: Flask) -> None:
    @app.before_request
    def start_timer() -> None:
        request.start_time = time.perf_counter()  # type: ignore[attr-defined]

    @app.after_request
    def record(response: Response) -> Response:
        # url_rule is the pattern ("/journal/<date>"), not the concrete URL,
        # so metrics do not explode into one series per date
        route = request.url_rule.rule if request.url_rule else "unmatched"
        REQUESTS.labels(request.method, route, response.status_code).inc()
        started = getattr(request, "start_time", None)
        if started is not None:
            LATENCY.labels(route).observe(time.perf_counter() - started)
        return response

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), content_type=CONTENT_TYPE_LATEST)
