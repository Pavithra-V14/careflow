"""The worker tick and the immediate poke (BUILD-SPEC.md §7.3, §7.4; CLAUDE.md rules 4, 5).

§7.4: the queue is "drained by a scheduled minute tick AND an immediate poke from the API on enqueue (so dispatch is near-instant in the
common case, with the tick as the safety net for missed pokes)". This module is both halves, in process, behind the existing `QueuePort`:

  * `run_tick` is ONE tick. It first relays pending outbox events into jobs (the transactional outbox, §7.2), then claims and runs jobs one
    at a time until the queue is empty or the 40-second wall clock of the tick (§7.3) is spent. The clock is shared by every job in the tick
    through `budget.TickClock`, and it is enforced hard: a handler that is still running when the tick's time is up is cancelled, and its job
    is left claimed, so the queue's visibility timeout returns it to the pool (its attempt was already counted at claim).
  * `Poker` is the poke: an `asyncio.Event` the API sets after it commits an enqueue. A poke that arrives while a tick is running is not
    lost (the next wait returns at once); any number of pokes before a wake coalesce into one.
  * `worker_loop` runs ticks forever: a tick on every poke, and one at least every 60 seconds regardless (the safety net for missed pokes).

Failure handling, per job, with what each outcome means to the queue:
    handler returns             -> acknowledged (completed)
    BudgetExceeded              -> dead-lettered at once, error text names the ceiling only (deterministic; retrying would exceed it again)
    no handler for the task     -> dead-lettered at once, error text names the task type only (a deployment fault, not a transient one)
    any other exception         -> NOT acknowledged and NOT dead-lettered: the visibility timeout retries it, and the queue dead-letters
                                   it after the fifth attempt (§7.3). The log line carries the job id and the exception CLASS only.
    tick time up mid-handler    -> cancelled; same as the line above (a handler must be safe to cancel at any await: side effects belong
                                   in the step just before a pause, §7.2, and carry idempotency keys, §7.2)

What this module never does: log a payload, put job content in an error, call a model or a network, or write any row except through the
queue port. The handlers (the Front Door graph and the rest) arrive in later packages; with none registered, every job is dead-lettered
visibly as "no handler" rather than silently dropped.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from careflow.budget import BudgetExceeded, RunBudget, TickClock
from careflow.db import set_request_claims
from careflow.queue import Job, NotOwner, QueueError, QueuePort

log = logging.getLogger("careflow.tick")

TICK_INTERVAL_SECONDS: Final = 60.0  # the scheduled tick (§7.4)
VISIBILITY_TIMEOUT_SECONDS: Final = 120.0  # longer than a tick, so a running job is never handed to a second worker
RELAY_BATCH: Final = 100


@dataclass(frozen=True)
class JobContext:
    """What a handler is given. The budget is this run's; the engine lets the handler open its own request-shaped transactions."""

    job: Job
    budget: RunBudget
    engine: AsyncEngine
    worker_account_id: uuid.UUID


Handler = Callable[[JobContext], Awaitable[None]]
Sweep = Callable[[AsyncConnection], Awaitable[int]]  # run at the end of every tick as the worker account; returns how many rows it created


@dataclass
class TickResult:
    relayed: int = 0
    completed: int = 0
    dead_lettered: int = 0
    left_for_retry: int = 0
    swept: int = 0  # rows the end-of-tick sweeps created (e.g. tasks for dead-lettered jobs)
    stopped_by: str = "empty"  # empty | deadline
    job_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return self.completed + self.dead_lettered + self.left_for_retry


class Poker:
    """The immediate poke. `poke()` is safe to call from any task of the same event loop, any number of times."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def poke(self) -> None:
        self._event.set()

    async def wait(self, timeout: float) -> bool:
        """True if poked (the poke is consumed), False if `timeout` seconds passed first."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            return False
        self._event.clear()
        return True


async def _as_worker(engine: AsyncEngine, account: uuid.UUID, fn, *args):
    """One request-shaped transaction as the worker account, committed on exit (the shape every database call here uses)."""
    async with engine.connect() as conn, conn.begin():
        await set_request_claims(conn, {"sub": str(account), "role": "authenticated"})
        return await fn(conn, *args)


