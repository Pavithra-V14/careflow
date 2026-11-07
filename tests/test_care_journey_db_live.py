"""The Care Journey graph over its SQL ports (careflow/care_journey_sql.py, migration 0050) against the real database, as the restricted `careflow_app` role with verified claims, inside ONE never-committed
outer transaction. The model is a stand-in that returns a closed-schema proposal; the rules screening is a stand-in that says `clear` (its decrypt-and-escalate logic is covered with a fake engine below). What is
proved here: nothing activates before a doctor confirms, a result wakes only the plan whose wait it matches, a met condition closes its step citing the result and proposes a referral, a report with no value goes
to a doctor and never closes the loop, no approved model means the plan is held for a person, and the stored tables refuse what the graph must never do."""

import base64
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import care_journey_sql as cj
from careflow.care_gap_sql import sqlstate
from careflow.crypto import DemographicVault
from careflow.rules import RedFlagEngine
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
VF = datetime(2026, 1, 1, tzinfo=UTC)
VAULT = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
GOOD = {
    "steps": [
        {"key": "book_review", "kind": "task", "label": "DEMO book a review visit"},
        {"key": "demo_test", "kind": "wait_result", "label": "DEMO wait for the test", "result_prefix": "DEMO-T1", "condition": {"field": "demo_value", "op": "lt", "threshold": 10}, "on_met": "propose_referral"},
    ]
}


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


def aal2():
    return {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 5}]}


@asynccontextmanager
async def as_user(db, person, claims=None):
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(person), "role": "authenticated", **(aal2() if claims is None else claims)})})
        yield db
        await db.exec_driver_sql("reset role")


async def state_of(db, person, sql, claims=None, **params):
    try:
        async with as_user(db, person, claims) as c:
            result = await c.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


class Clear:
    async def screen(self, conn, *, plan_id, clinic_id):
        return "clear"


def components(model, monkeypatch):
    monkeypatch.setenv("FEATURE_CARE_JOURNEY", "1")
    return cj.CareJourneyComponents(VAULT, RedFlagEngine(), model, "standard", "doctor", InMemorySaver(), screener=Clear())


async def world(db):
    handle = (await db.get_raw_connection()).driver_connection
    clinic, other = uuid.uuid4(), uuid.uuid4()
    doctor, nurse, doctor_b, patient = (uuid.uuid4() for _ in range(4))
    await handle.execute("insert into iam.clinics (id, name, origin) values ($1, 'CJ A', 'test'), ($2, 'CJ B', 'test')", clinic, other)
    await handle.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test'), ($2, 'staff', 'active', 'test'), ($3, 'staff', 'active', 'test')", doctor, nurse, doctor_b)
    await handle.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $4, 'doctor', 'test'), ($2, $4, 'nurse', 'test'), ($3, $5, 'doctor', 'test')", doctor, nurse, doctor_b, clinic, other)
    await handle.execute("insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', $3)", patient, clinic, f"cj-{patient}")
    return clinic, doctor, nurse, doctor_b, patient


async def make_plan(c, comps, clinic, doctor, patient, key):
    plan_id, created = await cj.create_plan(c, VAULT, clinic_id=clinic, origin="test", patient_id=patient, plan_text="DEMO plan text", created_by=doctor, key_version=1, idempotency_key=key)
    view = await comps.runner(c, clinic_id=clinic, origin="test").start(clinic_id=clinic, plan_id=plan_id, patient_id=patient, origin="test")
    return plan_id, created, view


