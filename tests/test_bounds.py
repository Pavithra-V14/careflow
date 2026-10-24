"""tests/bounds.py: a bound that cannot itself hang (offline)."""

import asyncio
import time

import pytest

from tests import bounds
from tests.bounds import bounded

pytestmark = pytest.mark.asyncio


async def test_it_returns_the_result_and_propagates_the_works_own_error():
    async def ok():
        return 7

    async def boom():
        raise KeyError("x")

    assert await bounded(ok(), "work") == 7
    with pytest.raises(KeyError):
        await bounded(boom(), "work")


async def test_it_raises_after_the_bound_even_if_the_work_ignores_cancellation(monkeypatch):
    monkeypatch.setattr(bounds, "CANCEL_GRACE_SECONDS", 0.05)

    release = asyncio.Event()

    async def stubborn():
        while not release.is_set():
            try:
                await asyncio.wait_for(release.wait(), 30)
            except asyncio.CancelledError:  # swallows the cancel, like work stuck unwinding a database call
                continue

    started = time.monotonic()
    try:
        with pytest.raises(AssertionError, match="did not finish within"):
            await bounded(stubborn(), "the stubborn work", 0.05)
        assert time.monotonic() - started < 2  # the old wait_for-based bound would have waited here forever
    finally:
        release.set()  # let the straggler finish so the test loop can close
        await asyncio.sleep(0.05)
