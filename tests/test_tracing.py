"""Trace relationships, export privacy, sampling and failure isolation."""

import json
from threading import Event

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import encode_spans
from opentelemetry.sdk.trace import Event as SpanEvent
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags, TraceState
from sqlalchemy import text

from app import db, tracing
from app.config import Settings


@pytest.fixture
def traced(monkeypatch, request):
    memory = InMemorySpanExporter()
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setenv("TRACING_SAMPLE_RATE", str(getattr(request, "param", 1)))

    def make_exporter(**kwargs):
        assert kwargs["endpoint"] == tracing.ENDPOINT
        assert kwargs["session"].trust_env is False
        assert kwargs["session"].max_redirects == 0
        assert kwargs["timeout"] == 2
        return memory

    monkeypatch.setattr(tracing, "OTLPSpanExporter", make_exporter)
    client = request.getfixturevalue("client")
    yield client, memory
    client.application.extensions["tracing_provider"].shutdown()
    tracing.SQLAlchemyInstrumentor().uninstrument()


def test_request_children_logs_exemplars_and_private_payload(traced, capsys):
    from app.logging import configure_logging

    configure_logging()
    client, memory = traced
    marker = "private-trace-marker"

    @client.application.get("/trace-check/<value>")
    def endpoint(value):
        db.db_session.execute(text("SELECT :value"), {"value": value}).scalar()
        with tracing.outbound_span():
            pass
        return "ok"

    @client.application.get("/trace-fail/<value>")
    def failing(value):
        raise ValueError(value)

    incoming = "1" * 32
    assert (
        client.get(
            f"/trace-check/{marker}?token={marker}",
            headers={
                "traceparent": f"00-{incoming}-{'2' * 16}-01",
                "tracestate": "private=" + marker,
                "baggage": "private=" + marker,
                "Authorization": "Bearer " + marker,
            },
        ).status_code
        == 200
    )
    provider = client.application.extensions["tracing_provider"]
    assert provider.force_flush(2000)
    spans = memory.get_finished_spans()
    root = next(s for s in spans if s.kind == SpanKind.SERVER)
    children = [s for s in spans if s.parent and s.parent.span_id == root.context.span_id]
    assert root.parent is None and f"{root.context.trace_id:032x}" != incoming
    assert any(s.name == "SELECT" for s in children)
    assert any(s.name == "HTTP request" for s in children)
    assert all(s.context.trace_id == root.context.trace_id for s in children)
    assert marker.encode() not in encode_spans(spans).SerializeToString()
    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    event = next(e for e in events if e.get("path") == "/trace-check/<value>")
    assert event["trace_id"] == f"{root.context.trace_id:032x}"
    assert tracing.current_trace() == {}
    metrics = client.get(
        "/metrics", headers={"Accept": "application/openmetrics-text; version=1.0.0"}
    )
    assert '# {trace_id="' + event["trace_id"] + '"}' in metrics.text
    assert "application/openmetrics-text" in metrics.content_type
    assert "text/plain" in client.get("/metrics").content_type
    before = len(spans)
    client.get("/healthz")
    assert provider.force_flush(2000)
    assert len(memory.get_finished_spans()) == before
    client.get("/trace-check/next")
    assert provider.force_flush(2000)
    roots = [s for s in memory.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert len(roots) == 2 and roots[0].context.trace_id != roots[1].context.trace_id
    assert client.get("/trace-fail/" + marker).status_code == 500
    assert (
        client.post("/settings/grace", include_csrf=False, data={"private": marker}).status_code
        == 400
    )
    assert provider.force_flush(2000)
    spans = memory.get_finished_spans()
    assert any(
        s.name == "HTTP /trace-fail/<value>" and s.status.status_code == StatusCode.ERROR
        for s in spans
    )
    assert marker.encode() not in encode_spans(spans).SerializeToString()
    assert tracing.current_trace() == {}


def test_export_filter_removes_non_attribute_channels():
    marker = "private-trace-marker"
    ctx = SpanContext(1, 2, False, TraceFlags(1), TraceState([("private", marker)]))
    span = ReadableSpan(
        name=marker,
        context=ctx,
        parent=ctx,
        kind=SpanKind.SERVER,
        resource=tracing.Resource({"private": marker}),
        attributes={"http.route": marker, "http.response.status_code": 500, "private": marker},
        status=Status(StatusCode.ERROR, marker),
        start_time=1,
        end_time=2,
        instrumentation_scope=tracing.InstrumentationScope(marker),
        events=[SpanEvent(marker, attributes={"private": marker}, timestamp=1)],
    )
    safe = tracing.safe_span(span, {"/"})
    assert safe.name == "HTTP unmatched" and safe.status.status_code == StatusCode.ERROR
    assert safe.context is not None and safe.parent is not None
    assert safe.context.trace_id == 1 and safe.parent.span_id == 2
    assert marker.encode() not in encode_spans([safe]).SerializeToString()


def test_export_failure_does_not_block_requests(traced, monkeypatch):
    client, memory = traced
    entered, release = Event(), Event()

    def blocked(spans):
        entered.set()
        assert release.wait(5)
        raise RuntimeError("private-export-error")

    monkeypatch.setattr(memory, "export", blocked)
    try:
        assert client.get("/missing").status_code == 404
        assert entered.wait(3)
        # The export thread is blocked, but the next response still completes.
        assert client.get("/missing-again").status_code == 404
    finally:
        release.set()
    assert client.application.extensions["tracing_provider"].force_flush(2000)
    assert tracing.current_trace() == {}


def test_disabled_and_sample_rate_validation(client):
    assert "tracing_provider" not in client.application.extensions
    for rate in (-0.1, 1.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            Settings(database_url="sqlite://", secret_key="test", tracing_sample_rate=rate)


@pytest.mark.parametrize("traced", [0], indirect=True)
def test_zero_sampling_ignores_inbound_sample_flag(traced):
    client, memory = traced
    assert (
        client.get(
            "/missing", headers={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}
        ).status_code
        == 404
    )
    assert client.application.extensions["tracing_provider"].force_flush(2000)
    assert memory.get_finished_spans() == ()
    assert tracing.exemplar() is None
