"""Migration 0049 against the real database, as the restricted `careflow_app` role inside ONE never-committed outer transaction: registration, versioned consent, activation and the claim of a patient record
(verified phone AND name+date-of-birth tokens, a confirmed record only, a lock after repeated failures, a pending request for the front desk)."""

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
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
PHONE, NAME_DOB, WRONG = b"\x01" * 32, b"\x02" * 32, b"\x03" * 32


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


async def pg(db):
    return (await db.get_raw_connection()).driver_connection


async def world(db):
    """A clinic (and another), a front desk, a nurse, an invited patient account, an active one, a confirmed record with tokens, an unconfirmed record with the same tokens, and two notices."""
    handle = await pg(db)
    a, b = uuid.uuid4(), uuid.uuid4()
    ids = {k: uuid.uuid4() for k in ("front_desk", "nurse", "invited", "active", "active2", "active3", "other_origin", "confirmed", "unconfirmed", "notice_old", "notice_new")}
    await handle.execute("insert into iam.clinics (id, name, origin) values ($1, 'PP A', 'test'), ($2, 'PP B', 'live')", a, b)
    await handle.execute(
        "insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test'), ($2, 'staff', 'active', 'test'), ($3, 'patient', 'invited', 'test'), ($4, 'patient', 'active', 'test'), "
        "($5, 'patient', 'active', 'test'), ($6, 'patient', 'active', 'test'), ($7, 'patient', 'active', 'live')",
        ids["front_desk"], ids["nurse"], ids["invited"], ids["active"], ids["active2"], ids["active3"], ids["other_origin"],
    )
    await handle.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test'), ($3, $2, 'nurse', 'test')", ids["front_desk"], a, ids["nurse"])
    for pid, confirmed in ((ids["confirmed"], True), (ids["unconfirmed"], False)):
        await handle.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', $3)", pid, a, f"pp-{pid}"
        )
        await handle.execute("insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin) values ($1, $2, 'phone', $3, 1, 'test'), ($1, $2, 'name_dob', $4, 1, 'test')", pid, a, PHONE if confirmed else WRONG, NAME_DOB if confirmed else WRONG)
        if confirmed:
            await handle.execute("insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key) values ($1, $2, $3, 'test', $4)", a, pid, ids["front_desk"], f"cf-{pid}")
    # the unconfirmed record carries the SAME tokens as the real person (a planted record): give it the same ones
    await handle.execute("update mpi.identity_tokens set token = $2 where patient_id = $1 and kind = 'phone'", ids["unconfirmed"], PHONE)
    await handle.execute("update mpi.identity_tokens set token = $2 where patient_id = $1 and kind = 'name_dob'", ids["unconfirmed"], NAME_DOB)
    await handle.execute("insert into iam.consent_notices (id, purpose, version, body) values ($1, 'care_delivery', 1, 'DEMO notice v1'), ($2, 'care_delivery', 2, 'DEMO notice v2')", ids["notice_old"], ids["notice_new"])
    for who in ("active", "active2", "active3", "other_origin"):  # these accounts have granted the CURRENT care-delivery notice (the claim checks it again)
        await handle.execute("insert into iam.consent_events (account_id, purpose, notice_id, action, idempotency_key) values ($1, 'care_delivery', $2, 'granted', $3)", ids[who], ids["notice_new"], f"w-{who}")
    return a, b, ids


CLAIM = "select mpi.claim_patient_record(:c, :p, :n, cast(1 as smallint))"


async def test_notices_are_readable_by_a_signed_in_session_and_never_writable_by_the_application_role(db):
    _, _, w = await world(db)
    rows = await state_of(db, w["active"], "select version from iam.consent_notices where purpose = 'care_delivery' order by version")
    assert rows == [(1,), (2,)]
    for sql in ("insert into iam.consent_notices (purpose, version, body) values ('care_delivery', 3, 'x')", "update iam.consent_notices set body = 'y'", "delete from iam.consent_notices"):
        assert await state_of(db, w["active"], sql) == "42501", sql


