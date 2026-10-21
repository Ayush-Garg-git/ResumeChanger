"""
Tests for the health endpoint.

TestClient sends requests straight to the FastAPI app in memory, so we
don't need to start a server to run these tests.

Run from the backend/ folder:
    pytest
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "AI Resume Refiner"


def test_unknown_route_returns_404():
    # Confirms FastAPI rejects routes we never defined, instead of
    # silently answering something.
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
