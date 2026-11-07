"""Migration 0065 against the real database: the findings of the set-wide review. Verified second factor on the erasure and staff-account functions; the recovery precedence fix; restrictive aal2 on three reads;
origin pinned on user-writable rows; guards on the autonomy proposals and the claim requests; the planted approval key. Inside ONE never-committed outer transaction, as `careflow_app`.
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
from tests.db_helpers import aal2_claims, ensure_app_role_sa

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


async def as_app(db, person, sql, aal2=True, **params):
    claims = aal2_claims(person) if aal2 else {"sub": str(person), "role": "authenticated"}
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)})
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
    ids = {k: uuid.uuid4() for k in ("clinic", "other_clinic", "admin1", "admin2", "fd1", "fd2", "nurse", "target", "patient", "patient_acct")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'H-A', 'test'), ($2, 'H-B', 'test')", ids["clinic"], ids["other_clinic"])
    for k in ("admin1", "admin2", "fd1", "fd2", "nurse", "target"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids["patient_acct"])
    for k, role in (("admin1", "admin"), ("admin2", "admin"), ("fd1", "front_desk"), ("fd2", "front_desk"), ("nurse", "nurse"), ("target", "nurse")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute(
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, 'test', $3)",
        ids["patient"], ids["clinic"], f"h-{ids['patient']}",
    )
    return ids


async def test_the_erasure_and_staff_account_functions_need_a_verified_second_factor(db):
    w = await world(db)
    assert await as_app(db, w["fd1"], "select mpi.request_patient_erasure(:p, 'entered_in_error', 'k1')", aal2=False, p=w["patient"]) == "42501"
    request = (await as_app(db, w["fd1"], "select mpi.request_patient_erasure(:p, 'entered_in_error', 'k2')", p=w["patient"]))[0][0]
    assert await as_app(db, w["fd2"], "select mpi.approve_patient_erasure(:q, 'ap')", aal2=False, q=request) == "42501"
    assert await as_app(db, w["fd1"], "select mpi.cancel_patient_erasure(:q)", aal2=False, q=request) == "42501"
    assert await as_app(db, w["fd2"], "select mpi.approve_patient_erasure(:q, 'ap')", q=request) == [(True,)]  # with the factor it still works
    new = uuid.uuid4()
    assert await as_app(db, w["admin1"], "select iam.invite_staff_account(:a, :c, 'nurse', 'inv')", aal2=False, a=new, c=w["clinic"]) == "42501"
    assert await as_app(db, w["admin1"], "select iam.grant_staff_role(:a, :c, 'doctor', 'gr')", aal2=False, a=w["nurse"], c=w["clinic"]) == "42501"
    assert await as_app(db, w["admin1"], "select iam.revoke_staff_role(:a, :c, 'nurse', 'rv')", aal2=False, a=w["nurse"], c=w["clinic"]) == "42501"
    assert await as_app(db, w["admin1"], "select iam.set_staff_account_status(:a, 'suspended', 'st')", aal2=False, a=w["nurse"]) == "42501"
    assert await as_app(db, w["admin1"], "select iam.grant_staff_role(:a, :c, 'doctor', 'gr2')", a=w["nurse"], c=w["clinic"]) == [(True,)]
    assert await as_app(db, w["target"], "select iam.activate_own_account()", aal2=False) == "42501"


async def test_cancelling_an_approved_recovery_still_works_after_the_account_gains_a_role_elsewhere(db):
    w = await world(db)
    h = await pg(db)
    rid = (await as_app(db, w["admin1"], "select request_id from iam.request_staff_recovery(:c, :t, 'rc1')", c=w["clinic"], t=w["target"]))[0][0]
    assert await as_app(db, w["admin2"], "select iam.resolve_staff_recovery(:r, 'approve')", r=rid) == [("approved",)]
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", w["target"], w["other_clinic"])
    assert await as_app(db, w["admin2"], "select iam.resolve_staff_recovery(:r, 'complete')", r=rid) == [("target_gone",)]  # approve and complete are stopped
    assert await as_app(db, w["admin1"], "select iam.resolve_staff_recovery(:r, 'cancel')", r=rid) == [("cancelled",)]  # a cancel is not


async def test_the_two_listings_need_a_verified_second_factor_and_the_claimant_reads_their_own(db):
    w = await world(db)
    h = await pg(db)
    await h.execute("insert into ops.autonomy_changes (clinic_id, origin, mode, changed_by, idempotency_key) values ($1, 'test', 'shadow', $2, 'a1')", w["clinic"], w["admin1"])
    await h.execute("insert into mpi.claim_requests (clinic_id, origin, account_id, status) values ($1, 'test', $2, 'pending')", w["clinic"], w["patient_acct"])
    assert await as_app(db, w["admin1"], "select count(*) from ops.autonomy_changes where clinic_id = :c", aal2=False, c=w["clinic"]) == [(1,)]  # the stored mode stays readable: it may only TIGHTEN the policy
    assert await as_app(db, w["fd1"], "select count(*) from mpi.claim_requests where clinic_id = :c", c=w["clinic"]) == [(1,)]
    assert await as_app(db, w["fd1"], "select count(*) from mpi.claim_requests where clinic_id = :c", aal2=False, c=w["clinic"]) == [(0,)]
    assert await as_app(db, w["patient_acct"], "select count(*) from mpi.claim_requests where account_id = :a", aal2=False, a=w["patient_acct"]) == [(1,)]  # a patient account reads its own without a staff factor


async def test_a_barrier_and_a_voice_row_cannot_hide_under_another_origin(db):
    w = await world(db)
    ins = "insert into ops.care_barriers (clinic_id, origin, patient_id, barrier, status, idempotency_key) values (:c, :o, :p, 'transport', 'open', :k)"
    assert await as_app(db, w["nurse"], ins, c=w["clinic"], o="live", p=w["patient"], k="b-live") == "42501"  # the clinic is `test`: a row tagged `live` is refused
    assert await as_app(db, w["nurse"], ins, c=w["clinic"], o="test", p=w["patient"], k="b-test") == 1
    assert await as_app(db, w["patient_acct"], "insert into msg.voice_preferences (account_id, origin, voice_enabled) values (:a, 'live', true)", aal2=False, a=w["patient_acct"]) == "42501"
    assert await as_app(db, w["patient_acct"], "insert into msg.voice_preferences (account_id, origin, voice_enabled) values (:a, 'test', true)", aal2=False, a=w["patient_acct"]) == 1


async def test_the_proposal_and_claim_request_guards_and_the_planted_approval_key(db):
    w = await world(db)
    h = await pg(db)
    proposal = await h.fetchval("insert into ops.autonomy_proposals (clinic_id, origin, proposed_by, proposer_role, idempotency_key) values ($1, 'test', $2, 'admin', 'p1') returning id", w["clinic"], w["admin1"])
    assert await owner(db, "update ops.autonomy_proposals set proposed_by = :b where id = :i", b=w["admin2"], i=proposal) == "23001"
    assert await owner(db, "update ops.autonomy_proposals set status = 'approved', decided_by = :d, decided_at = now() where id = :i", d=w["admin2"], i=proposal) == 1  # the one move the resolve function makes
    assert await owner(db, "update ops.autonomy_proposals set status = 'rejected' where id = :i", i=proposal) == "23001"  # and only once
    claim = await h.fetchval("insert into mpi.claim_requests (clinic_id, origin, account_id, status) values ($1, 'test', $2, 'pending') returning id", w["clinic"], w["patient_acct"])
    assert await owner(db, "update mpi.claim_requests set account_id = :a where id = :i", a=w["fd1"], i=claim) == "23001"
    assert await owner(db, "delete from mpi.claim_requests where id = :i", i=claim) == "23001"
    ins = "insert into ops.autonomy_changes (clinic_id, origin, mode, changed_by, idempotency_key) values (:c, 'test', 'shadow', :a, :k)"
    assert await as_app(db, w["admin1"], ins, c=w["clinic"], a=w["admin1"], k=f"autonomy-approve:{proposal}") == "42501"  # a planted approval key is refused
    assert await as_app(db, w["admin1"], ins, c=w["clinic"], a=w["admin1"], k="ordinary") == 1