async def test_a_consent_event_names_the_current_notice_only_is_the_accounts_own_and_is_append_only(db):
    _, _, w = await world(db)
    ins = "insert into iam.consent_events (account_id, purpose, notice_id, action, idempotency_key) values (:a, 'care_delivery', :n, :x, :k)"
    assert await state_of(db, w["invited"], ins, a=w["invited"], n=w["notice_old"], x="granted", k="k1") == "42501"  # an older text is no consent
    assert await state_of(db, w["invited"], ins, a=w["invited"], n=w["notice_new"], x="granted", k="k1") == 1
    assert await state_of(db, w["invited"], ins, a=w["invited"], n=w["notice_new"], x="granted", k="k1") == "23505"  # the same key: a repeat
    assert await state_of(db, w["invited"], ins, a=w["active"], n=w["notice_new"], x="granted", k="k2") == "42501"  # not for another account
    assert await state_of(db, w["nurse"], ins, a=w["nurse"], n=w["notice_new"], x="granted", k="k3") == "42501"  # staff accounts do not consent here
    assert await state_of(db, w["invited"], "select count(*) from iam.consent_events where true") == [(1,)]
    assert await state_of(db, w["active"], "select count(*) from iam.consent_events where account_id <> :me", me=w["active"]) == [(0,)]  # nobody reads another account's events (it sees only its own grant from the setup)
    assert await state_of(db, w["invited"], "update iam.consent_events set action = 'withdrawn'") == "42501"
    assert await state_of(db, w["invited"], "delete from iam.consent_events") == "42501"
    assert await state_of(db, w["invited"], ins, a=w["invited"], n=w["notice_new"], x="withdrawn", k="k4") == 1  # withdrawal is one more event


async def test_registration_creates_an_invited_patient_account_once_and_refuses_a_staff_account(db):
    _, _, w = await world(db)
    fresh = uuid.uuid4()
    assert await state_of(db, fresh, "select iam.register_patient_account('test')") == [("registered",)]
    assert await state_of(db, fresh, "select iam.register_patient_account('test')") == [("exists",)]
    row = (await db.execute(text("select account_type, status, origin from iam.accounts where id = :i"), {"i": fresh})).one()
    assert tuple(row) == ("patient", "invited", "test")
    assert (await db.execute(text("select count(*) from audit.log where action = 'patient.register' and resource_id = :i"), {"i": str(fresh)})).scalar() == 1
    assert await state_of(db, w["nurse"], "select iam.register_patient_account('test')") == "42501"
    assert await state_of(db, uuid.uuid4(), "select iam.register_patient_account('nonsense')") == "42501"


async def test_activation_needs_the_current_care_delivery_grant_and_a_withdrawal_blocks_it(db):
    _, _, w = await world(db)
    ins = "insert into iam.consent_events (account_id, purpose, notice_id, action, idempotency_key) values (:a, 'care_delivery', :n, :x, :k)"
    assert await state_of(db, w["invited"], "select iam.activate_patient_account()") == [("consent_required",)]
    assert await state_of(db, w["invited"], ins, a=w["invited"], n=w["notice_new"], x="granted", k="a1") == 1
    assert await state_of(db, w["invited"], ins, a=w["invited"], n=w["notice_new"], x="withdrawn", k="a2") == 1
    assert await state_of(db, w["invited"], "select iam.activate_patient_account()") == [("consent_required",)]  # the newest event is a withdrawal
    assert await state_of(db, w["invited"], ins, a=w["invited"], n=w["notice_new"], x="granted", k="a3") == 1
    assert await state_of(db, w["invited"], "select iam.activate_patient_account()") == [("activated",)]
    assert (await db.execute(text("select status from iam.accounts where id = :i"), {"i": w["invited"]})).scalar() == "active"
    assert await state_of(db, w["invited"], "select iam.activate_patient_account()") == [("already_active",)]
    assert await state_of(db, w["nurse"], "select iam.activate_patient_account()") == "42501"


