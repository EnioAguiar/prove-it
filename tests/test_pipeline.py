import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from app.containment import EventKind
from app.llm import LLMError
from app.mutation import MutationResult
from app.pipeline import StepEvent, run_review
from app.receipt import RUN_ARGV, RUN_TIMEOUT_S, Receipt
from app.sandbox import RunResult

CODE = (
    "def parcelas(total, n):\n"
    "    base = total // n\n"
    "    return [base] * n\n"
)
FIXED = (
    "def parcelas(total, n):\n"
    "    base, rest = divmod(total, n)\n"
    "    return [base + 1] * rest + [base] * (n - rest)\n"
)
STILL_BROKEN = "def parcelas(total, n):\n    return [total // n] * n\n"

TESTS = (
    "from hypothesis import given, strategies as st\n"
    "from target import *\n"
    "\n"
    "@given(st.integers(min_value=0), st.integers(min_value=1, max_value=50))\n"
    "def test_sum_is_total(total, n):\n"
    "    assert sum(parcelas(total, n)) == total\n"
)
OTHER_TESTS = TESTS.replace("test_sum_is_total", "test_parts_add_up")
TRIVIAL_TESTS = "from target import *\n\ndef test_nothing():\n    assert True\n"

FAIL_OUTPUT = (
    "F                                                                        [100%]\n"
    "=================================== FAILURES ===================================\n"
    "______________________________ test_sum_is_total _______________________________\n"
    "E       assert 0 == 1\n"
    "E        +  where 0 = sum([0, 0])\n"
    "E       Falsifying example: test_sum_is_total(\n"
    "E           total=1,\n"
    "E           n=2,\n"
    "E       )\n"
    "=========================== short test summary info ============================\n"
    "FAILED test_target.py::test_sum_is_total - assert 0 == 1\n"
    "1 failed in 0.31s\n"
)


def run(exit_code: int, stdout: str = "", stderr: str = "", timed_out: bool = False) -> RunResult:
    return RunResult(exit_code, stdout, stderr, 0.5, timed_out, "proveit-test")


FAIL = run(1, FAIL_OUTPUT)
PASS = run(0, ".                                                                        [100%]\n1 passed in 0.2s\n")


class FakeLLM:
    def __init__(self, tests=(TESTS,), fixes=(FIXED,), explanation="Plain explanation.",
                 tests_error: Exception | None = None, tests_error_at: int = 0,
                 fix_error: Exception | None = None, explain_error: Exception | None = None):
        self.tests = list(tests)
        self.fixes = list(fixes)
        self.explanation = explanation
        self.tests_error, self.tests_error_at = tests_error, tests_error_at
        self.fix_error, self.explain_error = fix_error, explain_error
        self.generate_calls: list[tuple[str, str | None, str | None]] = []
        self.fix_calls: list[tuple[str, str, str]] = []
        self.explain_calls: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.generate_calls) + len(self.fix_calls) + len(self.explain_calls)

    async def generate_tests(self, code, intent, feedback=None):
        self.generate_calls.append((code, intent, feedback))
        if self.tests_error is not None and len(self.generate_calls) > self.tests_error_at:
            raise self.tests_error
        return self.tests[min(len(self.generate_calls), len(self.tests)) - 1]

    async def fix_code(self, code, tests, failure):
        self.fix_calls.append((code, tests, failure))
        if self.fix_error is not None:
            raise self.fix_error
        return self.fixes[min(len(self.fix_calls), len(self.fixes)) - 1]

    async def explain(self, summary):
        self.explain_calls.append(summary)
        if self.explain_error is not None:
            raise self.explain_error
        return self.explanation


class FakeRunner:
    """Serves results in order (the last one repeats) and snapshots each job dir."""

    def __init__(self, *results: RunResult | BaseException):
        self.results = list(results)
        self.calls: list[dict] = []

    async def __call__(self, job_dir: Path, argv: list[str], timeout_s: float) -> RunResult:
        self.calls.append({
            "job_dir": job_dir,
            "argv": argv,
            "timeout_s": timeout_s,
            "files": {p.name: p.read_text() for p in job_dir.iterdir()},
            "modes": {p.name: p.stat().st_mode & 0o777 for p in [job_dir, *job_dir.iterdir()]},
        })
        result = self.results[min(len(self.calls), len(self.results)) - 1]
        if isinstance(result, BaseException):
            raise result
        return result


