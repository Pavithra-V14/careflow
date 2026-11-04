"""careflow.tick: the minute tick, the immediate poke and the 40-second wall clock (BUILD-SPEC.md §7.3, §7.4). Offline: a fake engine and a
scripted fake queue, so only the TICK's own behaviour is under test (the queue's claim/ack/ceiling behaviour is in test_queue*.py)."""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import pytest

from careflow.budget import BudgetExceeded, CallClass
from careflow.queue import Job, NotOwner
from careflow.tick import JobContext, Poker, run_tick, worker_loop

pytestmark = pytest.mark.asyncio

ACCOUNT = uuid.uuid4()


class FakeConn:
    async def execute(self, *a, **k):
        return None

    def begin(self):
        @asynccontextmanager
        async def cm():
            yield self

        return cm()


class FakeEngine:
    def connect(self):
        @asynccontextmanager
        async def cm():
            yield FakeConn()

        return cm()


def job(task="demo.task", attempt=1):
    import datetime as dt

    return Job(uuid.uuid4(), uuid.uuid4(), "test", task, {"secret_payload": "SENTINEL-PAYLOAD-8812"}, attempt, "w", dt.datetime.now(dt.UTC), f"k-{uuid.uuid4()}")


class FakeQueue:
    def __init__(self, jobs, relayed=0, ack_error=None):
        self.jobs, self.acked, self.dead, self.relayed, self.claims, self.ack_error = list(jobs), [], [], relayed, 0, ack_error

    async def claim(self, conn, worker_id, visibility_timeout_seconds):
        self.claims += 1
        return self.jobs.pop(0) if self.jobs else None

    async def acknowledge(self, conn, job_id, *, worker_id):
        if self.ack_error:
            raise self.ack_error
        self.acked.append(job_id)

    async def dead_letter(self, conn, job_id, error_message, *, worker_id):
        self.dead.append((job_id, error_message))

    async def relay_outbox(self, conn, limit=100):
        return self.relayed


async def tick(queue, handlers, **kw):
    return await run_tick(FakeEngine(), queue, handlers, worker_account_id=ACCOUNT, worker_id="w1", **kw)  # type: ignore[arg-type]


async def ok(ctx: JobContext):
    ctx.budget.step()


async def test_a_tick_relays_the_outbox_then_runs_every_job_and_acknowledges_them():
    jobs = [job(), job(), job()]
    q = FakeQueue(jobs, relayed=4)
    r = await tick(q, {"demo.task": ok})
    assert (r.relayed, r.completed, r.dead_lettered, r.left_for_retry, r.stopped_by) == (4, 3, 0, 0, "empty")
    assert q.acked == [j.id for j in jobs] and q.dead == []


async def test_an_empty_queue_is_an_ordinary_empty_tick():
    r = await tick(FakeQueue([]), {})
    assert (r.processed, r.stopped_by) == (0, "empty")


async def test_a_job_with_no_handler_is_dead_lettered_visibly_naming_only_the_task_type(caplog):
    caplog.set_level(logging.DEBUG)
    j = job("never.registered")
    q = FakeQueue([j])
    r = await tick(q, {})
    assert r.dead_lettered == 1 and q.acked == []
    ((jid, message),) = q.dead
    assert jid == j.id and "never.registered" in message and "SENTINEL-PAYLOAD-8812" not in message
    assert "SENTINEL-PAYLOAD-8812" not in caplog.text


async def test_a_ceiling_breach_dead_letters_at_once_naming_the_ceiling_only(caplog):
    caplog.set_level(logging.DEBUG)

    async def greedy(ctx: JobContext):
        for _ in range(13):
            ctx.budget.model_call()

    q = FakeQueue([job()])
    r = await tick(q, {"demo.task": greedy})
    assert r.dead_lettered == 1 and q.acked == []
    assert q.dead[0][1] == "budget ceiling reached: model_calls" and "SENTINEL-PAYLOAD-8812" not in caplog.text


async def test_any_other_failure_is_left_for_the_queues_retry_and_logged_by_class_only(caplog):
    caplog.set_level(logging.DEBUG)

    async def boom(ctx: JobContext):
        raise RuntimeError("the patient said SENTINEL-PAYLOAD-8812")

    q = FakeQueue([job(), job()])
    r = await tick(q, {"demo.task": boom})
    assert (r.left_for_retry, r.completed, r.dead_lettered) == (2, 0, 0)  # neither acked nor dead-lettered: the visibility timeout retries
    assert q.acked == [] and q.dead == []
    assert "RuntimeError" in caplog.text and "SENTINEL-PAYLOAD-8812" not in caplog.text


async def test_the_tick_stops_claiming_once_its_forty_seconds_are_spent():
    now = [0.0]

    async def slow(ctx: JobContext):
        now[0] += 25.0  # each job "takes" 25 s of the tick's clock

    q = FakeQueue([job(), job(), job(), job()])
    r = await tick(q, {"demo.task": slow}, clock=lambda: now[0])
    assert r.completed == 2 and r.stopped_by == "deadline"  # 25 s, 50 s: the third is never claimed
    assert len(q.jobs) == 2 and q.claims == 2


