"""Run the frozen tests against every mutant in one sandbox.

Layout of the job root (read-only): test_target.py and mutants/<id>/target.py.
Prints one JSON line {"id": n, "killed": bool} per mutant, in id order. A pytest
run that fails or exceeds MUTANT_TIMEOUT_S kills the mutant. Stops before
TOTAL_BUDGET_S; mutants never reported are survivors for the caller.

Each mutant runs `pytest -q -x <root>/test_target.py` in a forked child of this
process. A fresh interpreter needs ~3 s just to import pytest and Hypothesis at
0.5 CPU, more than the per-mutant timeout, so those imports (plus the modules the
test file and the mutants import) are done once here, before forking.

Mutant code runs as the same uid as this process. To keep it from writing forged
reports into our stdout through /proc/<pid>/fd/1, this process makes itself
non-dumpable first. After each mutant, every process it left behind is killed and
reaped, so leftovers cannot pile up against --pids-limit or disturb the next mutant.
"""

import ast
import ctypes
import importlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

MUTANT_TIMEOUT_S = 2
TOTAL_BUDGET_S = 28
_POLL_S = 0.005
_PR_SET_DUMPABLE = 4


def _make_undumpable() -> None:
    """Non-dumpable: other processes of our uid (the mutants) lose access to our
    /proc/<pid>/fd, mem and environ. Fail closed: no reports rather than forgeable ones."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"prctl(PR_SET_DUMPABLE, 0): {os.strerror(err)}")


def _mutant_ids(mutants_dir: Path) -> list[int]:
    return sorted(int(p.name) for p in mutants_dir.iterdir() if p.is_dir() and p.name.isdigit())


def _imported_modules(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _preload(paths: list[Path]) -> None:
    """Import what the children will need so each fork starts warm. Best effort."""
    for name in ("hypothesis", "hypothesis.extra.pytestplugin", "proveit_plugin"):
        _try_import(name)
    for name in sorted(set().union(*map(_imported_modules, paths)) - {"target", "test_target"}):
        _try_import(name)


def _try_import(name: str) -> None:
    try:
        importlib.import_module(name)
    except BaseException:  # noqa: BLE001 - preloading must never stop the run
        pass


def _child(tests: Path, mutant_dir: Path) -> None:
    """Never returns: runs pytest with this mutant first on the import path."""
    try:
        os.setpgid(0, 0)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        sys.path.insert(0, str(mutant_dir))
        code = int(pytest.main(["-q", "-x", str(tests)]))
    except BaseException:  # noqa: BLE001
        code = 3
    os._exit(code)


def _killed(tests: Path, mutant_dir: Path) -> bool:
    try:
        pid = os.fork()
    except OSError:
        return True  # cannot run the tests at all
    if pid == 0:
        _child(tests, mutant_dir)
    try:
        os.setpgid(pid, pid)
    except OSError:
        pass  # child already did it, or already exited
    deadline = time.monotonic() + MUTANT_TIMEOUT_S
    while time.monotonic() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status) != 0
        time.sleep(_POLL_S)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return True  # timed out


def _clean_up_leftovers() -> None:
    """Kill and reap whatever the last mutant left behind, e.g. processes that called
    setsid() to leave the process group killed on timeout."""
    flags = os.WNOHANG
    if os.getpid() == 1:
        # Sandbox init: every other process in the container descends from a mutant.
        # Never do this outside the container: kill(-1) would hit all our uid's processes.
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass  # nothing left
        flags = 0  # all of them are dying and are (or become) our children: wait for all
    while True:
        try:
            pid, _ = os.waitpid(-1, flags)
        except ChildProcessError:
            return
        if pid == 0:
            return


def main(root: Path) -> None:
    deadline = time.monotonic() + TOTAL_BUDGET_S
    _make_undumpable()
    tests = root / "test_target.py"
    mutants_dir = root / "mutants"
    ids = _mutant_ids(mutants_dir)
    _preload([tests, *(mutants_dir / str(i) / "target.py" for i in ids)])
    for mutant_id in ids:
        if time.monotonic() + MUTANT_TIMEOUT_S > deadline:
            break
        killed = _killed(tests, mutants_dir / str(mutant_id))
        _clean_up_leftovers()
        print(json.dumps({"id": mutant_id, "killed": killed}), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
