"""Mutation testing: sabotage the user's code with small AST edits and count how many
sabotaged versions the frozen tests catch. All mutants run in one sandbox."""

import ast
import copy
import json
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from app.sandbox import Runner

RUN_ARGV = ["python", "/opt/proveit/run_mutants.py", "/work"]
RUN_TIMEOUT_S = 30


@dataclass
class Mutant:
    id: int
    line: int
    description: str
    source: str


@dataclass
class MutationResult:
    total: int
    killed: int
    survivors: list[Mutant]


# An edit applies one mutation to the node at the same walk position in a copy of the tree.
Edit = Callable[[ast.AST], None]
Operator = Callable[[ast.AST], Iterator[Edit]]


def _set_op(new: type[ast.operator]) -> Edit:
    def apply(node: ast.AST) -> None:
        node.op = new()
    return apply


def _binop(swaps: dict[type[ast.operator], type[ast.operator]]) -> Operator:
    def operator(node: ast.AST) -> Iterator[Edit]:
        if isinstance(node, (ast.BinOp, ast.AugAssign)) and type(node.op) in swaps:
            yield _set_op(swaps[type(node.op)])
    return operator


def _compare(swaps: dict[type[ast.cmpop], type[ast.cmpop]]) -> Operator:
    def operator(node: ast.AST) -> Iterator[Edit]:
        if isinstance(node, ast.Compare):
            for k, op in enumerate(node.ops):
                if type(op) in swaps:
                    yield _set_cmp(k, swaps[type(op)])
    return operator


def _set_cmp(k: int, new: type[ast.cmpop]) -> Edit:
    def apply(node: ast.AST) -> None:
        node.ops[k] = new()
    return apply


def _int_plus_one(node: ast.AST) -> Iterator[Edit]:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        def apply(n: ast.AST) -> None:
            n.value += 1
        yield apply


def _flip_bool(node: ast.AST) -> Iterator[Edit]:
    if isinstance(node, ast.Constant) and type(node.value) is bool:
        def apply(n: ast.AST) -> None:
            n.value = not n.value
        yield apply


def _return_none(node: ast.AST) -> Iterator[Edit]:
    if isinstance(node, ast.Return) and node.value is not None:
        def apply(n: ast.AST) -> None:
            n.value = ast.Constant(None)
        yield apply


OPERATORS: list[Operator] = [
    _binop({ast.Add: ast.Sub, ast.Sub: ast.Add}),
    _binop({ast.Mult: ast.Div}),
    _compare({ast.Lt: ast.LtE, ast.LtE: ast.Lt}),
    _compare({ast.Gt: ast.GtE, ast.GtE: ast.Gt}),
    _compare({ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}),
    _int_plus_one,
    _flip_bool,
    _return_none,
]


def make_mutants(src: str, limit: int = 10) -> list[Mutant]:
    """Deterministic mutants: operators in OPERATORS order, nodes in `ast.walk` order.
    Raises SyntaxError for unparsable source."""
    tree = ast.parse(src)
    original = ast.unparse(tree)
    nodes = list(ast.walk(tree))
    mutants: list[Mutant] = []
    seen = {original}
    for operator in OPERATORS:
        for index, node in enumerate(nodes):
            for edit in operator(node):
                if len(mutants) >= limit:
                    return mutants
                mutated_tree = copy.deepcopy(tree)
                target = list(ast.walk(mutated_tree))[index]
                before = _snippet(target)
                edit(target)
                source = ast.unparse(mutated_tree)
                if source in seen:
                    continue
                seen.add(source)
                mutants.append(Mutant(
                    id=len(mutants) + 1,
                    line=node.lineno,
                    description=f"`{before}` → `{_snippet(target)}`",
                    source=source,
                ))
    return mutants


def _snippet(node: ast.AST, cap: int = 80) -> str:
    text = ast.unparse(node)
    return text if len(text) <= cap else text[:cap - 1] + "…"


async def run_mutation(code: str, tests: str, runner: Runner) -> MutationResult:
    """Run every mutant of `code` against the frozen `tests` in one sandbox run.
    A mutant counts as killed only if the runner reports it killed in a trustworthy run."""
    mutants = make_mutants(code)
    if not mutants:
        return MutationResult(total=0, killed=0, survivors=[])
    with tempfile.TemporaryDirectory(prefix="proveit-mut-") as tmp:
        job_dir = Path(tmp)
        _write_job(job_dir, tests, mutants)
        result = await runner(job_dir, RUN_ARGV, RUN_TIMEOUT_S)
    killed_ids = _killed_ids(result.stdout, [m.id for m in mutants])
    return MutationResult(
        total=len(mutants),
        killed=len(killed_ids),
        survivors=[m for m in mutants if m.id not in killed_ids],
    )


def _write_job(job_dir: Path, tests: str, mutants: list[Mutant]) -> None:
    """Layout: test_target.py (frozen tests) and mutants/<id>/target.py, all world-readable
    because the container runs as uid 65534."""
    job_dir.chmod(0o755)
    test_file = job_dir / "test_target.py"
    test_file.write_bytes(tests.encode())
    test_file.chmod(0o644)
    mutants_dir = job_dir / "mutants"
    mutants_dir.mkdir()
    mutants_dir.chmod(0o755)
    for mutant in mutants:
        mutant_dir = mutants_dir / str(mutant.id)
        mutant_dir.mkdir()
        mutant_dir.chmod(0o755)
        target = mutant_dir / "target.py"
        target.write_bytes(mutant.source.encode())
        target.chmod(0o644)


def _killed_ids(stdout: str, ids_in_order: list[int]) -> set[int]:
    """Killed ids from the runner's report lines, or none if the reports look forged.

    Non-JSON lines and JSON values that are not objects are ignored. Every JSON object
    is a report and must be exactly `{"id": int, "killed": bool}`, with reports for
    `ids_in_order` arriving once each, in that order (the runner may stop early). A
    mutant can write into the runner's stdout, so any duplicate, out-of-order, unknown
    or malformed report makes the whole run untrusted: every mutant survives."""
    killed: set[int] = set()
    expected = iter(ids_in_order)
    for line in stdout.splitlines():
        try:
            report = json.loads(line)
        except ValueError:
            continue
        if not isinstance(report, dict):
            continue
        is_killed = report.get("killed")
        if (report.keys() != {"id", "killed"} or type(is_killed) is not bool
                or report["id"] != next(expected, None) or type(report["id"]) is not int):
            return set()
        if is_killed:
            killed.add(report["id"])
    return killed