async def test_the_whole_path_confirm_then_a_matching_good_result_closes_the_step_and_proposes_a_referral(db, monkeypatch):
    clinic, doctor, nurse, _, patient = await world(db)
    comps = components(lambda text_: _async(GOOD), monkeypatch)
    async with as_user(db, doctor) as c:
        plan_id, created, view = await make_plan(c, comps, clinic, doctor, patient, "p1")
        assert created and view.status == "confirmation_requested" and view.waiting_for == "confirmation"
        assert (await c.execute(text("select status from ops.care_plans where id = :i"), {"i": plan_id})).scalar() == "awaiting_confirmation"
        assert (await c.execute(text("select count(*) from ops.care_plan_steps where plan_id = :i"), {"i": plan_id})).scalar() == 2
        assert (await c.execute(text("select count(*) from ops.care_plan_waits where plan_id = :i"), {"i": plan_id})).scalar() == 0  # nothing runs before a doctor confirms
        again_id, again_created = await cj.create_plan(c, VAULT, clinic_id=clinic, origin="test", patient_id=patient, plan_text="DEMO plan text", created_by=doctor, key_version=1, idempotency_key="p1")
        assert (again_id, again_created) == (plan_id, False)
        view = await comps.runner(c, clinic_id=clinic, origin="test").confirm(plan_id, "approve")
        assert view.status == "waiting" and view.waiting_for == "result"
        assert (await c.execute(text("select status from ops.care_plans where id = :i"), {"i": plan_id})).scalar() == "active"
        with pytest.raises(cj.WrongStep):
            await comps.runner(c, clinic_id=clinic, origin="test").confirm(plan_id, "approve")  # not waiting for a confirmation any more
    async with as_user(db, nurse) as c:
        wrong, _ = await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="OTHER-1", measures={"demo_value": 1.0}, verified=True, valid_from=VF, valid_to=None, supersedes_id=None, recorded_by=nurse, idempotency_key="r0")
        assert await cj.plans_waiting_for(c, clinic_id=clinic, patient_id=patient, code="OTHER-1") == []  # a different test wakes nothing
        rid, created = await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="DEMO-T1.a", measures={"demo_value": 5.0}, verified=True, valid_from=VF, valid_to=None, supersedes_id=None, recorded_by=nurse, idempotency_key="r1")
        assert created and wrong != rid
        assert await cj.plans_waiting_for(c, clinic_id=clinic, patient_id=patient, code="DEMO-T1.a") == [plan_id]
    async with as_user(db, doctor) as c:
        view = await comps.runner(c, clinic_id=clinic, origin="test").deliver_result(plan_id, rid)
        assert view.status == "completed" and view.task_id is not None  # the referral is proposed (a task), then the last step is done and the plan completes
        step = (await c.execute(text("select status, evidence_result_id from ops.care_plan_steps where plan_id = :i and step_key = 'demo_test'"), {"i": plan_id})).one()
        assert tuple(step) == ("met", rid)  # closed as met, citing the result that justified it
        assert (await c.execute(text("select open, claimed_result_id from ops.care_plan_waits where plan_id = :i"), {"i": plan_id})).one() == (False, rid)
        assert (await c.execute(text("select kind, assigned_role from agents.review_tasks where id = :i"), {"i": view.task_id})).one() == ("care_plan_referral_proposal", "doctor")
        assert await cj.plans_waiting_for(c, clinic_id=clinic, patient_id=patient, code="DEMO-T1.a") == []  # the wait is closed: a second result does not wake the plan again


async def test_a_report_with_no_value_goes_to_a_doctor_and_the_step_is_never_closed_as_not_met(db, monkeypatch):
    clinic, doctor, nurse, _, patient = await world(db)
    comps = components(lambda text_: _async(GOOD), monkeypatch)
    async with as_user(db, doctor) as c:
        plan_id, _, _ = await make_plan(c, comps, clinic, doctor, patient, "p2")
        await comps.runner(c, clinic_id=clinic, origin="test").confirm(plan_id, "approve")
    async with as_user(db, nurse) as c:
        rid, _ = await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="DEMO-T1.b", measures={}, verified=True, valid_from=VF, valid_to=None, supersedes_id=None, recorded_by=nurse, idempotency_key="r2")
    async with as_user(db, doctor) as c:
        view = await comps.runner(c, clinic_id=clinic, origin="test").deliver_result(plan_id, rid)
        assert view.status == "needs_doctor" and view.task_id is not None
        assert (await c.execute(text("select status from ops.care_plan_steps where plan_id = :i and step_key = 'demo_test'"), {"i": plan_id})).scalar() == "open"  # never closed as not met
        assert (await c.execute(text("select kind from agents.review_tasks where id = :i"), {"i": view.task_id})).scalar() == "care_plan_needs_doctor"


async def test_with_no_approved_model_the_plan_is_held_for_a_person_and_a_rejected_plan_is_final(db, monkeypatch):
    clinic, doctor, _, _, patient = await world(db)
    comps = components(None, monkeypatch)
    async with as_user(db, doctor) as c:
        plan_id, _, view = await make_plan(c, comps, clinic, doctor, patient, "p3")
        assert view.status == "plan_invalid" and view.waiting_for == "none" and view.task_id is not None
        assert (await c.execute(text("select status from ops.care_plans where id = :i"), {"i": plan_id})).scalar() == "held"
        assert (await c.execute(text("select count(*) from ops.care_plan_steps where plan_id = :i"), {"i": plan_id})).scalar() == 0  # nothing was guessed
    comps2 = components(lambda text_: _async(GOOD), monkeypatch)
    async with as_user(db, doctor) as c:
        plan2, _, _ = await make_plan(c, comps2, clinic, doctor, patient, "p4")
        view = await comps2.runner(c, clinic_id=clinic, origin="test").confirm(plan2, "reject")
        assert view.status == "rejected"
    assert await state_of(db, doctor, "update ops.care_plans set status = 'active' where id = :i", i=plan2) == "23001"


