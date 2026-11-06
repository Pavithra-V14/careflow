"""The voice tables (migration 0045) against the real database, as the restricted `careflow_app` role with verified claims, inside ONE never-committed outer transaction: the patient's own opt-in gates
a session in the database itself, the transcript sink is insert-only with a database-chosen expiry, and nothing is deleted before it expires."""

import base64
import json
import os
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
from careflow.crypto import DemographicVault
from careflow.voice_sql import (
    SqlTranscriptSink,
    get_preference,
    open_session,
    set_preference,
)
from tests.db_helpers import ensure_app_role_sa

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


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


async def world(db):
    """Clinic A and B; a bound patient account (patient record in A), an unbound patient account, a suspended bound one, and a nurse."""
    a, b = uuid.uuid4(), uuid.uuid4()
    p1, p2, p3, nurse = (uuid.uuid4() for _ in range(4))
    rec1, rec3 = uuid.uuid4(), uuid.uuid4()
    await raw(
        db,
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'V A', 'test'), ('{b}', 'V B', 'test');"
        f"insert into iam.accounts (id, account_type, status, origin) values ('{p1}', 'patient', 'active', 'test'), ('{p2}', 'patient', 'active', 'test'), ('{p3}', 'patient', 'active', 'test'), ('{nurse}', 'staff', 'active', 'test');"
        f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values ('{nurse}', '{a}', 'nurse', 'test');"
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key, account_id) values "
        f"('{rec1}', '{a}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'v-{rec1}', '{p1}'), ('{rec3}', '{a}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'v-{rec3}', '{p3}');"
        f"update iam.accounts set status = 'suspended' where id = '{p3}';",
    )
    return a, b, {"bound": p1, "unbound": p2, "suspended": p3, "nurse": nurse}


@asynccontextmanager
async def as_user(db, person):
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(person), "role": "authenticated"})})
        yield db
        await db.exec_driver_sql("reset role")


async def state_of(db, person, sql, **params):
    try:
        async with as_user(db, person) as c:
            result = await c.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


PREF_INSERT = "insert into msg.voice_preferences (account_id, origin, voice_enabled) values (:a, 'test', :e)"
SESSION_INSERT = (
    "insert into msg.voice_sessions (clinic_id, account_id, origin, workflow, transcript_retention_days, idempotency_key) values (:c, :a, 'test', 'slot_offer', :d, :k) returning id"
)


async def enable(db, person):
    assert await state_of(db, person, PREF_INSERT, a=person, e=True) == 1


async def test_a_patient_reads_and_writes_only_its_own_preference_and_staff_cannot_write_one(db):
    _, _, who = await world(db)
    assert await state_of(db, who["bound"], "select count(*) from msg.voice_preferences where true") == [(0,)]
    await enable(db, who["bound"])
    assert await state_of(db, who["bound"], "select voice_enabled from msg.voice_preferences") == [(True,)]
    assert await state_of(db, who["unbound"], "select count(*) from msg.voice_preferences where true") == [(0,)]  # another patient sees nothing
    assert await state_of(db, who["bound"], "update msg.voice_preferences set voice_enabled = false") == 1
    assert await state_of(db, who["unbound"], "update msg.voice_preferences set voice_enabled = true") == 0  # not theirs
    assert await state_of(db, who["unbound"], PREF_INSERT, a=who["bound"], e=True) == "42501"  # not for another account
    assert await state_of(db, who["nurse"], PREF_INSERT, a=who["nurse"], e=True) == "42501"  # staff accounts have no voice preference
    assert await state_of(db, who["nurse"], "select count(*) from msg.voice_preferences where true") == [(0,)]


async def test_the_preference_helpers_default_to_off_and_set_the_value(db):
    _, _, who = await world(db)
    async with as_user(db, who["bound"]) as c:
        assert await get_preference(c, who["bound"]) is False
        await set_preference(c, who["bound"], "test", True)
        assert await get_preference(c, who["bound"]) is True
        await set_preference(c, who["bound"], "test", False)
        assert await get_preference(c, who["bound"]) is False


