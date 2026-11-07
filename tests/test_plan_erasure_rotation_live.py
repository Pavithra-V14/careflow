"""Migration 0056 against the real database: the audited two-person erasure also erases a patient's care-plan text, step labels and result measures in the same transaction; the guards allow exactly the erasure
and rotation transitions and nothing else (even for the owner); and the care-plan text rotates to a new key version through careflow/rotation.py with a ledger row, skipping erased plans. Inside ONE never-committed
outer transaction, as the restricted `careflow_app` role where the application is meant to be the actor."""

import base64
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
from careflow.crypto import DemographicVault
from careflow.messages import decrypt_plan_text, encrypt_plan_text
from careflow.rotation import plan_version_usage, rotate_plan_texts, verify_plan_texts
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
KEYS = {f"CAREFLOW_{k}_SECRET_V{v}": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC") for v in (1, 2)}
VAULT = DemographicVault.from_env(KEYS)
REQUEST = "select mpi.request_patient_erasure(:p, :r, :k)"
APPROVE = "select mpi.approve_patient_erasure(:q, :k)"


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
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(person), "role": "authenticated"})})
            result = await db.execute(text(sql), params)
            value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def owner(db, sql, **params):
    """A statement as the owner inside a savepoint: the value, or the SQLSTATE of the refusal."""
    try:
        async with db.begin_nested():
            result = await db.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "fd1", "fd2", "doctor", "patient", "other_patient")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'ER-P', 'test')", ids["clinic"])
    for k in ("fd1", "fd2", "doctor"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $4, 'front_desk', 'test'), ($2, $4, 'front_desk', 'test'), ($3, $4, 'doctor', 'test')", ids["fd1"], ids["fd2"], ids["doctor"], ids["clinic"])
    for k in ("patient", "other_patient"):
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, 'test', $3)",
            ids[k], ids["clinic"], f"er-{ids[k]}",
        )
    return ids


async def make_plan(db, w, patient, text_, version=1):
    h = await pg(db)
    plan = uuid.uuid4()
    ct = encrypt_plan_text(VAULT, text_, clinic_id=w["clinic"], plan_id=plan, key_version=version)
    await h.execute(
        "insert into ops.care_plans (id, clinic_id, origin, patient_id, plan_ct, plan_key_version, created_by, idempotency_key) values ($1, $2, 'test', $3, $4, $5, $6, $7)",
        plan, w["clinic"], patient, ct, version, w["doctor"], f"pl-{plan}",
    )
    await h.execute("insert into ops.care_plan_steps (clinic_id, plan_id, step_key, position, kind, label, idempotency_key) values ($1, $2, 'book_review', 0, 'task', 'DEMO book a review visit', $3)", w["clinic"], plan, f"st-{plan}")
    return plan


async def make_result(db, w, patient, key):
    h = await pg(db)
    return await h.fetchval(
        "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, idempotency_key) values ($1, 'test', $2, 'DEMO-T1.a', '{\"demo_value\": 5.5}'::jsonb, true, now(), $3, $4) returning id",
        w["clinic"], patient, w["doctor"], key,
    )


