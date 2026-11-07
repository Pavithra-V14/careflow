"""Migration 0061 against the real database: the correction fan-out of results (spec 9.5) and append-only clinical notes with addenda (spec 9.6), their erasure with the patient and their rotation to a new
key. Inside ONE never-committed outer transaction, as the restricted `careflow_app` role where the application is the actor.
"""

import base64
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
from careflow.crypto import DemographicVault
from careflow.messages import decrypt_note_text, encrypt_note_text
from careflow.rotation import note_version_usage, rotate_note_texts, verify_note_texts
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
KEYS = {f"CAREFLOW_{k}_SECRET_V{v}": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC") for v in (1, 2)}
VAULT = DemographicVault.from_env(KEYS)
INSERT_NOTE = (
    "insert into clinical.notes (id, clinic_id, origin, patient_id, author_id, parent_id, body_ct, body_key_version, idempotency_key) "
    "values (:i, :c, 'test', :p, :a, :par, :ct, cast(1 as smallint), :k)"
)


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
    ids = {k: uuid.uuid4() for k in ("clinic", "nurse", "doctor", "fd", "admin", "pat1", "pat2")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'CR-A', 'test')", ids["clinic"])
    for k in ("nurse", "doctor", "fd", "admin"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k, role in (("nurse", "nurse"), ("doctor", "doctor"), ("fd", "front_desk"), ("admin", "admin")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    for k in ("pat1", "pat2"):
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, 'test', $3)",
            ids[k], ids["clinic"], f"cr-{ids[k]}",
        )
    return ids


async def result(db, w, patient, key, *, supersedes=None, kind=None, code="DEMO-T1.a", valid_from="2026-01-01T00:00:00Z", value=5.0):
    h = await pg(db)
    import datetime as dt

    return await h.fetchval(
        "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, supersedes_id, change_kind, idempotency_key) "
        "values ($1, 'test', $2, $3, $4::jsonb, true, $5, $6, $7, $8, $9) returning id",
        w["clinic"], patient, code, json.dumps({"demo_value": value}), dt.datetime.fromisoformat(valid_from), w["doctor"], supersedes, kind, key,
    )


async def plan_with_closed_step(db, w, patient, evidence):
    """A care plan with one wait_result step already closed as met on `evidence` (written by the owner; the graph is tested elsewhere)."""
    h = await pg(db)
    plan = uuid.uuid4()
    await h.execute("insert into ops.care_plans (id, clinic_id, origin, patient_id, plan_ct, plan_key_version, created_by, idempotency_key) values ($1, $2, 'test', $3, decode(repeat('03', 32), 'hex'), 1, $4, $5)", plan, w["clinic"], patient, w["doctor"], f"pl-{plan}")
    await h.execute("insert into ops.care_plan_steps (clinic_id, plan_id, step_key, position, kind, label, result_prefix, idempotency_key) values ($1, $2, 'demo_test', 0, 'wait_result', 'DEMO wait', 'DEMO-T1', $3)", w["clinic"], plan, f"st-{plan}")
    await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(w["doctor"]), "role": "authenticated"})})
    await h.execute("update ops.care_plans set status = 'awaiting_confirmation' where id = $1", plan)
    await h.execute("update ops.care_plans set status = 'active' where id = $1", plan)
    await db.execute(text("select set_config('request.jwt.claims', '', true)"))
    await h.execute("insert into ops.care_plan_waits (clinic_id, plan_id, step_key, wait_key, patient_id, prefix, idempotency_key) values ($1, $2, 'demo_test', $3, $4, 'DEMO-T1', $5)", w["clinic"], plan, f"w-{plan}", patient, f"w-{plan}")
    await h.execute("update ops.care_plan_waits set claimed_result_id = $1 where plan_id = $2", evidence, plan)
    await h.execute("update ops.care_plan_steps set status = 'met', evidence_result_id = $1 where plan_id = $2", evidence, plan)
    return plan


