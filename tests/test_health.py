"""
tests/test_health.py
--------------------
Phase 1 CI gate — this is the ONLY test that must be green before moving to Phase 2.

What it tests
-------------
1. GET /health returns HTTP 200
2. Response body matches the HealthResponse schema
3. trace_id is present in the response header (confirms middleware is running)

Run with:  pytest tests/test_health.py -v
"""
import pytest
from fastapi.testclient import TestClient
from app.core import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health_returns_200(client):
    """Liveness probe must always return 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_schema(client):
    """Response must include 'status' and 'version' fields."""
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_health_has_trace_id_header(client):
    """
    TraceMiddleware must inject X-Trace-ID on every response.
    If this fails, middleware is not registered correctly in core.py.
    """
    response = client.get("/health")
    assert "x-trace-id" in response.headers
    assert response.headers["x-trace-id"].startswith("trc_")


def test_query_stub_returns_200(client):
    """
    POST /query stub must return 200 with the expected shape.
    Content is a placeholder — full wiring happens in Phase 4.
    """
    response = client.post(
        "/query",
        json={"message": "Where is my order?", "session_id": "test-session-001"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    assert "trace_id" in data
    assert data["session_id"] == "test-session-001"