async def test_the_audited_erasure_also_erases_plan_text_step_labels_and_measures_of_that_patient_only(db):
    w = await world(db)
    mine, other = await make_plan(db, w, w["patient"], "DEMO plan text"), await make_plan(db, w, w["other_patient"], "DEMO other plan")
    r1, r2, r_other = await make_result(db, w, w["patient"], "r1"), await make_result(db, w, w["patient"], "r2"), await make_result(db, w, w["other_patient"], "r3")
    request = (await as_app(db, w["fd1"], REQUEST, p=w["patient"], r="entered_in_error", k="rq"))[0][0]
    assert await as_app(db, w["fd1"], APPROVE, q=request, k="self") == "42501"  # the requester cannot approve (unchanged)
    assert await as_app(db, w["fd2"], APPROVE, q=request, k="ap") == [(True,)]
    plan = (await db.execute(text("select plan_ct is null, plan_key_version is null, erased_at is not null from ops.care_plans where id = :i"), {"i": mine})).one()
    assert tuple(plan) == (True, True, True)
    assert (await db.execute(text("select label from ops.care_plan_steps where plan_id = :i"), {"i": mine})).scalar() == "erased"
    rows = (await db.execute(text("select measures::text, erased_at is not null from clinical.results where patient_id = :p order by idempotency_key"), {"p": w["patient"]})).all()
    assert [tuple(r) for r in rows] == [("{}", True), ("{}", True)] and r1 != r2
    kept = (await db.execute(text("select plan_ct is not null, erased_at is null from ops.care_plans where id = :i"), {"i": other})).one()
    assert tuple(kept) == (True, True)  # another patient's plan is untouched
    assert (await db.execute(text("select label from ops.care_plan_steps where plan_id = :i"), {"i": other})).scalar() == "DEMO book a review visit"
    assert (await db.execute(text("select measures ->> 'demo_value', erased_at is null from clinical.results where id = :i"), {"i": r_other})).one() == ("5.5", True)
    audit = (await db.execute(text("select details from audit.log where action = 'care_record.erased' and idempotency_key = :k"), {"k": f"sys:erase-care-record:{request}"})).scalar()
    assert audit == {"care_plans": 1, "results": 2}  # counts only, never content
    assert [a for (a,) in (await db.execute(text("select action from audit.log where resource_id = :p and action like 'patient.erase%' order by id"), {"p": str(w["patient"])})).all()] == ["patient.erase_request", "patient.erase"]
    assert await as_app(db, w["fd2"], APPROVE, q=request, k="again") == [(False,)]  # a replay does nothing more
    assert (await db.execute(text("select count(*) from audit.log where action = 'care_record.erased' and idempotency_key = :k and true"), {"k": f"sys:erase-care-record:{request}"})).scalar() == 1


async def test_the_guards_allow_only_the_erasure_and_rotation_transitions_even_for_the_owner(db):
    w = await world(db)
    plan = await make_plan(db, w, w["patient"], "DEMO plan text")
    result = await make_result(db, w, w["patient"], "g1")
    assert await owner(db, "update ops.care_plans set plan_ct = '\\x09'::bytea where id = :i", i=plan) == "23001"  # a text change without a key change is neither transition
    assert await owner(db, "update ops.care_plans set plan_ct = null where id = :i", i=plan) == "23001"  # nulling without the erased mark is not erasure
    assert await owner(db, "update ops.care_plans set status = 'held' where id = :i", i=plan) == 1  # ordinary transitions still work
    assert await owner(db, "update ops.care_plans set plan_ct = null, plan_key_version = null, erased_at = now(), status = 'screening' where id = :i", i=plan) == "23001"  # erasure changes nothing else
    assert await owner(db, "update clinical.results set measures = '{\"demo_value\": 9}'::jsonb where id = :i", i=result) == "23001"
    assert await owner(db, "update clinical.results set measures = '{}'::jsonb, erased_at = now(), verified = false where id = :i", i=result) == "23001"
    assert await owner(db, "delete from clinical.results where id = :i", i=result) == "23001"
    assert await owner(db, "update clinical.results set measures = '{}'::jsonb, erased_at = now() where id = :i", i=result) == 1  # the one transition
    assert await owner(db, "update clinical.results set erased_at = null where id = :i", i=result) == "23001"  # irreversible
    assert await owner(db, "update ops.care_plan_steps set label = 'something else' where plan_id = :i", i=plan) == "23001"
    assert await owner(db, "update ops.care_plan_steps set label = 'erased' where plan_id = :i", i=plan) == 1
    for table in ("clinical.results", "ops.care_plans", "ops.care_plan_steps"):
        assert await owner(db, f"truncate {table} cascade") in ("23001", "0A000"), table  # never emptied, by anyone
    for sql in ("update ops.care_plans set plan_ct = '\\x09'::bytea", "update ops.care_plans set erased_at = now()", "update ops.care_plan_steps set label = 'erased'", "update clinical.results set measures = '{}'::jsonb", "update clinical.results set erased_at = now()"):
        assert await as_app(db, w["doctor"], sql) == "42501", sql  # the application role can write none of them