async def test_a_session_cannot_be_opened_without_the_patients_own_opt_in_and_a_bound_record_in_the_clinic(db):
    a, b, who = await world(db)
    assert await state_of(db, who["bound"], SESSION_INSERT, c=a, a=who["bound"], d=30, k="s0") == "42501"  # no preference row: off by default
    await enable(db, who["bound"])
    await enable(db, who["unbound"])
    assert len(await state_of(db, who["bound"], SESSION_INSERT, c=a, a=who["bound"], d=30, k="s1")) == 1
    assert await state_of(db, who["unbound"], SESSION_INSERT, c=a, a=who["unbound"], d=30, k="s2") == "42501"  # opted in but not a patient of this clinic
    assert await state_of(db, who["bound"], SESSION_INSERT, c=b, a=who["bound"], d=30, k="s3") == "42501"  # another clinic
    assert await state_of(db, who["bound"], SESSION_INSERT, c=a, a=who["unbound"], d=30, k="s4") == "42501"  # for someone else
    await state_of(db, who["bound"], "update msg.voice_preferences set voice_enabled = false")
    assert await state_of(db, who["bound"], SESSION_INSERT, c=a, a=who["bound"], d=30, k="s5") == "42501"  # turned off again


async def test_a_suspended_account_cannot_open_a_session_even_with_a_stale_preference(db):
    a, _, who = await world(db)
    await raw(db, f"insert into msg.voice_preferences (account_id, origin, voice_enabled) values ('{who['suspended']}', 'test', true)")
    assert await state_of(db, who["suspended"], SESSION_INSERT, c=a, a=who["suspended"], d=30, k="x") == "42501"


async def test_the_retention_has_no_default_the_consent_time_is_the_databases_and_a_session_ends_once(db):
    a, _, who = await world(db)
    await enable(db, who["bound"])
    no_retention = "insert into msg.voice_sessions (clinic_id, account_id, origin, workflow, idempotency_key) values (:c, :a, 'test', 'slot_offer', 'nr')"
    assert await state_of(db, who["bound"], no_retention, c=a, a=who["bound"]) == "23502"  # not null: no default exists
    for bad in (0, -1, 3651):
        assert await state_of(db, who["bound"], SESSION_INSERT, c=a, a=who["bound"], d=bad, k=f"d{bad}") == "23514"
    assert await state_of(db, who["bound"], "insert into msg.voice_sessions (clinic_id, account_id, origin, workflow, transcript_retention_days, idempotency_key, consent_recorded_at) values (:c, :a, 'test', 'slot_offer', 30, 'cr', '2001-01-01')", c=a, a=who["bound"]) == "42501"
    assert await state_of(db, who["bound"], "insert into msg.voice_sessions (clinic_id, account_id, origin, workflow, transcript_retention_days, idempotency_key) values (:c, :a, 'test', 'open_ended', 30, 'w')", c=a, a=who["bound"]) == "23514"
    async with as_user(db, who["bound"]) as c:
        sid, created = await open_session(c, clinic_id=a, account_id=who["bound"], origin="test", workflow="slot_offer", retention_days=30, idempotency_key="ok")
        again, created_again = await open_session(c, clinic_id=a, account_id=who["bound"], origin="test", workflow="slot_offer", retention_days=30, idempotency_key="ok")
        assert sid == again and created and not created_again
    assert await state_of(db, who["bound"], "update msg.voice_sessions set status = 'ended' where id = :i", i=sid) == 1
    row = (await db.execute(text("select status, ended_at is not null from msg.voice_sessions where id = :i"), {"i": sid})).one()
    assert tuple(row) == ("ended", True)
    assert await state_of(db, who["bound"], "update msg.voice_sessions set status = 'ended' where id = :i", i=sid) == 0  # nothing open to end
    assert await state_of(db, who["bound"], "update msg.voice_sessions set workflow = 'readiness_item' where id = :i", i=sid) == "42501"  # no column grant
    assert await state_of(db, who["nurse"], "select count(*) from msg.voice_sessions where true") == [(0,)]  # staff never see a patient's session


async def open_one(db, who, a, retention=30):
    await enable(db, who["bound"])
    async with as_user(db, who["bound"]) as c:
        sid, _ = await open_session(c, clinic_id=a, account_id=who["bound"], origin="test", workflow="intake_question", retention_days=retention, idempotency_key=f"o-{uuid.uuid4()}")
    return sid


DENIED = ("42501", "23001")  # a refused insert: the missing grant/policy (42501), or the guard trigger finding no session visible to the caller (23001; it runs first)
CHUNK = "insert into msg.voice_transcript_chunks (session_id, clinic_id, seq, body_ct, body_key_version, idempotency_key) values (:s, :c, :q, '\\x0102'::bytea, 1, :k)"


