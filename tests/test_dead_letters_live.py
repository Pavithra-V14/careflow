"""A job that fails five times is dead-lettered and a nurse's review task exists for it, exactly once (careflow/dead_letters.py; BUILD-SPEC.md §7.3; owner decision 2026-10-24).

Real queue (ops.queue, migration 0006), real review tasks (0007/0013), real RLS: everything runs as the restricted `careflow_app` role with a verified account's claims inside ONE
never-committed outer transaction. "Five failures" is five claims of the same job without an acknowledgement, each after the visibility timeout expired; the sixth claim finds the
attempt ceiling reached and dead-letters the job inside `claim` (the path the tick cannot hook), and the end-of-tick sweep then raises the task.
"""

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.db import set_request_claims
from careflow.dead_letters import SqlDeadLetterTaskWriter, raise_dead_letter_tasks
from careflow.queue import MAX_ATTEMPTS, PostgresQueue
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


def _u() -> uuid.UUID:
    return uuid.uuid4()


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


class World:
    pass


async def world(conn) -> World:
    w = World()
    w.a, w.b = _u(), _u()
    roster = [("worker", w.a, "queue_worker"), ("nurse", w.a, "nurse"), ("fd", w.a, "front_desk"), ("nurse_b", w.b, "nurse"), ("worker_b", w.b, "queue_worker")]
    w.people = {k: _u() for k, _, _ in roster}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'DL A', 'test'), ('{w.b}', 'DL B', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{w.people[k]}', 'staff', 'active', 'test')" for k, _, _ in roster)
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{w.people[k]}', '{c}', '{r}', 'test')" for k, c, r in roster) + ";",
    )
    return w


async def as_user(conn, uid):
    await conn.exec_driver_sql("set local role careflow_app")
    await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})


async def enqueue(db, w, doc) -> uuid.UUID:
    async with db.begin_nested():
        await as_user(db, w.people["nurse"])
        result = await PostgresQueue().enqueue(db, w.a, "test", "front_door_run", {"document_id": str(doc)}, f"live-dl-{_u()}")
        await db.exec_driver_sql("reset role")
    return result.job_id


async def claim(db, w):
    async with db.begin_nested():
        await as_user(db, w.people["worker"])
        job = await PostgresQueue().claim(db, "w-1", 120.0)
        await db.exec_driver_sql("reset role")
    return job


async def expire(db, job_id):
    await db.execute(text("update ops.queue set visible_after = clock_timestamp() - interval '1 second' where id = :j"), {"j": job_id})


async def sweep(db, w, uid=None, priority="standard"):
    async with db.begin_nested():
        await as_user(db, w.people[uid or "worker"])
        created = await raise_dead_letter_tasks(db, SqlDeadLetterTaskWriter(priority=priority))
        await db.exec_driver_sql("reset role")
    return created


async def tasks(db, doc):
    return (await db.execute(text("select kind, priority, assigned_role, source_message_id, provenance, idempotency_key from agents.review_tasks where source_message_id = :s"),
                             {"s": f"document:{doc}"})).all()


async def test_a_job_that_fails_five_times_is_dead_lettered_and_exactly_one_nurse_task_exists_however_often_the_sweep_runs(db):
    w = await world(db)
    doc = _u()
    job_id = await enqueue(db, w, doc)
    for attempt in range(1, MAX_ATTEMPTS + 1):  # five failures: claimed, never acknowledged, the visibility timeout expires each time
        job = await claim(db, w)
        assert job is not None and job.id == job_id and job.attempt_count == attempt
        await expire(db, job_id)
    assert await sweep(db, w) == 0 and await tasks(db, doc) == []  # not dead-lettered yet: nothing to report
    assert await claim(db, w) is None  # the sixth claim finds the ceiling reached and dead-letters the job
    ((state, attempts),) = (await db.execute(text("select state, attempt_count from ops.queue where id = :j"), {"j": job_id})).all()
    assert (state, attempts) == ("dead_lettered", MAX_ATTEMPTS)
    assert await sweep(db, w) == 1
    assert await sweep(db, w) == 0 and await sweep(db, w) == 0  # a replay (or a second worker) creates no second task
    ((kind, priority, role, source, provenance, key),) = await tasks(db, doc)
    assert (kind, priority, role, source, key) == ("referral_dead_letter", "standard", "nurse", f"document:{doc}", f"dead-letter:{job_id}")
    assert provenance == {"job_id": str(job_id), "task_type": "front_door_run", "attempts": MAX_ATTEMPTS}  # ids and numbers only


async def test_a_job_dead_lettered_directly_for_a_missing_handler_gets_its_task_too(db):
    w = await world(db)
    doc = _u()
    job_id = await enqueue(db, w, doc)
    job = await claim(db, w)
    async with db.begin_nested():
        await as_user(db, w.people["worker"])
        await PostgresQueue().dead_letter(db, job.id, "no handler is registered for task type 'front_door_run'", worker_id="w-1")
        await db.exec_driver_sql("reset role")
    assert await sweep(db, w) == 1 and len(await tasks(db, doc)) == 1 and (await tasks(db, doc))[0].idempotency_key == f"dead-letter:{job_id}"


