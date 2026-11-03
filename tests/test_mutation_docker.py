import asyncio
import functools
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.mutation import RUN_ARGV, run_mutation
from app.sandbox import run_in_sandbox

pytestmark = pytest.mark.docker

SETTINGS = Settings(vultr_api_key="x", vultr_model="m", _env_file=None)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

PROPERTY_TESTS = '''\
from hypothesis import given, strategies as st
from target import *


@given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=1, max_value=60))
def test_installments_sum_to_total(total, n):
    assert sum(parcelas(total, n)) == total


@given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=1, max_value=60))
def test_installments_are_fair(total, n):
    result = parcelas(total, n)
    assert len(result) == n
    assert max(result) - min(result) <= 1
'''


def test_installment_tests_kill_mutants():
    code = (EXAMPLES / "parcelamento_fixed.py").read_text()
    runner = functools.partial(run_in_sandbox, settings=SETTINGS)

    result = asyncio.run(run_mutation(code, PROPERTY_TESTS, runner))

    print(f"killed {result.killed}/{result.total}; survivors: "
          f"{[(m.id, m.description) for m in result.survivors]}")
    assert result.total >= 1
    assert result.killed >= 1


SPIN_TESTS = '''\
from hypothesis import given, strategies as st
from target import *


@given(st.integers(min_value=0, max_value=50))
def test_counts_down_to_zero(n):
    assert spin(n) == 0
'''

SPIN_OK = "def spin(n):\n    while n > 0:\n        n -= 1\n    return n\n"
SPIN_HANGS = "def spin(n):\n    while n > 0:\n        n += 1\n    return n\n"


def test_runner_reports_hang_as_killed_and_original_as_survivor(tmp_path):
    # Mutant 1 is the unmutated code (must survive: proves kills are not startup
    # timeouts); mutant 2 loops forever (must be killed by the per-mutant timeout).
    tmp_path.chmod(0o755)
    (tmp_path / "test_target.py").write_text(SPIN_TESTS)
    for mutant_id, source in ((1, SPIN_OK), (2, SPIN_HANGS)):
        mutant_dir = tmp_path / "mutants" / str(mutant_id)
        mutant_dir.mkdir(parents=True)
        (mutant_dir / "target.py").write_text(source)
    for path in tmp_path.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)

    result = asyncio.run(run_in_sandbox(tmp_path, RUN_ARGV, 30, SETTINGS))

    # Not timed out: the hanging mutant was cut by the 2 s per-mutant limit, not the sandbox.
    assert result.timed_out is False, result.stderr
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        {"id": 1, "killed": False},
        {"id": 2, "killed": True},
    ]
