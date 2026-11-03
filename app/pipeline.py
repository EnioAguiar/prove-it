"""The review pipeline: parse → tests → gate → sandbox run → verdict → fix → mutation → explain.

Code decides the verdict from the pytest exit code of the user's original code; the LLM only
writes artifacts (tests, a suggested fix, an explanation)."""

import ast
import re
import shutil
import tempfile
import textwrap
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.containment import ContainmentEvent, detect_events
from app.llm import LLM, LLMError
from app.mutation import MutationResult
from app.receipt import RUN_ARGV, RUN_TIMEOUT_S, Receipt
from app.report import Report, Step, StepEvent, StepStatus, Verdict
from app.sandbox import Runner, RunResult
from app.test_gate import TARGET_MODULE, check_tests

__all__ = ["Emit", "MutateFn", "ReceiptFn", "Report", "Runner", "StepEvent", "run_review"]

Emit = Callable[[StepEvent], Awaitable[None]]
# (code, tests) -> MutationResult; callers bind the runner.
MutateFn = Callable[[str, str], Awaitable[MutationResult]]
# (code, tests, original_run) -> Receipt; callers bind settings and the image digest.
ReceiptFn = Callable[[str, str, RunResult], Receipt]

GATE_ATTEMPTS = 3  # first generation + 2 regenerations
MAX_TEST_REWRITES = 2
REWRITE_EXIT_CODES = frozenset({2, 4, 5})  # collection error, usage error, no tests collected
MAX_FIX_ATTEMPTS = 2
FEEDBACK_CHARS = 2000
FAILURE_CHARS = 4000

TARGET_FILE = f"{TARGET_MODULE}.py"
TESTS_FILE = Path(RUN_ARGV[-1]).name

_FAILED_TEST = re.compile(r"^(?:FAILED|ERROR) \S*?::(\S+)")
# Hypothesis 6.170 prints "Failing test case:"; older versions "Falsifying example:".
_COUNTEREXAMPLE = re.compile(r"Failing test case:|Falsifying (?:explicit )?example:")
_PYTEST_E_PREFIX = re.compile(r"^E(?=\s|$)")
_MAX_FAILED_NAMES = 10
_MAX_EXAMPLES = 3
_MAX_EXAMPLE_LINES = 20
_MAX_EXAMPLE_CHARS = 600


async def run_review(
    code: str,
    intent: str | None,
    llm: LLM,
    runner: Runner,
    emit: Emit,
    mutate: MutateFn,
    receipt: ReceiptFn,
    *,
    job_root: Path | None = None,
) -> Report:
    """Review `code`. LLMError from test generation propagates; job dirs never outlive the call."""
    jobs = _JobDirs(job_root)
    try:
        return await _Review(code, intent, llm, runner, emit, jobs).run(mutate, receipt)
    finally:
        jobs.remove_all()


class _JobDirs:
    """World-readable job dirs (the sandbox runs as uid 65534), removed together at the end."""

    def __init__(self, root: Path | None) -> None:
        self._root = root
        self._dirs: list[Path] = []

    def create(self, code: str, tests: str) -> Path:
        job = Path(tempfile.mkdtemp(prefix="proveit-job-", dir=self._root))
        self._dirs.append(job)
        job.chmod(0o755)
        for name, text in ((TARGET_FILE, code), (TESTS_FILE, tests)):
            path = job / name
            path.write_bytes(text.encode())
            path.chmod(0o644)
        return job

    def remove_all(self) -> None:
        for job in self._dirs:
            shutil.rmtree(job, ignore_errors=True)


