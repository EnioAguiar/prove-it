"""POST /api/review: fair queue, then the review, streamed as Server-Sent Events.

Events: `queue` ({"position": n}) while waiting, `step` (StepEvent JSON) while the pipeline runs, then exactly one `report` (Report JSON).
GET /api/public-key and POST /api/verify let anyone check a receipt's signature."""

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
from typing import Any

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
from app.signing import Signer

log = logging.getLogger(__name__)

MAX_CODE_BYTES = 20_000
RATE_LIMIT = 10  # reviews per IP per RATE_WINDOW_S
RATE_WINDOW_S = 600.0
HOURLY_LIMIT = 100  # reviews per hour for everyone: caps model spend under a many-IP flood
CONCURRENCY = 2  # reviews running at once (each sandbox: 0.5 CPU, 256 MB)
MAX_WAITING = 6  # queue length; beyond it, 429 "queue full"
REVIEW_TIMEOUT_S = 120.0  # one review never holds a slot longer than this
PING_S = 15

router = APIRouter()


@dataclass
class Services:
    llm: LLM
    runner: Runner
    mutate: MutateFn
    receipt: ReceiptFn
    job_root: Path | None
    signer: Signer


async def build_services(settings: Settings) -> Services:
    """Real wiring; the image digest is read once (called from the app lifespan)."""
    digest = await read_image_digest(settings.sandbox_image)
    runner: Runner = partial(run_in_sandbox, settings=settings)
    job_root = Path(settings.job_root) if settings.job_root else None
    signer = Signer.from_seed_b64(settings.signing_key)
    return Services(
        llm=VultrLLM(settings),
        runner=runner,
        mutate=lambda code, tests: run_mutation(code, tests, runner, job_root),
        receipt=lambda code, tests, run: build_receipt(code, tests, run, settings, digest, signer),
        job_root=job_root,
        signer=signer,
    )


def get_services(request: Request) -> Services:
    return request.app.state.services


class Ticket:
    """One client's place: waiting in line, then running. Each IP holds at most one at a time,
    so a client with many requests still occupies one slot. release() is idempotent."""

    def __init__(self, gate: "Gate", ip: str) -> None:
        self._gate = gate
        self.ip = ip
        self._acquire: asyncio.Future | None = None
        self._released = False

    def position(self) -> int:
        """1 = next to run; 0 = running."""
        if self._acquire is not None and self._acquire.done():
            return 0
        return self._gate.waiting.index(self) + 1

    async def turns(self) -> AsyncIterator[int]:
        """Yield the queue position whenever it changes, until it is this ticket's turn."""
        self._acquire = asyncio.ensure_future(self._gate.slots.acquire())
        await asyncio.sleep(0)  # a free slot is taken right here, so no queue event is sent
        last = None
        while not self._acquire.done():
            if (pos := self.position()) != last:
                last = pos
                yield pos
            await asyncio.wait({self._acquire}, timeout=1.0)
        self._gate.waiting.remove(self)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._gate.active.pop(self.ip, None)
        if self in self._gate.waiting:
            self._gate.waiting.remove(self)
        if self._acquire is None:
            return
        if self._acquire.done() and not self._acquire.cancelled():
            self._gate.slots.release()
        else:
            self._acquire.cancel()


class Gate:
    """Fair admission: per-IP rate limit, one ticket per IP, a bounded FIFO queue, an hourly cap."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.slots = asyncio.Semaphore(CONCURRENCY)
        self.waiting: list[Ticket] = []
        self.active: dict[str, Ticket] = {}
        self._hits: dict[str, deque[float]] = {}
        self._hour: deque[float] = deque()

    def _allow(self, ip: str, now: float) -> bool:
        for key in list(self._hits):  # prune every IP so the store cannot grow unbounded
            hits = self._hits[key]
            while hits and now - hits[0] >= RATE_WINDOW_S:
                hits.popleft()
            if not hits:
                del self._hits[key]
        return len(self._hits.get(ip, ())) < RATE_LIMIT

    def admit(self, ip: str) -> Ticket:
        """A ticket, or HTTPException(429) with the reason the client should see."""
        now = time.monotonic()
        while self._hour and now - self._hour[0] >= 3600:
            self._hour.popleft()
        if ip in self.active:
            raise HTTPException(429, "already running")
        if not self._allow(ip, now):
            raise HTTPException(429, "rate limited")
        if len(self._hour) >= HOURLY_LIMIT:
            raise HTTPException(429, "hourly limit")
        if len(self.waiting) >= MAX_WAITING:
            raise HTTPException(429, "queue full")
        self._hits.setdefault(ip, deque()).append(now)
        self._hour.append(now)
        ticket = Ticket(self, ip)
        self.active[ip] = ticket
        self.waiting.append(ticket)
        return ticket


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
                        ticket: Ticket) -> AsyncIterator[dict[str, str]]:
    """Wait in line (`queue` events), then the review: `step` events and one `report`.
    Always releases `ticket`."""
    queue: asyncio.Queue[object] = asyncio.Queue()
    task: asyncio.Task | None = None
    try:
        async for position in ticket.turns():
            yield {"event": "queue", "data": json.dumps({"position": position})}

        async def emit(event: StepEvent) -> None:
            await queue.put(event)

        task = asyncio.create_task(run_review(
            code, intent, services.llm, services.runner, emit, services.mutate, services.receipt,
            job_root=services.job_root))
        task.add_done_callback(lambda _: queue.put_nowait(_END))
        deadline = asyncio.get_running_loop().time() + REVIEW_TIMEOUT_S
        timed_out = False
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), deadline - asyncio.get_running_loop().time())
            except TimeoutError:
                timed_out = True
                break
            if item is _END:
                break
            yield _sse("step", item)
        if timed_out:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            report = _error_report(code, f"the review took longer than {REVIEW_TIMEOUT_S:.0f} s and was stopped")
        else:
            try:
                report = task.result()
            except LLMError as exc:
                report = _error_report(code, str(exc))
            except Exception:
                log.exception("review failed")
                report = _error_report(code, "internal error")
        yield _sse("report", report)
    finally:
        if task is not None and not task.done():  # client went away mid-run
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        ticket.release()


class _TicketResponse(EventSourceResponse):
    """Releases the ticket even if the stream never started (client gone before first byte)."""

    def __init__(self, ticket: Ticket, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ticket = ticket

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._ticket.release()


@router.post("/api/review")
async def review(body: ReviewRequest, request: Request,
                 settings: Settings = Depends(get_settings),
                 services: Services = Depends(get_services)) -> EventSourceResponse:
    if len(body.code.encode()) > MAX_CODE_BYTES:
        raise HTTPException(413, "code too large")
    ticket = gate.admit(client_ip(request, trust_proxy=settings.trust_proxy))
    return _TicketResponse(ticket, review_events(body.code, body.intent, services, ticket), ping=PING_S)


@router.get("/api/public-key")
def public_key(services: Services = Depends(get_services)) -> dict[str, str]:
    return {"algorithm": "Ed25519", "key_id": services.signer.key_id,
            "public_key": services.signer.public_key_b64}


@router.post("/api/verify")
def verify(receipt: dict[str, Any], services: Services = Depends(get_services)) -> dict[str, Any]:
    """Is this receipt exactly what this server signed? Any edited field breaks the signature."""
    signer = services.signer
    if receipt.get("key_id") != signer.key_id:
        return {"valid": False, "reason": "not signed by this server's key"}
    if not signer.verify(receipt):
        return {"valid": False, "reason": "contents were changed after signing"}
    return {"valid": True, "reason": "signature matches: unchanged since the run"}