async def test_who_may_write_what_and_what_the_tables_refuse(db, monkeypatch):
    clinic, doctor, nurse, doctor_b, patient = await world(db)
    comps = components(lambda text_: _async(GOOD), monkeypatch)
    async with as_user(db, doctor) as c:
        plan_id, _, _ = await make_plan(c, comps, clinic, doctor, patient, "p5")
    insert_plan = "insert into ops.care_plans (clinic_id, origin, patient_id, plan_ct, plan_key_version, created_by, idempotency_key) values (:c, 'test', :p, '\\x01'::bytea, 1, :by, :k)"
    assert await state_of(db, nurse, insert_plan, c=clinic, p=patient, by=nurse, k="n1") == "42501"  # only a doctor writes a plan
    assert await state_of(db, doctor_b, insert_plan, c=clinic, p=patient, by=doctor_b, k="n2") == "42501"  # not another clinic's doctor
    assert await state_of(db, doctor, insert_plan, c=clinic, p=patient, by=doctor, k="n3") == 1
    assert await state_of(db, doctor, insert_plan, claims={"aal": "aal1", "amr": []}, c=clinic, p=patient, by=doctor, k="n4") == "42501"  # no verified second factor: the database refuses
    assert await state_of(db, doctor, "select count(*) from ops.care_plans where clinic_id = :c", c=clinic) == [(2,)]
    assert await state_of(db, doctor_b, "select count(*) from ops.care_plans where clinic_id = :c", c=clinic) == [(0,)]  # another clinic sees nothing
    assert await state_of(db, nurse, "update ops.care_plans set plan_ct = '\\x09'::bytea where id = :i", i=plan_id) == "42501"  # only the status is writable
    assert await state_of(db, nurse, "update ops.care_plans set status = 'active' where id = :i", i=plan_id) == "42501"  # a nurse cannot confirm the doctor's plan
    assert await state_of(db, doctor, "update ops.care_plans set status = 'completed' where id = :i", i=plan_id) == "23001"  # not a legal step from awaiting_confirmation
    assert await state_of(db, doctor, "update ops.care_plans set status = 'rejected' where id = :i", i=plan_id) == 1
    assert await state_of(db, doctor, "update ops.care_plans set status = 'active' where id = :i", i=plan_id) == "23001"  # a finished plan is final
    assert await state_of(db, doctor, "delete from ops.care_plans") == "42501"
    res = "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, idempotency_key) values (:c, 'test', :p, 'DEMO-T1.z', '{}'::jsonb, true, now(), :by, :k)"
    assert await state_of(db, nurse, res, c=clinic, p=patient, by=nurse, k="x1") == 1
    assert await state_of(db, nurse, res, claims={"aal": "aal1", "amr": []}, c=clinic, p=patient, by=nurse, k="x2") == "42501"
    assert await state_of(db, doctor_b, res, c=clinic, p=patient, by=doctor_b, k="x3") == "42501"
    assert await state_of(db, nurse, "update clinical.results set verified = false") == "42501"
    assert await state_of(db, nurse, "delete from clinical.results") == "42501"
    assert (await state_of(db, nurse, "select count(*) from clinical.results where clinic_id = :c", c=clinic)) == [(1,)]


