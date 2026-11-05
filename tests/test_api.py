import asyncio
import json
import threading
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from app import api
from app.llm import LLMError
from app.config import Settings, get_settings
from app.main import app
from app.receipt import build_receipt
from app.sandbox import RunResult
from app.signing import Signer
from app.pipeline import Report, StepEvent


def _report(code: str, verdict: str = "FAIL") -> Report:
    return Report(verdict=verdict, error=None, code=code, tests="def test_x(): pass\n",
                  original_run=None, events=[], fix=None, fix_run=None, mutation=None,
                  explanation="explained", receipt=None)


def _events(text: str) -> list[tuple[str, dict]]:
    out, name = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name:
            out.append((name, json.loads(line.split(":", 1)[1])))
            name = None
    return out

SIGNER = Signer.from_seed_b64(None)
SERVICES = api.Services(None, None, None, None, None, SIGNER)


@pytest.fixture(autouse=True)
def fresh_state():
    api.gate.reset()
    app.dependency_overrides[api.get_services] = lambda: api.Services(
        llm=None, runner=None, mutate=None, receipt=None, job_root=None, signer=SIGNER)
    app.dependency_overrides[get_settings] = lambda: Settings(
        vultr_api_key="x", vultr_model="m", _env_file=None)
    yield
    app.dependency_overrides.clear()
    api.gate.reset()


@pytest.fixture
def client():
    return TestClient(app)  # no `with`: lifespan (docker digest) does not run


def test_streams_steps_then_report(client, monkeypatch):
    seen = {}

    async def fake(code, intent, llm, runner, emit, mutate, receipt, *, job_root=None):
        seen["intent"] = intent
        await emit(StepEvent("parse", "start", ""))
        await emit(StepEvent("parse", "ok", ""))
        return _report(code)

    monkeypatch.setattr(api, "run_review", fake)
    resp = client.post("/api/review", json={"code": "x = 1\n", "intent": ""})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _events(resp.text)
    assert [n for n, _ in events] == ["step", "step", "report"]
    assert events[0][1] == {"step": "parse", "status": "start", "detail": ""}
    assert events[-1][1]["verdict"] == "FAIL"
    assert events[-1][1]["code"] == "x = 1\n"
    assert events[-1][1]["tests"] == "def test_x(): pass\n"
    assert seen["intent"] is None


def test_rejects_large_code(client, monkeypatch):
    monkeypatch.setattr(api, "run_review", None)
    resp = client.post("/api/review", json={"code": "é" * 10_001})  # 20 002 UTF-8 bytes
    assert resp.status_code == 413


def test_second_request_from_same_ip_is_refused(client, monkeypatch):
    started, release = threading.Event(), threading.Event()

    async def slow(code, intent, *args, job_root=None):
        started.set()
        await asyncio.to_thread(release.wait, 10)
        return _report(code)

    monkeypatch.setattr(api, "run_review", slow)
    first: dict = {}
    t = threading.Thread(target=lambda: first.setdefault(
        "resp", client.post("/api/review", json={"code": "x = 1\n"})))
    t.start()
    assert started.wait(10)
    second = client.post("/api/review", json={"code": "x = 1\n"})
    assert second.status_code == 429
    assert second.json() == {"detail": "already running"}
    release.set()
    t.join(10)
    assert first["resp"].status_code == 200
    assert _events(first["resp"].text)[-1][0] == "report"


def test_lock_released_after_llm_error(client, monkeypatch):
    async def broken(code, intent, llm, runner, emit, *args, job_root=None):
        await emit(StepEvent("tests", "fail", "model unavailable"))
        raise LLMError("model unavailable")

    monkeypatch.setattr(api, "run_review", broken)
    resp = client.post("/api/review", json={"code": "x = 1\n"})
    assert resp.status_code == 200
    events = _events(resp.text)
    assert [n for n, _ in events] == ["step", "report"]
    report = events[-1][1]
    assert report["verdict"] == "ERROR"
    assert report["error"] == "model unavailable"
    assert report["code"] == "x = 1\n"

    async def ok(code, *args, job_root=None):
        return _report(code, "PASS")

    monkeypatch.setattr(api, "run_review", ok)
    again = client.post("/api/review", json={"code": "x = 1\n"})
    assert again.status_code == 200
    assert _events(again.text)[-1][1]["verdict"] == "PASS"


def test_unexpected_error_becomes_internal_error(client, monkeypatch):
    async def boom(*args, job_root=None):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(api, "run_review", boom)
    report = _events(client.post("/api/review", json={"code": "x"}).text)[-1][1]
    assert report["verdict"] == "ERROR"
    assert report["error"] == "internal error"
    assert "secret" not in json.dumps(report)
    assert not api.gate.active