class _Review:
    def __init__(self, code: str, intent: str | None, llm: LLM, runner: Runner, emit: Emit,
                 jobs: _JobDirs) -> None:
        self.code = code
        self.intent = intent
        self.llm = llm
        self.runner = runner
        self.emit = emit
        self.jobs = jobs

    async def run(self, mutate: MutateFn, receipt: ReceiptFn) -> Report:
        await self._step("parse", "start")
        if (error := _parse_error(self.code)) is not None:
            await self._step("parse", "fail", error)
            return await self._error(error, tests="")
        await self._step("parse", "ok")

        tests, reasons = await self._gated_tests(feedback=None)
        rewrites = 0
        while True:
            if reasons:
                return await self._error(_gate_error(reasons), tests)
            run = await self._run("run", self.code, tests)
            if run.timed_out or run.exit_code not in REWRITE_EXIT_CODES:
                break
            if rewrites == MAX_TEST_REWRITES:
                error = (f"generated tests could not run (pytest exit {run.exit_code}) "
                         f"after {MAX_TEST_REWRITES} rewrites")
                return await self._error(error, tests, run, detect_events(run))
            rewrites += 1
            tests, reasons = await self._gated_tests(feedback=_rewrite_feedback(run))

        # From here on the tests are frozen.
        events = detect_events(run)
        verdict: Verdict = "PASS" if _passed(run) else "FAIL"
        fix, fix_run, fix_unavailable = None, None, False
        if verdict == "FAIL" and not events:
            fix, fix_run, fix_unavailable = await self._fix(tests, run)

        passing_code = self.code if verdict == "PASS" else fix if fix_run and _passed(fix_run) else None
        mutation = None
        if passing_code is not None:
            mutation = await self._mutation(mutate, passing_code, tests)

        summary = _summary(verdict, run, events, fix_run, fix_unavailable, mutation,
                           mutated_fix=verdict == "FAIL")
        explanation = await self._explain(summary)
        try:
            run_receipt = receipt(self.code, tests, run)
        except Exception:  # the verdict is decided; a missing receipt must not lose it
            run_receipt = None
        report = Report(
            verdict=verdict, error=None, code=self.code, tests=tests, original_run=run,
            events=events, fix=fix, fix_run=fix_run, mutation=mutation,
            explanation=explanation, receipt=run_receipt,
        )
        await self._step("done", "ok", verdict)
        return report

    async def _step(self, step: Step, status: StepStatus, detail: str = "") -> None:
        await self.emit(StepEvent(step, status, detail))

    async def _error(self, error: str, tests: str, run: RunResult | None = None,
                     events: list[ContainmentEvent] | None = None) -> Report:
        await self._step("done", "fail", error)
        return Report(
            verdict="ERROR", error=error, code=self.code, tests=tests, original_run=run,
            events=events or [], fix=None, fix_run=None, mutation=None,
            explanation=error, receipt=None,
        )

    async def _gated_tests(self, feedback: str | None) -> tuple[str, list[str]]:
        """Generate tests until they pass the static gate: (tests, []) or (last tests, reasons)."""
        tests, reasons = "", []
        for generation in range(1, GATE_ATTEMPTS + 1):
            await self._step("tests", "start", f"generation {generation}")
            try:
                tests = await self.llm.generate_tests(self.code, self.intent, feedback)
            except LLMError as exc:
                await self._step("tests", "fail", str(exc))
                raise
            await self._step("tests", "ok")
            await self._step("gate", "start")
            gate = check_tests(tests, self.code)
            if gate.ok:
                await self._step("gate", "ok")
                return tests, []
            reasons = gate.reasons
            feedback = "; ".join(reasons)
            await self._step("gate", "fail", feedback)
        return tests, reasons

    async def _run(self, step: Step, code: str, tests: str) -> RunResult:
        await self._step(step, "start")
        result = await self.runner(self.jobs.create(code, tests), RUN_ARGV, RUN_TIMEOUT_S)
        detail = "timed out" if result.timed_out else f"exit {result.exit_code}"
        await self._step(step, "ok" if _passed(result) else "fail", detail)
        return result

    async def _fix(self, tests: str, original_run: RunResult) -> tuple[str | None, RunResult | None, bool]:
        """Each attempt builds on the previous one; stop at the first passing run.
        Returns (last fix, its run, whether the model failed to produce an attempt)."""
        code, failed = self.code, original_run
        fix, fix_run = None, None
        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            await self._step("fix", "start", f"attempt {attempt}")
            try:
                candidate = await self.llm.fix_code(code, tests, _tail(_output(failed), FAILURE_CHARS))
            except LLMError as exc:
                await self._step("fix", "fail", str(exc))
                return fix, fix_run, True
            await self._step("fix", "ok", f"attempt {attempt}")
            fix, fix_run = candidate, await self._run("rerun", candidate, tests)
            if _passed(fix_run):
                break
            code, failed = fix, fix_run
        return fix, fix_run, False

    async def _mutation(self, mutate: MutateFn, code: str, tests: str) -> MutationResult | None:
        await self._step("mutation", "start")
        try:
            mutation = await mutate(code, tests)
        except Exception as exc:  # e.g. docker failure; the verdict is already decided
            await self._step("mutation", "fail", f"mutation testing could not run: {exc}"[:200])
            return None
        await self._step("mutation", "ok", f"{mutation.killed} of {mutation.total} mutants killed")
        return mutation

    async def _explain(self, summary: str) -> str:
        await self._step("explain", "start")
        try:
            explanation = await self.llm.explain(summary)
        except LLMError as exc:
            await self._step("explain", "fail", str(exc))
            return summary
        await self._step("explain", "ok")
        return explanation if explanation.strip() else summary


