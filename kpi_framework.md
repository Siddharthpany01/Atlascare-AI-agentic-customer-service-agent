# AtlasCare KPI Framework

> This KPI framework defines how AtlasCare should be evaluated from business, quality, operational, and AI-safety perspectives. The metrics are intended for both submission review and future production monitoring.

---

## 1. Business KPIs

| KPI | Definition | Target | Measurement Approach |
|---|---|:---:|---|
| Self-Service Resolution Rate | % of customer interactions resolved without human escalation | > 70% | `resolved_without_escalation / total_interactions` |
| Auto-Refund Acceptance Rate | % of eligible refund requests auto-processed below the configured threshold | > 85% | `payments_success / refund_intent_count` |
| Compound Request Success Rate | % of compound journeys where all required approved steps complete successfully | > 90% | `compound_success / compound_total` |
| Escalation Accuracy | % of escalations genuinely required by policy or tool constraints | > 95% | Human-reviewed audit sample |
| Cost per Contact | LLM and infrastructure cost per resolved interaction | Monitor trend | Token usage and infrastructure logs |

---

## 2. Quality KPIs

| KPI | Definition | Target | Measurement Approach |
|---|---|:---:|---|
| Response Factual Accuracy | % of responses containing only facts present in tool results | > 99% | Trace-grounded response review |
| Plan Parse Success Rate | % of planner responses that pass Pydantic validation on first parse | > 95% | Planner parse counters |
| Intent Classification Accuracy | Correct intent classification against labelled journeys | > 97% | Evaluation cases and manual labelled set |
| Trace Completeness | % of traces containing all required fields | 100% | Trace schema validation |
| Tool Argument Validity | % of planned tool args accepted by Pydantic/tool validation | > 99% | Tool validation logs |

---

## 3. Operational KPIs

| KPI | Definition | Target | Measurement Approach |
|---|---|:---:|---|
| P50 Latency | Median end-to-end latency for successful requests | < 1,500 ms | Request latency histogram |
| P95 Latency | 95th percentile latency across all journeys | < 3,000 ms | Request latency histogram |
| Tool Call Error Rate | % of tool calls returning `FAILED` | < 2% | `tool_call_total{status="FAILED"}` |
| LLM Timeout Rate | % of LLM calls timing out | < 1% | LLM call metrics |
| Rate-Limit Rejection Rate | % of requests rejected by rate limiting | Monitor trend | HTTP 429 count |
| System Availability | Availability measured through `/health` | > 99.5% | Health check monitor |

---

## 4. AI Safety KPIs

| KPI | Definition | Target | Measurement Approach |
|---|---|:---:|---|
| Threshold Bypass Attempts | Count of attempted over-limit refunds | 0 successful bypasses | Policy violation counter |
| Unauthorized Tool Calls | Count of invalid or unregistered tools emitted by the planner | 0 executed | Planner validation and executor records |
| PII Leak Rate | Count of log or trace records containing unmasked PII | 0 | Regex scan over audit and trace outputs |
| Prompt Injection Handling Rate | % of adversarial inputs sanitized or safely handled | > 99% | Adversarial test suite |
| Hallucination Rate | % of replies containing unsupported facts | < 0.5% | Trace-grounded review |

---

## 5. Journey-Specific Acceptance KPIs

### J1 — Tracking

- Exactly one order lookup tool should be sufficient for simple order-status requests.
- The response should include a trace ID.
- No escalation should occur unless the order lookup fails or the case requires handoff.

### J2 — Compound Request

- The planner should decompose the request into valid ordered tool steps.
- The executor should run approved steps sequentially and record each result.
- Failed transient tool calls should be retried according to configured retry settings.

### J3 — Escalation

- Refunds above the configured auto-refund limit must not be successfully processed through payments.
- A CRM case should be created for human follow-up when escalation is required.
- The trace ID should be included in the handoff context.

---

## 6. Reporting View

A lightweight submission dashboard should summarize:

| Dimension | Description |
|---|---|
| Requests by intent | Total requests broken down by intent type |
| Escalations by reason | Count and classification of all escalations |
| Tool calls by status | Success, failed, and retried tool call counts |
| Latency percentiles | P50 and P95 end-to-end latency |
| Refund blocks by threshold rule | Count of refunds blocked per policy rule |
| PII masks applied | Total PII masking events in logs and traces |
| Evaluation pass/fail by journey | J1, J2, J3 pass/fail breakdown |

---

## 7. Review Guidance

The most important KPIs for this submission are:

| Priority KPI | Rationale |
|---|---|
| **Policy correctness** | Demonstrates AtlasCare enforces business rules reliably |
| **Trace completeness** | Ensures full auditability of every interaction |
| **J3 escalation behavior** | Validates safety-critical refund and handoff flows |
| **Reproducible evaluation** | Confirms results are consistent and trustworthy |

> These directly demonstrate that AtlasCare treats safety, auditability, and production reliability as first-class requirements.
