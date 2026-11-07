"""Migration 0060 against the real database: staff account recovery through two admins. One admin requests, a SECOND admin (not the requester, not the account) approves, completion is recorded after the Auth
admin port has reset the factors; nothing but the two functions writes a request; every step is an audit row. Inside ONE never-committed outer transaction, as the restricted `careflow_app` role.
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

from careflow.auth_admin import AuthAdminError, FakeAuthAdmin, SupabaseAuthAdmin
from careflow.care_gap_sql import sqlstate
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
DB_SKIP = pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")
pytestmark = [pytest.mark.asyncio(loop_scope="module")]
REQUEST = "select request_id, created from iam.request_staff_recovery(:c, :t, :k)"
RESOLVE = "select iam.resolve_staff_recovery(:r, :a)"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    if not MIGRATOR:
        pytest.skip("DATABASE_URL_MIGRATOR not configured")
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


def claims_for(person, aal2):
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


async def owner(db, sql, **params):
    try:
        async with db.begin_nested():
            result = await db.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other_clinic", "admin1", "admin2", "nurse", "nurse2", "other_admin", "patient_acct")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'RC-A', 'test'), ($2, 'RC-B', 'test')", ids["clinic"], ids["other_clinic"])
    for k in ("admin1", "admin2", "nurse", "nurse2", "other_admin"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids["patient_acct"])
    for k, role in (("admin1", "admin"), ("admin2", "admin"), ("nurse", "nurse"), ("nurse2", "nurse")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'admin', 'test')", ids["other_admin"], ids["other_clinic"])
    return ids


async def ask(db, w, who="admin1", target="nurse", key=None):
    return await as_app(db, w[who], REQUEST, c=w["clinic"], t=w[target], k=key or f"k-{uuid.uuid4()}")


@DB_SKIP
async def test_two_admins_request_approve_and_complete_and_every_step_is_audited(db):
    w = await world(db)
    asked = await ask(db, w, key="r1")
    rid = asked[0][0]
    assert asked[0][1] is True and await ask(db, w, key="r1") == [(rid, False)]
    assert await ask(db, w, key="r2") == "42501"  # one open recovery per account
    assert await as_app(db, w["admin1"], RESOLVE, r=rid, a="approve") == [("same_person",)]  # the requester cannot approve their own request
    assert await as_app(db, w["nurse"], RESOLVE, r=rid, a="approve") == "42501"  # the account being recovered is no admin
    assert await as_app(db, w["other_admin"], RESOLVE, r=rid, a="approve") == "42501"  # an admin of another clinic
    assert await as_app(db, w["admin2"], RESOLVE, aal2=False, r=rid, a="approve") == "42501"
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="complete") == [("not_approved",)]
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="approve") == [("approved",)]
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="approve") == [("approved",)]  # a replay by the approver
    assert await as_app(db, w["nurse"], RESOLVE, r=rid, a="complete") == "42501"  # the account being recovered is no admin
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="complete") == [("completed_now",)]  # the caller resets the factors only on THIS answer
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="complete") == [("completed",)]  # a replay: no second reset
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="cancel") == [("not_open",)]
    actions = [a for (a,) in (await db.execute(text("select action from audit.log where details ->> 'request_id' = :r order by id"), {"r": str(rid)})).all()]
    assert actions == ["staff.recovery_request", "staff.recovery_approve", "staff.recovery_complete"]
    assert await ask(db, w, key="r3") != "42501"  # after completion a new recovery for the same account may be asked


@DB_SKIP
async def test_who_may_ask_and_what_the_database_refuses(db):
    w = await world(db)
    assert await ask(db, w, who="admin1", target="admin1") == "42501"  # not for yourself
    assert await ask(db, w, who="nurse", target="nurse2") == "42501"  # only admins
    assert await ask(db, w, who="other_admin") == "42501"  # not an admin of this clinic
    assert await ask(db, w, target="other_admin") == "42501"  # the account is not staff of this clinic
    assert await as_app(db, w["admin1"], REQUEST, c=w["clinic"], t=w["patient_acct"], k="p") == "42501"  # a patient account is not staff
    assert await as_app(db, w["admin1"], REQUEST, aal2=False, c=w["clinic"], t=w["nurse"], k="x") == "42501"
    assert await as_app(db, w["admin1"], REQUEST, c=w["clinic"], t=uuid.uuid4(), k="y") == "42501"
    # a staff account with a live role in ANOTHER clinic too: its factor is account-wide, so this clinic's admins cannot reset it
    h = await pg(db)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", w["nurse2"], w["other_clinic"])
    assert await ask(db, w, target="nurse2") == "42501"


@DB_SKIP
async def test_cancel_before_completion_and_the_account_being_recovered_cannot_approve(db):
    w = await world(db)
    rid = (await ask(db, w, key="c1", target="admin2"))[0][0]  # recovery of the OTHER admin's account
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="approve") == [("same_person",)]  # the account being recovered cannot approve it
    assert await as_app(db, w["admin1"], RESOLVE, r=rid, a="cancel") == [("cancelled",)]
    assert await as_app(db, w["admin1"], RESOLVE, r=rid, a="approve") == [("not_requested",)]
    rid2 = (await ask(db, w, key="c2"))[0][0]
    assert await as_app(db, w["admin2"], RESOLVE, r=rid2, a="approve") == [("approved",)]
    assert await as_app(db, w["admin1"], RESOLVE, r=rid2, a="cancel") == [("cancelled",)]  # an approved request can still be cancelled


@DB_SKIP
async def test_a_target_that_lost_its_role_or_was_suspended_cannot_be_approved_or_completed(db):
    w = await world(db)
    h = await pg(db)
    rid = (await ask(db, w, key="g1"))[0][0]
    await h.execute("update iam.accounts set status = 'suspended' where id = $1", w["nurse"])
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="approve") == [("target_gone",)]
    assert await as_app(db, w["admin2"], RESOLVE, r=rid, a="cancel") == [("cancelled",)]
    rid2 = (await ask(db, w, key="g2", target="nurse2"))[0][0]
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", w["nurse2"], w["other_clinic"])  # a role gained elsewhere since the request
    assert await as_app(db, w["admin2"], RESOLVE, r=rid2, a="approve") == [("target_gone",)]


@DB_SKIP
async def test_no_application_write_reaches_a_request_and_the_guards_hold_for_the_owner(db):
    w = await world(db)
    rid = (await ask(db, w))[0][0]
    for sql in (
        "insert into iam.recovery_requests (clinic_id, origin, target_account_id, requested_by, idempotency_key) values (gen_random_uuid(), 'test', gen_random_uuid(), gen_random_uuid(), 'k')",
        "update iam.recovery_requests set status = 'approved'",
        "delete from iam.recovery_requests",
    ):
        assert await as_app(db, w["admin1"], sql) == "42501", sql
    assert await owner(db, "delete from iam.recovery_requests where id = :r", r=rid) == "23001"
    assert await owner(db, "update iam.recovery_requests set target_account_id = requested_by where id = :r", r=rid) == "23001"
    assert await owner(db, "update iam.recovery_requests set status = 'completed' where id = :r", r=rid) == "23001"  # no jump past the approval
    assert await owner(db, "update iam.recovery_requests set status = 'approved', approved_by = requested_by, decided_at = now() where id = :r", r=rid) == "23514"  # the approver must be a second person
    assert await owner(db, "truncate iam.recovery_requests") in ("23001", "0A000")
    seen = lambda who, aal2=True: as_app(db, w[who], "select count(*) from iam.recovery_requests where id = :r", aal2=aal2, r=rid)
    assert await seen("admin1") == [(1,)] and await seen("admin2") == [(1,)]
    assert await seen("nurse") == [(0,)] and await seen("other_admin") == [(0,)] and await seen("admin1", aal2=False) == [(0,)]


async def test_the_auth_admin_port_resets_factors_in_the_fake_and_refuses_the_unverified_real_adapter():
    fake = FakeAuthAdmin()
    target = uuid.uuid4()
    await fake.reset_factors(target)
    await fake.reset_factors(target)
    assert fake.reset == {target}  # idempotent
    real = SupabaseAuthAdmin("https://example.supabase.co", "not-a-key", verified_on="2026-01-01")
    with pytest.raises(AuthAdminError):
        await real.reset_factors(target)  # no HTTP is written for an unverified endpoint
