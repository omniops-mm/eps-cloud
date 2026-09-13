"""Prometheus metrics: request counts and latencies, served at /metrics.

The counters live in this process's memory. Prometheus scrapes the endpoint on
an interval and does the storage and math; the app only counts.
"""

import time

from flask import Flask, Response, request
from prometheus_client import REGISTRY, Counter, Histogram
from prometheus_client.exposition import choose_encoder

HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}

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

# Prometheus and the container healthcheck request these on a timer. Counting
# them would dominate the request rate of a single-user application.
UNCOUNTED_ROUTES = frozenset({"/metrics", "/healthz", "/readyz"})


def init_app(app: Flask) -> None:
    @app.before_request
    def start_timer() -> None:
        request.start_time = time.perf_counter()  # type: ignore[attr-defined]

    @app.after_request
    def record(response: Response) -> Response:
        from app.tracing import exemplar

        # url_rule is the pattern ("/journal/<date>"), not the concrete URL,
        # so metrics do not explode into one series per date
        route = request.url_rule.rule if request.url_rule else "unmatched"
        if route in UNCOUNTED_ROUTES:
            return response
        method = request.method if request.method in HTTP_METHODS else "OTHER"
        REQUESTS.labels(method, route, response.status_code).inc(exemplar=exemplar())
        started = getattr(request, "start_time", None)
        if started is not None:
            LATENCY.labels(route).observe(time.perf_counter() - started, exemplar=exemplar())
        return response

    @app.get("/metrics")
    def metrics() -> Response:
        encoder, content_type = choose_encoder(request.headers.get("Accept", ""))
        return Response(encoder(REGISTRY), content_type=content_type)
