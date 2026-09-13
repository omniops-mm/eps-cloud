"""Opt-in tracing with local request roots and a restricted export payload."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import requests
import structlog
from flask import Flask, Response, current_app, has_app_context, request
from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode

from app import db
from app.config import get_settings

RESOURCE = Resource({"service.name": "eps-web"})
ENDPOINT = "http://tempo.monitoring.svc.cluster.local:4318/v1/traces"
SQL_OPERATIONS = {"SELECT", "INSERT", "UPDATE", "DELETE", "COMMIT", "ROLLBACK", "CONNECT"}


def clean_context(value: SpanContext | None) -> SpanContext | None:
    if value is None:
        return None
    return SpanContext(value.trace_id, value.span_id, value.is_remote, value.trace_flags)


def safe_span(span: ReadableSpan, routes: set[str]) -> ReadableSpan:
    """Preserve IDs/timing while removing arbitrary text from every export field."""
    attributes: dict = {}
    source = span.attributes or {}
    if span.kind == SpanKind.SERVER:
        route = source.get("http.route")
        route = route if isinstance(route, str) and route in routes else "unmatched"
        attributes["http.route"] = route
        status = source.get("http.response.status_code")
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            attributes["http.response.status_code"] = status
        name = "HTTP " + route
    elif source.get("db.system") in {"postgresql", "sqlite"}:
        attributes["db.system"] = source["db.system"]
        operation = span.name.split(maxsplit=1)[0].upper() if span.name else "DB"
        name = operation if operation in SQL_OPERATIONS else "DB"
    else:
        name = "HTTP request"
    return ReadableSpan(
        name=name,
        context=clean_context(span.context),
        parent=clean_context(span.parent),
        resource=RESOURCE,
        attributes=attributes,
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=InstrumentationScope("eps"),
    )


class PrivateExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter, app: Flask) -> None:
        self.exporter = exporter
        self.app = app

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        routes = {rule.rule for rule in self.app.url_map.iter_rules()}
        try:
            return self.exporter.export([safe_span(span, routes) for span in spans])
        except Exception:  # noqa: BLE001  # Telemetry must fail independently of the application.
            # Export failure must not expose endpoint responses or interrupt requests.
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self.exporter.shutdown()


def current_trace() -> dict[str, str]:
    value = trace.get_current_span().get_span_context()
    if not value.is_valid:
        return {}
    return {"trace_id": f"{value.trace_id:032x}", "span_id": f"{value.span_id:016x}"}


def exemplar() -> dict[str, str] | None:
    value = trace.get_current_span().get_span_context()
    if not value.is_valid or not value.trace_flags.sampled:
        return None
    return {"trace_id": f"{value.trace_id:032x}"}


@contextmanager
def outbound_span() -> Iterator[None]:
    provider = current_app.extensions.get("tracing_provider") if has_app_context() else None
    if provider is None:
        yield
        return
    with provider.get_tracer("eps").start_as_current_span(
        "HTTP request", kind=SpanKind.CLIENT, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield
        except Exception:
            span.set_status(StatusCode.ERROR)
            raise


def init_app(app: Flask) -> None:
    from app.metrics import UNCOUNTED_ROUTES

    settings = get_settings()
    if not settings.tracing_enabled or "tracing_provider" in app.extensions:
        return
    session = requests.Session()
    session.trust_env = False
    session.max_redirects = 0
    exporter = OTLPSpanExporter(
        endpoint=ENDPOINT,
        timeout=2,
        session=session,
        headers={"Content-Type": "application/x-protobuf"},
    )
    provider = TracerProvider(
        resource=RESOURCE,
        sampler=ParentBased(TraceIdRatioBased(settings.tracing_sample_rate)),
        span_limits=SpanLimits(
            max_attributes=16, max_attribute_length=128, max_events=0, max_links=0
        ),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            PrivateExporter(exporter, app),
            max_queue_size=256,
            max_export_batch_size=32,
            schedule_delay_millis=1000,
            export_timeout_millis=3000,
        )
    )
    SQLAlchemyInstrumentor().instrument(
        engine=db.get_engine(), tracer_provider=provider, enable_commenter=False
    )
    app.extensions["tracing_provider"] = provider
    tracer = provider.get_tracer("eps")

    @app.before_request
    def start_request_span() -> None:
        route = request.url_rule.rule if request.url_rule else "unmatched"
        if route in UNCOUNTED_ROUTES:
            return
        # Public trace headers cannot select sampling or inject baggage/tracestate.
        span = tracer.start_span(
            "HTTP " + route,
            context=context.Context(),
            kind=SpanKind.SERVER,
            attributes={"http.route": route},
        )
        token = context.attach(trace.set_span_in_context(span, context.Context()))
        request.environ["eps.tracing"] = (span, token)
        structlog.contextvars.bind_contextvars(**current_trace())

    @app.after_request
    def record_status(response: Response) -> Response:
        state = request.environ.get("eps.tracing")
        if state:
            state[0].set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                state[0].set_status(StatusCode.ERROR)
        return response

    @app.teardown_request
    def end_request_span(error: BaseException | None) -> None:
        state = request.environ.pop("eps.tracing", None)
        if state:
            span, token = state
            if error is not None:
                span.set_status(StatusCode.ERROR)
            span.end()
            context.detach(token)
            structlog.contextvars.unbind_contextvars("trace_id", "span_id")
