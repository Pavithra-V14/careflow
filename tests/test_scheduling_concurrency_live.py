"""Concurrent bookings and holds against the real hosted project (BUILD-SPEC.md §14.3 hard gate "double-booking and idempotency tests pass";
§21 "200 concurrent booking attempts on one slot resolve to exactly one booking; idempotent retries (including concurrent duplicates) replay
the original response"; CLAUDE.md rules 4, 5, 8).

These need several REAL sessions, so they run through the real transaction-mode pooler as the restricted `careflow_app` role, with claims set
per transaction exactly as a request would (the same shape as tests/test_queue_live.py). They COMMIT a small fixture tagged origin='test'
(one clinic, a front-desk account, a provider, a location, a visit type, 200 patients, and the slots each test makes) and delete it in
teardown, by clinic. Nothing here touches an append-only table.

What "200 concurrent" means here, stated precisely: 200 booking attempts are launched at once against ONE slot, but a semaphore lets only
IN_FLIGHT of them hold a pooled connection at a time (the pool is IN_FLIGHT wide). That is deliberate: the slot row lock serialises the
attempts whatever the width, so the property under test (exactly one wins, every other attempt is refused cleanly) does not depend on how
many are in the database at the same instant, and an unbounded pool would only exhaust the pooler's client limit. The whole 200 are still
issued, each by a different patient under its own key.

Latency: each attempt is a few round trips to the hosted project, serialised by the slot lock, so the 200-attempt test is the slowest here
(expect roughly a minute or two in CI). Every gather is wrapped in tests.bounds.bounded so a stuck lock fails the test instead of the job.
"""

import asyncio
import contextlib
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import scheduling as sch
from careflow.db import make_app_engine, set_request_claims
from careflow.scheduling import SlotManager
from tests.bounds import bounded

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
APP = os.environ.get("DATABASE_URL_APP") or _ENV.get("DATABASE_URL_APP")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not (MIGRATOR and APP), reason="database URLs not configured"),
]

ATTEMPTS = 200  # BUILD-SPEC.md §21
IN_FLIGHT = 20  # pooled connections in use at once (see the module docstring)
BOUND = 300.0  # seconds for the small tests
BOUND_200 = 900.0  # the 200-attempt test: every attempt takes the SAME slot lock, so the whole chain is serial, and each attempt is several round
#                    trips to a hosted database. One CI run exceeded 300 s here, so it is bounded generously and runs in its own CI shard.
LOCK_TIMEOUT = "120s"  # a waiter queues behind at most IN_FLIGHT others, never behind all 200, so this is far more than it needs
STATEMENT_TIMEOUT = "300s"


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _key() -> str:
    return f"conc-{_u()}"