async def run_tick(
    engine: AsyncEngine,
    queue: QueuePort,
    handlers: dict[str, Handler],
    *,
    worker_account_id: uuid.UUID,
    worker_id: str,
    tick_seconds: float = 40.0,
    clock: Callable[[], float] = time.monotonic,
    on_warn: Callable[[str, int, int], None] | None = None,
    relay: Callable[..., Awaitable[int]] | None = None,
    sweeps: Sequence[Sweep] = (),
) -> TickResult:
    """Run one tick. `relay` defaults to the queue's own `relay_outbox` when it has one. `sweeps` run after the jobs, even when the tick's time ran out; a failing sweep is
    logged (class only) and never fails the tick."""
    result = await _drain(engine, queue, handlers, worker_account_id, worker_id, tick_seconds, clock, on_warn, relay)
    for sweep in sweeps:
        try:
            result.swept += await _as_worker(engine, worker_account_id, sweep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  (a sweep must not take the tick down)
            log.error("a tick sweep failed (error=%s)", type(exc).__name__)
    return result


async def _drain(engine, queue, handlers, worker_account_id, worker_id, tick_seconds, clock, on_warn, relay) -> TickResult:
    tick = TickClock(tick_seconds, clock=clock)
    result = TickResult()
    relay_fn = relay or getattr(queue, "relay_outbox", None)
    if relay_fn is not None:
        result.relayed = await _as_worker(engine, worker_account_id, relay_fn, RELAY_BATCH)
    while True:
        if tick.expired():
            result.stopped_by = "deadline"
            return result
        job = await _as_worker(engine, worker_account_id, queue.claim, worker_id, VISIBILITY_TIMEOUT_SECONDS)
        if job is None:
            return result
        result.job_ids.append(job.id)
        await _run_job(engine, queue, handlers, job, tick, result, worker_account_id, worker_id, on_warn)


async def _run_job(engine, queue, handlers, job: Job, tick: TickClock, result: TickResult, account, worker_id, on_warn) -> None:
    handler = handlers.get(job.task_type)
    if handler is None:
        log.error("no handler for a job's task type (job=%s task_type=%s)", job.id, job.task_type)
        await _dead_letter(engine, queue, job, f"no handler is registered for task type {job.task_type!r}", account, worker_id)
        result.dead_lettered += 1
        return
    budget = RunBudget(tick, on_warn=on_warn)
    try:
        await asyncio.wait_for(handler(JobContext(job, budget, engine, account)), timeout=tick.remaining())
    except BudgetExceeded as exc:
        log.warning("a job exceeded a ceiling and is dead-lettered (job=%s ceiling=%s)", job.id, exc.kind)
        await _dead_letter(engine, queue, job, f"budget ceiling reached: {exc.kind}", account, worker_id)
        result.dead_lettered += 1
    except TimeoutError:
        log.warning("the tick's time ran out during a job; it is left for the visibility timeout (job=%s attempt=%s)", job.id, job.attempt_count)
        result.left_for_retry += 1
        result.stopped_by = "deadline"
    except asyncio.CancelledError:
        raise  # shutdown: never swallow it
    except Exception as exc:  # noqa: BLE001  (a handler is arbitrary code: any failure is logged by class and retried by the queue)
        log.warning("a job failed and is left for retry (job=%s attempt=%s error=%s)", job.id, job.attempt_count, type(exc).__name__)
        result.left_for_retry += 1
    else:
        try:
            await _acknowledge(engine, queue, job, account, worker_id)
        except NotOwner:
            # The claim expired and another worker holds it: it will finish (or fail) under its own claim. Nothing to undo here.
            log.warning("a finished job could not be acknowledged: the claim is no longer ours (job=%s)", job.id)
            result.left_for_retry += 1
            return
        result.completed += 1


async def _acknowledge(engine, queue, job: Job, account, worker_id) -> None:
    async def call(conn):
        await queue.acknowledge(conn, job.id, worker_id=worker_id)

    await _as_worker(engine, account, call)


async def _dead_letter(engine, queue, job: Job, message: str, account, worker_id) -> None:
    async def call(conn):
        await queue.dead_letter(conn, job.id, message, worker_id=worker_id)

    try:
        await _as_worker(engine, account, call)
    except (NotOwner, QueueError) as exc:
        log.warning("a job could not be dead-lettered (job=%s error=%s)", job.id, type(exc).__name__)


async def worker_loop(
    engine: AsyncEngine,
    queue: QueuePort,
    handlers: dict[str, Handler],
    poker: Poker,
    *,
    worker_account_id: uuid.UUID,
    worker_id: str,
    interval_seconds: float = TICK_INTERVAL_SECONDS,
    tick_seconds: float = 40.0,
    sweeps: Sequence[Sweep] = (),
) -> None:
    """Tick on every poke and at least every `interval_seconds`, until cancelled. A failing tick is logged (class only) and the loop goes on."""
    while True:
        try:
            outcome = await run_tick(engine, queue, handlers, worker_account_id=worker_account_id, worker_id=worker_id, tick_seconds=tick_seconds, sweeps=sweeps)
            if outcome.processed:
                log.info("tick done (processed=%s completed=%s dead_lettered=%s retry=%s stopped_by=%s)", outcome.processed,
                         outcome.completed, outcome.dead_lettered, outcome.left_for_retry, outcome.stopped_by)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  (the loop must outlive any failing tick)
            log.error("a tick failed (error=%s)", type(exc).__name__)
        await poker.wait(interval_seconds)