class Recorder:
    def __init__(self):
        self.steps: list[StepEvent] = []
        self.mutate_calls: list[tuple[str, str]] = []
        self.receipt_calls: list[tuple[str, str, RunResult]] = []

    async def emit(self, event: StepEvent) -> None:
        self.steps.append(event)

    async def mutate(self, code: str, tests: str) -> MutationResult:
        self.mutate_calls.append((code, tests))
        return MutationResult(total=4, killed=3, survivors=[])

    def receipt(self, code: str, tests: str, original_run: RunResult) -> Receipt:
        self.receipt_calls.append((code, tests, original_run))
        return Receipt("FAIL", "c", "t", "img", "", "runc", {"network": "none"}, original_run.exit_code,
                       original_run.timed_out, original_run.duration_s, "docker run ...",
                       "2026-11-03T00:00:00Z", "k", "sig")


def review(llm, runner, rec=None, code=CODE, intent="split into installments", job_root=None):
    rec = rec or Recorder()
    report = asyncio.run(run_review(code, intent, llm, runner, rec.emit, rec.mutate, rec.receipt,
                                    job_root=job_root))
    return report, rec


def test_verdict_comes_from_exit_code():
    llm = FakeLLM(explanation="All tests passed. The code is correct.")
    report, _ = review(llm, FakeRunner(FAIL))
    assert report.verdict == "FAIL"
    assert report.original_run.exit_code == 1
    assert report.explanation == "All tests passed. The code is correct."
    assert "FAIL" in llm.explain_calls[0]


def test_pass_runs_mutation_on_original_and_no_fix():
    llm = FakeLLM()
    runner = FakeRunner(PASS)
    report, rec = review(llm, runner)
    assert report.verdict == "PASS"
    assert report.error is None
    assert report.tests == TESTS
    assert llm.fix_calls == []
    assert report.fix is None and report.fix_run is None
    assert rec.mutate_calls == [(CODE, TESTS)]
    assert report.mutation == MutationResult(4, 3, [])
    assert rec.receipt_calls == [(CODE, TESTS, PASS)]
    assert report.receipt.exit_code == 0
    assert runner.calls[0]["argv"] == RUN_ARGV
    assert runner.calls[0]["timeout_s"] == RUN_TIMEOUT_S
    assert runner.calls[0]["files"] == {"target.py": CODE, "test_target.py": TESTS}


def test_job_files_are_world_readable(tmp_path):
    runner = FakeRunner(PASS)
    review(FakeLLM(), runner, job_root=tmp_path)
    assert runner.calls[0]["modes"] == {
        runner.calls[0]["job_dir"].name: 0o755, "target.py": 0o644, "test_target.py": 0o644,
    }


def test_tests_frozen_during_fix():
    first = run(1, FAIL_OUTPUT + "first attempt\n")
    llm = FakeLLM(tests=(TESTS, OTHER_TESTS), fixes=(STILL_BROKEN, FIXED))
    runner = FakeRunner(FAIL, first, run(1, "second attempt\n"))
    report, _ = review(llm, runner)
    assert len(runner.calls) == 3
    assert {c["files"]["test_target.py"] for c in runner.calls} == {TESTS}
    assert [c["files"]["target.py"] for c in runner.calls] == [CODE, STILL_BROKEN, FIXED]
    assert len(llm.generate_calls) == 1
    # attempt 2 builds on attempt 1 and its failure
    assert llm.fix_calls[0][:2] == (CODE, TESTS)
    assert "test_sum_is_total" in llm.fix_calls[0][2]
    assert llm.fix_calls[1][:2] == (STILL_BROKEN, TESTS)
    assert "first attempt" in llm.fix_calls[1][2]
    assert report.verdict == "FAIL"
    assert report.fix == FIXED
    assert report.fix_run.exit_code == 1


@pytest.mark.parametrize("exit_code", [2, 4, 5])
def test_rewrites_only_on_collection_errors(exit_code):
    broken = run(exit_code, "ERROR collecting test_target.py\nNameError: name 'parcela' is not defined\n")
    llm = FakeLLM(tests=(TESTS, OTHER_TESTS))
    runner = FakeRunner(broken, FAIL)
    report, _ = review(llm, runner)
    assert len(llm.generate_calls) == 2
    assert llm.generate_calls[0][2] is None
    assert "NameError: name 'parcela' is not defined" in llm.generate_calls[1][2]
    assert report.tests == OTHER_TESTS
    assert report.original_run == FAIL
    assert report.verdict == "FAIL"


@pytest.mark.parametrize("result", [FAIL, run(3, "INTERNALERROR> boom\n")])
def test_no_rewrite_on_other_exit_codes(result):
    llm = FakeLLM()
    report, _ = review(llm, FakeRunner(result, FAIL))
    assert len(llm.generate_calls) == 1
    assert report.original_run == result
    assert report.verdict == "FAIL"


