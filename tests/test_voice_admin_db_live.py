"""Migration 0047 against the real database, as the restricted `careflow_app` role inside ONE never-committed outer transaction: the aal2 session check, admin-only audited reading and purging of voice
transcripts, the barrier refusal for an erased patient, the clinic-pinning key on care_review_samples, and the closed-door canary settings table."""

import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.care_gap_sql import sqlstate
from tests.db_helpers import ensure_app_role_sa
from tests.test_voice_db_live import raw, world

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


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


def aal2():
    return {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 5}]}


@asynccontextmanager
async def as_user(db, person, **claims):
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(person), "role": "authenticated", **claims})})
        yield db
        await db.exec_driver_sql("reset role")


async def state_of(db, person, sql, claims=None, **params):
    try:
        async with as_user(db, person, **(aal2() if claims is None else claims)) as c:
            result = await c.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


async def with_admin(db):
    a, b, who = await world(db)
    admin, admin_b = uuid.uuid4(), uuid.uuid4()
    await raw(
        db,
        f"insert into iam.accounts (id, account_type, status, origin) values ('{admin}', 'staff', 'active', 'test'), ('{admin_b}', 'staff', 'active', 'test');"
        f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values ('{admin}', '{a}', 'admin', 'test'), ('{admin_b}', '{b}', 'admin', 'test');",
    )
    return a, b, {**who, "admin": admin, "admin_b": admin_b}


async def a_session_with_chunks(db, a, who, retention=7, n=2):
    """A session of the bound patient with `n` unexpired chunks, made by the owner; one more EXPIRED chunk made with triggers off (the only way to backdate one)."""
    sid = uuid.uuid4()
    await raw(db, f"insert into msg.voice_sessions (id, clinic_id, account_id, origin, workflow, transcript_retention_days, idempotency_key) values ('{sid}', '{a}', '{who['bound']}', 'test', 'slot_offer', {retention}, 's-{sid}')")
    for q in range(n):
        await raw(db, f"insert into msg.voice_transcript_chunks (session_id, clinic_id, seq, body_ct, body_key_version, idempotency_key) values ('{sid}', '{a}', {q}, '\\x0102'::bytea, 1, 'k-{sid}-{q}')")
    await raw(db, "set local session_replication_role = replica")
    await raw(
        db,
        f"insert into msg.voice_transcript_chunks (session_id, clinic_id, seq, body_ct, body_key_version, idempotency_key, created_at, expires_at) values "
        f"('{sid}', '{a}', 99, '\\x0304'::bytea, 1, 'old-{sid}', now() - interval '10 days', now() - interval '1 day')",
    )
    await raw(db, "set local session_replication_role = origin")
    return sid


async def test_aal2_session_is_true_only_for_a_verified_totp_factor_and_unknown_is_false(db):
    _, _, who = await with_admin(db)
    now = int(time.time())
    q = "select iam.aal2_session()"
    assert await state_of(db, who["admin"], q) == [(True,)]
    for claims in (
        {},  # nothing at all
        {"aal": "aal1", "amr": [{"method": "totp", "timestamp": now - 5}]},
        {"aal": "aal2", "amr": [{"method": "password", "timestamp": now - 5}]},
        {"aal": "aal2", "amr": ["totp"]},  # legacy string shape
        {"aal": "aal2", "amr": [{"method": "totp", "timestamp": now + 3600}]},  # in the future
        {"aal": "aal2", "amr": [{"method": "totp", "timestamp": "soon"}]},
        {"aal": "aal2", "amr": "totp"},
        {"aal": "aal2"},
    ):
        assert await state_of(db, who["admin"], q, claims=claims) == [(False,)], claims


async def test_only_an_admin_with_aal2_may_list_read_or_purge_and_everyone_gets_the_same_refusal(db):
    a, _, who = await with_admin(db)
    sid = await a_session_with_chunks(db, a, who)
    calls = (
        ("select * from msg.voice_sessions_for_admin(:c)", {"c": a}),
        ("select * from msg.read_voice_transcript(:c, :s)", {"c": a, "s": sid}),
        ("select msg.purge_expired_voice_chunks(:c, :k, 10)", {"c": a, "k": f"p-{uuid.uuid4()}"}),
    )
    for sql, params in calls:
        for who_key, claims in (("nurse", aal2()), ("bound", aal2()), ("admin_b", aal2()), ("admin", {"aal": "aal1", "amr": []}), ("admin", {})):
            assert await state_of(db, who[who_key], sql, claims=claims, **params) == "42501", (sql, who_key, claims)
    assert (await db.execute(text("select count(*) from audit.log where action in ('voice.transcript_read', 'voice.transcript_purge') and clinic_id = :c"), {"c": a})).scalar() == 0  # a refusal writes nothing


