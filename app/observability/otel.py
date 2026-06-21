"""OpenTelemetry initializer (add-on).

This module configures an OTLP/Jaeger/Console exporter and
instruments FastAPI and httpx. It's intended as an add-on — it
does not replace the existing `TraceMiddleware` which continues
to provide `request.state.trace_id` and simple request headers.

Usage: call `init_tracing(app)` from the application factory.
"""
import os
from typing import Optional

from fastapi import FastAPI

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    OPENTELEMETRY_AVAILABLE = True
except Exception:
    trace = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]
    TracerProvider = None  # type: ignore[assignment]
    BatchSpanProcessor = None  # type: ignore[assignment]
    ConsoleSpanExporter = None  # type: ignore[assignment]
    OPENTELEMETRY_AVAILABLE = False

_OTEL_EXPORTER = os.getenv("OTEL_EXPORTER", "console").lower()
_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "atlascare")
_OTEL_JAEGER_AGENT_HOST = os.getenv("OTEL_JAEGER_AGENT_HOST", "localhost")
_OTEL_JAEGER_AGENT_PORT = int(os.getenv("OTEL_JAEGER_AGENT_PORT", "6831"))
_OTEL_JAEGER_ENDPOINT = os.getenv(
    "OTEL_JAEGER_ENDPOINT", "http://localhost:14268/api/traces"
)


def _make_tracer_provider(service_name: str) -> TracerProvider:
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    return provider


def init_tracing(app: Optional[FastAPI] = None) -> None:
    """Initialize OpenTelemetry tracing and instrument the app.

    Call this from the app factory after middleware/routers are registered.
    It is safe to call even if OTEL is not enabled — defaults to console exporter.
    """
    service_name = os.getenv("OTEL_SERVICE_NAME", _SERVICE_NAME)
    exporter = _OTEL_EXPORTER

    provider = _make_tracer_provider(service_name)

    if exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            span_exporter = OTLPSpanExporter()
        except Exception:
            span_exporter = ConsoleSpanExporter()
    elif exporter == "jaeger":
        try:
            from opentelemetry.exporter.jaeger.thrift import JaegerExporter

            span_exporter = JaegerExporter(
                agent_host_name=_OTEL_JAEGER_AGENT_HOST,
                agent_port=_OTEL_JAEGER_AGENT_PORT,
                collector_endpoint=_OTEL_JAEGER_ENDPOINT,
            )
        except Exception:
            span_exporter = ConsoleSpanExporter()
    else:
        span_exporter = ConsoleSpanExporter()

    if not OPENTELEMETRY_AVAILABLE:
        return

    span_processor = BatchSpanProcessor(span_exporter)
    provider.add_span_processor(span_processor)
    trace.set_tracer_provider(provider)

    # Instrument FastAPI (Starlette) and common HTTP clients (httpx)
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
        except Exception:
            pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass


def set_trace_id_in_current_span(trace_id: str) -> None:
    if not OPENTELEMETRY_AVAILABLE:
        return

    span = trace.get_current_span()
    if span is None:
        return
    try:
        span.set_attribute("app.trace_id", trace_id)
    except Exception:
        pass


__all__ = ["init_tracing", "set_trace_id_in_current_span"]
