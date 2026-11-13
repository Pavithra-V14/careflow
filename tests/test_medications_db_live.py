"""Migration 0074 against the real database: clinical.medications. Append-only for everyone, bi-temporal (a stop or a correction is a new row that supersedes the old one, once), nurse and doctor of the clinic only,
with a verified second factor on read and write, nothing for an erased patient. Inside ONE never-committed outer transaction, as the restricted `careflow_app` role. Codes are meaningless markers.
"""

import json
import os
import time
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


async def pg(db):
    return (await db.get_raw_connection()).driver_connection


def claims_for(person, aal2=True):
    c = {"sub": str(person), "role": "authenticated"}
    if aal2:
        c.update({"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 60}]})
    return json.dumps(c)


async def as_app(db, person, sql, aal2=True, **params):
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims_for(person, aal2)})
            result = await db.execute(text(sql), params)
            value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other", "nurse", "doctor", "fd", "admin", "outsider", "p1", "p2")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'MED-A', 'test'), ($2, 'MED-B', 'test')", ids["clinic"], ids["other"])
    for k in ("nurse", "doctor", "fd", "admin", "outsider"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k, role in (("nurse", "nurse"), ("doctor", "doctor"), ("fd", "front_desk"), ("admin", "admin")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", ids["outsider"], ids["other"])
    for k in ("p1", "p2"):
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) values ($1, $2, '\x0102'::bytea, 1, '\x0304'::bytea, 1, '\x0506'::bytea, 1, 'test', $3)",
            ids[k], ids["clinic"], f"med-{ids[k]}",
        )
    return ids


INSERT = (
    "insert into clinical.medications (clinic_id, origin, patient_id, code, verified, valid_from, valid_to, supersedes_id, recorded_by, idempotency_key) "
    "values (:c, 'test', :p, :code, :v, '2026-01-01', :vt, :s, :by, :k) returning id"
)


async def add(db, w, who="nurse", patient="p1", code="DEMO-M1", v=True, vt=None, s=None, key=None, aal2=True):
    return await as_app(db, w[who], INSERT, aal2=aal2, c=w["clinic"], p=w[patient], code=code, v=v, vt=vt, s=s, by=w[who], k=key or f"k-{uuid.uuid4()}")


async def test_nurse_and_doctor_record_and_nobody_else_does(db):
    w = await world(db)
    for who in ("nurse", "doctor"):
        assert len(await add(db, w, who)) == 1, who
    for who in ("fd", "admin", "outsider"):
        assert await add(db, w, who) == "42501", who
    assert await add(db, w, aal2=False) == "42501"  # no second factor
    other_actor = await as_app(db, w["nurse"], INSERT, c=w["clinic"], p=w["p1"], code="DEMO-M1", v=True, vt=None, s=None, by=w["doctor"], k="k-x")
    assert other_actor == "42501"  # only as yourself


async def test_reads_are_for_nurse_and_doctor_of_the_clinic_with_a_second_factor(db):
    w = await world(db)
    await add(db, w, key="r1")
    q = "select id from clinical.medications where patient_id = :p"
    for who in ("nurse", "doctor"):
        assert len(await as_app(db, w[who], q, p=w["p1"])) == 1, who
    for who in ("fd", "admin", "outsider"):
        assert await as_app(db, w[who], q, p=w["p1"]) == [], who
    assert await as_app(db, w["nurse"], q, aal2=False, p=w["p1"]) == []  # a session without the second factor reads nothing


async def test_a_stop_or_correction_supersedes_once_and_only_for_the_same_patient(db):
    w = await world(db)
    first = (await add(db, w, key="s1"))[0][0]
    assert len(await add(db, w, s=first, vt="2026-02-01", key="s2")) == 1  # a stop: the same code with an end
    assert await add(db, w, s=first, key="s3") == "23505"  # superseded at most once
    assert await add(db, w, patient="p2", s=first, key="s4") in ("23503", "42501")  # a row of ANOTHER patient cannot be superseded
    assert await add(db, w, vt="2025-01-01", key="s5") == "23514"  # an end before the start
    assert await add(db, w, code="bad code", key="s6") == "23514"
    assert await add(db, w, v=None, key="s7") in ("23502", "23514")  # verified has no default
    same = await add(db, w, key="s1")  # a replay by key hits the unique key
    assert same == "23505"


async def test_the_table_is_append_only_for_everyone(db):
    w = await world(db)
    first = (await add(db, w, key="a1"))[0][0]
    h = await pg(db)
    for stmt in ("update clinical.medications set code = 'X' where id = :i", "delete from clinical.medications where id = :i"):
        assert await as_app(db, w["nurse"], stmt, i=first) == "42501"
        try:
            async with db.begin_nested():
                await db.execute(text(stmt), {"i": first})
            raise AssertionError("the owner changed an append-only row")
        except DBAPIError as exc:
            assert sqlstate(exc) in ("23001", "42501")
    with pytest.raises(DBAPIError):
        async with db.begin_nested():
            await db.execute(text("truncate clinical.medications"))
    assert h is not None
    triggers = (await db.execute(text("select tgenabled from pg_trigger where tgrelid = 'clinical.medications'::regclass and not tgisinternal and tgname like 'medications_no_%'"))).all()
    assert triggers and all(t[0] == "A" for t in triggers)