class World:
    pass


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    admin = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    async with admin.connect() as c:
        absent = (await c.execute(text("select to_regclass('sched.holds') is null"))).scalar_one()
    if absent:
        await admin.dispose()
        pytest.skip("migration 0004 is not applied yet; the concurrency tests need committed tables")
    w = World()
    w.clinic, w.fd = _u(), _u()
    w.patients = [_u() for _ in range(ATTEMPTS)]
    w.provider, w.location, w.visit_type = _u(), _u(), _u()
    tag = _u().hex[:8]
    async with admin.begin() as c:
        await c.execute(text("insert into iam.clinics (id, name, origin) values (:c, 'Booking Concurrency', 'test')"), {"c": w.clinic})
        await c.execute(text("insert into iam.accounts (id, account_type, status, origin) values (:a, 'staff', 'active', 'test')"), {"a": w.fd})
        await c.execute(
            text("insert into iam.staff_roles (account_id, clinic_id, role, origin) values (:a, :c, 'front_desk', 'test')"), {"a": w.fd, "c": w.clinic}
        )
        await c.execute(
            text("insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values (:i, :c, 'Dr Conc', 'test', :k)"),
            {"i": w.provider, "c": w.clinic, "k": f"prov-{tag}"},
        )
        await c.execute(
            text("insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values (:i, :c, 'Room', 'test', :k)"),
            {"i": w.location, "c": w.clinic, "k": f"loc-{tag}"},
        )
        await c.execute(
            text("insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values (:i, :c, 'Review', 30, 'test', :k)"),
            {"i": w.visit_type, "c": w.clinic, "k": f"vt-{tag}"},
        )
        await c.execute(
            text(
                "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
                "values (:i, :c, :ct, 1, :ct, 1, 'test', :k)"
            ),
            [{"i": p, "c": w.clinic, "ct": b"x" * 40, "k": f"pat-{tag}-{n}"} for n, p in enumerate(w.patients)],
        )
    w.engine = make_app_engine(APP or "", pool_size=IN_FLIGHT, max_overflow=0, pool_timeout=BOUND)
    w.admin = admin
    w.next_hour = 0
    try:
        yield w
    finally:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(w.engine.dispose(), 30)  # never an unbounded wait on teardown
        async with admin.begin() as c:
            for table in ("sched.bookings", "sched.holds", "sched.waitlist", "sched.slots", "sched.visit_types", "sched.locations",
                          "sched.providers", "mpi.patients"):
                await c.execute(text(f"delete from {table} where clinic_id = :c"), {"c": w.clinic})
            await c.execute(text("delete from iam.staff_roles where clinic_id = :c"), {"c": w.clinic})
            await c.execute(text("delete from iam.accounts where id = :a"), {"a": w.fd})
            await c.execute(text("delete from iam.clinics where id = :c"), {"c": w.clinic})
        await admin.dispose()


async def new_slot(w) -> uuid.UUID:
    """A fresh open slot (its own hour, so no two tests ever overlap), inserted as the migrator."""
    w.next_hour += 1
    slot = _u()
    async with w.admin.begin() as c:
        await c.execute(
            text(
                "insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, origin, idempotency_key) "
                "values (:i, :c, :p, :l, :v, timestamp with time zone '2034-01-01 00:00+00' + make_interval(hours => :h), "
                "timestamp with time zone '2034-01-01 00:30+00' + make_interval(hours => :h), 'test', :k)"
            ),
            {"i": slot, "c": w.clinic, "p": w.provider, "l": w.location, "v": w.visit_type, "h": w.next_hour, "k": f"slot-{slot}"},
        )
    return slot


async def as_front_desk(w, fn, *args, **kwargs):
    """One request-shaped transaction on the real app engine (transaction-mode pooler), committed on exit."""
    async with w.engine.connect() as conn, conn.begin():
        # SET LOCAL lives in this transaction only, so it is safe through the transaction-mode pooler: the DATABASE gives up on a stuck wait.
        await conn.execute(text(f"set local lock_timeout = '{LOCK_TIMEOUT}'"))
        await conn.execute(text(f"set local statement_timeout = '{STATEMENT_TIMEOUT}'"))
        await set_request_claims(conn, {"sub": str(w.fd), "role": "authenticated"})
        return await fn(conn, *args, **kwargs)


async def rows(w, sql, **params):
    async with w.admin.connect() as c:
        return (await c.execute(text(sql), params)).all()


def classify(outcomes) -> list[str]:
    """Name each outcome: a result is a win, a SlotUnavailable is a clean refusal, anything else is an error named by CLASS only."""
    return [
        "refused" if isinstance(o, sch.SlotUnavailable) else f"error:{type(o).__name__}" if isinstance(o, BaseException)
        else "replayed" if o.replayed else "won"
        for o in outcomes
    ]


def _gate():
    return asyncio.Semaphore(IN_FLIGHT)


mgr = SlotManager()


# ---- the hard gate: 200 concurrent bookings of one slot -----------------------------------------------------------------------------------


