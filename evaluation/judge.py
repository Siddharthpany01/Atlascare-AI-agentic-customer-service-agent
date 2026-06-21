def evaluate_trace(trace: dict, expected: dict) -> dict:
    details = []
    passed = True
    
    # Check tool calls count
    if "tool_calls_count" in expected:
        actual_count = len(trace.get("tool_calls", []))
        if actual_count != expected["tool_calls_count"]:
            passed = False
            details.append(f"Fail: Expected {expected['tool_calls_count']} tool calls, got {actual_count}")
            
    # Check tools used
    if "tools_used" in expected:
        actual_tools = [call.get("tool") for call in trace.get("tool_calls", [])]
        for expected_tool in expected["tools_used"]:
            if expected_tool not in actual_tools:
                passed = False
                details.append(f"Fail: Expected tool '{expected_tool}' was not called")
                
    # Check escalation status
    if "escalation" in expected:
        actual_escalation = trace.get("escalation_triggered", False)
        if actual_escalation != expected["escalation"]:
            passed = False
            details.append(f"Fail: Expected escalation={expected['escalation']}, got {actual_escalation}")
            
    # Check latency
    if "latency_under_ms" in expected:
        actual_latency = trace.get("latency_ms", float("inf"))
        if actual_latency > expected["latency_under_ms"]:
            passed = False
            details.append(f"Fail: Latency {actual_latency}ms exceeded target {expected['latency_under_ms']}ms")
            
    # Check payments specifically (for J3 boundary check)
    if "payments_called" in expected:
        payment_success = any(
            call.get("tool") == "payments_refund" and call.get("status") in ("SUCCESS", "IDEMPOTENT")
            for call in trace.get("tool_calls", [])
        )
        if payment_success != expected["payments_called"]:
            passed = False
            details.append(f"Fail: Expected payments_called={expected['payments_called']}, got {payment_success}")
            
    # Check CRM case creation (for J3 handoff)
    if "crm_case_created" in expected:
        crm_success = any(
            call.get("tool") == "crm_create_case" and call.get("status") == "SUCCESS" 
            for call in trace.get("tool_calls", [])
        )
        if crm_success != expected["crm_case_created"]:
            passed = False
            details.append(f"Fail: Expected crm_case_created={expected['crm_case_created']}, got {crm_success}")

    if passed:
        details.append("Pass: All expected criteria met.")
        
    return {
        "score": 1 if passed else 0,
        "passed": passed,
        "details": details
    }