async def test_a_handler_still_running_when_the_tick_time_is_up_is_cancelled_and_left_for_retry():
    cancelled = []

    async def forever(ctx: JobContext):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    q = FakeQueue([job()])
    r = await tick(q, {"demo.task": forever}, tick_seconds=0.05)
    assert cancelled == [True]
    assert (r.left_for_retry, r.completed, r.dead_lettered, r.stopped_by) == (1, 0, 0, "deadline")
    assert q.acked == [] and q.dead == []


async def test_a_finished_job_whose_claim_was_lost_is_not_counted_as_completed():
    q = FakeQueue([job()], ack_error=NotOwner("held by another worker"))
    r = await tick(q, {"demo.task": ok})
    assert (r.completed, r.left_for_retry) == (0, 1)


async def test_the_red_flag_path_still_runs_after_a_job_has_used_up_every_ordinary_ceiling():
    """The tick hands every handler a budget; a safety call is honoured even when the ordinary model-call ceiling is spent."""
    seen = []

    async def handler(ctx: JobContext):
        for _ in range(12):
            ctx.budget.model_call()
        with pytest.raises(BudgetExceeded):
            ctx.budget.model_call()
        ctx.budget.model_call(CallClass.SAFETY)  # the safety net is never refused
        seen.append(ctx.budget.safety_model_calls)

    q = FakeQueue([job()])
    r = await tick(q, {"demo.task": handler})
    assert seen == [1] and r.completed == 1


async def test_each_job_gets_its_own_budget_but_all_share_the_ticks_clock():
    budgets = []

    async def h(ctx: JobContext):
        budgets.append(ctx.budget)
        ctx.budget.model_call()

    await tick(FakeQueue([job(), job()]), {"demo.task": h})
    assert budgets[0] is not budgets[1] and budgets[0].tick is budgets[1].tick
    assert [b.model_calls for b in budgets] == [1, 1]


# ---- the poke and the loop ----------------------------------------------------------------------------------------------------------------


async def test_a_poke_wakes_a_waiting_loop_and_a_timeout_returns_false():
    poker = Poker()
    assert await poker.wait(0.01) is False
    waiter = asyncio.create_task(poker.wait(5))
    await asyncio.sleep(0)
    poker.poke()
    assert await asyncio.wait_for(waiter, 1) is True


async def test_a_poke_before_the_wait_is_not_lost_and_many_pokes_coalesce():
    poker = Poker()
    for _ in range(5):
        poker.poke()
    assert await poker.wait(0.01) is True
    assert await poker.wait(0.01) is False  # consumed once


class ScriptedPoker(Poker):
    """A Poker whose waiting is driven by the TEST instead of the wall clock. `wait(timeout)` records the timeout it was given, announces that the loop is idle, and then blocks until the test
    releases it with `release(poked)`: True means "a poke arrived", False means "the interval elapsed". No sleep, no timer: the loop's behaviour is a pure function of the order of releases."""

    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[float] = []
        self.idle = asyncio.Event()
        self._release: asyncio.Queue[bool] = asyncio.Queue()

    async def wait(self, timeout: float) -> bool:
        self.timeouts.append(timeout)
        self.idle.set()
        return await self._release.get()

    async def until_idle(self) -> None:
        """Returns once the loop has finished a tick and is waiting (a bounded wait, so a loop that hung would fail the test instead of hanging the suite)."""
        await asyncio.wait_for(self.idle.wait(), 5)
        self.idle.clear()

    def release(self, poked: bool) -> None:
        self._release.put_nowait(poked)


async def test_the_loop_ticks_on_a_poke_and_again_on_the_interval_and_survives_a_failing_tick(caplog):
    """DETERMINISTIC (no wall clock): the loop is driven through a ScriptedPoker, so 'a poke' and 'the interval elapsed' are events the test chooses, in order.
    Same claims as before, stated exactly: one tick at start, then a tick for every release whichever way it ends (poke or interval), a tick that raises is logged by CLASS only and the loop goes on,
    and the loop asks to wait for exactly the interval it was given."""
    caplog.set_level(logging.DEBUG)
    ticks = []

    class Q(FakeQueue):
        async def claim(self, conn, worker_id, visibility_timeout_seconds):
            ticks.append(1)
            if len(ticks) == 2:
                raise RuntimeError("SENTINEL-PAYLOAD-8812")  # a failing tick

    poker = ScriptedPoker()
    task = asyncio.create_task(
        worker_loop(FakeEngine(), Q([]), {}, poker, worker_account_id=ACCOUNT, worker_id="w1", interval_seconds=0.05)  # type: ignore[arg-type]
    )
    await poker.until_idle()
    assert len(ticks) == 1  # the first tick runs at once, before any wait is over
    poker.release(True)  # a POKE: an immediate tick (the one that fails)
    await poker.until_idle()
    assert len(ticks) == 2 and "RuntimeError" in caplog.text and "SENTINEL-PAYLOAD-8812" not in caplog.text  # logged by class only
    assert not task.done()  # the failing tick did not end the loop
    poker.release(False)  # the INTERVAL elapsed: another tick, and the loop is healthy again
    await poker.until_idle()
    assert len(ticks) == 3
    poker.release(False)
    await poker.until_idle()
    assert len(ticks) == 4 and not task.done()
    assert poker.timeouts == [0.05] * 4  # it waited for exactly the interval, every time
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "SENTINEL-PAYLOAD-8812" not in caplog.text