async def test_an_erased_plan_is_final_and_the_graph_ports_treat_it_as_unreadable_not_clear(db):
    w = await world(db)
    plan = await make_plan(db, w, w["patient"], "DEMO plan text")
    assert await owner(db, "update ops.care_plans set plan_ct = null, plan_key_version = null, erased_at = now() where id = :i", i=plan) == 1
    assert await owner(db, "update ops.care_plans set status = 'held' where id = :i", i=plan) == "23001"
    from careflow.care_journey_sql import SqlPlanParser, SqlPlanScreener

    class _Engine:
        async def evaluate_text(self, *_args, **_kwargs):
            raise AssertionError("an erased plan must never reach the rules as if it were clear")

    async def model(_text):
        raise AssertionError("an erased plan must never reach a model")

    assert await SqlPlanScreener(VAULT, _Engine()).screen(db, plan_id=plan, clinic_id=w["clinic"]) == "held_unassessable"  # type: ignore[arg-type]
    assert await SqlPlanParser(VAULT, model).parse(db, plan_id=plan, clinic_id=w["clinic"]) is None


async def test_plan_text_rotates_to_a_new_key_with_a_ledger_row_skips_erased_plans_and_is_owner_only(db):
    w = await world(db)
    a, b = await make_plan(db, w, w["patient"], "DEMO plan A"), await make_plan(db, w, w["other_patient"], "DEMO plan B")
    gone = await make_plan(db, w, w["patient"], "DEMO plan gone")
    assert await owner(db, "update ops.care_plans set plan_ct = null, plan_key_version = null, erased_at = now() where id = :i", i=gone) == 1
    before = await plan_version_usage(db, clinic_id=w["clinic"])
    assert before == {1: 2}  # the erased plan is not counted
    report = await rotate_plan_texts(db, VAULT, from_version=1, to_version=2, clinic_id=w["clinic"])
    assert report.plans_rotated == 2 and report.batches == 1 and len(report.batch_keys) == 1
    for plan, expected in ((a, "DEMO plan A"), (b, "DEMO plan B")):
        row = (await db.execute(text("select plan_ct, plan_key_version from ops.care_plans where id = :i"), {"i": plan})).one()
        assert row.plan_key_version == 2 and decrypt_plan_text(VAULT, bytes(row.plan_ct), clinic_id=w["clinic"], plan_id=plan, key_version=2) == expected
    assert (await verify_plan_texts(db, VAULT, to_version=2, clinic_id=w["clinic"])).ok
    assert await plan_version_usage(db, clinic_id=w["clinic"]) == {2: 2}
    again = await rotate_plan_texts(db, VAULT, from_version=1, to_version=2, clinic_id=w["clinic"])
    assert again.plans_rotated == 0  # the selection is the progress marker: a re-run touches nothing
    ledger = (await db.execute(text("select plans_rotated, from_version, to_version from ops.plan_rotation_batches where clinic_id = :c"), {"c": w["clinic"]})).all()
    assert [tuple(r) for r in ledger] == [(2, 1, 2)]
    assert (await db.execute(text("select plan_ct is null from ops.care_plans where id = :i"), {"i": gone})).scalar() is True
    assert await as_app(db, w["doctor"], "select count(*) from ops.plan_rotation_batches where true") == "42501"  # owner-only ledger
    assert await as_app(db, w["doctor"], "update ops.care_plans set plan_ct = '\\x09'::bytea, plan_key_version = 1") == "42501"
    assert await owner(db, "update ops.care_plans set plan_key_version = 2, plan_ct = plan_ct where id = :i", i=a) == "23001"  # a key-version change with the same ciphertext is not a rotation
