"""A hard upper bound for tests that run work concurrently or wait on a lock (no new dependency).

A live concurrency test that deadlocks, waits on a lock another session never releases, or exhausts a connection pool would otherwise block the
whole CI run until the job's own timeout. Wrapping the awaited work in `bounded` turns that into an ordinary, named test failure after
`BOUND_SECONDS`.

WHY NOT `asyncio.wait_for`: on a timeout it cancels the work and then WAITS FOR THE CANCELLATION TO FINISH. Work stuck on a database call through
the transaction-mode pooler can take a very long time to unwind (a cancel request does not reach a statement waiting on a lock, and the
rollback queues behind it). A real CI run (docs/design/ci-sharding.md) sat 23 minutes in that unwind after its 300-second bound had expired.
`bounded` therefore cancels, gives the work only `CANCEL_GRACE_SECONDS` to unwind, and then raises regardless, leaving the stragglers to the
event loop. Tests that use it should ALSO set `lock_timeout` and `statement_timeout` inside their transactions so the database gives up first.
"""

import asyncio
from collections.abc import Awaitable
from typing import Final

BOUND_SECONDS: Final = 120.0
CANCEL_GRACE_SECONDS: Final = 20.0


async def bounded[T](work: Awaitable[T], what: str, seconds: float = BOUND_SECONDS) -> T:
    task = asyncio.ensure_future(work)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if task in done:
        return task.result()
    task.cancel()
    await asyncio.wait({task}, timeout=CANCEL_GRACE_SECONDS)  # a bounded chance to unwind; never an unbounded wait
    raise AssertionError(f"{what} did not finish within {seconds:.0f}s (a lock wait, a deadlock or an exhausted pool?)")