def test_rewrites_are_capped():
    llm = FakeLLM()
    runner = FakeRunner(run(2, "ERROR collecting test_target.py\n"))
    report, rec = review(llm, runner)
    assert len(llm.generate_calls) == 3
    assert len(runner.calls) == 3
    assert report.verdict == "ERROR"
    assert "pytest exit 2" in report.error
    assert llm.fix_calls == [] and llm.explain_calls == []
    assert rec.mutate_calls == []
    assert report.receipt is None


def test_fix_shown_separately():
    llm = FakeLLM()
    runner = FakeRunner(FAIL, PASS)
    report, rec = review(llm, runner)
    assert report.verdict == "FAIL"
    assert report.original_run.exit_code == 1
    assert report.fix == FIXED
    assert report.fix_run.exit_code == 0
    assert len(llm.fix_calls) == 1
    assert rec.mutate_calls == [(FIXED, TESTS)]
    assert rec.receipt_calls == [(CODE, TESTS, FAIL)]


def test_mutation_skipped_when_nothing_passed():
    llm = FakeLLM()
    report, rec = review(llm, FakeRunner(FAIL, FAIL, FAIL))
    assert len(llm.fix_calls) == 2
    assert report.mutation is None
    assert rec.mutate_calls == []


def test_no_fix_when_contained():
    contained = run(1, FAIL_OUTPUT, "OSError: [Errno 30] Read-only file system: '/work/x'\n")
    llm = FakeLLM()
    report, rec = review(llm, FakeRunner(contained, PASS))
    assert report.verdict == "FAIL"
    assert report.fix is None and report.fix_run is None
    assert llm.fix_calls == []
    assert [e.kind for e in report.events] == [EventKind.WRITE_BLOCKED]
    assert report.mutation is None and rec.mutate_calls == []


def test_timeout_is_fail_without_fix():
    llm = FakeLLM()
    report, _ = review(llm, FakeRunner(run(137, "", "", timed_out=True), PASS))
    assert report.verdict == "FAIL"
    assert EventKind.TIMEOUT in [e.kind for e in report.events]
    assert llm.fix_calls == []
    assert len(llm.generate_calls) == 1


@pytest.mark.parametrize("code", ["def (", "", "   \n\t\n", "x = 1\x00"])
def test_syntax_error_input(code):
    llm = FakeLLM()
    runner = FakeRunner(PASS)
    report, rec = review(llm, runner, code=code)
    assert report.verdict == "ERROR"
    assert report.error.startswith("code does not parse")
    assert llm.calls == 0
    assert runner.calls == []
    assert rec.receipt_calls == [] and report.receipt is None
    assert [(s.step, s.status) for s in rec.steps] == [("parse", "start"), ("parse", "fail"), ("done", "fail")]


def test_gate_failure_exhausts():
    llm = FakeLLM(tests=(TRIVIAL_TESTS,))
    runner = FakeRunner(PASS)
    report, _ = review(llm, runner)
    assert report.verdict == "ERROR"
    assert len(llm.generate_calls) == 3
    assert runner.calls == []
    assert llm.generate_calls[0][2] is None
    assert "assert True" in llm.generate_calls[1][2]
    assert "quality gate" in report.error


def test_gate_failure_then_valid_tests():
    llm = FakeLLM(tests=(TRIVIAL_TESTS, TESTS))
    runner = FakeRunner(PASS)
    report, _ = review(llm, runner)
    assert report.verdict == "PASS"
    assert len(llm.generate_calls) == 2
    assert runner.calls[0]["files"]["test_target.py"] == TESTS


