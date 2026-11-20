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


# ---- migration 0100: the public profile ----

SET_PROFILE = "select iam.set_clinic_public_profile(:c, :a, cast(:d as jsonb), :ad, :o, :p, :pp, :k) as r"


def _args(clinic, key, **over):
    base = {"c": clinic, "a": None, "d": None, "ad": None, "o": None, "p": None, "pp": None, "k": key}
    base.update(over)
    return base


async def _publish(conn, clinic):
    await conn.execute(text("delete from iam.public_clinics"))
    await conn.execute(text("insert into iam.public_clinics (clinic_id) values (:c)"), {"c": clinic})


async def _profile_as_app(conn):
    sp = await conn.begin_nested()
    try:
        await conn.execute(text("set local role careflow_app"))
        return (await conn.execute(text("select * from iam.public_clinic_profile()"))).all()
    finally:
        await sp.rollback()


async def test_the_profile_function_answers_nothing_for_an_unpublished_clinic_and_never_names_another(_conn):
    await _conn.execute(text("delete from iam.public_clinics"))
    other = await _clinic(_conn, "Hidden clinic")
    await _conn.execute(
        text("insert into iam.clinic_public_profile (clinic_id, about) values (:c, 'secret about')"), {"c": other}
    )
    assert await _profile_as_app(_conn) == []  # nothing published: no row, whatever profiles exist
    shown = await _clinic(_conn, "Shown clinic")
    await _publish(_conn, shown)
    rows = await _profile_as_app(_conn)
    assert len(rows) == 1 and rows[0].o_name == "Shown clinic" and rows[0].o_about is None  # the published clinic has no profile: nulls, not the other clinic's text
    assert (rows[0].o_departments, rows[0].o_address, rows[0].o_opening_hours, rows[0].o_phone, rows[0].o_privacy_promise) == (None, None, None, None, None)
    assert rows[0].o_placeholder is False and rows[0].o_has_bookable_type is False and rows[0].o_has_family_rule is False
    assert "secret about" not in str(rows[0])


async def test_the_application_role_has_no_grant_on_the_profile_table(_conn):
    sp = await _conn.begin_nested()
    try:
        await _conn.execute(text("set local role careflow_app"))
        with pytest.raises(DBAPIError, match="permission denied"):
            await _conn.execute(text("select * from iam.clinic_public_profile"))
    finally:
        await sp.rollback()


async def test_an_admin_with_a_second_factor_sets_the_profile_it_is_read_back_audited_and_idempotent(_conn):
    clinic = await _clinic(_conn)
    admin = await _staff(_conn, clinic, "admin")
    await _publish(_conn, clinic)
    deps = json.dumps([{"name": "Dept", "doctors": ["Dr Name"]}, {"name": "Other"}])
    args = _args(clinic, "k-1", a="About text", d=deps, ad="1 Street", o="9 to 5", p="000")
    assert (await _as_app(_conn, admin, SET_PROFILE, args))[0].r == "recorded"
    assert (await _as_app(_conn, admin, SET_PROFILE, args))[0].r == "replay"
    sp = await _conn.begin_nested()
    try:
        with pytest.raises(DBAPIError, match="reused"):
            await _as_app(_conn, admin, SET_PROFILE, _args(clinic, "k-1", a="Different"))
    finally:
        await sp.rollback()
    rows = await _profile_as_app(_conn)
    assert rows[0].o_about == "About text" and rows[0].o_address == "1 Street" and rows[0].o_phone == "000" and rows[0].o_privacy_promise is None
    assert rows[0].o_departments == [{"name": "Dept", "doctors": ["Dr Name"]}, {"name": "Other"}]
    audit = (await _conn.execute(text("select details::text as d from audit.log where clinic_id = :c and action = 'clinic.public_profile.set'"), {"c": clinic})).all()
    assert len(audit) == 1  # the replay wrote no second row
    assert "About text" not in audit[0].d and "1 Street" not in audit[0].d and "about" in audit[0].d  # names the fields, never their text


async def test_a_real_write_clears_the_demo_label_and_the_startup_count_sees_a_labelled_row(_conn):
    clinic = await _clinic(_conn)
    admin = await _staff(_conn, clinic, "admin")
    await _publish(_conn, clinic)
    await _conn.execute(text("insert into iam.clinic_public_profile (clinic_id, about, provenance) values (:c, 'DEMO about', 'DEMO PLACEHOLDER, not approved by the clinic owner')"), {"c": clinic})
    assert (await _profile_as_app(_conn))[0].o_placeholder is True
    sources = {r.o_source: r.o_rows for r in (await _conn.execute(text("select o_source, o_rows from ops.demo_placeholder_rows()"))).all()}
    assert sources["iam.clinic_public_profile"] >= 1
    await _as_app(_conn, admin, SET_PROFILE, _args(clinic, "k-2", a="Real text from the hospital"))
    assert (await _profile_as_app(_conn))[0].o_placeholder is False


async def test_only_an_admin_of_that_clinic_with_a_second_factor_may_set_it_and_bad_fields_are_refused(_conn):
    clinic, other = await _clinic(_conn), await _clinic(_conn)
    admin, nurse, outsider = await _staff(_conn, clinic, "admin"), await _staff(_conn, clinic, "nurse"), await _staff(_conn, other, "admin")
    for who, aal2 in ((nurse, True), (outsider, True), (admin, False)):
        sp = await _conn.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="refused"):
                await _as_app(_conn, who, SET_PROFILE, _args(clinic, "k-3", a="x"), aal2=aal2)
        finally:
            await sp.rollback()
    for bad in ({"a": ""}, {"a": "   "}, {"a": "two\nlines"}, {"a": "x" * 2001}, {"d": json.dumps([])}, {"d": json.dumps([{"name": "d", "extra": 1}])}, {"d": json.dumps([{"doctors": ["x"]}])}, {"d": json.dumps({"name": "d"})}):
        sp = await _conn.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="refused"):
                await _as_app(_conn, admin, SET_PROFILE, _args(clinic, "k-4", **bad))
        finally:
            await sp.rollback()
    sp = await _conn.begin_nested()
    try:
        with pytest.raises(DBAPIError, match="refused"):
            await _as_app(_conn, admin, SET_PROFILE, _args(clinic, ""))  # no idempotency key
    finally:
        await sp.rollback()