async def test_other_task_types_and_other_clinics_jobs_are_not_swept_and_the_error_text_is_never_copied(db):
    w = await world(db)
    doc = _u()
    job_id = await enqueue(db, w, doc)
    other = await db.execute(
        text("insert into ops.queue (clinic_id, origin, task_type, payload, idempotency_key, state, attempt_count, dead_lettered_at, last_error) "
             "values (:c, 'test', 'something.else', '{}'::jsonb, :k, 'dead_lettered', 5, now(), 'x') returning id"),
        {"c": w.a, "k": f"other-{_u()}"},
    )
    assert other.first() is not None
    job = await claim(db, w)
    async with db.begin_nested():
        await as_user(db, w.people["worker"])
        await PostgresQueue().dead_letter(db, job.id, "SENTINEL-ERROR-TEXT patient name", worker_id="w-1")
        await db.exec_driver_sql("reset role")
    assert await sweep(db, w) == 1  # only the front_door_run job
    assert await sweep(db, w, uid="worker_b") == 0  # clinic B's worker sees none of clinic A's jobs
    ((_, _, _, _, provenance, _),) = await tasks(db, doc)
    assert "SENTINEL-ERROR-TEXT" not in str(provenance) and "patient name" not in str(provenance) and job_id


async def test_the_nurse_of_the_clinic_can_see_the_task_front_desk_and_another_clinic_cannot(db):
    w = await world(db)
    doc = _u()
    await enqueue(db, w, doc)
    job = await claim(db, w)
    async with db.begin_nested():
        await as_user(db, w.people["worker"])
        await PostgresQueue().dead_letter(db, job.id, "ceiling", worker_id="w-1")
        await db.exec_driver_sql("reset role")
    await sweep(db, w)
    for who, expected in (("nurse", 1), ("fd", 0), ("nurse_b", 0)):
        async with db.begin_nested():
            await as_user(db, w.people[who])
            n = (await db.execute(text("select count(*) from agents.review_tasks where kind = 'referral_dead_letter' and source_message_id = :s"), {"s": f"document:{doc}"})).scalar_one()
            await db.exec_driver_sql("reset role")
        assert n == expected, who


# ---- the worker's expired-hold sweep (migration 0033) --------------------------------------------------------------------------------------------------------------------------


async def stale_hold(db, w, clinic=None):
    """A slot 'held' by an active hold whose time has passed (seeded as the owner; the insert trigger needs a future time, so it is moved into the past afterwards)."""
    clinic = clinic or w.a
    ids = {k: _u() for k in ("provider", "location", "vt", "slot", "hold")}
    start = "date_trunc('hour', now()) + interval '400 days'"
    await raw(
        db,
        f"insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ('{ids['provider']}', '{clinic}', 'P', 'test', 'sw-p-{ids['provider']}');\n"
        f"insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values ('{ids['location']}', '{clinic}', 'L', 'test', 'sw-l-{ids['location']}');\n"
        f"insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values ('{ids['vt']}', '{clinic}', 'V', 20, 'test', 'sw-v-{ids['vt']}');\n"
        f"insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) "
        f"values ('{ids['slot']}', '{clinic}', '{ids['provider']}', '{ids['location']}', '{ids['vt']}', {start}, {start} + interval '20 minutes', 'held', 'test', 'sw-s-{ids['slot']}');\n"
        f"insert into sched.holds (id, clinic_id, slot_id, expires_at, origin, idempotency_key, status) values ('{ids['hold']}', '{clinic}', '{ids['slot']}', now() + interval '1 hour', 'test', 'sw-h-{ids['hold']}', 'active');\n"
        f"update sched.holds set expires_at = clock_timestamp() - interval '1 second', created_at = clock_timestamp() - interval '1 hour' where id = '{ids['hold']}';",
    )
    return ids


async def run_sweep(db, w, who):
    async with db.begin_nested():
        await as_user(db, w.people[who])
        done = (await db.execute(text("select sched.release_expired_holds()"))).scalar()
        await db.exec_driver_sql("reset role")  # (a refusal rolls the savepoint back, which restores the role too)
        return done


async def hold_and_slot(db, ids):
    h = (await db.execute(text("select status from sched.holds where id = :h"), {"h": ids["hold"]})).scalar()
    s = (await db.execute(text("select status from sched.slots where id = :s"), {"s": ids["slot"]})).scalar()
    return h, s


async def test_the_worker_sweep_expires_the_stale_hold_and_reopens_the_slot_in_its_own_clinic_only(db):
    w = await world(db)
    mine, theirs = await stale_hold(db, w), await stale_hold(db, w, w.b)
    assert await hold_and_slot(db, mine) == ("active", "held")
    assert await run_sweep(db, w, "worker") == 1  # worker A: clinic A only
    assert await hold_and_slot(db, mine) == ("expired", "open")
    assert await hold_and_slot(db, theirs) == ("active", "held")  # clinic B's stale hold is not this worker's business
    assert await run_sweep(db, w, "worker") == 0  # safe to repeat
    assert await run_sweep(db, w, "worker_b") == 1 and await hold_and_slot(db, theirs) == ("expired", "open")


async def test_a_live_hold_is_left_alone_by_the_sweep(db):
    w = await world(db)
    live = await stale_hold(db, w)
    await db.execute(text("update sched.holds set expires_at = clock_timestamp() + interval '1 hour' where id = :h"), {"h": live["hold"]})
    assert await run_sweep(db, w, "worker") == 0 and await hold_and_slot(db, live) == ("active", "held")


@pytest.mark.parametrize("who", ["nurse", "fd", "nurse_b"])
async def test_nobody_but_a_queue_worker_can_run_the_sweep(db, who):
    w = await world(db)
    ids = await stale_hold(db, w)
    with pytest.raises(Exception, match="sweep refused"):
        await run_sweep(db, w, who)
    assert await hold_and_slot(db, ids) == ("active", "held")


async def test_a_call_with_no_identity_is_refused(db):
    w = await world(db)
    with pytest.raises(Exception, match="sweep refused"):
        async with db.begin_nested():  # (the savepoint is rolled back by the refusal, which also undoes the local role)
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', '', true)"))
            await db.execute(text("select sched.release_expired_holds()"))
    assert w.a
