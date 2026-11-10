"""Migration 0070 against the real database: the public clinic is owner-managed, one row at most, readable by the application only through the definer function; the break-glass reason list is role-checked.
Inside one never-committed outer transaction."""

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

from tests.db_helpers import aal2_claims

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        yield conn
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


async def _clinic(conn, name="Pick-list clinic"):
    cid = uuid.uuid4()
    await conn.execute(text("insert into iam.clinics (id, name, origin) values (:i, :n, 'test')"), {"i": cid, "n": name})
    return cid


async def _staff(conn, clinic, role):
    acc = uuid.uuid4()
    await conn.execute(text("insert into iam.accounts (id, account_type, status, origin) values (:i, 'staff', 'active', 'test')"), {"i": acc})
    await conn.execute(text("insert into iam.staff_roles (account_id, clinic_id, role, origin) values (:a, :c, :r, 'test')"), {"a": acc, "c": clinic, "r": role})
    return acc


async def _as_app(conn, account, sql, params=None, *, aal2=True):
    await conn.execute(text("set local role careflow_app"))
    claims = aal2_claims(str(account)) if aal2 else {"sub": str(account), "role": "authenticated", "aal": "aal1"}
    await conn.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)})
    rows = (await conn.execute(text(sql), params or {})).all()
    await conn.execute(text("reset role"))  # (after a refusal the caller's savepoint is rolled back, which restores the role)
    return rows


async def test_the_application_role_has_no_grant_on_the_public_clinic_table(_conn):
    sp = await _conn.begin_nested()
    try:
        await _conn.execute(text("set local role careflow_app"))
        with pytest.raises(DBAPIError, match="permission denied"):
            await _conn.execute(text("select * from iam.public_clinics"))
    finally:
        await sp.rollback()


async def test_the_function_answers_nothing_until_a_clinic_is_published_and_then_only_id_and_name(_conn):
    await _conn.execute(text("delete from iam.public_clinics"))
    sp = await _conn.begin_nested()
    try:
        await _conn.execute(text("set local role careflow_app"))
        assert (await _conn.execute(text("select * from iam.public_clinic()"))).all() == []
    finally:
        await sp.rollback()
    cid = await _clinic(_conn)
    await _conn.execute(text("insert into iam.public_clinics (clinic_id) values (:c)"), {"c": cid})
    sp = await _conn.begin_nested()
    try:
        await _conn.execute(text("set local role careflow_app"))
        rows = (await _conn.execute(text("select * from iam.public_clinic()"))).all()
        assert [(r.clinic_id, r.name) for r in rows] == [(cid, "Pick-list clinic")] and set(rows[0]._fields) == {"clinic_id", "name"}
    finally:
        await sp.rollback()


async def test_a_second_published_clinic_is_refused(_conn):
    await _conn.execute(text("delete from iam.public_clinics"))
    a, b = await _clinic(_conn), await _clinic(_conn, "Other")
    await _conn.execute(text("insert into iam.public_clinics (clinic_id) values (:c)"), {"c": a})
    with pytest.raises(DBAPIError, match="public_clinics_one_row|duplicate"):
        async with _conn.begin_nested():
            await _conn.execute(text("insert into iam.public_clinics (clinic_id) values (:c)"), {"c": b})


async def test_the_reason_list_is_for_the_clinics_own_roles_with_a_second_factor(_conn):
    clinic, other = await _clinic(_conn), await _clinic(_conn)
    await _conn.execute(text("insert into iam.break_glass_reasons (clinic_id, code) values (:c, 'zeta'), (:c, 'alpha')"), {"c": clinic})
    nurse, owner, outsider = await _staff(_conn, clinic, "nurse"), await _staff(_conn, clinic, "business_owner"), await _staff(_conn, other, "nurse")
    rows = await _as_app(_conn, nurse, "select code from iam.break_glass_reason_codes(:c)", {"c": clinic})
    assert [r.code for r in rows] == ["alpha", "zeta"]
    for who, aal2 in ((owner, True), (outsider, True), (nurse, False)):
        sp = await _conn.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="refused"):
                await _as_app(_conn, who, "select code from iam.break_glass_reason_codes(:c)", {"c": clinic}, aal2=aal2)
        finally:
            await sp.rollback()