async def test_the_kind_is_inferred_when_the_writer_cannot_say_and_an_amendment_raises_nothing(db):
    w = await world(db)
    first = await result(db, w, w["pat1"], "r1")
    amended = await result(db, w, w["pat1"], "r2", supersedes=first, code="DEMO-T1.a", valid_from="2026-02-01T00:00:00Z")
    kinds = dict((await db.execute(text("select id, change_kind from clinical.results where id in (:a, :b)"), {"a": first, "b": amended})).all())
    assert kinds[first] is None and kinds[amended] == "amendment"  # a new effective date: a new measurement
    corrected = await result(db, w, w["pat1"], "r3", supersedes=amended, valid_from="2026-02-01T00:00:00Z", value=6.0)
    assert (await db.execute(text("select change_kind from clinical.results where id = :i"), {"i": corrected})).scalar() == "correction"  # same test, same effective date
    declared = await result(db, w, w["pat1"], "r4", supersedes=corrected, kind="amendment", valid_from="2026-02-01T00:00:00Z")
    assert (await db.execute(text("select change_kind from clinical.results where id = :i"), {"i": declared})).scalar() == "amendment"  # the writer's word stands
    assert await owner(db, "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, change_kind, idempotency_key) values (:c, 'test', :p, 'X', '{}'::jsonb, true, now(), :d, 'correction', 'nosupersede')", c=w["clinic"], p=w["pat1"], d=w["doctor"]) == "23514"  # a kind without a supersession is meaningless (refused by the trigger)
    assert (await db.execute(text("select count(*) from agents.review_tasks where kind = 'result_correction' and clinic_id = :c"), {"c": w["clinic"]})).scalar() == 0  # no step cited any of them


async def test_a_correction_raises_a_review_task_for_each_decision_that_used_the_old_value_and_names_ids_only(db):
    w = await world(db)
    old = await result(db, w, w["pat1"], "o1")
    plan = await plan_with_closed_step(db, w, w["pat1"], old)
    other_old = await result(db, w, w["pat2"], "o2")
    await plan_with_closed_step(db, w, w["pat2"], other_old)
    new = await result(db, w, w["pat1"], "n1", supersedes=old, kind="correction", value=9.0)
    tasks = (await db.execute(text("select kind, priority, assigned_role, status, provenance from agents.review_tasks where source_message_id = :s"), {"s": f"result:{new}"})).all()
    assert len(tasks) == 1
    t = tasks[0]
    assert (t.kind, t.priority, t.assigned_role, t.status) == ("result_correction", "standard", "doctor", "open")
    assert t.provenance == {"old_result_id": str(old), "new_result_id": str(new), "plan_id": str(plan), "step_key": "demo_test", "decision": "met", "declared": "correction"}
    assert "9.0" not in json.dumps(t.provenance)  # ids and a fixed word, never a value
    assert (await db.execute(text("select count(*) from agents.review_tasks where kind = 'result_correction' and clinic_id = :c"), {"c": w["clinic"]})).scalar() == 1  # the other patient's decision is untouched
    amend = await result(db, w, w["pat2"], "n2", supersedes=other_old, kind="amendment", valid_from="2026-03-01T00:00:00Z")
    assert amend and (await db.execute(text("select count(*) from agents.review_tasks where kind = 'result_correction' and clinic_id = :c"), {"c": w["clinic"]})).scalar() == 1  # an amendment raises nothing
    # a DECLARED amendment for the same test and the same effective instant cannot silence the review
    third_old = await result(db, w, w["pat1"], "o3", valid_from="2026-04-01T00:00:00Z")
    await plan_with_closed_step(db, w, w["pat1"], third_old)
    sneaky = await result(db, w, w["pat1"], "n3", supersedes=third_old, kind="amendment", valid_from="2026-04-01T00:00:00Z")
    assert (await db.execute(text("select count(*) from agents.review_tasks where source_message_id = :s"), {"s": f"result:{sneaky}"})).scalar() == 1


