"""
tests/test_observability.py
----------------------------
Phase 6 CI gate — Observability tests.

TraceStore tests use fakeredis so no real Redis is needed in CI.
AuditLogger tests write to a temp file.
Metrics tests verify helpers are callable and don't raise.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.trace import ToolCallRecord, TraceRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trace_record(trace_id: str = "trc_20250524102455_a1b2c3d4") -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id="sess_test",
        intent="TRACKING",
        latency_ms=342.5,
        latency_breach=False,
        tool_calls=[
            ToolCallRecord(
                seq=1,
                tool="oms_get_order",
                args={"order_id": "ORD-78321"},
                status="SUCCESS",
                latency_ms=120.0,
                result_summary="SUCCESS keys=['order_id', 'status']",
            )
        ],
        escalation_triggered=False,
        policy_violations=[],
        model_calls=3,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# TraceStore tests (fakeredis)
# ---------------------------------------------------------------------------

class TestTraceStore:

    def _make_store(self):
        """Return a TraceStore backed by fakeredis."""
        try:
            import fakeredis
        except ImportError:
            pytest.skip("fakeredis not installed — install fakeredis for TraceStore tests")

        from app.observability.tracer import TraceStore
        store = TraceStore.__new__(TraceStore)
        store._redis_url = "redis://localhost:6379/0"
        store._client = fakeredis.FakeRedis(decode_responses=True)
        return store

    def test_append_and_get_roundtrip(self):
        store = self._make_store()
        record = _make_trace_record("trc_test_001")
        store.append(record)
        retrieved = store.get("trc_test_001")
        assert retrieved is not None
        assert retrieved.trace_id == "trc_test_001"
        assert retrieved.intent == "TRACKING"
        assert retrieved.session_id == "sess_test"

    def test_get_missing_returns_none(self):
        store = self._make_store()
        result = store.get("trc_does_not_exist")
        assert result is None

    def test_append_preserves_tool_calls(self):
        store = self._make_store()
        record = _make_trace_record("trc_test_002")
        store.append(record)
        retrieved = store.get("trc_test_002")
        assert len(retrieved.tool_calls) == 1
        assert retrieved.tool_calls[0].tool == "oms_get_order"
        assert retrieved.tool_calls[0].status == "SUCCESS"

    def test_append_preserves_all_fields(self):
        store = self._make_store()
        record = _make_trace_record("trc_test_003")
        store.append(record)
        retrieved = store.get("trc_test_003")
        assert retrieved.latency_ms == pytest.approx(342.5)
        assert retrieved.latency_breach is False
        assert retrieved.escalation_triggered is False
        assert retrieved.policy_violations == []
        assert retrieved.model_calls == 3

    def test_overwrite_same_trace_id(self):
        """append() on same trace_id must overwrite (last-write-wins)."""
        store = self._make_store()
        r1 = _make_trace_record("trc_test_004")
        store.append(r1)

        r2 = TraceRecord(
            trace_id="trc_test_004",
            session_id="sess_updated",
            intent="COMPOUND",
            latency_ms=500.0,
            latency_breach=False,
            tool_calls=[],
            escalation_triggered=False,
            policy_violations=[],
            model_calls=3,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        store.append(r2)

        retrieved = store.get("trc_test_004")
        assert retrieved.session_id == "sess_updated"
        assert retrieved.intent == "COMPOUND"

    def test_append_silent_on_redis_unavailable(self):
        """append() must not raise when Redis client is None."""
        from app.observability.tracer import TraceStore
        store = TraceStore.__new__(TraceStore)
        store._redis_url = "redis://localhost:6379/0"
        store._client = None

        record = _make_trace_record("trc_test_005")
        # Must not raise
        store.append(record)

    def test_get_silent_on_redis_unavailable(self):
        """get() must return None (not raise) when Redis client is None."""
        from app.observability.tracer import TraceStore
        store = TraceStore.__new__(TraceStore)
        store._redis_url = "redis://localhost:6379/0"
        store._client = None

        result = store.get("trc_test_006")
        assert result is None

    def test_escalated_trace_persisted(self):
        store = self._make_store()
        record = TraceRecord(
            trace_id="trc_test_007",
            session_id="sess_test",
            intent="COMPOUND",
            latency_ms=800.0,
            latency_breach=False,
            tool_calls=[
                ToolCallRecord(
                    seq=1, tool="payments_refund",
                    args={"amount": 50000.0},
                    status="ESCALATE",
                    latency_ms=0.0,
                    result_summary="POLICY_BLOCK: THRESHOLD_CHECK",
                )
            ],
            escalation_triggered=True,
            policy_violations=[{"reason": "POLICY_BLOCK: THRESHOLD_CHECK"}],
            model_calls=3,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        store.append(record)
        retrieved = store.get("trc_test_007")
        assert retrieved.escalation_triggered is True
        assert retrieved.policy_violations == [{"reason": "POLICY_BLOCK: THRESHOLD_CHECK"}]

    def test_singleton_returns_same_instance(self):
        from app.observability.tracer import get_trace_store
        a = get_trace_store()
        b = get_trace_store()
        assert a is b


# ---------------------------------------------------------------------------
# AuditLogger tests
# ---------------------------------------------------------------------------

class TestAuditLogger:

    def test_log_writes_json_line(self, tmp_path):
        from app.observability.audit import AuditLogger
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(path=str(log_file))
        logger.log("trc_001", "QUERY_COMPLETE", {"intent": "TRACKING", "escalated": False})

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["trace_id"] == "trc_001"
        assert entry["event"] == "QUERY_COMPLETE"
        assert entry["payload"]["intent"] == "TRACKING"
        assert "timestamp_utc" in entry

    def test_log_appends_not_overwrites(self, tmp_path):
        from app.observability.audit import AuditLogger
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(path=str(log_file))

        logger.log("trc_001", "QUERY_COMPLETE", {"n": 1})
        logger.log("trc_002", "QUERY_COMPLETE", {"n": 2})
        logger.log("trc_003", "QUERY_COMPLETE", {"n": 3})

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["trace_id"] == "trc_001"
        assert json.loads(lines[2])["trace_id"] == "trc_003"

    def test_log_silent_on_write_error(self, tmp_path):
        """log() must not raise even if the file cannot be written."""
        from app.observability.audit import AuditLogger
        logger = AuditLogger(path="/nonexistent_dir/audit.log")
        # Must not raise
        logger.log("trc_001", "QUERY_COMPLETE", {})

    def test_log_payload_serialises_non_json_types(self, tmp_path):
        """default=str in json.dumps must handle non-serialisable payloads."""
        from app.observability.audit import AuditLogger
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(path=str(log_file))
        logger.log("trc_001", "TEST", {"dt": datetime.now(timezone.utc)})

        lines = log_file.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        assert "dt" in entry["payload"]  # serialised as string

    def test_log_is_thread_safe(self, tmp_path):
        """Multiple threads writing simultaneously must not corrupt lines."""
        import threading
        from app.observability.audit import AuditLogger
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(path=str(log_file))

        def write(n: int):
            for _ in range(10):
                logger.log(f"trc_{n:03d}", "TEST", {"n": n})

        threads = [threading.Thread(target=write, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 50  # 5 threads × 10 writes each
        for line in lines:
            json.loads(line)  # every line must be valid JSON

    def test_singleton_returns_same_instance(self):
        from app.observability.audit import get_audit_logger
        a = get_audit_logger()
        b = get_audit_logger()
        assert a is b


# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------

class TestMetrics:

    def test_record_request_does_not_raise(self):
        from app.observability.metrics import record_request
        record_request(intent="TRACKING", escalated=False, latency_seconds=0.5)
        record_request(intent="COMPOUND", escalated=True, latency_seconds=1.2)
        record_request(intent="UNKNOWN", escalated=False, latency_seconds=0.1)

    def test_record_tool_call_does_not_raise(self):
        from app.observability.metrics import record_tool_call
        record_tool_call(tool="oms_get_order", status="SUCCESS")
        record_tool_call(tool="payments_refund", status="ESCALATE")
        record_tool_call(tool="kb_search", status="FAILED")

    def test_metrics_available_or_noop(self):
        """Whether or not prometheus_client is installed, helpers must be callable."""
        from app.observability import metrics
        assert callable(metrics.record_request)
        assert callable(metrics.record_tool_call)


# ---------------------------------------------------------------------------
# TraceRecord schema tests
# ---------------------------------------------------------------------------

class TestTraceRecordSchema:

    def test_valid_trace_record_instantiates(self):
        record = _make_trace_record()
        assert record.trace_id == "trc_20250524102455_a1b2c3d4"
        assert record.intent == "TRACKING"
        assert record.model_calls == 3

    def test_trace_record_serialises_to_json(self):
        record = _make_trace_record()
        json_str = record.model_dump_json()
        data = json.loads(json_str)
        assert data["trace_id"] == "trc_20250524102455_a1b2c3d4"
        assert isinstance(data["tool_calls"], list)
        assert data["tool_calls"][0]["tool"] == "oms_get_order"

    def test_trace_record_roundtrip(self):
        record = _make_trace_record("trc_roundtrip")
        json_str = record.model_dump_json()
        restored = TraceRecord.model_validate_json(json_str)
        assert restored.trace_id == record.trace_id
        assert restored.latency_ms == record.latency_ms
        assert len(restored.tool_calls) == len(record.tool_calls)