def test_generate_tests_llm_error_propagates(tmp_path):
    llm = FakeLLM(tests_error=LLMError("model unavailable"))
    with pytest.raises(LLMError, match="model unavailable"):
        review(llm, FakeRunner(PASS), job_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_job_dirs_cleaned(tmp_path):
    runner = FakeRunner(FAIL, FAIL, PASS)
    review(FakeLLM(fixes=(STILL_BROKEN, FIXED)), runner, job_root=tmp_path)
    dirs = [c["job_dir"] for c in runner.calls]
    assert len(set(dirs)) == 3
    assert all(d.parent == tmp_path for d in dirs)
    assert list(tmp_path.iterdir()) == []


def test_job_dirs_cleaned_when_llm_raises(tmp_path):
    llm = FakeLLM(tests_error=LLMError("model returned invalid output"), tests_error_at=1)
    runner = FakeRunner(run(2, "ERROR collecting\n"))
    with pytest.raises(LLMError):
        review(llm, runner, job_root=tmp_path)
    assert len(runner.calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_job_dirs_cleaned_on_cancellation(tmp_path):
    runner = FakeRunner(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        review(FakeLLM(), runner, job_root=tmp_path)
    assert len(runner.calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_fix_llm_error_keeps_verdict():
    llm = FakeLLM(fix_error=LLMError("model unavailable"))
    report, rec = review(llm, FakeRunner(FAIL, PASS))
    assert report.verdict == "FAIL"
    assert report.fix is None and report.fix_run is None
    assert len(llm.fix_calls) == 1
    assert report.explanation == "Plain explanation."
    assert report.receipt is not None
    assert ("fix", "fail") in [(s.step, s.status) for s in rec.steps]
    assert "No fix could be generated." in llm.explain_calls[0]


def test_second_fix_llm_error_keeps_first_attempt():
    class OneFix(FakeLLM):
        async def fix_code(self, code, tests, failure):
            if self.fix_calls:
                self.fix_calls.append((code, tests, failure))
                raise LLMError("model unavailable")
            return await super().fix_code(code, tests, failure)

    second = run(1, "still failing\n")
    llm = OneFix(fixes=(STILL_BROKEN,))
    report, _ = review(llm, FakeRunner(FAIL, second))
    assert report.fix == STILL_BROKEN
    assert report.fix_run == second
    summary = llm.explain_calls[0]
    assert "The suggested fix did not pass the tests either." in summary
    assert "No fix could be generated." not in summary


class FailingMutation(Recorder):
    async def mutate(self, code, tests):
        self.mutate_calls.append((code, tests))
        raise RuntimeError("docker: Cannot connect to the Docker daemon")


class FailingReceipt(Recorder):
    def receipt(self, code, tests, original_run):
        self.receipt_calls.append((code, tests, original_run))
        raise RuntimeError("docker: Cannot connect to the Docker daemon")


@pytest.mark.parametrize("runs, verdict", [((PASS,), "PASS"), ((FAIL, PASS), "FAIL")])
def test_mutation_failure_keeps_verdict(runs, verdict):
    rec = FailingMutation()
    report, _ = review(FakeLLM(), FakeRunner(*runs), rec)
    assert report.verdict == verdict
    assert len(rec.mutate_calls) == 1
    assert report.mutation is None
    assert report.receipt is not None
    steps = [(s.step, s.status) for s in rec.steps]
    assert ("mutation", "fail") in steps and ("mutation", "ok") not in steps
    assert rec.steps[-1] == StepEvent("done", "ok", verdict)


def test_receipt_failure_keeps_verdict():
    rec = FailingReceipt()
    report, _ = review(FakeLLM(), FakeRunner(FAIL, PASS), rec)
    assert report.verdict == "FAIL"
    assert len(rec.receipt_calls) == 1
    assert report.receipt is None
    assert report.mutation == MutationResult(4, 3, [])
    assert rec.steps[-1] == StepEvent("done", "ok", "FAIL")


def test_explain_llm_error_falls_back_to_summary():
    llm = FakeLLM(explain_error=LLMError("model unavailable"))
    report, rec = review(llm, FakeRunner(FAIL, PASS))
    assert report.verdict == "FAIL"
    assert report.explanation == llm.explain_calls[0]
    assert "FAIL" in report.explanation
    assert ("explain", "fail") in [(s.step, s.status) for s in rec.steps]
    assert rec.steps[-1].step == "done"


# Hypothesis 6.170 (the runner image) prints "Failing test case:"; older versions "Falsifying example:".
@pytest.mark.parametrize("header", ["Failing test case:", "Falsifying example:"])
def test_summary_is_built_from_facts(header):
    llm = FakeLLM()
    failed = run(1, FAIL_OUTPUT.replace("Falsifying example:", header))
    review(llm, FakeRunner(failed, PASS))
    summary = llm.explain_calls[0]
    assert "FAIL" in summary
    assert "test_sum_is_total" in summary
    assert f"{header} test_sum_is_total(" in summary
    assert "    total=1," in summary
    assert "3 of 4" in summary
    assert "E  " not in summary


def test_emits_steps_in_order():
    rec = Recorder()
    review(FakeLLM(), FakeRunner(FAIL, PASS), rec)
    assert [(s.step, s.status) for s in rec.steps] == [
        ("parse", "start"), ("parse", "ok"),
        ("tests", "start"), ("tests", "ok"),
        ("gate", "start"), ("gate", "ok"),
        ("run", "start"), ("run", "fail"),
        ("fix", "start"), ("fix", "ok"),
        ("rerun", "start"), ("rerun", "ok"),
        ("mutation", "start"), ("mutation", "ok"),
        ("explain", "start"), ("explain", "ok"),
        ("done", "ok"),
    ]


def test_report_is_json_serializable():
    contained = run(1, FAIL_OUTPUT, "OSError: [Errno 30] Read-only file system\n")
    report, _ = review(FakeLLM(), FakeRunner(contained))
    data = json.loads(json.dumps(dataclasses.asdict(report)))
    assert data["verdict"] == "FAIL"
    assert data["events"][0]["kind"] == "write_blocked"
