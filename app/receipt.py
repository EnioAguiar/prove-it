import asyncio
import hashlib
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.sandbox import RunResult, sandbox_command

RUN_ARGV = ["python", "-m", "pytest", "-q", "/work/test_target.py"]
RUN_TIMEOUT_S = 10
_PWD = '"$PWD"'
_PLACEHOLDER_JOB = Path("/__proveit_job__")
_DOCKER_CLI_TIMEOUT_S = 10.0

# docker flag -> limits key; values are read from sandbox_command, never restated.
_LIMIT_FLAGS = {
    "--network": "network", "--memory": "memory", "--memory-swap": "memory_swap",
    "--cpus": "cpus", "--pids-limit": "pids", "--tmpfs": "tmpfs", "--user": "user",
}


@dataclass
class Receipt:
    code_sha256: str
    tests_sha256: str
    image: str
    image_digest: str
    runtime: str
    limits: dict[str, str]
    exit_code: int
    duration_s: float
    command: str
    created_at: str


def build_receipt(code: str, tests: str, run: RunResult, settings: Settings, image_digest: str) -> Receipt:
    digest = image_digest if "@" in image_digest else ""
    image = digest or settings.sandbox_image
    argv = sandbox_command(_PLACEHOLDER_JOB, RUN_ARGV, "receipt", settings)
    i = argv.index("--name")
    del argv[i:i + 2]
    argv[argv.index(settings.sandbox_image)] = image

    limits = {key: argv[argv.index(flag) + 1] for flag, key in _LIMIT_FLAGS.items()}
    limits["read_only"] = str("--read-only" in argv).lower()
    limits["timeout"] = f"{RUN_TIMEOUT_S}s"

    mount = f"{_PLACEHOLDER_JOB.resolve()}:/work:ro"
    command = " ".join(
        f"{_PWD}:/work:ro" if tok == mount else shlex.quote(tok) for tok in argv
    )
    return Receipt(
        code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        tests_sha256=hashlib.sha256(tests.encode()).hexdigest(),
        image=image,
        image_digest=digest,
        runtime=settings.sandbox_runtime,
        limits=limits,
        exit_code=run.exit_code,
        duration_s=run.duration_s,
        command=command,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


async def read_image_digest(image: str) -> str:
    """RepoDigests[0] of a pulled/pushed image, or "" (e.g. a local-only build)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), _DOCKER_CLI_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ""
    digest = out.decode(errors="replace").strip()
    return digest if proc.returncode == 0 and "@" in digest else ""