async def test_200_concurrent_bookings_of_one_slot_produce_exactly_one_booking(world):
    w, slot, gate = world, await new_slot(world), _gate()

    async def attempt(patient):
        async with gate:
            return await as_front_desk(w, mgr.book_appointment, slot, patient, _key())

    outcomes = await bounded(asyncio.gather(*(attempt(p) for p in w.patients), return_exceptions=True), "the 200 concurrent bookings", BOUND_200)
    results = classify(outcomes)
    assert len(results) == ATTEMPTS
    assert results.count("won") == 1, results.count("won")
    assert results.count("refused") == ATTEMPTS - 1, sorted(set(results))
    ((bookings, confirmed),) = [(r[0], r[1]) for r in await rows(w, "select count(*), count(*) filter (where status = 'confirmed') from sched.bookings where slot_id = :s", s=slot)]
    assert (bookings, confirmed) == (1, 1)
    assert (await rows(w, "select status from sched.slots where id = :s", s=slot))[0][0] == "booked"


# ---- idempotent retries, concurrent duplicates -----------------------------------------------------------------------------------------


async def test_concurrent_duplicate_booking_retries_with_one_key_replay_the_original(world):
    w, slot, key, gate = world, await new_slot(world), _key(), _gate()
    patient, copies = w.patients[0], 20

    async def retry(_):
        async with gate:
            return await as_front_desk(w, mgr.book_appointment, slot, patient, key)

    results = await bounded(asyncio.gather(*(retry(i) for i in range(copies))), "the concurrent duplicate booking retries", BOUND)
    assert len({r.booking_id for r in results}) == 1  # the SAME booking every time
    assert sum(1 for r in results if not r.replayed) == 1 and sum(1 for r in results if r.replayed) == copies - 1
    assert (await rows(w, "select count(*) from sched.bookings where slot_id = :s", s=slot))[0][0] == 1


async def test_concurrent_duplicate_hold_retries_with_one_key_replay_the_original(world):
    w, slot, key, gate = world, await new_slot(world), _key(), _gate()
    patient, copies = w.patients[1], 20

    async def retry(_):
        async with gate:
            return await as_front_desk(w, mgr.hold_slot, slot, 10, idempotency_key=key, patient_id=patient)

    results = await bounded(asyncio.gather(*(retry(i) for i in range(copies))), "the concurrent duplicate hold retries", BOUND)
    assert len({r.hold_id for r in results}) == 1
    assert sum(1 for r in results if not r.replayed) == 1 and sum(1 for r in results if r.replayed) == copies - 1
    assert (await rows(w, "select count(*) from sched.holds where slot_id = :s", s=slot))[0][0] == 1
    assert (await rows(w, "select status from sched.slots where id = :s", s=slot))[0][0] == "held"


async def test_concurrent_holds_under_different_keys_on_one_slot_let_exactly_one_through(world):
    w, slot, gate = world, await new_slot(world), _gate()

    async def attempt(patient):
        async with gate:
            return await as_front_desk(w, mgr.hold_slot, slot, 10, idempotency_key=_key(), patient_id=patient)

    outcomes = await bounded(asyncio.gather(*(attempt(p) for p in w.patients[:50]), return_exceptions=True), "the 50 concurrent holds", BOUND)
    results = classify(outcomes)
    assert results.count("won") == 1 and results.count("refused") == 49, sorted(set(results))
    assert (await rows(w, "select count(*) from sched.holds where slot_id = :s", s=slot))[0][0] == 1


async def test_a_retried_booking_after_the_slot_is_taken_does_not_double_book(world):
    """The same key replays the original booking even once other patients are refused; a DIFFERENT key for the same patient is refused."""
    w, slot = world, await new_slot(world)
    key = _key()
    first = await as_front_desk(w, mgr.book_appointment, slot, w.patients[2], key)
    again = await as_front_desk(w, mgr.book_appointment, slot, w.patients[2], key)
    assert again.replayed and again.booking_id == first.booking_id
    with pytest.raises(sch.SlotUnavailable):
        await as_front_desk(w, mgr.book_appointment, slot, w.patients[2], _key())
    assert (await rows(w, "select count(*) from sched.bookings where slot_id = :s", s=slot))[0][0] == 1
