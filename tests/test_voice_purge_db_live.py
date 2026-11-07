"""Migration 0063 against the real database: the scheduled purge of EXPIRED voice transcript chunks, callable only by the queue worker of the clinic, deleting only chunks past their own expiry and writing one
audit row when it deleted something. Inside ONE never-committed outer transaction, as the restricted `careflow_app` role.
"""

import json
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

from careflow.care_gap_sql import sqlstate
from careflow.producer_sweeps import voice_purge_sweep
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
PURGE = "select msg.purge_expired_voice_chunks_as_worker(:c, :n)"


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


async def pg(db):
    return (await db.get_raw_connection()).driver_connection


async def as_app(db, person, sql, **params):
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            claims = {"sub": str(person), "role": "authenticated"}
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)})
            result = await db.execute(text(sql), params)
            value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other_clinic", "worker", "admin", "patient_acct")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'VP-A', 'test'), ($2, 'VP-B', 'test')", ids["clinic"], ids["other_clinic"])
    for k in ("worker", "admin"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids["patient_acct"])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'queue_worker', 'test'), ($3, $2, 'admin', 'test')", ids["worker"], ids["clinic"], ids["admin"])
    session = await h.fetchval(
        "insert into msg.voice_sessions (clinic_id, account_id, origin, workflow, transcript_retention_days, idempotency_key) values ($1, $2, 'test', 'slot_offer', 30, $3) returning id", ids["clinic"], ids["patient_acct"], f"vp-{ids['clinic']}"
    )
    ids["session"] = session
    # one chunk inside its window (the guard sets the expiry), and two already past it (written with the insert guard switched off, inside this rolled-back transaction only)
    await h.execute("insert into msg.voice_transcript_chunks (session_id, clinic_id, seq, body_ct, body_key_version, idempotency_key) values ($1, $2, 1, decode(repeat('02', 32), 'hex'), 1, 'live-1')", session, ids["clinic"])
    await h.execute("alter table msg.voice_transcript_chunks disable trigger voice_chunks_guard")
    for seq in (2, 3):
        await h.execute(
            "insert into msg.voice_transcript_chunks (session_id, clinic_id, seq, body_ct, body_key_version, expires_at, idempotency_key) values ($1, $2, $3, decode(repeat('02', 32), 'hex'), 1, now() - interval '1 day', $4)",
            session, ids["clinic"], seq, f"old-{seq}",
        )
    await h.execute("alter table msg.voice_transcript_chunks enable trigger voice_chunks_guard")
    return ids


async def remaining(db, w):
    return (await db.execute(text("select count(*) from msg.voice_transcript_chunks where session_id = :s"), {"s": w["session"]})).scalar()


async def test_the_worker_deletes_only_expired_chunks_and_writes_one_audit_row(db):
    w = await world(db)
    assert await as_app(db, w["worker"], PURGE, c=w["clinic"], n=500) == [(2,)]
    assert await remaining(db, w) == 1  # the chunk inside its window stays
    rows = (await db.execute(text("select actor_role, details from audit.log where action = 'voice.transcript_purge' and clinic_id = :c"), {"c": w["clinic"]})).all()
    assert len(rows) == 1 and rows[0].actor_role == "queue_worker" and rows[0].details == {"deleted": 2, "by": "schedule"}
    assert await as_app(db, w["worker"], PURGE, c=w["clinic"], n=500) == [(0,)]  # nothing left to do: no second audit row
    assert (await db.execute(text("select count(*) from audit.log where action = 'voice.transcript_purge' and clinic_id = :c"), {"c": w["clinic"]})).scalar() == 1


async def test_only_the_queue_worker_of_the_clinic_may_call_it_and_the_limit_is_checked(db):
    w = await world(db)
    assert await as_app(db, w["admin"], PURGE, c=w["clinic"], n=500) == "42501"  # an admin uses the audited admin function, not this one
    assert await as_app(db, w["patient_acct"], PURGE, c=w["clinic"], n=500) == "42501"
    assert await as_app(db, w["worker"], PURGE, c=w["other_clinic"], n=500) == "42501"  # not the worker of that clinic
    assert await as_app(db, w["worker"], PURGE, c=w["clinic"], n=0) == "42501" and await as_app(db, w["worker"], PURGE, c=w["clinic"], n=1001) == "42501"
    assert await remaining(db, w) == 3  # every refusal deleted nothing
    assert await as_app(db, w["worker"], "delete from msg.voice_transcript_chunks") == "42501"  # and the application role still has no direct delete


async def test_the_sweep_runs_only_while_the_retention_setting_is_set(db):
    w = await world(db)
    assert await voice_purge_sweep(db, None, origin="test") == 0
    assert await remaining(db, w) == 3
