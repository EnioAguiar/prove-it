import subprocess

import pytest

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


def test_hypothesis_profile_loaded():
    code = (
        "def test_profile():\n"
        "    from hypothesis import settings\n"
        "    s = settings()\n"
        "    assert s.database is None\n"
        "    assert s.deadline is None\n"
        "    assert s.max_examples == 200\n"
        "    assert s.derandomize is True\n"
    )
    script = f"mkdir -p /tmp/work && cd /tmp/work && printf '%s' \"$CODE\" > test_p.py && pytest -q --rootdir=/tmp/work --confcutdir=/tmp/work test_p.py"
    result = subprocess.run(
        ["docker", "run", "--rm", "-e", f"CODE={code}", IMAGE, "sh", "-c", script],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
