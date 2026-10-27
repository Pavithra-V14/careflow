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
    assert "SENTINEL" not in str(provenance) and job_id


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
