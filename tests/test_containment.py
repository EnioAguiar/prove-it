import pytest

from app.containment import ContainmentEvent, EventKind, detect_events
from app.sandbox import RunResult


def _rr(stdout="", stderr="", exit_code=1, timed_out=False):
    return RunResult(exit_code=exit_code, stdout=stdout, stderr=stderr,
                     duration_s=0.1, timed_out=timed_out, container_name="c")


@pytest.mark.parametrize("result,kind,evidence", [
    (_rr(stderr="Traceback\nOSError: [Errno 30] Read-only file system: '/work/x'\n"),
     EventKind.WRITE_BLOCKED, "OSError: [Errno 30] Read-only file system: '/work/x'"),
    (_rr(stderr="OSError: [Errno 101] Network is unreachable\n"),
     EventKind.NETWORK_BLOCKED, "OSError: [Errno 101] Network is unreachable"),
    (_rr(stderr="socket.gaierror: [Errno -3] Temporary failure in name resolution\n"),
     EventKind.NETWORK_BLOCKED, "socket.gaierror: [Errno -3] Temporary failure in name resolution"),
    (_rr(stderr="BlockingIOError: [Errno 11] Resource temporarily unavailable\n"),
     EventKind.PROCESS_LIMIT, "BlockingIOError: [Errno 11] Resource temporarily unavailable"),
    (_rr(stderr="MemoryError\n"), EventKind.MEMORY_LIMIT, "MemoryError"),
    (_rr(exit_code=137), EventKind.MEMORY_LIMIT, "exit code 137 (out of memory)"),
    (_rr(exit_code=137, timed_out=True), EventKind.TIMEOUT, "killed after timeout"),
    (_rr(stdout="PROVEIT_SECRET_CHECK=None\n", exit_code=0),
     EventKind.SECRET_ABSENT, "PROVEIT_SECRET_CHECK=None"),
])
def test_detects_each_kind(result, kind, evidence):
    assert detect_events(result) == [ContainmentEvent(kind, evidence)]


def test_clean_run_has_no_events():
    assert detect_events(_rr(stdout="ok\n", exit_code=0)) == []


def test_secret_leak_is_not_absent():
    assert detect_events(_rr(stdout="PROVEIT_SECRET_CHECK=abc\n", exit_code=0)) == []


def test_order_dedupe_and_truncation():
    long = "OSError: Read-only file system " + "x" * 300
    r = _rr(stderr=f"OSError: [Errno 101] Network is unreachable\n{long}\n{long}\n")
    events = detect_events(r)
    assert [e.kind for e in events] == [EventKind.WRITE_BLOCKED, EventKind.NETWORK_BLOCKED]
    assert len(events[0].evidence) == 200
