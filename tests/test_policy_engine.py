"""
tests/test_policy_engine.py
----------------------------
Phase 5 CI gate — Policy Engine tests.

Covers:
  - PolicyEngine.validate_plan() THRESHOLD_CHECK rule
  - REFUND_AUTO_LIMIT_INR boundary (at-limit allowed, above-limit blocked)
  - Multi-step plan: single BLOCK violation blocks the entire plan
  - Executor early-exit on BLOCK
  - Approved plans execute normally
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agents.policy import PolicyEngine, get_policy_engine
from app.schemas.agent import ToolCallPlan, ToolCallStep
from app.schemas.policy import RequestContext
from app.schemas.trace import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(trace_id: str = "trc_test_policy") -> RequestContext:
    return RequestContext(trace_id=trace_id, session_id="sess_test")


def _step(tool: str, args: dict) -> ToolCallStep:
    return ToolCallStep(tool=tool, args=args, rationale="test")


def _plan(*steps: ToolCallStep) -> ToolCallPlan:
    return ToolCallPlan(steps=list(steps))


# ---------------------------------------------------------------------------
# THRESHOLD_CHECK rule
# ---------------------------------------------------------------------------

class TestThresholdCheck:

    def setup_method(self):
        self.engine = PolicyEngine()

    def test_amount_below_limit_approved(self):
        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 1000.0, "method": "UPI"}))
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is True
        assert result.violations == []

    def test_amount_at_limit_approved(self):
        """Exactly at 25000.0 must be approved — rule is strictly >."""
        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 25000.0, "method": "UPI"}))
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is True
        assert result.violations == []

    def test_amount_above_limit_blocked(self):
        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 25000.01, "method": "UPI"}))
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is False
        block = next(v for v in result.violations if v.action == "BLOCK")
        assert block.rule == "THRESHOLD_CHECK"
        assert block.tool == "payments_refund"

    def test_large_amount_blocked(self):
        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 42000.0, "method": "UPI"}))
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is False

    def test_missing_amount_field_skipped(self):
        """No amount arg → rule does not apply → no violation."""
        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "method": "UPI"}))
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is True

    def test_non_payment_step_not_checked(self):
        """oms_get_order has no THRESHOLD_CHECK tag — rule never fires."""
        plan = _plan(_step("oms_get_order", {"order_id": "ORD-78321"}))
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is True
        assert result.violations == []

    def test_cancel_item_not_checked(self):
        """oms_cancel_item executes directly — no policy rule applies."""
        plan = _plan(_step("oms_cancel_item", {"order_id": "ORD-78321", "line_id": 1}))
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is True
        assert result.violations == []

    def test_limit_read_from_settings(self):
        """Patching settings.REFUND_AUTO_LIMIT_INR must change the threshold."""
        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 5000.0, "method": "UPI"}))
        with patch("app.agents.policy.settings") as mock_settings:
            mock_settings.REFUND_AUTO_LIMIT_INR = 1000.0
            result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is False

    def test_violation_message_contains_amounts(self):
        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 30000.0, "method": "UPI"}))
        result = self.engine.validate_plan(plan, _ctx())
        violation = next(v for v in result.violations if v.rule == "THRESHOLD_CHECK")
        assert "30,000.00" in violation.message
        assert "25,000.00" in violation.message


# ---------------------------------------------------------------------------
# Multi-step plans
# ---------------------------------------------------------------------------

class TestMultiStepPlans:

    def setup_method(self):
        self.engine = PolicyEngine()

    def test_single_block_fails_entire_plan(self):
        plan = _plan(
            _step("oms_get_order", {"order_id": "ORD-78321"}),
            _step("payments_refund", {"customer_id": "CUST-001", "amount": 50000.0, "method": "UPI"}),
        )
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is False

    def test_two_read_steps_approved(self):
        plan = _plan(
            _step("oms_get_order", {"order_id": "ORD-78321"}),
            _step("kb_search", {"tags": ["refund", "policy"]}),
        )
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is True
        assert result.violations == []

    def test_empty_plan_approved(self):
        result = self.engine.validate_plan(_plan(), _ctx())
        assert result.approved is True
        assert result.violations == []

    def test_cancel_then_refund_under_limit_approved(self):
        """Cancel item + small refund — both should pass."""
        plan = _plan(
            _step("oms_cancel_item", {"order_id": "ORD-78321", "line_id": 1}),
            _step("payments_refund", {"customer_id": "CUST-001", "amount": 500.0, "method": "UPI"}),
        )
        result = self.engine.validate_plan(plan, _ctx())
        assert result.approved is True
        assert result.violations == []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:

    def test_same_instance(self):
        assert get_policy_engine() is get_policy_engine()

    def test_is_policy_engine(self):
        assert isinstance(get_policy_engine(), PolicyEngine)


# ---------------------------------------------------------------------------
# Executor integration
# ---------------------------------------------------------------------------

class TestExecutorPolicyGate:

    def test_block_prevents_tool_execution(self):
        from app.agents.executor import execute

        handler = MagicMock(return_value=ToolResult(status="SUCCESS", data={}))
        fake_registry = {
            "payments_refund": {"handler": handler, "policy": ["WRITE", "REFUND", "THRESHOLD_CHECK"], "description": "test"}
        }
        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 50000.0, "method": "UPI"}))

        with patch("app.agents.executor.TOOL_REGISTRY", fake_registry):
            records = execute(plan, trace_id="trc_block", session_id="sess_test")

        handler.assert_not_called()
        assert records[0].status == "ESCALATE"
        assert "POLICY_BLOCK" in records[0].result_summary

    def test_block_attaches_tool_result(self):
        from app.agents.executor import execute

        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 99000.0, "method": "UPI"}))
        records = execute(plan, trace_id="trc_tr", session_id="sess_test")

        tr = records[0].__dict__.get("_tool_result")
        assert tr is not None
        assert tr.error_code == "POLICY_BLOCK"

    def test_block_sets_escalated_flag(self):
        from app.agents.executor import execute

        plan = _plan(_step("payments_refund", {"customer_id": "CUST-001", "amount": 50000.0, "method": "UPI"}))
        records = execute(plan, trace_id="trc_esc", session_id="sess_test")
        assert any(r.status == "ESCALATE" for r in records)

    def test_approved_plan_executes_normally(self):
        from app.agents.executor import execute

        handler = MagicMock(return_value=ToolResult(status="SUCCESS", data={"order_id": "ORD-78321"}))
        fake_registry = {
            "oms_get_order": {"handler": handler, "policy": ["READ"], "description": "test"}
        }
        plan = _plan(_step("oms_get_order", {"order_id": "ORD-78321"}))

        with patch("app.agents.executor.TOOL_REGISTRY", fake_registry):
            records = execute(plan, trace_id="trc_ok", session_id="sess_test")

        handler.assert_called_once_with(order_id="ORD-78321")
        assert records[0].status == "SUCCESS"

    def test_cancel_item_executes_directly(self):
        """oms_cancel_item must not be blocked — no policy rule applies to it."""
        from app.agents.executor import execute

        handler = MagicMock(return_value=ToolResult(status="SUCCESS", data={"line_id": 1}))
        fake_registry = {
            "oms_cancel_item": {"handler": handler, "policy": ["WRITE", "REQUIRES_CONFIRM"], "description": "test"}
        }
        plan = _plan(_step("oms_cancel_item", {"order_id": "ORD-78321", "line_id": 1}))

        with patch("app.agents.executor.TOOL_REGISTRY", fake_registry):
            records = execute(plan, trace_id="trc_cancel", session_id="sess_test")

        handler.assert_called_once()
        assert records[0].status == "SUCCESS"

    def test_payments_refund_line_id_is_propagated_from_cancel_item(self):
        """Compound cancel+refund plans should inherit line_id and amount for payments_refund."""
        from app.agents.executor import execute

        cancel_handler = MagicMock(return_value=ToolResult(status="SUCCESS", data={"line_id": 2}))
        refund_handler = MagicMock(return_value=ToolResult(status="SUCCESS", data={}))
        fake_registry = {
            "oms_cancel_item": {"handler": cancel_handler, "policy": ["WRITE", "REQUIRES_CONFIRM"], "description": "test"},
            "payments_refund": {"handler": refund_handler, "policy": ["WRITE", "REFUND", "THRESHOLD_CHECK"], "description": "test"},
        }

        plan = _plan(
            _step("oms_cancel_item", {"order_id": "ORD-78321", "line_id": 2}),
            _step("payments_refund", {"order_id": "ORD-78321", "method": "original"}),
        )

        with patch("app.agents.executor.TOOL_REGISTRY", fake_registry):
            records = execute(plan, trace_id="trc_propagation", session_id="sess_test")

        refund_handler.assert_called_once()
        refund_args = refund_handler.call_args.kwargs
        assert refund_args["order_id"] == "ORD-78321"
        assert refund_args["line_id"] == 2
        assert refund_args["amount"] == 1500.0
        assert records[1].status == "SUCCESS"
