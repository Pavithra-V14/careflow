"""A hard upper bound for tests that run work concurrently or wait on a lock (no new dependency).

A live concurrency test that deadlocks, waits on a lock another session never releases, or exhausts a connection pool would otherwise block the
whole CI run until the job's own timeout. Wrapping the awaited work in `bounded` turns that into an ordinary, named test failure after
`BOUND_SECONDS`. The two-connection lock-wait test (tests/test_rls_msg_live.py) additionally sets `lock_timeout` and `statement_timeout`
on its own sessions, so the database gives up first.
"""

import asyncio
from collections.abc import Awaitable
from typing import Final

BOUND_SECONDS: Final = 120.0


async def bounded[T](work: Awaitable[T], what: str, seconds: float = BOUND_SECONDS) -> T:
    try:
        return await asyncio.wait_for(work, seconds)
    except TimeoutError:
        raise AssertionError(f"{what} did not finish within {seconds:.0f}s (a lock wait, a deadlock or an exhausted pool?)") from None
