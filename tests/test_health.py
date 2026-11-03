from fastapi.testclient import TestClient

from app.main import app


def test_health_ok(monkeypatch):
    monkeypatch.delenv("VULTR_API_KEY", raising=False)
    monkeypatch.delenv("VULTR_MODEL", raising=False)
    resp = TestClient(app).get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