async def test_a_step_closes_only_on_a_verified_result_of_the_plans_own_patient_and_a_wait_is_claimed_only_by_that_patient(db, monkeypatch):
    clinic, doctor, nurse, _, patient = await world(db)
    handle = (await db.get_raw_connection()).driver_connection
    other = uuid.uuid4()
    await handle.execute("insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, '\x01'::bytea, 1, '\x01'::bytea, 1, 'test', $3)", other, clinic, f"cj-{other}")
    comps = components(lambda text_: _async(GOOD), monkeypatch)
    async with as_user(db, doctor) as c:
        plan_id, _, _ = await make_plan(c, comps, clinic, doctor, patient, "p6")
        await comps.runner(c, clinic_id=clinic, origin="test").confirm(plan_id, "approve")
        add_step = "insert into ops.care_plan_steps (clinic_id, plan_id, step_key, position, kind, label, idempotency_key) values (:c, :p, 'late_step', 9, 'task', 'DEMO late', 'late')"
    assert await state_of(db, doctor, add_step, c=clinic, p=plan_id) == "23001"  # an active plan's steps are closed
    async with as_user(db, nurse) as c:
        mine_unverified, _ = await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="DEMO-T1.u", measures={"demo_value": 1.0}, verified=False, valid_from=VF, valid_to=None, supersedes_id=None, recorded_by=nurse, idempotency_key="u1")
        theirs, _ = await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=other, code="DEMO-T1.o", measures={"demo_value": 1.0}, verified=True, valid_from=VF, valid_to=None, supersedes_id=None, recorded_by=nurse, idempotency_key="o1")
        wrong_code, _ = await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="ZZZ-1", measures={"demo_value": 1.0}, verified=True, valid_from=VF, valid_to=None, supersedes_id=None, recorded_by=nurse, idempotency_key="z1")
        with pytest.raises(cj.Refused):  # a correction cannot name another patient's result
            await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="DEMO-T1.x", measures={}, verified=True, valid_from=VF, valid_to=None, supersedes_id=theirs, recorded_by=nurse, idempotency_key="x1")
    close = "update ops.care_plan_steps set status = 'met', evidence_result_id = :r where plan_id = :p and step_key = 'demo_test'"
    for bad in (mine_unverified, theirs, wrong_code):
        assert await state_of(db, nurse, close, r=bad, p=plan_id) == "23001", bad  # unverified, another patient, a different test: none closes the step
    claim = "update ops.care_plan_waits set claimed_result_id = :r where plan_id = :p"
    assert await state_of(db, nurse, claim, r=theirs, p=plan_id) == "23001"  # another patient's result cannot hold this patient's wait
    assert await state_of(db, nurse, claim, r=wrong_code, p=plan_id) == "23001"  # a result for a different test cannot either


async def test_a_correction_supersedes_a_result_once_and_the_valid_range_must_make_sense(db):
    clinic, _, nurse, _, patient = await world(db)
    async with as_user(db, nurse) as c:
        first, _ = await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="DEMO-T1.c", measures={"demo_value": 1.0}, verified=False, valid_from=VF, valid_to=None, supersedes_id=None, recorded_by=nurse, idempotency_key="c1")
        second, _ = await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="DEMO-T1.c", measures={"demo_value": 2.0}, verified=True, valid_from=VF, valid_to=None, supersedes_id=first, recorded_by=nurse, idempotency_key="c2")
        assert second != first
        with pytest.raises(cj.Refused):  # a result is superseded at most once
            await cj.record_result(c, clinic_id=clinic, origin="test", patient_id=patient, code="DEMO-T1.c", measures={"demo_value": 3.0}, verified=True, valid_from=VF, valid_to=None, supersedes_id=first, recorded_by=nurse, idempotency_key="c3")
    ins = "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, valid_to, recorded_by, idempotency_key) values (:c, 'test', :p, 'DEMO-T1.d', '{}'::jsonb, true, '2026-02-01', '2026-01-01', :by, 'bad-range')"
    assert await state_of(db, nurse, ins, c=clinic, p=patient, by=nurse) == "23514"


async def test_the_plan_screener_holds_what_it_cannot_read_escalates_a_red_flag_and_clears_only_a_clear(db):
    clinic, doctor, _, _, patient = await world(db)
    calls = []

    class Eval:
        def __init__(self, outcome):
            self.outcome = outcome

    class FakeEngine:
        def __init__(self, outcome):
            self.outcome = outcome

        async def evaluate_text(self, conn, clinic_id, origin, text_):
            calls.append(("evaluate", text_))
            return Eval(self.outcome)

        async def escalate_red_flag(self, conn, clinic_id, origin, evaluation, source):
            calls.append(("escalate", source))

    async with as_user(db, doctor) as c:
        plan_id, _ = await cj.create_plan(c, VAULT, clinic_id=clinic, origin="test", patient_id=patient, plan_text="DEMO plan text", created_by=doctor, key_version=1, idempotency_key="s1")
        assert await cj.SqlPlanScreener(VAULT, FakeEngine("clear")).screen(c, plan_id=plan_id, clinic_id=clinic) == "clear"
        assert await cj.SqlPlanScreener(VAULT, FakeEngine("unassessable")).screen(c, plan_id=plan_id, clinic_id=clinic) == "held_unassessable"
        calls.clear()
        assert await cj.SqlPlanScreener(VAULT, FakeEngine("red_flag")).screen(c, plan_id=plan_id, clinic_id=clinic) == "red_flag_escalated"
        assert calls == [("evaluate", "DEMO plan text"), ("escalate", f"plan:{plan_id}")]
        other = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
        assert await cj.SqlPlanScreener(other, FakeEngine("clear")).screen(c, plan_id=plan_id, clinic_id=clinic) == "held_unassessable"  # cannot be read: unknown is not clear
        assert await cj.SqlPlanScreener(VAULT, FakeEngine("clear")).screen(c, plan_id=uuid.uuid4(), clinic_id=clinic) == "held_unassessable"


async def _async(value):
    return value
