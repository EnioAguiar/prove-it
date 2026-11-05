"""POST /api/review: one review at a time, streamed as Server-Sent Events.

Events: `step` (StepEvent JSON) while the pipeline runs, then exactly one `report` (Report JSON)."""

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictStr, field_validator
from sse_starlette import EventSourceResponse
from starlette.types import Receive, Scope, Send

from app.config import Settings, get_settings
from app.llm import LLM, LLMError, VultrLLM
from app.mutation import run_mutation
from app.pipeline import MutateFn, ReceiptFn, Report, Runner, StepEvent, run_review
from app.receipt import build_receipt, read_image_digest
from app.sandbox import run_in_sandbox

log = logging.getLogger(__name__)

MAX_CODE_BYTES = 20_000
RATE_LIMIT = 10
RATE_WINDOW_S = 600.0
PING_S = 15

router = APIRouter()


@dataclass
class Services:
    llm: LLM
    runner: Runner
    mutate: MutateFn
    receipt: ReceiptFn
    job_root: Path | None


async def build_services(settings: Settings) -> Services:
    """Real wiring; the image digest is read once (called from the app lifespan)."""
    digest = await read_image_digest(settings.sandbox_image)
    runner: Runner = partial(run_in_sandbox, settings=settings)
    job_root = Path(settings.job_root) if settings.job_root else None
    return Services(
        llm=VultrLLM(settings),
        runner=runner,
        mutate=lambda code, tests: run_mutation(code, tests, runner, job_root),
        receipt=lambda code, tests, run: build_receipt(code, tests, run, settings, digest),
        job_root=job_root,
    )


def get_services(request: Request) -> Services:
    return request.app.state.services


class _Job:
    """One holder of the busy lock; release() is idempotent so double cleanup is harmless."""

    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self._held = True

    def release(self) -> None:
        if self._held:
            self._held = False
            self._lock.release()


class Gate:
    """Busy lock plus in-memory sliding-window rate limit per client IP."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.lock = asyncio.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        for key in list(self._hits):  # prune every IP so the store cannot grow unbounded
            hits = self._hits[key]
            while hits and now - hits[0] >= RATE_WINDOW_S:
                hits.popleft()
            if not hits:
                del self._hits[key]
        hits = self._hits.setdefault(ip, deque())
        if len(hits) >= RATE_LIMIT:
            return False
        hits.append(now)
        return True

    async def try_acquire(self) -> _Job | None:
        if self.lock.locked():
            return None
        await self.lock.acquire()  # uncontended: returns without suspending
        return _Job(self.lock)


gate = Gate()


def client_ip(request: Request, *, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if last := forwarded.split(",")[-1].strip():
            return last  # the entry our own proxy (Caddy) appended
    return request.client.host if request.client else "unknown"


class ReviewRequest(BaseModel):
    code: StrictStr
    intent: StrictStr | None = None

    @field_validator("intent")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return value if value and value.strip() else None


def _sse(event: str, payload: object) -> dict[str, str]:
    return {"event": event, "data": json.dumps(dataclasses.asdict(payload), default=str)}


def _error_report(code: str, error: str) -> Report:
    return Report(verdict="ERROR", error=error, code=code, tests="", original_run=None, events=[],
                  fix=None, fix_run=None, mutation=None, explanation="", receipt=None)


_END = object()


async def review_events(code: str, intent: str | None, services: Services,
                        job: _Job) -> AsyncIterator[dict[str, str]]:
    """Run the review, yielding step events then one report; always releases `job`."""
    queue: asyncio.Queue[object] = asyncio.Queue()
    try:
        async def emit(event: StepEvent) -> None:
            await queue.put(event)

        task = asyncio.create_task(run_review(
            code, intent, services.llm, services.runner, emit, services.mutate, services.receipt,
            job_root=services.job_root))
        task.add_done_callback(lambda _: queue.put_nowait(_END))
        try:
            while (item := await queue.get()) is not _END:
                yield _sse("step", item)
            try:
                report = task.result()
            except LLMError as exc:
                report = _error_report(code, str(exc))
            except Exception:
                log.exception("review failed")
                report = _error_report(code, "internal error")
            yield _sse("report", report)
        finally:
            if not task.done():  # client went away mid-run
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
    finally:
        job.release()


class _JobResponse(EventSourceResponse):
    """Releases the job even if the stream never started (client gone before first byte)."""

    def __init__(self, job: _Job, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._job = job

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._job.release()


@router.post("/api/review")
async def review(body: ReviewRequest, request: Request,
                 settings: Settings = Depends(get_settings),
                 services: Services = Depends(get_services)) -> EventSourceResponse:
    if len(body.code.encode()) > MAX_CODE_BYTES:
        raise HTTPException(413, "code too large")
    if not gate.allow(client_ip(request, trust_proxy=settings.trust_proxy)):
        raise HTTPException(429, "rate limited")
    job = await gate.try_acquire()
    if job is None:
        raise HTTPException(429, "busy")
    return _JobResponse(job, review_events(body.code, body.intent, services, job), ping=PING_S)
