"""What a review produces. Plain dataclasses so the API can `dataclasses.asdict` them to JSON."""

from dataclasses import dataclass
from typing import Literal

from app.containment import ContainmentEvent
from app.mutation import MutationResult
from app.receipt import Receipt
from app.sandbox import RunResult

Step = Literal["parse", "tests", "gate", "run", "fix", "rerun", "mutation", "explain", "done"]
Verdict = Literal["PASS", "FAIL", "ERROR"]
StepStatus = Literal["start", "ok", "fail"]


@dataclass
class StepEvent:
    step: Step
    status: StepStatus
    detail: str


@dataclass
class Report:
    verdict: Verdict
    error: str | None
    code: str
    tests: str
    original_run: RunResult | None
    events: list[ContainmentEvent]
    fix: str | None
    fix_run: RunResult | None
    mutation: MutationResult | None
    explanation: str
    receipt: Receipt | None
