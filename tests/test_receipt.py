import asyncio
import hashlib
import re
import shlex
from pathlib import Path

from app import receipt as receipt_mod
from app.config import Settings
from app.receipt import RUN_ARGV, build_receipt, read_image_digest
from app.sandbox import RunResult, sandbox_command

DIGEST = "ghcr.io/enioaguiar/proveit-runner@sha256:" + "a" * 64


def _settings(**kw) -> Settings:
    return Settings(vultr_api_key="x", vultr_model="m", _env_file=None, **kw)


def _run() -> RunResult:
    return RunResult(exit_code=0, stdout="", stderr="", duration_s=1.5, timed_out=False, container_name="c")


def test_hashes_match_sha256():
    r = build_receipt("def f(): pass\n", "def test_f(): pass\n", _run(), _settings(), DIGEST)
    assert r.code_sha256 == hashlib.sha256(b"def f(): pass\n").hexdigest()
    assert r.tests_sha256 == hashlib.sha256(b"def test_f(): pass\n").hexdigest()
    assert r.exit_code == 0 and r.duration_s == 1.5
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r.created_at)


def test_command_contains_all_limits():
    r = build_receipt("x", "y", _run(), _settings(), DIGEST)
    for flag in ["--rm", "--network none", "--read-only", "--tmpfs /tmp:rw,size=64m", "--memory 256m",
                 "--memory-swap 256m", "--cpus 0.5", "--pids-limit 64", "--cap-drop ALL",
                 "--security-opt no-new-privileges", "--user 65534:65534", '-v "$PWD":/work:ro']:
        assert flag in r.command
    assert r.command.endswith(f"{DIGEST} python -m pytest -q /work/test_target.py")
    assert "--name" not in r.command
    assert r.image == DIGEST and r.image_digest == DIGEST
    assert r.limits == {"network": "none", "memory": "256m", "memory_swap": "256m", "cpus": "0.5",
                        "pids": "64", "tmpfs": "/tmp:rw,size=64m", "read_only": "true",
                        "user": "65534:65534", "timeout": "10s"}


def test_command_has_no_env():
    r = build_receipt("x", "y", _run(), _settings(), DIGEST)
    tokens = shlex.split(r.command)
    assert "-e" not in tokens and "--env" not in tokens and "--env-file" not in tokens
    assert not any(t.startswith(("--env=", "--env-file=")) for t in tokens)


def test_command_round_trips_to_sandbox_flags():
    for runtime in ("runc", "runsc"):
        s = _settings(sandbox_runtime=runtime)
        r = build_receipt("x", "y", _run(), s, DIGEST)
        assert r.runtime == runtime
        job = Path("/some/job")
        tokens = shlex.split(r.command.replace('"$PWD"', str(job)))
        expected = sandbox_command(job, RUN_ARGV, "n", s)
        i = expected.index("--name")
        del expected[i:i + 2]
        expected[expected.index(s.sandbox_image)] = DIGEST
        assert tokens == expected


def test_without_digest_uses_local_image():
    s = _settings()
    r = build_receipt("x", "y", _run(), s, "")
    assert r.image_digest == ""
    assert r.image == s.sandbox_image
    assert f" {s.sandbox_image} python -m pytest" in r.command


def test_read_image_digest_empty_when_unavailable(monkeypatch):
    async def fake_exec(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(receipt_mod.asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(read_image_digest("nope")) == ""