async def test_an_admin_reads_unexpired_ciphertext_and_each_read_is_audited(db):
    a, _, who = await with_admin(db)
    sid = await a_session_with_chunks(db, a, who, n=2)
    listing = await state_of(db, who["admin"], "select session_id, chunk_count, expired_chunk_count from msg.voice_sessions_for_admin(:c)", c=a)
    assert listing == [(sid, 3, 1)]
    rows = await state_of(db, who["admin"], "select seq, body_ct from msg.read_voice_transcript(:c, :s)", c=a, s=sid)
    assert [r[0] for r in rows] == [0, 1]  # the expired chunk is not returned
    assert all(isinstance(r[1], bytes | memoryview) for r in rows)
    audit = (await db.execute(text("select count(*), min(actor_role), min(origin) from audit.log where action = 'voice.transcript_read' and resource_id = :s"), {"s": str(sid)})).one()
    assert tuple(audit) == (1, "admin", "test")
    other_clinic = await state_of(db, who["admin_b"], "select * from msg.read_voice_transcript(:c, :s)", c=a, s=sid)
    assert other_clinic == "42501"
    wrong_session = await state_of(db, who["admin"], "select * from msg.read_voice_transcript(:c, :s)", c=a, s=uuid.uuid4())
    assert wrong_session == "42501"  # the same refusal as no access


async def test_the_purge_deletes_only_expired_chunks_is_audited_and_a_replay_does_nothing(db):
    a, _, who = await with_admin(db)
    sid = await a_session_with_chunks(db, a, who, n=2)
    key = f"p-{uuid.uuid4()}"
    assert await state_of(db, who["admin"], "select msg.purge_expired_voice_chunks(:c, :k, 10)", c=a, k=key) == [(1,)]
    assert (await db.execute(text("select count(*) from msg.voice_transcript_chunks where session_id = :s"), {"s": sid})).scalar() == 2  # the unexpired two stay
    assert await state_of(db, who["admin"], "select msg.purge_expired_voice_chunks(:c, :k, 10)", c=a, k=key) == [(0,)]  # a replay
    audit = (await db.execute(text("select count(*), min((details ->> 'deleted')::int) from audit.log where action = 'voice.transcript_purge' and idempotency_key = :k"), {"k": f"sys:voice-purge:{a}:{key}"})).one()
    assert tuple(audit) == (1, 1)
    for bad in (0, 1001):
        assert await state_of(db, who["admin"], "select msg.purge_expired_voice_chunks(:c, :k, :n)", c=a, k=f"x-{uuid.uuid4()}", n=bad) == "42501"
    # the table's own guard still refuses an application delete of an unexpired chunk: no grant, and the trigger would refuse it
    assert await state_of(db, who["admin"], "delete from msg.voice_transcript_chunks") == "42501"


async def test_the_new_functions_are_executable_by_the_application_role_only_and_the_trigger_functions_by_nobody(db):
    for fn in ("msg.voice_sessions_for_admin(uuid)", "msg.read_voice_transcript(uuid,uuid)", "msg.purge_expired_voice_chunks(uuid,text,integer)", "iam.aal2_session()"):
        for role, expected in (("public", False), ("careflow_app", True), ("anon", False), ("authenticated", False), ("service_role", False)):
            if not (await db.execute(text("select exists (select 1 from pg_roles where rolname = :r) or :r = 'public'"), {"r": role})).scalar():
                continue
            assert (await db.execute(text("select has_function_privilege(:r, :f, 'execute')"), {"r": role, "f": fn})).scalar() is expected, (fn, role)
    assert (await db.execute(text("select has_function_privilege('careflow_app', 'ops.care_barriers_not_erased()', 'execute')"))).scalar() is False


async def test_a_barrier_cannot_be_recorded_for_an_erased_patient_and_the_clinic_is_pinned_on_samples(db):
    a, _, who = await with_admin(db)
    live, erased = uuid.uuid4(), uuid.uuid4()
    await raw(
        db,
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values "
        f"('{live}', '{a}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'b-{live}'), ('{erased}', '{a}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'b-{erased}');"
        f"update mpi.patients set erased_at = now(), name_ct = null, name_key_version = null, dob_ct = null, dob_key_version = null, phone_ct = null, phone_key_version = null where id = '{erased}';",
    )
    ins = "insert into ops.care_barriers (clinic_id, origin, patient_id, barrier, idempotency_key) values (:c, 'test', :p, 'transport', :k)"
    assert await state_of(db, who["admin"], ins, c=a, p=live, k=f"b1-{uuid.uuid4()}") == 1
    assert await state_of(db, who["admin"], ins, c=a, p=erased, k=f"b2-{uuid.uuid4()}") == "23001"
    fk = (await db.execute(text("select pg_get_constraintdef(oid) from pg_constraint where conrelid = 'ops.care_review_samples'::regclass and contype = 'f' and confrelid = 'agents.review_decisions'::regclass"))).scalars().all()
    assert len(fk) == 1 and "decision_id, clinic_id" in fk[0]


async def test_the_canary_cap_table_is_closed_to_the_application_role(db):
    for priv in ("select", "insert", "update", "delete", "truncate"):
        assert (await db.execute(text("select has_table_privilege('careflow_app', 'docs.canary_settings', :p)"), {"p": priv})).scalar() is False, priv
    forced = (await db.execute(text("select relrowsecurity and relforcerowsecurity from pg_class where oid = 'docs.canary_settings'::regclass"))).scalar()
    assert forced is True