def test_rate_limit(client, monkeypatch):
    async def ok(code, *args, job_root=None):
        return _report(code)

    monkeypatch.setattr(api, "run_review", ok)
    for _ in range(10):
        assert client.post("/api/review", json={"code": "x"}).status_code == 200
    resp = client.post("/api/review", json={"code": "x"})
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate limited"}


def test_client_ip_trusts_last_forwarded_only_when_configured():
    class Req:
        headers = {"x-forwarded-for": "1.1.1.1, 2.2.2.2"}

        class client:
            host = "10.0.0.1"

    assert api.client_ip(Req(), trust_proxy=False) == "10.0.0.1"
    assert api.client_ip(Req(), trust_proxy=True) == "2.2.2.2"


def test_generator_close_releases_ticket(monkeypatch):
    """A client disconnect closes the event generator mid-run; the slot must come back."""
    cancelled = []

    async def stuck(code, intent, llm, runner, emit, *args, job_root=None):
        await emit(StepEvent("parse", "start", ""))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(api, "run_review", stuck)

    async def scenario():
        ticket = api.gate.admit("1.1.1.1")
        gen = api.review_events("x", None, SERVICES, ticket)
        first = await anext(gen)
        assert first["event"] == "step"
        assert "1.1.1.1" in api.gate.active
        await gen.aclose()
        assert not api.gate.active
        # the slot is free again: a new client runs at once
        again = api.review_events("x", None, SERVICES, api.gate.admit("2.2.2.2"))
        assert (await anext(again))["event"] == "step"
        await again.aclose()

    asyncio.run(scenario())
    assert cancelled == [True, True]


def test_waiting_client_sees_its_place_then_runs(monkeypatch):
    """With every slot busy, the next client gets queue positions, then its review."""
    gates = []

    async def held(code, intent, llm, runner, emit, *args, job_root=None):
        await emit(StepEvent("parse", "start", ""))
        gate = asyncio.Event()
        gates.append(gate)
        await gate.wait()
        return _report(code)

    monkeypatch.setattr(api, "run_review", held)

    async def scenario():
        running = [api.review_events("x", None, SERVICES, api.gate.admit(f"10.0.0.{i}"))
                   for i in range(api.CONCURRENCY)]
        for gen in running:
            assert (await anext(gen))["event"] == "step"
        waiter = api.review_events("x", None, SERVICES, api.gate.admit("10.0.1.1"))
        first = await anext(waiter)
        assert first == {"event": "queue", "data": json.dumps({"position": 1})}
        nxt = asyncio.ensure_future(anext(waiter))
        await asyncio.sleep(0.05)
        assert not nxt.done()  # still waiting
        gates[0].set()
        assert [e["event"] async for e in running[0]] == ["report"]
        assert (await asyncio.wait_for(nxt, 3))["event"] == "step"
        for gen in (*running[1:], waiter):
            await gen.aclose()

    asyncio.run(scenario())
    assert not api.gate.active


def test_queue_full_is_refused():
    for i in range(api.MAX_WAITING):  # admitted, none has started waiting for a slot yet
        api.gate.admit(f"10.0.0.{i}")
    with pytest.raises(api.HTTPException) as exc:
        api.gate.admit("10.9.9.9")
    assert exc.value.detail == "queue full"


def test_hourly_limit(monkeypatch):
    monkeypatch.setattr(api, "HOURLY_LIMIT", 3)
    for i in range(3):
        api.gate.admit(f"10.0.0.{i}").release()
    with pytest.raises(api.HTTPException) as exc:
        api.gate.admit("10.9.9.9")
    assert exc.value.detail == "hourly limit"


def test_review_is_stopped_at_the_time_limit(monkeypatch):
    cancelled = []

    async def forever(code, intent, llm, runner, emit, *args, job_root=None):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(api, "run_review", forever)
    monkeypatch.setattr(api, "REVIEW_TIMEOUT_S", 0.2)

    async def scenario():
        return [e async for e in api.review_events("x", None, SERVICES, api.gate.admit("1.1.1.1"))]

    events = asyncio.run(scenario())
    assert [e["event"] for e in events] == ["report"]
    report = json.loads(events[0]["data"])
    assert report["verdict"] == "ERROR" and "longer than" in report["error"]
    assert cancelled == [True]
    assert not api.gate.active


def test_verify_endpoint_detects_forgery():
    run = RunResult(exit_code=1, stdout="", stderr="", duration_s=1.0, timed_out=False, container_name="c")
    settings = Settings(vultr_api_key="x", vultr_model="m", _env_file=None)
    receipt = json.loads(json.dumps(asdict(build_receipt("x", "y", run, settings, "", SIGNER))))
    client = TestClient(app)
    assert client.post("/api/verify", json=receipt).json()["valid"] is True
    receipt["verdict"] = "PASS"
    forged = client.post("/api/verify", json=receipt).json()
    assert forged == {"valid": False, "reason": "contents were changed after signing"}
    assert client.get("/api/public-key").json()["key_id"] == receipt["key_id"]
