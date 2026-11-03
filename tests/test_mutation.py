import ast
import asyncio
import json
from pathlib import Path

from app.mutation import Mutant, MutationResult, make_mutants, run_mutation
from app.sandbox import RunResult

ADD = "def add(a, b):\n    return a + b\n"

RICH = '''\
def f(xs, limit):
    """Doc string stays."""
    total = 0
    for x in xs:
        if x < limit and x > 0:
            total += x * 2
        elif x == limit:
            return True
    flag = False
    return total - 1 if flag != True else total
'''


def _ast_dump(src: str) -> str:
    return ast.dump(ast.parse(src))


def test_mutants_for_addition():
    mutants = make_mutants(ADD)
    sources = [m.source for m in mutants]
    assert any("return a - b" in s for s in sources)
    assert any("return None" in s for s in sources)
    first = mutants[0]
    assert isinstance(first, Mutant)
    assert first.line == 2
    assert "+" in first.description and "-" in first.description
    assert "return a - b" in first.source


def test_limit_respected():
    assert len(make_mutants(RICH, limit=3)) == 3
    assert len(make_mutants(RICH, limit=10)) == 10
    assert make_mutants(RICH, limit=0) == []


def test_deterministic():
    assert make_mutants(RICH) == make_mutants(RICH)
    assert [m.id for m in make_mutants(RICH)] == list(range(1, 11))


def test_no_identical_mutants():
    src = "def g(x):\n    if x:\n        return None\n    return\n"
    assert make_mutants(src) == []
    for m in make_mutants(RICH, limit=100):
        assert _ast_dump(m.source) != _ast_dump(RICH)


def test_operator_order():
    # Operator families in spec order; within a family, `ast.walk` (breadth-first) order.
    mutants = make_mutants(RICH, limit=100)
    assert [(m.line, m.description) for m in mutants] == [
        (6, "`total += x * 2` → `total -= x * 2`"),
        (10, "`total - 1` → `total + 1`"),
        (6, "`x * 2` → `x / 2`"),
        (5, "`x < limit` → `x <= limit`"),
        (5, "`x > 0` → `x >= 0`"),
        (10, "`flag != True` → `flag == True`"),
        (7, "`x == limit` → `x != limit`"),
        (3, "`0` → `1`"),
        (10, "`1` → `2`"),
        (5, "`0` → `1`"),
        (6, "`2` → `3`"),
        (9, "`False` → `True`"),
        (10, "`True` → `False`"),
        (8, "`True` → `False`"),
        (10, "`return total - 1 if flag != True else total` → `return None`"),
        (8, "`return True` → `return None`"),
    ]
    assert [m.id for m in mutants] == list(range(1, 17))


def test_all_operators():
    mutants = make_mutants(RICH, limit=100)
    sources = {m.source for m in mutants}
    expected_fragments = [
        "total -= x * 2",
        "total += x / 2",
        "x <= limit",
        "x >= 0",
        "x != limit",
        "total = 1",
        "x * 3",
        "return False",
        "flag = True",
        "flag = False\n    return None",
        "flag == True",
    ]
    for fragment in expected_fragments:
        assert any(fragment in s for s in sources), fragment
    # docstrings untouched, bools never treated as ints
    assert all('"""Doc string stays."""' in s or "'Doc string stays.'" in s for s in sources)
    assert not any("flag = 2" in s or "flag = 1" in s for s in sources)


def _fake_runner(stdout: str, seen: dict):
    async def runner(job_dir: Path, argv: list[str], timeout_s: float) -> RunResult:
        seen["argv"] = argv
        seen["timeout_s"] = timeout_s
        seen["files"] = sorted(
            str(p.relative_to(job_dir)) for p in job_dir.rglob("*") if p.is_file()
        )
        seen["tests"] = (job_dir / "test_target.py").read_bytes()
        seen["mutant_1"] = (job_dir / "mutants" / "1" / "target.py").read_text()
        seen["modes"] = {
            str(p.relative_to(job_dir)): p.stat().st_mode & 0o777
            for p in [job_dir, *job_dir.rglob("*")]
        }
        return RunResult(0, stdout, "", 1.0, False, "proveit-fake")
    return runner


def test_run_mutation_layout_and_parsing():
    tests = "from target import *\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    mutants = make_mutants(ADD)
    assert len(mutants) == 2
    stdout = "\n".join([
        "some noise",
        json.dumps({"id": 1, "killed": True}),
        '{"id": 2, "killed": tru',  # malformed
        json.dumps({"id": 99, "killed": True}),  # unknown id
        json.dumps({"id": True, "killed": True}),  # bool id
        json.dumps(["not", "a", "dict"]),
    ])
    seen: dict = {}
    result = asyncio.run(run_mutation(ADD, tests, _fake_runner(stdout, seen)))

    assert isinstance(result, MutationResult)
    assert result.total == 2
    assert result.killed == 1
    assert result.survivors == [mutants[1]]
    assert seen["argv"] == ["python", "/opt/proveit/run_mutants.py", "/work"]
    assert seen["timeout_s"] == 30
    assert seen["files"] == ["mutants/1/target.py", "mutants/2/target.py", "test_target.py"]
    assert seen["tests"] == tests.encode()
    assert seen["mutant_1"] == mutants[0].source
    for path, mode in seen["modes"].items():
        assert mode & 0o004, path  # world-readable for container uid 65534


def test_run_mutation_killed_false_and_unreported_survive():
    stdout = json.dumps({"id": 1, "killed": False}) + "\n"
    result = asyncio.run(run_mutation(ADD, "x = 1\n", _fake_runner(stdout, {})))
    assert result.total == 2
    assert result.killed == 0
    assert [m.id for m in result.survivors] == [1, 2]


def test_run_mutation_without_mutants_skips_sandbox():
    async def runner(*args):
        raise AssertionError("sandbox must not run")

    result = asyncio.run(run_mutation("X = 'a'\n", "", runner))
    assert result == MutationResult(total=0, killed=0, survivors=[])
