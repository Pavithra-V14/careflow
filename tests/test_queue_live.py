"""PostgresQueue and the outbox against the real hosted project (careflow/queue.py; BUILD-SPEC.md §7.3, §7.4;
CLAUDE.md rules 5 and 8).

Two kinds of test:

* Rolled-back tests share one connection and one never-committed outer transaction, each in its own savepoint;
  migration 0006 is executed inside that outer transaction if it is not applied yet, so the file is verified
  without being applied. Every call goes through the real queue as the restricted `careflow_app` role with
  verified claims set per transaction.
* Concurrency tests need several real sessions, so they commit a small fixture (origin='test') and need 0006
  applied; they skip with a reason before that, and delete the fixture afterwards. They use the real
  transaction-mode pooler and the real app engine.
"""

import asyncio
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import queue as q
from careflow.db import make_app_engine, set_request_claims
from careflow.queue import PostgresQueue
from tests.bounds import bounded
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
APP = os.environ.get("DATABASE_URL_APP") or _ENV.get("DATABASE_URL_APP")
SQL_0006 = (ROOT / "db" / "migrations" / "0006_outbox_and_queue.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _key() -> str:
    return f"k-{_u()}"


@pytest.fixture(scope="module")
def queue():
    return PostgresQueue()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await ensure_app_role_sa(conn)
        if (await conn.execute(text("select to_regclass('ops.queue') is null"))).scalar_one():
            await raw(conn, SQL_0006)
        yield conn
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


async def act(conn, uid, fn, *args, **kwargs):
    """Call `fn(conn, ...)` as a request would: app role, claims set (empty for "no identity"), one savepoint."""
    async with conn.begin_nested():
        await conn.exec_driver_sql("set local role careflow_app")
        if uid is not None:
            await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        else:
            await conn.execute(text("select set_config('request.jwt.claims', '', true)"))
        result = await fn(conn, *args, **kwargs)
        await conn.exec_driver_sql("reset role")
        return result


async def rows(conn, sql, **params):
    return (await conn.execute(text(sql), params)).all()


async def one(conn, sql, **params):
    return (await rows(conn, sql, **params))[0][0]


class World:
    pass


async def world(conn) -> World:
    w = World()
    w.a, w.b = _u(), _u()
    spec = {"fd": (w.a, "front_desk"), "nurse": (w.a, "nurse"), "auditor": (w.a, "auditor"), "admin": (w.a, "admin"),
            "worker": (w.a, "queue_worker"), "fd_b": (w.b, "front_desk"), "worker_b": (w.b, "queue_worker")}
    ids = {k: _u() for k in spec}
    for k, v in ids.items():
        setattr(w, k, v)
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'Clinic A', 'test'), ('{w.b}', 'Clinic B', 'test'); "
        "insert into iam.accounts (id, account_type, status, origin) values "
        + ", ".join(f"('{ids[k]}', 'staff', 'active', 'test')" for k in spec)
        + "; insert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{ids[k]}', '{c}', '{r}', 'test')" for k, (c, r) in spec.items())
        + ";",
    )
    return w


async def enqueue(conn, queue, w, n=1, *, uid=None, task="demo.task", key=None):
    ids = []
    for i in range(n):
        r = await act(conn, uid or w.fd, queue.enqueue, w.a, "test", task, {"n": i}, key or _key())
        ids.append(r.job_id)
    return ids


async def expire(conn, job_id):
    await conn.execute(text("update ops.queue set visible_after = clock_timestamp() - interval '1 second' where id = :j"), {"j": job_id})


async def claim(conn, queue, w, who="w1", timeout=30, uid=None):
    return await act(conn, uid or w.worker, queue.claim, who, timeout)


# ---- enqueue ---------------------------------------------------------------------------------------


