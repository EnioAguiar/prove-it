"""Turn sandbox output into containment evidence events."""

import re
from dataclasses import dataclass
from enum import StrEnum

from app.sandbox import RunResult

MAX_EVIDENCE = 200


class EventKind(StrEnum):
    WRITE_BLOCKED = "write_blocked"
    NETWORK_BLOCKED = "network_blocked"
    PROCESS_LIMIT = "process_limit"
    MEMORY_LIMIT = "memory_limit"
    TIMEOUT = "timeout"
    SECRET_ABSENT = "secret_absent"


@dataclass
class ContainmentEvent:
    kind: EventKind
    evidence: str


# Ordered line checks over stdout+stderr; order matches EventKind.
_LINE_PATTERNS: list[tuple[EventKind, re.Pattern[str]]] = [
    (EventKind.WRITE_BLOCKED, re.compile(r"Read-only file system|EROFS")),
    (EventKind.NETWORK_BLOCKED,
     re.compile(r"Network is unreachable|ENETUNREACH|Temporary failure in name resolution")),
    (EventKind.PROCESS_LIMIT,
     re.compile(r"(?=.*Resource temporarily unavailable)(?=.*(?:BlockingIOError|fork))", re.I)),
    (EventKind.MEMORY_LIMIT, re.compile(r"MemoryError")),
]
_SECRET_ABSENT = re.compile(r"^PROVEIT_SECRET_CHECK=None$")


def _first_line(pattern: re.Pattern[str], lines: list[str]) -> str | None:
    for line in lines:
        if pattern.search(line):
            return line.strip()[:MAX_EVIDENCE]
    return None


def detect_events(result: RunResult) -> list[ContainmentEvent]:
    lines = (result.stdout + "\n" + result.stderr).splitlines()
    found: dict[EventKind, str] = {}
    for kind, pattern in _LINE_PATTERNS:
        if (line := _first_line(pattern, lines)) is not None:
            found[kind] = line
    if EventKind.MEMORY_LIMIT not in found and result.exit_code == 137 and not result.timed_out:
        found[EventKind.MEMORY_LIMIT] = "exit code 137 (out of memory)"
    if result.timed_out:
        found[EventKind.TIMEOUT] = "killed after timeout"
    stdout_lines = [l.strip() for l in result.stdout.splitlines()]
    if (line := _first_line(_SECRET_ABSENT, stdout_lines)) is not None:
        found[EventKind.SECRET_ABSENT] = line
    return [ContainmentEvent(k, found[k]) for k in EventKind if k in found]
