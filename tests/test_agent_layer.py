"""
tests/test_agent_layer.py
--------------------------
Phase 4 CI gate — Agent Layer tests.

All Gemini SDK calls are mocked (no live API key required).
All tool adapter calls go through the real TOOL_REGISTRY against the real DataStore.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.agent import ClassifyResult, ToolCallPlan, ToolCallStep
from app.schemas.trace import ToolCallRecord, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_gemini_response(text: str) -> MagicMock:
    """Return a mock object that looks like a Gemini GenerativeModel response."""
    response = MagicMock()
    message = MagicMock()
    message.content = text
    choice = MagicMock()
    choice.message = message
    response.choices = [choice]
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client, "test-model"


# ---------------------------------------------------------------------------
# Classifier tests
# ---------------------------------------------------------------------------

class TestClassifier:

    def test_tracking_intent(self):
        from app.agents.classifier import classify
        model = _mock_gemini_response('{"intent": "TRACKING", "confidence": 0.95}')
        with patch("app.agents.classifier._get_model", return_value=model):
            result = classify("Where is my order ORD-78321?", trace_id="trc_test_0001")
        assert result.intent == "TRACKING"
        assert result.confidence == pytest.approx(0.95)

    def test_compound_intent(self):
        from app.agents.classifier import classify
        model = _mock_gemini_response('{"intent": "COMPOUND", "confidence": 0.9}')
        with patch("app.agents.classifier._get_model", return_value=model):
            result = classify("Cancel item 2 and refund me", trace_id="trc_test_0002")
        assert result.intent == "COMPOUND"

    def test_escalation_intent(self):
        from app.agents.classifier import classify
        model = _mock_gemini_response('{"intent": "ESCALATION", "confidence": 0.99}')
        with patch("app.agents.classifier._get_model", return_value=model):
            result = classify("Get me a human agent NOW", trace_id="trc_test_0003")
        assert result.intent == "ESCALATION"

    def test_unknown_intent(self):
        from app.agents.classifier import classify
        model = _mock_gemini_response('{"intent": "UNKNOWN", "confidence": 0.4}')
        with patch("app.agents.classifier._get_model", return_value=model):
            result = classify("What is the meaning of life?", trace_id="trc_test_0004")
        assert result.intent == "UNKNOWN"

    def test_fallback_on_json_error(self):
        from app.agents.classifier import classify
        model = _mock_gemini_response("not json at all")
        with patch("app.agents.classifier._get_model", return_value=model):
            result = classify("something", trace_id="trc_test_0005")
        assert result.intent == "UNKNOWN"
        assert result.confidence == pytest.approx(0.0)

    def test_fallback_on_api_exception(self):
        from app.agents.classifier import classify
        model = MagicMock()
        model.generate_content.side_effect = RuntimeError("API down")
        with patch("app.agents.classifier._get_model", return_value=model):
            result = classify("something", trace_id="trc_test_0006")
        assert result.intent == "UNKNOWN"

    def test_strips_markdown_fences(self):
        from app.agents.classifier import classify
        raw = '```json\n{"intent": "TRACKING", "confidence": 0.88}\n```'
        model = _mock_gemini_response(raw)
        with patch("app.agents.classifier._get_model", return_value=model):
            result = classify("Where is my order?", trace_id="trc_test_0007")
        assert result.intent == "TRACKING"

    def test_confidence_bounds(self):
        from app.agents.classifier import classify
        model = _mock_gemini_response('{"intent": "TRACKING", "confidence": 0.75}')
        with patch("app.agents.classifier._get_model", return_value=model):
            result = classify("Order status?", trace_id="trc_test_0008")
        assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# Planner tests
# ---------------------------------------------------------------------------

class TestPlanner:

    def test_tracking_plan(self):
        from app.agents.planner import plan
        payload = json.dumps({
            "steps": [
                {"tool": "oms_get_order", "args": {"order_id": "ORD-78321"}, "rationale": "Fetch order details"}
            ]
        })
        model = _mock_gemini_response(payload)
        with patch("app.agents.planner._get_model", return_value=model):
            result = plan("Where is ORD-78321?", "TRACKING", {}, "trc_test_0010")
        assert len(result.steps) == 1
        assert result.steps[0].tool == "oms_get_order"
        assert result.steps[0].args["order_id"] == "ORD-78321"

    def test_compound_plan_cancel_then_refund(self):
        from app.agents.planner import plan
        payload = json.dumps({
            "steps": [
                {"tool": "oms_get_order", "args": {"order_id": "ORD-78321"}, "rationale": "Fetch order"},
                {"tool": "oms_cancel_item", "args": {"order_id": "ORD-78321", "line_id": 2}, "rationale": "Cancel item"},
                {"tool": "payments_refund", "args": {"customer_id": "CUST-001", "amount": 500.0, "method": "UPI"}, "rationale": "Refund"},
            ]
        })
        model = _mock_gemini_response(payload)
        with patch("app.agents.planner._get_model", return_value=model):
            result = plan("Cancel item 2 and refund me", "COMPOUND", {}, "trc_test_0011")
        assert len(result.steps) == 3
        assert result.steps[1].tool == "oms_cancel_item"
        assert result.steps[1].args["line_id"] == 2  # 1-indexed

    def test_empty_kb_tags_dropped(self):
        from app.agents.planner import plan
        payload = json.dumps({
            "steps": [
                {"tool": "kb_search", "args": {"tags": []}, "rationale": "Bad step"},
            ]
        })
        model = _mock_gemini_response(payload)
        with patch("app.agents.planner._get_model", return_value=model):
            result = plan("Help me", "UNKNOWN", {}, "trc_test_0012")
        # The bad kb_search step must be dropped
        assert len(result.steps) == 0

    def test_invalid_tool_name_dropped(self):
        from app.agents.planner import plan
        payload = json.dumps({
            "steps": [
                {"tool": "crm_get_customer", "args": {"customer_id": "CUST-001"}, "rationale": "Invalid"},
            ]
        })
        model = _mock_gemini_response(payload)
        with patch("app.agents.planner._get_model", return_value=model):
            result = plan("Lookup customer", "UNKNOWN", {}, "trc_test_0013")
        assert len(result.steps) == 0

    def test_fallback_on_json_error(self):
        from app.agents.planner import plan
        model = _mock_gemini_response("not json")
        with patch("app.agents.planner._get_model", return_value=model):
            result = plan("something", "TRACKING", {}, "trc_test_0014")
        assert result.steps == []

    def test_fallback_on_api_exception(self):
        from app.agents.planner import plan
        model = MagicMock()
        model.generate_content.side_effect = RuntimeError("API down")
        with patch("app.agents.planner._get_model", return_value=model):
            result = plan("something", "TRACKING", {}, "trc_test_0015")
        assert result.steps == []

    def test_strips_markdown_fences(self):
        from app.agents.planner import plan
        payload = '```json\n{"steps": [{"tool": "oms_get_order", "args": {"order_id": "ORD-78321"}, "rationale": "Fetch"}]}\n```'
        model = _mock_gemini_response(payload)
        with patch("app.agents.planner._get_model", return_value=model):
            result = plan("Order status?", "TRACKING", {}, "trc_test_0016")
        assert len(result.steps) == 1

    def test_valid_kb_search_kept(self):
        from app.agents.planner import plan
        payload = json.dumps({
            "steps": [
                {"tool": "kb_search", "args": {"tags": ["refund", "policy"]}, "rationale": "Search KB"},
            ]
        })
        model = _mock_gemini_response(payload)
        with patch("app.agents.planner._get_model", return_value=model):
            result = plan("What is the refund policy?", "TRACKING", {}, "trc_test_0017")
        assert len(result.steps) == 1
        assert result.steps[0].args["tags"] == ["refund", "policy"]


# ---------------------------------------------------------------------------
# Executor tests
# ---------------------------------------------------------------------------

class TestExecutor:

    def test_executes_single_read_step(self):
        from app.agents.executor import execute
        plan = ToolCallPlan(steps=[
            ToolCallStep(tool="oms_get_order", args={"order_id": "ORD-78321"}, rationale="Fetch order")
        ])
        records = execute(plan, trace_id="trc_test_0020")
        assert len(records) == 1
        assert records[0].tool == "oms_get_order"
        assert records[0].status in ("SUCCESS", "FAILED")  # depends on data fixture

    def test_unknown_tool_returns_failed_record(self):
        from app.agents.executor import execute
        plan = ToolCallPlan(steps=[
            ToolCallStep(tool="oms_get_order", args={"order_id": "DOES-NOT-EXIST"}, rationale="Test")
        ])
        # Temporarily patch registry to have no keys
        with patch("app.agents.executor.TOOL_REGISTRY", {}):
            records = execute(plan, trace_id="trc_test_0021")
        assert records[0].status == "FAILED"
        assert "not found in registry" in records[0].result_summary

    def test_empty_plan_returns_empty_list(self):
        from app.agents.executor import execute
        plan = ToolCallPlan(steps=[])
        records = execute(plan, trace_id="trc_test_0022")
        assert records == []

    def test_seq_numbers_are_1_indexed(self):
        from app.agents.executor import execute
        plan = ToolCallPlan(steps=[
            ToolCallStep(tool="oms_get_order", args={"order_id": "ORD-78321"}, rationale="Step 1"),
            ToolCallStep(tool="kb_search", args={"tags": ["refund"]}, rationale="Step 2"),
        ])
        records = execute(plan, trace_id="trc_test_0023")
        assert records[0].seq == 1
        assert records[1].seq == 2

    def test_tool_exception_returns_failed_record(self):
        from app.agents.executor import execute
        bad_handler = MagicMock(side_effect=RuntimeError("boom"))
        fake_registry = {
            "oms_get_order": {"handler": bad_handler, "policy": ["READ"], "description": "test"}
        }
        plan = ToolCallPlan(steps=[
            ToolCallStep(tool="oms_get_order", args={"order_id": "ORD-78321"}, rationale="Test")
        ])
        with patch("app.agents.executor.TOOL_REGISTRY", fake_registry):
            with patch("app.agents.executor.time.sleep"):  # skip backoff
                records = execute(plan, trace_id="trc_test_0024")
        assert records[0].status == "FAILED"
        assert records[0].result_summary.startswith("FAILED")

    def test_idempotent_not_retried(self):
        from app.agents.executor import execute
        handler = MagicMock(return_value=ToolResult(status="IDEMPOTENT"))
        fake_registry = {
            "oms_cancel_item": {"handler": handler, "policy": ["WRITE"], "description": "test"}
        }
        plan = ToolCallPlan(steps=[
            ToolCallStep(tool="oms_cancel_item", args={"order_id": "ORD-78321", "line_id": 1}, rationale="Test")
        ])
        with patch("app.agents.executor.TOOL_REGISTRY", fake_registry):
            records = execute(plan, trace_id="trc_test_0025")
        assert handler.call_count == 1  # called exactly once, not retried
        assert records[0].status == "IDEMPOTENT"

    def test_escalate_not_retried(self):
        from app.agents.executor import execute
        handler = MagicMock(return_value=ToolResult(status="ESCALATE", error_code="COD_NOT_SUPPORTED"))
        fake_registry = {
            "payments_refund": {"handler": handler, "policy": ["WRITE"], "description": "test"}
        }
        plan = ToolCallPlan(steps=[
            ToolCallStep(tool="payments_refund", args={"customer_id": "CUST-001", "amount": 100.0, "method": "COD"}, rationale="Test")
        ])
        with patch("app.agents.executor.TOOL_REGISTRY", fake_registry):
            records = execute(plan, trace_id="trc_test_0026")
        assert handler.call_count == 1
        assert records[0].status == "ESCALATE"

    def test_tool_result_attached_to_record(self):
        from app.agents.executor import execute
        handler = MagicMock(return_value=ToolResult(status="SUCCESS", data={"order_id": "ORD-78321"}))
        fake_registry = {
            "oms_get_order": {"handler": handler, "policy": ["READ"], "description": "test"}
        }
        plan = ToolCallPlan(steps=[
            ToolCallStep(tool="oms_get_order", args={"order_id": "ORD-78321"}, rationale="Test")
        ])
        with patch("app.agents.executor.TOOL_REGISTRY", fake_registry):
            records = execute(plan, trace_id="trc_test_0027")
        # Executor attaches _tool_result for the synthesizer
        assert "_tool_result" in records[0].__dict__
        assert records[0].__dict__["_tool_result"].data["order_id"] == "ORD-78321"


# ---------------------------------------------------------------------------
# Synthesizer tests
# ---------------------------------------------------------------------------

class TestSynthesizer:

    def _make_record(self, tool: str, status: str, data: dict | None = None) -> ToolCallRecord:
        record = ToolCallRecord(
            seq=1, tool=tool, args={}, status=status,
            latency_ms=10.0, result_summary="test"
        )
        record.__dict__["_tool_result"] = ToolResult(status=status, data=data)
        return record

    def test_returns_string_reply(self):
        from app.agents.synthesizer import synthesize
        model = _mock_gemini_response("Your order ORD-78321 is currently shipped and will arrive soon.")
        records = [self._make_record("oms_get_order", "SUCCESS", {"order_id": "ORD-78321", "status": "shipped"})]
        with patch("app.agents.synthesizer._get_model", return_value=model):
            reply = synthesize("Where is my order?", records, trace_id="trc_test_0030")
        assert isinstance(reply, str)
        assert len(reply) > 0

    def test_fallback_on_api_exception(self):
        from app.agents.synthesizer import synthesize, _FALLBACK_REPLY
        model = MagicMock()
        model.generate_content.side_effect = RuntimeError("API down")
        with patch("app.agents.synthesizer._get_model", return_value=model):
            reply = synthesize("Where is my order?", [], trace_id="trc_test_0031")
        assert reply == _FALLBACK_REPLY

    def test_empty_records_handled(self):
        from app.agents.synthesizer import synthesize
        model = _mock_gemini_response("I'm sorry, I could not process your request.")
        with patch("app.agents.synthesizer._get_model", return_value=model):
            reply = synthesize("Something unclear", [], trace_id="trc_test_0032")
        assert isinstance(reply, str)

    def test_tool_result_data_passed_to_prompt(self):
        """Verify that _build_tool_results_summary includes data fields."""
        from app.agents.synthesizer import _build_tool_results_summary
        record = self._make_record(
            "payments_refund", "SUCCESS",
            {"refund_sla_days": 5, "amount_inr": 1500.0, "method": "UPI"}
        )
        summary_json = _build_tool_results_summary([record])
        summary = json.loads(summary_json)
        assert summary[0]["data"]["refund_sla_days"] == 5

    def test_escalate_record_included_in_summary(self):
        from app.agents.synthesizer import _build_tool_results_summary
        record = self._make_record("payments_refund", "ESCALATE")
        record.__dict__["_tool_result"] = ToolResult(
            status="ESCALATE", error_code="COD_NOT_SUPPORTED"
        )
        summary_json = _build_tool_results_summary([record])
        summary = json.loads(summary_json)
        assert summary[0]["status"] == "ESCALATE"
        assert summary[0]["error_code"] == "COD_NOT_SUPPORTED"
