"""
app/observability/metrics.py
-----------------------------
Prometheus metrics for AtlasCare.

Three metrics are defined:

  atlascare_requests_total          Counter    labels: intent, escalated
  atlascare_tool_calls_total        Counter    labels: tool, status
  atlascare_request_latency_seconds Histogram  labels: intent

Usage:
  from app.observability.metrics import record_request, record_tool_call

  record_request(intent="TRACKING", escalated=False, latency_seconds=0.42)
  record_tool_call(tool="oms_get_order", status="SUCCESS")

The /metrics endpoint is exposed by adding the prometheus_client ASGI app
to app/core.py. The functions in this module are for recording metrics from the main app logic;
updates the metrics — it does not expose an HTTP endpoint.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Histogram

    REQUESTS_TOTAL = Counter(
        "atlascare_requests_total",
        "Total number of /query requests handled",
        labelnames=["intent", "escalated"],
    )

    TOOL_CALLS_TOTAL = Counter(
        "atlascare_tool_calls_total",
        "Total number of tool calls executed",
        labelnames=["tool", "status"],
    )

    REQUEST_LATENCY = Histogram(
        "atlascare_request_latency_seconds",
        "End-to-end /query request latency in seconds",
        labelnames=["intent"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
    )

    _PROMETHEUS_AVAILABLE = True
    logger.info("Prometheus metrics registered")

except ImportError:
    logger.warning(
        "prometheus_client not installed — metrics will be no-ops. "
        "Add prometheus-client to requirements.txt."
    )
    _PROMETHEUS_AVAILABLE = False
    REQUESTS_TOTAL = None       # type: ignore[assignment]
    TOOL_CALLS_TOTAL = None     # type: ignore[assignment]
    REQUEST_LATENCY = None      # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helper functions — always safe to call, even if prometheus unavailable
# ---------------------------------------------------------------------------

def record_request(
    intent: str,
    escalated: bool,
    latency_seconds: float,
) -> None:
    """
    Increment request counter and observe latency histogram.
    No-op if prometheus_client is not installed.
    """
    if not _PROMETHEUS_AVAILABLE:
        return
    try:
        REQUESTS_TOTAL.labels(
            intent=intent,
            escalated=str(escalated).lower(),   # "true" / "false"
        ).inc()
        REQUEST_LATENCY.labels(intent=intent).observe(latency_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("metrics.record_request failed: %s", exc)


def record_tool_call(tool: str, status: str) -> None:
    """
    Increment tool call counter.
    No-op if prometheus_client is not installed.
    """
    if not _PROMETHEUS_AVAILABLE:
        return
    try:
        TOOL_CALLS_TOTAL.labels(tool=tool, status=status).inc()
    except Exception as exc:  # noqa: BLE001
        logger.warning("metrics.record_tool_call failed: %s", exc)