async def test_notes_are_written_by_a_nurse_or_doctor_with_a_second_factor_and_addenda_stay_with_the_patient(db):
    w = await world(db)
    n1 = uuid.uuid4()
    ct = encrypt_note_text(VAULT, "DEMO note text", clinic_id=w["clinic"], note_id=n1, key_version=1)
    args = {"i": n1, "c": w["clinic"], "p": w["pat1"], "a": w["nurse"], "par": None, "ct": ct, "k": "n1"}
    assert await as_app(db, w["nurse"], INSERT_NOTE, **args) == 1
    assert await as_app(db, w["fd"], INSERT_NOTE, **{**args, "i": uuid.uuid4(), "a": w["fd"], "k": "x"}) == "42501"  # front desk writes no notes
    assert await as_app(db, w["admin"], INSERT_NOTE, **{**args, "i": uuid.uuid4(), "a": w["admin"], "k": "y"}) == "42501"
    assert await as_app(db, w["nurse"], INSERT_NOTE, aal2=False, **{**args, "i": uuid.uuid4(), "k": "z"}) == "42501"  # no second factor
    assert await as_app(db, w["nurse"], INSERT_NOTE, **{**args, "i": uuid.uuid4(), "a": w["doctor"], "k": "w"}) == "42501"  # only as yourself
    n2 = uuid.uuid4()
    assert await as_app(db, w["doctor"], INSERT_NOTE, **{**args, "i": n2, "a": w["doctor"], "par": n1, "k": "n2", "ct": encrypt_note_text(VAULT, "DEMO addendum", clinic_id=w["clinic"], note_id=n2, key_version=1)}) == 1
    assert await as_app(db, w["doctor"], INSERT_NOTE, **{**args, "i": uuid.uuid4(), "a": w["doctor"], "p": w["pat2"], "par": n1, "k": "cross"}) == "23503"  # an addendum names a note of the SAME patient
    seen = lambda who, aal2=True: as_app(db, w[who], "select count(*) from clinical.notes where patient_id = :p", aal2=aal2, p=w["pat1"])
    assert await seen("nurse") == [(2,)] and await seen("doctor") == [(2,)]
    assert await seen("fd") == [(0,)] and await seen("admin") == [(0,)] and await seen("nurse", aal2=False) == [(0,)]
    stored = bytes((await db.execute(text("select body_ct from clinical.notes where id = :i"), {"i": n1})).scalar())
    assert decrypt_note_text(VAULT, stored, clinic_id=w["clinic"], note_id=n1, key_version=1) == "DEMO note text" and b"DEMO note" not in stored


async def test_notes_cannot_be_edited_or_deleted_by_anyone_and_the_owner_may_only_erase_or_rotate(db):
    w = await world(db)
    n1 = uuid.uuid4()
    ct = encrypt_note_text(VAULT, "DEMO note text", clinic_id=w["clinic"], note_id=n1, key_version=1)
    assert await as_app(db, w["nurse"], INSERT_NOTE, i=n1, c=w["clinic"], p=w["pat1"], a=w["nurse"], par=None, ct=ct, k="n1") == 1
    for sql in ("update clinical.notes set body_ct = body_ct", "delete from clinical.notes", "update clinical.notes set erased_at = now()", "select count(*) from clinical.note_rotation_batches where true"):
        assert await as_app(db, w["doctor"], sql) == "42501", sql
    assert await owner(db, "update clinical.notes set author_id = :a where id = :i", a=w["doctor"], i=n1) == "23001"
    assert await owner(db, "delete from clinical.notes where id = :i", i=n1) == "23001"
    assert await owner(db, "truncate clinical.notes") in ("23001", "0A000")
    assert await owner(db, "update clinical.notes set body_ct = null, body_key_version = null, erased_at = now() where id = :i", i=n1) == 1  # the one erasure transition
    assert await owner(db, "update clinical.notes set erased_at = null where id = :i", i=n1) == "23001"  # final


