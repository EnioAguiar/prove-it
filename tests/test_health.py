from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    resp = TestClient(app).get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