async def test_chunks_go_only_into_the_patients_own_open_session_and_the_expiry_is_the_databases(db):
    a, _, who = await world(db)
    sid = await open_one(db, who, a, retention=7)
    assert await state_of(db, who["bound"], CHUNK, s=sid, c=a, q=0, k="c0") == 1
    row = (await db.execute(text("select expires_at - created_at from msg.voice_transcript_chunks where session_id = :s"), {"s": sid})).scalar_one()
    assert row.days == 7  # from the session's retention, chosen by no caller
    assert await state_of(db, who["bound"], "insert into msg.voice_transcript_chunks (session_id, clinic_id, seq, body_ct, body_key_version, idempotency_key, expires_at) values (:s, :c, 1, '\\x01'::bytea, 1, 'e', '2001-01-01')", s=sid, c=a) == "42501"
    assert await state_of(db, who["unbound"], CHUNK, s=sid, c=a, q=1, k="other") in DENIED  # someone else's session
    assert await state_of(db, who["nurse"], CHUNK, s=sid, c=a, q=1, k="staff") in DENIED
    assert await state_of(db, who["bound"], CHUNK, s=sid, c=a, q=0, k="c0b") == "23505"  # the same sequence number again
    assert await state_of(db, who["bound"], CHUNK, s=sid, c=a, q=2, k="c0") == "23505"  # the same key again: a replay
    assert await state_of(db, who["bound"], "insert into msg.voice_transcript_chunks (session_id, clinic_id, seq, body_ct, body_key_version, idempotency_key) values (:s, :c, 3, '\\x'::bytea, 1, 'empty')", s=sid, c=a) == "23514"
    await state_of(db, who["bound"], "update msg.voice_sessions set status = 'ended' where id = :i", i=sid)
    assert await state_of(db, who["bound"], CHUNK, s=sid, c=a, q=4, k="late") in DENIED  # an ended session takes no more


async def test_the_application_role_can_only_insert_chunks_and_nothing_is_deleted_before_it_expires(db):
    a, _, who = await world(db)
    sid = await open_one(db, who, a)
    await state_of(db, who["bound"], CHUNK, s=sid, c=a, q=0, k="c0")
    assert await state_of(db, who["bound"], "select count(*) from msg.voice_transcript_chunks where true") == "42501"  # no SELECT right at all
    assert await state_of(db, who["bound"], "update msg.voice_transcript_chunks set seq = 9") == "42501"
    assert await state_of(db, who["bound"], "delete from msg.voice_transcript_chunks") == "42501"
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin_nested():
            await db.execute(text("delete from msg.voice_transcript_chunks where session_id = :s"), {"s": sid})  # not even the owner, before the chunk has expired
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin_nested():
            await db.execute(text("update msg.voice_transcript_chunks set seq = 5 where session_id = :s"), {"s": sid})


async def test_the_sink_stores_ciphertext_only_numbers_chunks_and_a_replay_is_a_no_op(db):
    a, _, who = await world(db)
    sid = await open_one(db, who, a)
    vault = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
    async with as_user(db, who["bound"]) as c:
        sink = SqlTranscriptSink(c, vault, clinic_id=a, key_version=1)
        await sink.append(sid, "first spoken words")
        await sink.append(sid, "second chunk")
    rows = (await db.execute(text("select seq, body_ct from msg.voice_transcript_chunks where session_id = :s order by seq"), {"s": sid})).all()
    assert [r.seq for r in rows] == [0, 1]
    assert all(b"spoken" not in bytes(r.body_ct) and b"second" not in bytes(r.body_ct) for r in rows)
    async with as_user(db, who["bound"]) as c:
        replay = SqlTranscriptSink(c, vault, clinic_id=a, key_version=1)
        await replay.append(sid, "first spoken words")  # seq 0 again in a fresh sink: the unique key makes it a no-op
    assert (await db.execute(text("select count(*) from msg.voice_transcript_chunks where session_id = :s"), {"s": sid})).scalar() == 2


async def test_no_other_role_has_any_access_to_the_voice_tables(db):
    for t in ("voice_preferences", "voice_sessions", "voice_transcript_chunks"):
        for priv in ("delete", "truncate"):
            assert (await db.execute(text("select has_table_privilege('careflow_app', :t, :p)"), {"t": f"msg.{t}", "p": priv})).scalar() is False
        for role in ("anon", "authenticated", "service_role"):
            exists = (await db.execute(text("select exists (select 1 from pg_roles where rolname = :r)"), {"r": role})).scalar()
            if exists:
                assert (await db.execute(text("select has_table_privilege(:r, :t, 'select')"), {"r": role, "t": f"msg.{t}"})).scalar() is False
