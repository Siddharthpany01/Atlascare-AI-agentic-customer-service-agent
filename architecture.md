# AtlasCare Architecture Overview

## 1. Purpose

AtlasCare is an agentic AI customer-support system for Acme Retail Co. It is built as a production-oriented FastAPI service rather than a notebook-style proof of concept. The system supports three core customer journeys: basic order tracking, compound service requests, and refund escalation.

The current implementation uses Python 3.11, FastAPI, Pydantic v2, and Groq Llama 3.1 8B Instant. Redis for Trace and Session store, along with Prometheus and OpenTelemetry for tracking app performance.

## 2. High-Level Architecture

AtlasCare uses a planner-executor pattern with deterministic policy enforcement between planning and execution.

```text
Client
  -> FastAPI API Layer
  -> TraceMiddleware + RateLimitMiddleware
  -> Intent Classifier
  -> Planner
  -> Policy Engine
  -> Executor
  -> Tool Registry
  -> OMS / CRM / KB / Payments Adapters / Case
  -> Synthesizer
  -> Trace Store + Audit Logger
  -> API Response
```

The key architectural rule is that the LLM may classify, plan, and synthesize, but it does not directly execute tools and does not decide whether a sensitive action is permitted. The policy engine and tool adapters enforce business rules in Python.

## 3. API Layer

The application is exposed through FastAPI.

- `GET /health` returns service health and version information.
- `POST /query` accepts a customer message and session ID, runs the agent pipeline, and returns a reply, trace ID, session ID, and escalation flag.

The application factory is `create_app() -> FastAPI`. Middleware is registered in `app/core.py`, including trace assignment, rate limiting, routers, OpenTelemetry initialization, and lifespan shutdown cleanup.

## 4. Agent Layer

The agent layer is split into four responsibilities.

### Classifier

`classify(message: str, trace_id: str) -> ClassifyResult` maps customer input to one of:

- `TRACKING`, `COMPOUND`, `ESCALATION`, `UNKNOWN`,
The classifier is deterministic by configuration: Groq Llama 3.1 8B Instant is called with temperature `0.0`.

### Planner

`plan(message: str, intent: str, session_context: dict, trace_id: str) -> ToolCallPlan` emits a typed, ordered list of tool-call steps. The planner output is validated using Pydantic. Invalid tool names are dropped, and `kb_search` steps with empty tag lists are dropped.

### Executor

`execute(plan: ToolCallPlan, trace_id: str, session_id: str = "", customer_tier: str | None = None) -> list[ToolCallRecord]` runs approved tool calls synchronously. It performs policy validation before any tool is called, retries `FAILED` tool calls according to configured retry settings, and does not retry `ESCALATE` or `IDEMPOTENT` outcomes.

### Synthesizer

`synthesize(message: str, tool_results: list[ToolCallRecord], trace_id: str) -> str` turns structured tool results into a user-facing response. It is instructed to use only available tool-result facts and to fall back to a safe response on errors. with temperature `0.3`.

## 5. Tool Layer

Tool access is centralized through `TOOL_REGISTRY`. Registered tools are:

- `oms_get_order`, `oms_cancel_item`, `oms_update_address`, `payments_refund`, `crm_create_case`, `kb_search`, `case_search`

Every tool returns `ToolResult` with one of four statuses:

- `SUCCESS`, `FAILED`, `IDEMPOTENT`, `ESCALATE`

The tools are synchronous and wrap failures rather than raising raw exceptions to callers.

## 6. Policy and Guardrails

The policy engine is a first-class deterministic component. Its primary rule is the refund threshold check: `payments_refund` with an amount above `REFUND_AUTO_LIMIT_INR` is blocked before tool execution.

Additional guardrails are implemented outside the prompt layer:

- COD refunds escalate in the payments adapter.
- Address updates are blocked for delivered or cancelled orders.
- Empty KB tag lists fail explicitly.
- Prompt injection sanitation is applied before LLM calls.
- PII masking is applied at trace and audit boundaries.
- Rate limiting is enforced per session ID, with client IP fallback.

## 7. Observability

AtlasCare records structured traces and audit events. Trace records include identifiers, session context, intent, tool-call records, latency, status, and timestamps. `TraceStore.close() -> None` is called during application shutdown to close Redis connections cleanly.

OpenTelemetry initialization is exposed through `init_tracing(app: Optional[FastAPI] = None) -> None`, with graceful fallback if optional observability dependencies are unavailable.

## 8. Evaluation

The evaluation pipeline is deterministic. `evaluation/judge.py` exposes `evaluate_trace(trace: dict, expected: dict) -> dict`, and `evaluation/run_eval.py` exposes `run_evals() -> None`. The harness validates the end-to-end HTTP pipeline and confirms that key journey outcomes match expectations.

For J3, the important distinction is that the planner may produce a payment step, but the policy engine must prevent successful payment execution for over-limit refunds. The evaluator checks that no successful payment call occurs.

## 9. Deployment and Packaging

Lastly for production-hardening elements such as rate limiting, lifespan cleanup, connection pooling, centralized settings, and Groq timeout configuration. Finally the project for review with:

- `README.md`, `docs/architecture.md`, `docs/kpi_framework.md`, `docs/test_plan.docx`

## 10. Core Design Principle

AtlasCare separates what the AI proposes from what the system is allowed to do. This separation is the central safety and reliability design choice: prompts guide behavior, but deterministic application code enforces policy.

