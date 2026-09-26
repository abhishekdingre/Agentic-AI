"""OpenTelemetry instrumentation.

This is observability, not enforcement — the four hard commitments are
mechanically enforced elsewhere (allowlist.py/ingest.py/tools.py for #1,
grounding.py for #2, schemas.py/tools.py for #3, agent.py's degrade path for
#4). This module exists so an operator watching SigNoz (or any other OTLP
backend) can see what the agent is doing, not to gate behavior.

Provides:
  - `setup_telemetry()` — configures a TracerProvider + MeterProvider with a
    gRPC OTLP exporter, called once at process startup (`backend/main.py`).
    Safe to call multiple times (idempotent) and safe to call even if no
    collector is reachable — export failures are swallowed by OTel's own
    background export thread and never raise into request handling.
  - `start_query_span(question, domains)` — one span per `/api/query` call.
  - `start_iteration_span(iteration)` — one child span per agent loop
    iteration (one `messages.create()` call).
  - `start_tool_span(tool_name)` — one child span per tool call
    (search_corpus / submit_structured_answer).
  - `record_query_result(...)` — records the request-latency histogram and
    increments the request/refusal counters; call once per finished query.

All of these degrade to harmless no-ops if `setup_telemetry()` was never
called (OTel's default no-op TracerProvider/MeterProvider), so importing
this module is always safe even outside a fully instrumented run (e.g. in
tests).
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from backend.config import OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME

_initialized = False


def setup_telemetry() -> None:
    """Configure global TracerProvider/MeterProvider with an OTLP gRPC
    exporter pointed at OTEL_EXPORTER_OTLP_ENDPOINT (default
    http://localhost:4317, matching docker-compose.observability.yml's
    SigNoz OTel collector port). Idempotent; call once from
    backend/main.py's startup hook."""
    global _initialized
    if _initialized:
        return

    resource = Resource.create({"service.name": OTEL_SERVICE_NAME})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    _initialized = True


def _tracer():
    return trace.get_tracer(OTEL_SERVICE_NAME)


def _meter():
    return metrics.get_meter(OTEL_SERVICE_NAME)


# Metric instruments are created lazily against whatever meter provider is
# currently set (no-op until setup_telemetry() runs), so importing this
# module before setup_telemetry() is always safe.
def _request_counter():
    return _meter().create_counter(
        "rag_requests_total", description="Total /api/query requests handled"
    )


def _refusal_counter():
    return _meter().create_counter(
        "rag_refusals_total", description="Requests where the final answer was refused=true"
    )


def _latency_histogram():
    return _meter().create_histogram(
        "rag_request_latency_seconds", description="End-to-end /api/query latency", unit="s"
    )


@contextmanager
def start_query_span(question: str, domains: list[str]):
    with _tracer().start_as_current_span("rag.query") as span:
        span.set_attribute("rag.question_length", len(question))
        span.set_attribute("rag.selected_domains", ",".join(domains))
        yield span


@contextmanager
def start_iteration_span(iteration: int):
    with _tracer().start_as_current_span("rag.agent_loop_iteration") as span:
        span.set_attribute("rag.iteration", iteration)
        yield span


@contextmanager
def start_tool_span(tool_name: str):
    with _tracer().start_as_current_span(f"rag.tool.{tool_name}") as span:
        span.set_attribute("rag.tool_name", tool_name)
        yield span


def record_query_result(
    *,
    duration_seconds: float,
    refused: bool,
    overall_confidence: str,
    retry_count: int,
    retrieved_chunk_count: int,
) -> None:
    """Call once per finished /api/query request."""
    attributes = {
        "confidence": overall_confidence,
        "retry_count": retry_count,
        "retrieved_chunk_count": retrieved_chunk_count,
    }
    _request_counter().add(1, attributes)
    if refused:
        _refusal_counter().add(1, attributes)
    _latency_histogram().record(duration_seconds, attributes)


def annotate_span(span, **attributes) -> None:
    """Small helper for setting several attributes at once, e.g.

        annotate_span(span, retrieved_chunk_count=3, grounding_passed=True)
    """
    for key, value in attributes.items():
        if value is None:
            continue
        span.set_attribute(f"rag.{key}", value)


class Timer:
    """`with Timer() as t: ...` then `t.elapsed` in seconds."""

    def __enter__(self):
        self._start = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *exc_info):
        self.elapsed = time.perf_counter() - self._start
        return False