async def test_enqueue_creates_an_unclaimed_job_with_its_tag_and_key(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    (row,) = await rows(db, "select state, attempt_count, origin, clinic_id, claimed_by, task_type, payload from ops.queue where id = :j", j=jid)
    assert (row.state, row.attempt_count, row.origin, row.clinic_id, row.claimed_by) == ("unclaimed", 0, "test", w.a, None)
    assert (row.task_type, row.payload) == ("demo.task", {"n": 0})


async def test_enqueue_is_idempotent_and_conflicts_on_an_altered_request(db, queue):
    w = await world(db)
    key = _key()
    first = await act(db, w.fd, queue.enqueue, w.a, "test", "demo.task", {"a": 1}, key)
    again = await act(db, w.fd, queue.enqueue, w.a, "test", "demo.task", {"a": 1}, key)
    assert (again.replayed, again.job_id) == (True, first.job_id)
    assert await one(db, "select count(*) from ops.queue where clinic_id = :c", c=w.a) == 1
    for args in (("test", "demo.task", {"a": 2}), ("test", "other.task", {"a": 1}), ("eval", "demo.task", {"a": 1})):
        with pytest.raises(q.IdempotencyConflict):
            await act(db, w.fd, queue.enqueue, w.a, *args, key)


@pytest.mark.parametrize("bad_key", ["", "  ", None])
async def test_a_key_is_required(db, queue, bad_key):
    w = await world(db)
    with pytest.raises(q.QueueError):
        await act(db, w.fd, queue.enqueue, w.a, "test", "demo.task", {}, bad_key)
    with pytest.raises(q.QueueError):
        await act(db, w.fd, queue.record_event, w.a, "test", "demo.event", {}, bad_key)


async def test_bad_inputs_are_refused_before_the_database(db, queue):
    w = await world(db)
    for args in (("production", "demo.task", {}), ("test", "", {}), ("test", "Bad Type", {}), ("test", "demo.task", ["not", "an", "object"])):
        with pytest.raises((ValueError, TypeError, DBAPIError)):
            await act(db, w.fd, queue.enqueue, w.a, *args, _key())
    assert await one(db, "select count(*) from ops.queue where clinic_id = :c", c=w.a) == 0


# ---- who may do what (RLS) -------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["fd", "nurse", "admin", "worker"])
async def test_producers_and_workers_can_enqueue_into_their_own_clinic(db, queue, who):
    w = await world(db)
    await enqueue(db, queue, w, uid=getattr(w, who))
    assert await one(db, "select count(*) from ops.queue where clinic_id = :c", c=w.a) == 1


@pytest.mark.parametrize("who", ["auditor", "fd_b", "worker_b", None])
async def test_everyone_else_cannot_enqueue_into_the_clinic(db, queue, who):
    w = await world(db)
    with pytest.raises(q.NotPermitted):
        await act(db, getattr(w, who) if who else None, queue.enqueue, w.a, "test", "demo.task", {}, _key())
    assert await one(db, "select count(*) from ops.queue where clinic_id = :c", c=w.a) == 0


async def test_only_a_worker_of_the_clinic_can_claim(db, queue):
    w = await world(db)
    await enqueue(db, queue, w)
    for who in ("fd", "nurse", "admin", "auditor", "fd_b", "worker_b"):
        assert await claim(db, queue, w, uid=getattr(w, who)) is None, who  # RLS hides the job: nothing to claim
    assert await one(db, "select state from ops.queue where clinic_id = :c", c=w.a) == "unclaimed"
    assert (await claim(db, queue, w)) is not None


async def test_producers_cannot_acknowledge_or_dead_letter_and_tenants_are_isolated(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    job = await claim(db, queue, w, "w1")
    for who in ("fd", "worker_b"):
        with pytest.raises((q.NotOwner, q.JobNotFound)):
            await act(db, getattr(w, who), queue.acknowledge, job.id, worker_id="w1")
    assert await one(db, "select state from ops.queue where id = :j", j=jid) == "claimed"


# ---- claim and the visibility timeout --------------------------------------------------------------


async def test_claim_takes_the_oldest_job_first_and_records_the_attempt(db, queue):
    w = await world(db)
    first, second, third = await enqueue(db, queue, w, 3)
    job = await claim(db, queue, w, "w1", 30)
    assert (job.id, job.attempt_count, job.claimed_by) == (first, 1, "w1")
    assert (await claim(db, queue, w, "w2")).id == second
    assert (await claim(db, queue, w, "w3")).id == third
    assert await claim(db, queue, w, "w4") is None
    (row,) = await rows(db, "select state, claimed_by, claimed_at is not null as stamped from ops.queue where id = :j", j=first)
    assert (row.state, row.claimed_by, row.stamped) == ("claimed", "w1", True)


async def test_a_claimed_job_is_hidden_for_its_visibility_timeout(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    job = await claim(db, queue, w, "w1", timeout=60)
    left = await one(db, "select visible_after - clock_timestamp() from ops.queue where id = :j", j=jid)
    assert 55 < left.total_seconds() <= 60 and job.visible_after is not None
    assert await claim(db, queue, w, "w2") is None  # hidden while w1 works


async def test_an_expired_claim_is_handed_to_another_worker_as_a_new_attempt(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    await claim(db, queue, w, "w1")
    await expire(db, jid)  # w1 never finished and its timeout has passed
    again = await claim(db, queue, w, "w2")
    assert (again.id, again.attempt_count, again.claimed_by) == (jid, 2, "w2")


async def test_claim_validates_its_arguments(db, queue):
    w = await world(db)
    for bad in (0, -1, True, "30", None):
        with pytest.raises(ValueError):
            await claim(db, queue, w, "w1", bad)
    for bad_worker in ("", "  ", None, "x" * 201):
        with pytest.raises(q.QueueError):
            await claim(db, queue, w, bad_worker, 30)


# ---- acknowledge -----------------------------------------------------------------------------------


async def test_acknowledge_completes_the_job_and_is_repeatable(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    job = await claim(db, queue, w, "w1")
    await act(db, w.worker, queue.acknowledge, job.id, worker_id="w1")
    await act(db, w.worker, queue.acknowledge, job.id, worker_id="w1")  # replay: no error
    (row,) = await rows(db, "select state, completed_at is not null as done from ops.queue where id = :j", j=jid)
    assert (row.state, row.done) == ("completed", True)
    assert await claim(db, queue, w, "w2") is None  # a completed job is never handed out again


async def test_only_the_current_holder_can_acknowledge(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    await claim(db, queue, w, "w1")
    with pytest.raises(q.NotOwner):
        await act(db, w.worker, queue.acknowledge, jid, worker_id="someone-else")
    await expire(db, jid)
    await claim(db, queue, w, "w2")  # re-claimed
    with pytest.raises(q.NotOwner):  # w1's late acknowledgement must not complete w2's attempt
        await act(db, w.worker, queue.acknowledge, jid, worker_id="w1")
    assert await one(db, "select state from ops.queue where id = :j", j=jid) == "claimed"
    with pytest.raises(q.JobNotFound):
        await act(db, w.worker, queue.acknowledge, _u(), worker_id="w2")


# ---- the five-attempt ceiling and the dead-letter state --------------------------------------------


async def test_a_job_that_keeps_timing_out_is_dead_lettered_at_the_fifth_attempt(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    for attempt in range(1, 6):
        job = await claim(db, queue, w, f"w{attempt}")
        assert job.attempt_count == attempt
        await expire(db, jid)  # the worker dies without acknowledging
    assert await claim(db, queue, w, "w6") is None  # no sixth attempt is ever handed out
    (row,) = await rows(db, "select state, attempt_count, dead_lettered_at is not null as stamped, last_error from ops.queue where id = :j", j=jid)
    assert (row.state, row.attempt_count, row.stamped) == ("dead_lettered", 5, True)
    assert "ceiling 5" in row.last_error
    assert await claim(db, queue, w, "w7") is None


async def test_the_database_itself_refuses_a_sixth_attempt(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    with pytest.raises(DBAPIError, match="attempt_count"):
        async with db.begin_nested():
            await db.execute(text("update ops.queue set state = 'claimed', claimed_at = now(), claimed_by = 'x', attempt_count = 6 where id = :j"), {"j": jid})


async def test_dead_letter_records_the_traceback_and_ends_the_job(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    job = await claim(db, queue, w, "w1")
    trace = "Traceback (most recent call last):\n  File \"handler.py\", line 3\nValueError: boom"
    await act(db, w.worker, queue.dead_letter, job.id, trace, worker_id="w1")
    await act(db, w.worker, queue.dead_letter, job.id, trace, worker_id="w1")  # replay: no error
    (row,) = await rows(db, "select state, last_error from ops.queue where id = :j", j=jid)
    assert (row.state, row.last_error) == ("dead_lettered", trace)
    assert await claim(db, queue, w, "w2") is None


async def test_dead_letter_truncates_a_huge_error_and_needs_a_message_and_ownership(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    job = await claim(db, queue, w, "w1")
    with pytest.raises(q.QueueError):
        await act(db, w.worker, queue.dead_letter, job.id, "  ", worker_id="w1")
    with pytest.raises(q.NotOwner):
        await act(db, w.worker, queue.dead_letter, job.id, "boom", worker_id="intruder")
    await act(db, w.worker, queue.dead_letter, job.id, "x" * 50000, worker_id="w1")
    stored = await one(db, "select last_error from ops.queue where id = :j", j=jid)
    assert len(stored) == q.MAX_ERROR_CHARS and stored.endswith("[truncated]")


async def test_a_completed_job_cannot_be_dead_lettered_afterwards(db, queue):
    w = await world(db)
    await enqueue(db, queue, w)
    job = await claim(db, queue, w, "w1")
    await act(db, w.worker, queue.acknowledge, job.id, worker_id="w1")
    with pytest.raises(q.NotOwner):
        await act(db, w.worker, queue.dead_letter, job.id, "late", worker_id="w1")


# ---- the outbox ------------------------------------------------------------------------------------


async def test_an_event_becomes_exactly_one_job_even_if_relayed_twice(db, queue):
    w = await world(db)
    key = _key()
    ev = await act(db, w.fd, queue.record_event, w.a, "test", "booking.confirmed", {"booking": "b1"}, key)
    again = await act(db, w.fd, queue.record_event, w.a, "test", "booking.confirmed", {"booking": "b1"}, key)
    assert (again.replayed, again.job_id) == (True, ev.job_id)
    assert await act(db, w.worker, queue.relay_outbox) == 1
    assert await act(db, w.worker, queue.relay_outbox) == 0
    (job,) = await rows(db, "select id, task_type, payload, state, idempotency_key from ops.queue where clinic_id = :c", c=w.a)
    assert (job.task_type, job.payload, job.state, job.idempotency_key) == ("booking.confirmed", {"booking": "b1"}, "unclaimed", key)
    (out,) = await rows(db, "select dispatched_at is not null as done, job_id from ops.outbox where clinic_id = :c", c=w.a)
    assert (out.done, out.job_id) == (True, job.id)


async def test_relay_recovers_when_the_job_already_exists_under_the_events_key(db, queue):
    w = await world(db)
    key = _key()
    await act(db, w.fd, queue.record_event, w.a, "test", "booking.confirmed", {"x": 1}, key)
    await act(db, w.fd, queue.enqueue, w.a, "test", "booking.confirmed", {"x": 1}, key)  # an earlier, partly-failed relay
    assert await act(db, w.worker, queue.relay_outbox) == 1
    assert await one(db, "select count(*) from ops.queue where clinic_id = :c", c=w.a) == 1


async def test_only_a_worker_relays_and_clinics_are_isolated(db, queue):
    w = await world(db)
    await act(db, w.fd, queue.record_event, w.a, "test", "booking.confirmed", {}, _key())
    for who in ("fd", "nurse", "auditor", "worker_b"):
        assert await act(db, getattr(w, who), queue.relay_outbox) == 0, who  # nothing visible to lock
    assert await one(db, "select count(*) from ops.queue where clinic_id = :c", c=w.a) == 0
    assert await act(db, w.worker, queue.relay_outbox) == 1


async def test_the_outbox_event_shares_the_fate_of_the_business_transaction(db, queue):
    """The point of an outbox: if the surrounding transaction rolls back, the event never existed."""
    w = await world(db)
    with pytest.raises(RuntimeError):
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await set_request_claims(db, {"sub": str(w.fd), "role": "authenticated"})
            await queue.record_event(db, w.a, "test", "booking.confirmed", {}, _key())
            raise RuntimeError("the business change failed")
    assert await one(db, "select count(*) from ops.outbox where clinic_id = :c", c=w.a) == 0


async def test_outbox_events_with_other_keys_stay_independent(db, queue):
    w = await world(db)
    for i in range(3):
        await act(db, w.fd, queue.record_event, w.a, "test", "e.t", {"i": i}, _key())
    assert await act(db, w.worker, queue.relay_outbox, 2) == 2
    assert await act(db, w.worker, queue.relay_outbox, 2) == 1
    assert await one(db, "select count(*) from ops.queue where clinic_id = :c", c=w.a) == 3


# ---- table-level guarantees ------------------------------------------------------------------------


async def test_no_delete_forced_rls_and_origin_has_no_default(db):
    for table in ("queue", "outbox"):
        assert not await one(db, "select has_table_privilege('careflow_app', :t, 'delete')", t=f"ops.{table}")
        for role in ("anon", "authenticated"):
            assert not await one(db, "select has_table_privilege(:r, :t, 'select')", r=role, t=f"ops.{table}")
        flags = (await rows(db, "select relrowsecurity, relforcerowsecurity from pg_class where oid = cast(:t as regclass)", t=f"ops.{table}"))[0]
        assert tuple(flags) == (True, True)
        col = (await rows(db, "select column_default, is_nullable from information_schema.columns where table_schema = 'ops' and table_name = :t and column_name = 'origin'", t=table))[0]
        assert (col.column_default, col.is_nullable) == (None, "NO")


async def test_the_app_role_cannot_rewrite_what_defines_a_job(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    for column, value in (("payload", "{}"), ("clinic_id", str(w.b)), ("idempotency_key", "new"), ("task_type", "other.task"), ("origin", "live")):
        with pytest.raises(DBAPIError, match="permission denied"):
            sql_type = "jsonb" if column == "payload" else ("uuid" if column == "clinic_id" else "")
            await act(db, w.worker, _set_column, jid, column, value, sql_type)


async def _set_column(conn, job_id, column, value, sql_type):
    placeholder = f"cast(:v as {sql_type})" if sql_type else ":v"
    await conn.execute(text(f"update ops.queue set {column} = {placeholder} where id = :j"), {"v": value, "j": job_id})


async def test_state_checks_keep_the_row_consistent(db, queue):
    w = await world(db)
    (jid,) = await enqueue(db, queue, w)
    for bad in (
        "state = 'claimed'",  # claimed with no claimer
        "state = 'completed'",  # completed with no timestamp
        "state = 'dead_lettered', dead_lettered_at = now()",  # dead-lettered with no error text
        "state = 'bogus'",
    ):
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text(f"update ops.queue set {bad} where id = :j"), {"j": jid})


# ---- concurrency: real sessions through the real pooler --------------------------------------------


@pytest_asyncio.fixture(loop_scope="module")
async def committed():
    if not APP:
        pytest.skip("DATABASE_URL_APP not configured")
    admin = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    async with admin.connect() as c:
        absent = (await c.execute(text("select to_regclass('ops.queue') is null"))).scalar_one()
    if absent:
        await admin.dispose()
        pytest.skip("migration 0006 is not applied yet; the concurrency tests need committed tables")
    clinic, fd, worker = _u(), _u(), _u()
    async with admin.begin() as c:
        await c.execute(text("insert into iam.clinics (id, name, origin) values (:c, 'Queue Concurrency', 'test')"), {"c": clinic})
        for acct, role in ((fd, "front_desk"), (worker, "queue_worker")):
            await c.execute(text("insert into iam.accounts (id, account_type, status, origin) values (:a, 'staff', 'active', 'test')"), {"a": acct})
            await c.execute(text("insert into iam.staff_roles (account_id, clinic_id, role, origin) values (:a, :c, :r, 'test')"), {"a": acct, "c": clinic, "r": role})
    engine = make_app_engine(APP or "", pool_size=8, max_overflow=0)
    try:
        yield clinic, fd, worker, engine, admin
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("delete from ops.outbox where clinic_id = :c"), {"c": clinic})
            await c.execute(text("delete from ops.queue where clinic_id = :c"), {"c": clinic})
            await c.execute(text("delete from iam.staff_roles where clinic_id = :c"), {"c": clinic})
            await c.execute(text("delete from iam.accounts where id = any(:a)"), {"a": [fd, worker]})
            await c.execute(text("delete from iam.clinics where id = :c"), {"c": clinic})
        await admin.dispose()


async def _as(engine, uid, fn, *args):
    """One request-shaped transaction on the real app engine (transaction-mode pooler), committed on exit."""
    async with engine.connect() as conn, conn.begin():
        await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        return await fn(conn, *args)


async def test_parallel_workers_each_job_is_delivered_to_exactly_one_worker(committed, queue):
    clinic, _, worker, engine, admin = committed
    total = 40
    async with admin.begin() as c:
        for i in range(total):
            await c.execute(
                text("insert into ops.queue (clinic_id, origin, task_type, payload, idempotency_key) values (:c, 'test', 'demo.task', cast(:p as jsonb), :k)"),
                {"c": clinic, "p": f'{{"i": {i}}}', "k": _key()},
            )

    async def run_worker(name):
        got = []
        while True:
            job = await _as(engine, worker, queue.claim, name, 60)
            if job is None:
                return got
            got.append(job.id)

    results = await bounded(asyncio.gather(*(run_worker(f"worker-{n}") for n in range(8))), "the eight concurrent workers")
    every = [jid for got in results for jid in got]
    assert len(every) == total and len(set(every)) == total, "a job was delivered twice or not at all"
    assert sum(1 for got in results if got) >= 2, "no parallelism: a single worker took everything"
    async with admin.connect() as c:
        attempts = {r[0] for r in (await c.execute(text("select attempt_count from ops.queue where clinic_id = :c"), {"c": clinic})).all()}
        states = {r[0] for r in (await c.execute(text("select state from ops.queue where clinic_id = :c"), {"c": clinic})).all()}
    assert attempts == {1} and states == {"claimed"}


async def test_a_claimed_job_is_invisible_then_returns_after_the_timeout(committed, queue):
    clinic, fd, worker, engine, admin = committed
    job_id = (await _as(engine, fd, queue.enqueue, clinic, "test", "demo.task", {"v": 1}, _key())).job_id
    # A generous window: on a slow link the second claim can arrive seconds after the first, and the test must
    # check "hidden while the first works" inside the window, then wait out whatever is genuinely left of it.
    first = await _as(engine, worker, queue.claim, "slow-worker", 15)
    assert first.id == job_id
    assert await _as(engine, worker, queue.claim, "impatient-worker", 15) is None  # hidden while the first works
    async with admin.connect() as c:
        left = (await c.execute(text("select extract(epoch from visible_after - clock_timestamp()) from ops.queue where id = :j"), {"j": job_id})).scalar_one()
    assert left > 0, "the visibility window ended before the hidden-job check finished; raise the timeout"
    await asyncio.sleep(float(left) + 0.5)
    second = await _as(engine, worker, queue.claim, "rescuer", 30)
    assert (second.id, second.attempt_count) == (job_id, 2)
    with pytest.raises(q.NotOwner):  # the slow worker's late finish does not count
        await _ack_as(engine, worker, queue, job_id, "slow-worker")
    await _ack_as(engine, worker, queue, job_id, "rescuer")


async def _ack_as(engine, uid, queue, job_id, worker_id):
    async with engine.connect() as conn, conn.begin():
        await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        await queue.acknowledge(conn, job_id, worker_id=worker_id)


async def test_five_timeouts_through_the_real_pooler_reach_the_dead_letter_state(committed, queue):
    clinic, fd, worker, engine, admin = committed
    job_id = (await _as(engine, fd, queue.enqueue, clinic, "test", "demo.task", {"v": 2}, _key())).job_id
    for attempt in range(1, 6):
        job = await _as(engine, worker, queue.claim, f"w{attempt}", 1)
        assert (job.id, job.attempt_count) == (job_id, attempt)
        await asyncio.sleep(1.2)
    assert await _as(engine, worker, queue.claim, "w6", 1) is None
    async with admin.connect() as c:
        state, attempts = (await c.execute(text("select state, attempt_count from ops.queue where id = :j"), {"j": job_id})).one()
    assert (state, attempts) == ("dead_lettered", 5)


async def test_simultaneous_identical_enqueues_make_exactly_one_job(committed, queue):
    clinic, fd, _, engine, admin = committed
    key = _key()
    results = await bounded(
        asyncio.gather(*(_as(engine, fd, queue.enqueue, clinic, "test", "demo.task", {"same": True}, key) for _ in range(6))),
        "the concurrent enqueues",
    )
    assert len({r.job_id for r in results}) == 1
    assert sum(1 for r in results if not r.replayed) == 1
    async with admin.connect() as c:
        assert (await c.execute(text("select count(*) from ops.queue where idempotency_key = :k"), {"k": key})).scalar_one() == 1


async def test_two_relays_racing_turn_each_event_into_exactly_one_job(committed, queue):
    clinic, fd, worker, engine, admin = committed
    for i in range(10):
        await _as(engine, fd, queue.record_event, clinic, "test", "booking.confirmed", {"i": i}, _key())
    counts = await bounded(asyncio.gather(*(_as(engine, worker, queue.relay_outbox, 10) for _ in range(3))), "the concurrent relays")
    assert sum(counts) == 10, counts  # SKIP LOCKED: no event dispatched twice, none left behind
    async with admin.connect() as c:
        jobs = (await c.execute(text("select count(*) from ops.queue where clinic_id = :c and task_type = 'booking.confirmed'"), {"c": clinic})).scalar_one()
        pending = (await c.execute(text("select count(*) from ops.outbox where clinic_id = :c and dispatched_at is null"), {"c": clinic})).scalar_one()
    assert (jobs, pending) == (10, 0)
