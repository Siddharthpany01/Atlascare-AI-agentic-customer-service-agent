import json
import sys
import uuid
import httpx
import asyncio
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from evaluation.judge import evaluate_trace

API_URL = "http://localhost:8000/query"
CASES_FILE = Path(__file__).resolve().parent / "cases.json"

async def run_evals():
    if not CASES_FILE.exists():
        print(f"Error: {CASES_FILE} not found.")
        return

    with open(CASES_FILE, "r", encoding="utf-8") as f:
        cases = json.load(f)

    print(f"Loaded {len(cases)} evaluation cases.")
    print("-" * 50)

    total_score = 0

    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        for case in cases:
            print(f"Running Case: {case['id']}")
            print(f"Input: {case['input']}")
            
            # Generate a fresh session ID for each evaluation case to prevent state bleed
            session_id = f"eval_sess_{uuid.uuid4().hex[:8]}"
            
            try:
                response = await client.post(API_URL, json={
                    "message": case["input"],
                    "session_id": session_id
                })
                
                if response.status_code != 200:
                    print(f"HTTP Error {response.status_code}: {response.text}")
                    print("Status: FAILED")
                    continue
                    
                data = response.json()
                
                # Fetch the full trace payload from the router's response. 
                # Note: In a production setup this might be pulled directly from the TraceStore backend.
                # Since Phase 4 doesn't serialize the full trace back to the client directly in QueryResponse, 
                # we need to simulate the trace object that the judge expects based on the API response.
                
                # Wait, the J3 schema expectation requires access to tool_calls and latency_ms. 
                # Assuming the trace_id is returned, let's fetch it from the internal backend for testing purposes, 
                # or assume the API route in Phase 8 was updated to return it.
                # For this script, we will mock the trace reconstruction if the API doesn't return it natively, 
                # but in a real test env, this script would read directly from app.state.tracer.
                
                # For demonstration, we assume `data` contains the trace or we can pull it via Redis.
                # To keep it isolated without Redis, we will print a placeholder warning if trace data is missing.
                
                if "trace" not in data:
                    print("Warning: Full trace not returned in API response. To run complete eval, ensure router.py attaches the trace or fetch directly from TracerStore.")
                    # Placeholder trace to prevent script crash
                    trace = {
                        "tool_calls": [],
                        "escalation_triggered": data.get("escalated", False),
                        "latency_ms": 0
                    }
                else:
                    trace = data["trace"]

                result = evaluate_trace(trace, case["expected"])
                
                total_score += result["score"]
                status_str = "✅ PASS" if result["passed"] else "❌ FAIL"
                
                print(f"Status: {status_str}")
                for detail in result["details"]:
                    print(f"  - {detail}")
                    
            except Exception as e:
                print(f"Status: ❌ FAIL (Exception: {str(e)})")
                
            print("-" * 50)

    print(f"Final Score: {total_score}/{len(cases)}")

if __name__ == "__main__":
    asyncio.run(run_evals())