async def test_a_claim_needs_both_tokens_and_a_confirmed_record_and_binds_the_account(db):
    a, _, w = await world(db)
    assert await state_of(db, w["active"], CLAIM, c=a, p=PHONE, n=WRONG) == [("pending_review",)]  # right phone, wrong name and date of birth
    assert await state_of(db, w["active"], CLAIM, c=a, p=WRONG, n=NAME_DOB) == [("pending_review",)]
    assert (await db.execute(text("select count(*) from mpi.claim_requests where account_id = :i and status = 'pending'"), {"i": w["active"]})).scalar() == 1  # one pending request per account and clinic
    assert await state_of(db, w["active2"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == [("claimed",)]
    bound = (await db.execute(text("select id from mpi.patients where account_id = :i"), {"i": w["active2"]})).scalars().all()
    assert bound == [w["confirmed"]]  # the CONFIRMED record, never the unconfirmed one with the same tokens
    assert (await db.execute(text("select count(*) from audit.log where action = 'patient.claim' and actor_id = :i"), {"i": w["active2"]})).scalar() == 1
    assert await state_of(db, w["active2"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == [("already_bound",)]
    assert await state_of(db, w["active3"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == [("pending_review",)]  # the record is taken: the same word as no match


async def test_the_account_is_locked_after_five_failed_claims_and_the_lock_never_reveals_a_match(db):
    a, _, w = await world(db)
    for _ in range(5):
        assert await state_of(db, w["active"], CLAIM, c=a, p=WRONG, n=WRONG) == [("pending_review",)]
    assert await state_of(db, w["active"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == [("locked",)]  # even the right details, while locked
    assert (await db.execute(text("select count(*) from mpi.patients where account_id = :i"), {"i": w["active"]})).scalar() == 0


async def test_a_revoked_confirmation_a_withdrawn_consent_and_a_fresh_account_do_not_open_the_gate(db):
    a, _, w = await world(db)
    handle = await pg(db)
    await handle.execute("insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key, kind, reason) values ($1, $2, $3, 'test', $4, 'revoke', 'planted_or_suspect')", a, w["confirmed"], w["front_desk"], f"rv-{uuid.uuid4()}")
    assert await state_of(db, w["active"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == [("pending_review",)]  # confirmed once, then revoked: not claimable
    assert (await db.execute(text("select count(*) from mpi.patients where account_id is not null and id = :i"), {"i": w["confirmed"]})).scalar() == 0
    await handle.execute("insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key) values ($1, $2, $3, 'test', $4)", a, w["confirmed"], w["front_desk"], f"rc-{uuid.uuid4()}")
    ins = "insert into iam.consent_events (account_id, purpose, notice_id, action, idempotency_key) values (:a, 'care_delivery', :n, 'withdrawn', 'wd')"
    assert await state_of(db, w["active2"], ins, a=w["active2"], n=w["notice_new"]) == 1
    assert await state_of(db, w["active2"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == [("consent_required",)]  # a withdrawal after activation stops the claim
    assert await state_of(db, w["active"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == [("claimed",)]  # re-confirmed and still consenting


async def test_the_failure_limit_follows_the_phone_token_so_new_accounts_do_not_reset_it(db):
    a, _, w = await world(db)
    for _ in range(5):
        assert await state_of(db, w["active"], CLAIM, c=a, p=WRONG, n=WRONG) == [("pending_review",)]
    assert await state_of(db, w["active2"], CLAIM, c=a, p=WRONG, n=NAME_DOB) == [("locked",)]  # a different account with the same phone token is locked too
    assert await state_of(db, w["active3"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == [("claimed",)]  # another phone is unaffected


async def test_a_claim_is_refused_for_a_non_patient_an_invited_account_an_unknown_clinic_a_different_origin_and_a_malformed_token(db):
    a, b, w = await world(db)
    for who in ("nurse", "invited", "front_desk"):
        assert await state_of(db, w[who], CLAIM, c=a, p=PHONE, n=NAME_DOB) == "42501", who
    assert await state_of(db, w["active"], CLAIM, c=uuid.uuid4(), p=PHONE, n=NAME_DOB) == "42501"
    assert await state_of(db, w["active"], CLAIM, c=b, p=PHONE, n=NAME_DOB) == "42501"  # a clinic of another origin
    assert await state_of(db, w["other_origin"], CLAIM, c=a, p=PHONE, n=NAME_DOB) == "42501"
    assert await state_of(db, w["active"], CLAIM, c=a, p=b"short", n=NAME_DOB) == "42501"
    assert await state_of(db, w["active"], "select mpi.claim_patient_record(:c, :p, :n, cast(0 as smallint))", c=a, p=PHONE, n=NAME_DOB) == "42501"


async def test_claim_tables_follow_the_visibility_rules_and_the_functions_are_closed_to_everyone_else(db):
    a, _, w = await world(db)
    assert await state_of(db, w["active"], CLAIM, c=a, p=WRONG, n=WRONG) == [("pending_review",)]
    assert await state_of(db, w["front_desk"], "select count(*) from mpi.claim_requests where clinic_id = :c", c=a) == [(1,)]  # front desk of the clinic sees pending requests
    assert await state_of(db, w["nurse"], "select count(*) from mpi.claim_requests where clinic_id = :c", c=a) == [(0,)]
    assert await state_of(db, w["active2"], "select count(*) from mpi.claim_requests where clinic_id = :c", c=a) == [(0,)]  # another claimant does not see it
    assert await state_of(db, w["active"], "select count(*) from mpi.claim_attempts where true") == "42501"  # no grant at all
    assert await state_of(db, w["front_desk"], "insert into mpi.claim_requests (clinic_id, origin, account_id) values (:c, 'test', :i)", c=a, i=w["active"]) == "42501"
    for fn in ("iam.register_patient_account(text)", "iam.activate_patient_account()", "mpi.claim_patient_record(uuid,bytea,bytea,smallint)"):
        for role, expected in (("public", False), ("careflow_app", True), ("anon", False), ("authenticated", False), ("service_role", False)):
            if not (await db.execute(text("select exists (select 1 from pg_roles where rolname = :r) or :r = 'public'"), {"r": role})).scalar():
                continue
            assert (await db.execute(text("select has_function_privilege(:r, :f, 'execute')"), {"r": role, "f": fn})).scalar() is expected, (fn, role)