def _parse_error(code: str) -> str | None:
    if not code.strip():
        return "code does not parse: the input is empty"
    try:
        code.encode()
        ast.parse(code)
    except SyntaxError as exc:
        return f"code does not parse: {exc.msg} (line {exc.lineno})"
    except (ValueError, UnicodeError) as exc:
        return f"code does not parse: {exc}"
    return None


def _passed(run: RunResult) -> bool:
    return run.exit_code == 0 and not run.timed_out


def _output(run: RunResult) -> str:
    return f"{run.stdout}\n{run.stderr}".strip()


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "[...]\n" + text[-limit:]


def _gate_error(reasons: list[str]) -> str:
    return (f"generated tests failed the quality gate {GATE_ATTEMPTS} times: "
            + "; ".join(reasons))


def _rewrite_feedback(run: RunResult) -> str:
    return (f"pytest exited with code {run.exit_code} (the tests could not be collected or "
            f"ran nothing). Output:\n{_tail(_output(run), FEEDBACK_CHARS)}\n")


def _failing_tests(output: str) -> list[str]:
    names = (m.group(1) for line in output.splitlines() if (m := _FAILED_TEST.match(line)))
    return list(dict.fromkeys(names))[:_MAX_FAILED_NAMES]


def _counterexamples(output: str) -> list[str]:
    """Hypothesis counterexample blocks, without pytest's `E` prefix."""
    lines = [_PYTEST_E_PREFIX.sub(" ", line) for line in output.splitlines()]
    examples: list[str] = []
    i = 0
    while i < len(lines):
        if not _COUNTEREXAMPLE.search(lines[i]):
            i += 1
            continue
        block = [lines[i]]
        if lines[i].rstrip().endswith("("):
            for line in lines[i + 1:i + 1 + _MAX_EXAMPLE_LINES]:
                block.append(line)
                if line.strip() == ")":
                    break
        i += len(block)
        example = textwrap.dedent("\n".join(block)).strip()[:_MAX_EXAMPLE_CHARS]
        if example not in examples:
            examples.append(example)
    return examples[:_MAX_EXAMPLES]


def _summary(verdict: Verdict, run: RunResult, events: list[ContainmentEvent],
             fix_run: RunResult | None, fix_unavailable: bool, mutation: MutationResult | None,
             mutated_fix: bool) -> str:
    """Facts only, built by code. The verdict line is final; the explanation cannot change it."""
    if verdict == "PASS":
        lines = ["Verdict: PASS (final). All generated tests passed on the user's original code "
                 "(pytest exit code 0)."]
    elif run.timed_out:
        lines = [f"Verdict: FAIL (final). The tests on the user's original code were stopped "
                 f"after {RUN_TIMEOUT_S} s."]
    else:
        lines = [f"Verdict: FAIL (final). The generated tests failed on the user's original code "
                 f"(pytest exit code {run.exit_code})."]
    output = _output(run)
    if failing := _failing_tests(output):
        lines.append("Failing tests: " + ", ".join(failing) + ".")
    if examples := _counterexamples(output):
        lines.append("Counterexamples found by Hypothesis:\n" + "\n".join(examples))
    if events:
        lines.append("The sandbox blocked: "
                     + "; ".join(f"{e.kind.value} ({e.evidence})" for e in events) + ".")
        if verdict == "FAIL":
            lines.append("No fix was attempted because the sandbox had to block the code.")
    if fix_run is not None:
        if _passed(fix_run):
            lines.append("A suggested fix, shown separately, passes all the tests. "
                         "The verdict for the original code stays FAIL.")
        else:
            lines.append("The suggested fix did not pass the tests either.")
    elif fix_unavailable:
        lines.append("No fix could be generated.")
    if mutation is not None:
        which = "the suggested fix" if mutated_fix else "the original code"
        lines.append(f"Mutation score: the tests caught {mutation.killed} of {mutation.total} "
                     f"deliberately broken versions of {which}.")
    return "\n".join(lines)
