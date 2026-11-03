import subprocess
import uuid

import pytest

from app.config import Settings
from app.sandbox import sandbox_command

IMAGE = "proveit-runner:py312"

pytestmark = pytest.mark.docker


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "run", "--rm", IMAGE, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_image_has_tools():
    result = _run("python", "-c", "import pytest, hypothesis, numpy, pandas")
    assert result.returncode == 0, result.stderr


def test_runs_as_nobody():
    result = _run("id", "-u")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "65534"


def test_hypothesis_profile_loaded(tmp_path):
    target = tmp_path / "test_target.py"
    target.write_text(
        "from hypothesis import settings\n"
        "\n"
        "def test_profile():\n"
        "    s = settings()\n"
        "    assert s.database is None\n"
        "    assert s.deadline is None\n"
        "    assert s.max_examples == 200\n"
        "    assert s.derandomize is True\n"
    )
    tmp_path.chmod(0o755)
    target.chmod(0o644)
    settings = Settings(
        vultr_api_key="x", vultr_model="x", sandbox_image=IMAGE, _env_file=None,
    )
    cmd = sandbox_command(
        tmp_path,
        ["python", "-m", "pytest", "-q", "/work/test_target.py"],
        f"proveit-test-{uuid.uuid4().hex[:12]}",
        settings,
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
