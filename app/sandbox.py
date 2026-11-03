import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

OUTPUT_CAP_BYTES = 65_536
TRUNCATED_SUFFIX = "\n[truncated]"
TIMEOUT_EXIT_CODE = 137

_READ_CHUNK = 65_536
_KILL_ATTEMPTS = 5
_KILL_GRACE_S = 2.0
_DOCKER_CLI_TIMEOUT_S = 10.0

_SANDBOX_FLAGS = [
    "--rm", "--network", "none", "--read-only",
    "--tmpfs", "/tmp:rw,size=64m",
    "--memory", "256m", "--memory-swap", "256m", "--cpus", "0.5",
    "--pids-limit", "64", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges", "--user", "65534:65534",
]


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    container_name: str


def sandbox_command(job_dir: Path, argv: list[str], name: str, settings: Settings) -> list[str]:
    """Exact `docker run` argv. No -e/--env-file: the sandbox gets no host env."""
    cmd = ["docker", "run", "--name", name, *_SANDBOX_FLAGS,
           "-v", f"{job_dir.resolve()}:/work:ro"]
    if settings.sandbox_runtime == "runsc":
        cmd.append("--runtime=runsc")
    return [*cmd, settings.sandbox_image, *argv]


async def run_in_sandbox(job_dir: Path, argv: list[str], timeout_s: float, settings: Settings) -> RunResult:
    name = f"proveit-{uuid.uuid4().hex[:12]}"
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *sandbox_command(job_dir, argv, name, settings),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_task = asyncio.create_task(_read_capped(proc.stdout))
    stderr_task = asyncio.create_task(_read_capped(proc.stderr))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
        except TimeoutError:
            timed_out = True
            await _stop(proc, name)
    finally:
        if proc.returncode is None:  # cancelled while running
            await _stop(proc, name)
    duration_s = time.monotonic() - start
    stdout, stderr = await stdout_task, await stderr_task
    return RunResult(
        exit_code=TIMEOUT_EXIT_CODE if timed_out else proc.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration_s,
        timed_out=timed_out,
        container_name=name,
    )


async def _read_capped(stream: asyncio.StreamReader) -> str:
    """Keep the first OUTPUT_CAP_BYTES; drain the rest so the child never blocks."""
    buf = bytearray()
    truncated = False
    while chunk := await stream.read(_READ_CHUNK):
        room = OUTPUT_CAP_BYTES - len(buf)
        if room > 0:
            buf += chunk[:room]
        if len(chunk) > room:
            truncated = True
    text = buf.decode("utf-8", errors="replace")
    return text + TRUNCATED_SUFFIX if truncated else text


async def _stop(proc: asyncio.subprocess.Process, name: str) -> None:
    """Kill the container (killing the `docker run` client alone leaves it running),
    wait for the client to exit, and make sure the container is gone."""
    for _ in range(_KILL_ATTEMPTS):
        await _docker("kill", name)
        try:
            await asyncio.wait_for(proc.wait(), _KILL_GRACE_S)
            break
        except TimeoutError:
            continue
    else:
        proc.kill()
        await proc.wait()
    await _docker("rm", "-f", name)


async def _docker(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), _DOCKER_CLI_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
