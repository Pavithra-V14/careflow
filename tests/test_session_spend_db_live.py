"""Migration 0075 against the real database: the session spend ledger. A front-desk person, nurse or doctor with a second factor records a decision-tier ordinary call through a definer function; no limit row = refused; above the
owner's cap = refused; the function forces role and class; keys are per person; the application role has no table access. Inside ONE never-committed outer transaction, as the restricted `careflow_app` role.
"""

import json
import os
import time
import uuid
from decimal import Decimal
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
    ids = {k: uuid.uuid4() for k in ("clinic", "other", "fd", "nurse", "doctor", "admin", "outsider")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'SP-A', 'test'), ($2, 'SP-B', 'test')", ids["clinic"], ids["other"])
    for k in ("fd", "nurse", "doctor", "admin", "outsider"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k, role in (("fd", "front_desk"), ("nurse", "nurse"), ("doctor", "doctor"), ("admin", "admin")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", ids["outsider"], ids["other"])
    return ids


REC = "select ops.record_session_spend(:c, :o, 'fake', 'fake-1', :a, :s, :k)"
TOT = "select ops.session_spend_today(:c, :o)"


async def rec(db, w, who="fd", a="0.01", s="0.01", k=None, aal2=True, origin="test", clinic=None):
    return await as_app(db, w[who], REC, aal2=aal2, c=clinic or w["clinic"], o=origin, a=Decimal(a), s=Decimal(s), k=k or f"k-{uuid.uuid4()}")


async def set_cap(db, w, cap="0.50"):
    await (await pg(db)).execute("insert into ops.spend_limits (clinic_id, max_usd_per_call) values ($1, $2)", w["clinic"], Decimal(cap))


async def test_no_limit_row_means_nothing_can_be_recorded(db):
    w = await world(db)
    assert await rec(db, w) == "42501"  # unknown is not a value
    await set_cap(db, w)
    assert await rec(db, w) == [(True,)]


async def test_who_may_record_and_what_the_function_refuses(db):
    w = await world(db)
    await set_cap(db, w)
    for who in ("fd", "nurse", "doctor"):
        assert await rec(db, w, who) == [(True,)], who
    assert await rec(db, w, "admin") == "42501"
    assert await rec(db, w, "outsider") == "42501"  # a person of another clinic
    assert await rec(db, w, aal2=False) == "42501"
    assert await rec(db, w, origin="live") == "42501"  # not the clinic's origin
    assert await rec(db, w, a="0.51") == "42501"  # above the owner's cap
    assert await rec(db, w, s="0.51") == "42501"
    assert await rec(db, w, a="-0.01") == "42501"
    assert await as_app(db, w["fd"], REC, c=w["clinic"], o="test", a=Decimal("0.01"), s=Decimal("0.01"), k=" ") == "42501"


async def test_the_row_is_forced_to_the_decision_tier_ordinary_class_and_a_key_belongs_to_one_person(db):
    w = await world(db)
    await set_cap(db, w)
    assert await rec(db, w, "fd", k="same") == [(True,)]
    assert await rec(db, w, "fd", k="same") == [(False,)]  # a replay writes nothing
    assert await rec(db, w, "nurse", k="same") == [(True,)]  # another person's key is their own
    rows = (await db.execute(text("select role, call_class, origin, idempotency_key from ops.spend_ledger where clinic_id = :c order by idempotency_key"), {"c": w["clinic"]})).all()
    assert len(rows) == 2 and all((r.role, r.call_class, r.origin) == ("decision", "ordinary", "test") for r in rows)
    assert all(r.idempotency_key.startswith("session:") for r in rows)


async def test_the_total_is_for_the_callers_clinic_and_origin_only(db):
    w = await world(db)
    await set_cap(db, w)
    await rec(db, w, "fd", a="0.10")
    await rec(db, w, "nurse", a="0.25")
    assert await as_app(db, w["doctor"], TOT, c=w["clinic"], o="test") == [(Decimal("0.350000"),)]
    assert await as_app(db, w["outsider"], TOT, c=w["clinic"], o="test") == "42501"
    assert await as_app(db, w["admin"], TOT, c=w["clinic"], o="test") == "42501"
    assert await as_app(db, w["doctor"], TOT, aal2=False, c=w["clinic"], o="test") == "42501"
    assert await as_app(db, w["doctor"], TOT, c=w["clinic"], o="live") == "42501"


async def test_the_application_role_has_no_direct_access_to_the_limits_or_the_ledger(db):
    w = await world(db)
    await set_cap(db, w)
    assert await as_app(db, w["fd"], "select * from ops.spend_limits") == "42501"
    assert await as_app(db, w["fd"], "insert into ops.spend_limits (clinic_id, max_usd_per_call) values (:c, 999)", c=w["other"]) == "42501"
    assert await as_app(db, w["fd"], "update ops.spend_limits set max_usd_per_call = 999") == "42501"
    direct = "insert into ops.spend_ledger (clinic_id, role, provider, model_id, call_class, usd_actual, usd_shadow, origin, idempotency_key) values (:c, 'decision', 'x', 'y', 'ordinary', 0, 0, 'test', 'direct')"
    assert await as_app(db, w["fd"], direct, c=w["clinic"]) == "42501"  # a front-desk person has no table insert
