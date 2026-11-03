import asyncio
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.sandbox import RunResult, run_in_sandbox, sandbox_command

pytestmark = pytest.mark.docker

ARGV = ["python", "/work/target.py"]


def _settings(**overrides: str) -> Settings:
    return Settings(vultr_api_key="x", vultr_model="x", _env_file=None, **overrides)


def _job(tmp_path: Path, code: str) -> Path:
    target = tmp_path / "target.py"
    target.write_text(code)
    tmp_path.chmod(0o755)
    target.chmod(0o644)
    return tmp_path


def _run(tmp_path: Path, code: str, timeout_s: float = 30) -> RunResult:
    job = _job(tmp_path, code)
    return asyncio.run(run_in_sandbox(job, ARGV, timeout_s, _settings()))


def _container_exists(name: str) -> bool:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "-q"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip() != ""


def test_write_blocked(tmp_path):
    result = _run(tmp_path, "open('/work/x', 'w')\n")
    assert result.exit_code != 0
    assert "Read-only file system" in result.stderr


def test_no_network(tmp_path):
    result = _run(
        tmp_path,
        "import socket\nsocket.create_connection(('1.1.1.1', 80), 2)\n",
    )
    assert result.exit_code != 0
    assert "Network is unreachable" in result.stderr


def test_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("VULTR_API_KEY", "secret")
    result = _run(tmp_path, "import os\nprint(os.environ.get('VULTR_API_KEY'))\n")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "None"


def test_infinite_loop_killed(tmp_path):
    result = _run(tmp_path, "while True:\n    pass\n", timeout_s=3)
    assert result.timed_out is True
    assert result.exit_code == 137
    assert result.duration_s < 6
    assert not _container_exists(result.container_name)


def test_import_time_block(tmp_path):
    result = _run(tmp_path, "import time\ntime.sleep(60)\n", timeout_s=3)
    assert result.timed_out is True
    assert result.exit_code == 137
    assert not _container_exists(result.container_name)


def test_fork_bomb_contained(tmp_path):
    code = (
        "import os, time\n"
        "try:\n"
        "    while True:\n"
        "        if os.fork() == 0:\n"
        "            time.sleep(30)\n"
        "            os._exit(0)\n"
        "except Exception as e:\n"
        "    print(type(e).__name__, e, flush=True)\n"
    )
    result = _run(tmp_path, code)
    assert result.timed_out is False
    assert (
        "Resource temporarily unavailable" in result.stdout
        or "BlockingIOError" in result.stdout
    ), result.stdout + result.stderr


def test_memory_cap(tmp_path):
    result = _run(tmp_path, "data = b'x' * (512 * 2**20)\nprint(len(data))\n")
    assert result.exit_code == 137 or "MemoryError" in result.stderr


def test_huge_output_truncated(tmp_path):
    result = _run(tmp_path, "print('x' * 10**8)\n", timeout_s=60)
    assert result.timed_out is False
    assert len(result.stdout) <= 65_536 + 20
    assert result.stdout.endswith("[truncated]")


def test_container_removed(tmp_path):
    result = _run(tmp_path, "print('hi')\n")
    assert result.exit_code == 0, result.stderr
    assert result.stdout == "hi\n"
    assert result.container_name.startswith("proveit-")
    assert not _container_exists(result.container_name)


FLAGS = [
    "--rm", "--network", "none", "--read-only",
    "--tmpfs", "/tmp:rw,size=64m",
    "--memory", "256m", "--memory-swap", "256m", "--cpus", "0.5",
    "--pids-limit", "64", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges", "--user", "65534:65534",
]


def _contains_slice(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def test_command_flags(tmp_path):
    settings = _settings(sandbox_runtime="runc")
    cmd = sandbox_command(tmp_path, ARGV, "proveit-abc", settings)
    assert cmd[:2] == ["docker", "run"]
    assert _contains_slice(cmd, ["--name", "proveit-abc"])
    assert _contains_slice(cmd, FLAGS)
    assert _contains_slice(cmd, ["-v", f"{tmp_path.resolve()}:/work:ro"])
    assert cmd[-3:] == [settings.sandbox_image, *ARGV]
    assert not any(a.startswith("--runtime") for a in cmd)
    assert not any(a in ("-e", "--env", "--env-file") for a in cmd)
    assert not any(a.startswith(("--env=", "--env-file=", "-e=")) for a in cmd)

    runsc = sandbox_command(tmp_path, ARGV, "proveit-abc", _settings(sandbox_runtime="runsc"))
    assert "--runtime=runsc" in runsc
    assert runsc.index("--runtime=runsc") < runsc.index(settings.sandbox_image)