async def test_the_audited_erasure_erases_the_patients_notes_and_counts_them(db):
    w = await world(db)
    h = await pg(db)
    for pat, key in (("pat1", "e1"), ("pat2", "e2")):
        n = uuid.uuid4()
        await h.execute("insert into clinical.notes (id, clinic_id, origin, patient_id, author_id, body_ct, body_key_version, idempotency_key) values ($1, $2, 'test', $3, $4, $5, 1, $6)", n, w["clinic"], w[pat], w["nurse"], encrypt_note_text(VAULT, "DEMO note", clinic_id=w["clinic"], note_id=n, key_version=1), key)
    fd2 = uuid.uuid4()
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", fd2)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", fd2, w["clinic"])
    request = (await as_app(db, w["fd"], "select mpi.request_patient_erasure(:p, :r, :k)", p=w["pat1"], r="entered_in_error", k="rq"))[0][0]
    assert await as_app(db, fd2, "select mpi.approve_patient_erasure(:q, :k)", q=request, k="ap") == [(True,)]
    states = {r[0]: (r[1], r[2]) for r in (await db.execute(text("select patient_id, body_ct is null, erased_at is not null from clinical.notes where clinic_id = :c"), {"c": w["clinic"]})).all()}
    assert states[w["pat1"]] == (True, True) and states[w["pat2"]] == (False, False)
    audit = (await db.execute(text("select details from audit.log where action = 'care_record.erased' and idempotency_key = :k"), {"k": f"sys:erase-care-record:{request}"})).scalar()
    assert audit["notes"] == 1


async def test_note_texts_rotate_to_a_new_key_with_a_ledger_row_and_erased_ones_are_skipped(db):
    w = await world(db)
    h = await pg(db)
    made = []
    for n in range(2):
        i = uuid.uuid4()
        await h.execute("insert into clinical.notes (id, clinic_id, origin, patient_id, author_id, body_ct, body_key_version, idempotency_key) values ($1, $2, 'test', $3, $4, $5, 1, $6)", i, w["clinic"], w["pat1"], w["nurse"], encrypt_note_text(VAULT, f"DEMO text {n}", clinic_id=w["clinic"], note_id=i, key_version=1), f"rot-{n}")
        made.append(i)
    gone = uuid.uuid4()
    await h.execute("insert into clinical.notes (id, clinic_id, origin, patient_id, author_id, body_ct, body_key_version, idempotency_key) values ($1, $2, 'test', $3, $4, $5, 1, 'rot-gone')", gone, w["clinic"], w["pat1"], w["nurse"], encrypt_note_text(VAULT, "x", clinic_id=w["clinic"], note_id=gone, key_version=1))
    assert await owner(db, "update clinical.notes set body_ct = null, body_key_version = null, erased_at = now() where id = :i", i=gone) == 1
    assert await note_version_usage(db, clinic_id=w["clinic"]) == {1: 2}
    report = await rotate_note_texts(db, VAULT, from_version=1, to_version=2, clinic_id=w["clinic"])
    assert report.texts_rotated == 2 and report.batches == 1
    for i, expected in zip(made, ("DEMO text 0", "DEMO text 1"), strict=True):
        row = (await db.execute(text("select body_ct, body_key_version from clinical.notes where id = :i"), {"i": i})).one()
        assert row.body_key_version == 2 and decrypt_note_text(VAULT, bytes(row.body_ct), clinic_id=w["clinic"], note_id=i, key_version=2) == expected
    assert (await verify_note_texts(db, VAULT, to_version=2, clinic_id=w["clinic"])).ok
    assert (await rotate_note_texts(db, VAULT, from_version=1, to_version=2, clinic_id=w["clinic"])).texts_rotated == 0
    assert (await db.execute(text("select texts_rotated from clinical.note_rotation_batches where clinic_id = :c"), {"c": w["clinic"]})).scalars().all() == [2]
