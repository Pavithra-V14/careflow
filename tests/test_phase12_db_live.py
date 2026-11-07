"""Migration 0051 against the real database, as the restricted `careflow_app` role inside ONE never-committed outer transaction: front-desk resolution of portal claims, the consent notice publication, reads
that need a verified second factor (the worker exempt), the step / plan guards the second review asked for, and the outbox-woken Care Journey handler running as the WORKER identity over the durable checkpoint
tables (a plan started by a doctor, a result recorded by a nurse, the plan resumed by the worker)."""

import asyncio
import base64
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import care_journey_sql as cj
from careflow.care_gap_sql import sqlstate
from careflow.crypto import DemographicVault
from careflow.queue import PostgresQueue
from careflow.rules import RedFlagEngine
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
VAULT = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
VF = datetime(2026, 1, 1, tzinfo=UTC)
PHONE, NAME_DOB = b"\x01" * 32, b"\x02" * 32
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


AAL1 = {"aal": "aal1", "amr": []}


class Clear:
    async def screen(self, conn, *, plan_id, clinic_id):
        return "clear"


async def _async(value):
    return value


async def world(db):
    handle = (await db.get_raw_connection()).driver_connection
    ids = {k: uuid.uuid4() for k in ("clinic", "other", "doctor", "nurse", "front_desk", "admin", "admin2", "worker", "claimant", "patient", "unconfirmed", "other_patient")}
    await handle.execute("insert into iam.clinics (id, name, origin) values ($1, 'P12 A', 'test'), ($2, 'P12 B', 'test')", ids["clinic"], ids["other"])
    for k in ("doctor", "nurse", "front_desk", "admin", "admin2", "worker"):
        await handle.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    await handle.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids["claimant"])
    for k, role in (("doctor", "doctor"), ("nurse", "nurse"), ("front_desk", "front_desk"), ("admin", "admin"), ("admin2", "admin"), ("worker", "queue_worker")):
        await handle.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    for k in ("patient", "unconfirmed", "other_patient"):
        await handle.execute("insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', $3)", ids[k], ids["clinic"], f"p12-{ids[k]}")
    await handle.execute("insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key) values ($1, $2, $3, 'test', $4)", ids["clinic"], ids["patient"], ids["front_desk"], f"cf-{ids['patient']}")
    notice = uuid.uuid4()
    await handle.execute("insert into iam.consent_notices (id, purpose, version, body) values ($1, 'care_delivery', 900, 'DEMO notice')", notice)
    await handle.execute("insert into iam.consent_events (account_id, purpose, notice_id, action, idempotency_key) values ($1, 'care_delivery', $2, 'granted', 'p12')", ids["claimant"], notice)
    return ids


# ---- portal claim resolution (decision 5) -------------------------------------------------------------------------------------------------------------------------------------------------------------------


async def test_front_desk_resolves_a_pending_claim_only_onto_a_confirmed_unbound_record_and_every_resolution_is_audited(db):
    w = await world(db)
    handle = (await db.get_raw_connection()).driver_connection
    await handle.execute("insert into mpi.claim_requests (clinic_id, origin, account_id) values ($1, 'test', $2)", w["clinic"], w["claimant"])
    req = await handle.fetchval("select id from mpi.claim_requests where account_id = $1", w["claimant"])
    call = "select mpi.resolve_claim_request(:r, :d, :p, :k)"
    for who in ("nurse", "doctor", "admin", "worker"):
        assert await state_of(db, w[who], call, r=req, d="reject", p=None, k="k1") == "42501", who  # only front desk
    assert await state_of(db, w["front_desk"], call, claims=AAL1, r=req, d="reject", p=None, k="k1") == "42501"  # a verified second factor is needed
    assert await state_of(db, w["front_desk"], call, r=req, d="approve", p=None, k="k1") == "42501"  # an approval names a record
    assert await state_of(db, w["front_desk"], call, r=req, d="approve", p=w["unconfirmed"], k="k2") == [("record_not_claimable",)]  # no identity event: stays unclaimable
    assert await state_of(db, w["front_desk"], call, r=req, d="approve", p=w["other_patient"], k="k2") == [("record_not_claimable",)]
    assert await state_of(db, w["front_desk"], call, r=uuid.uuid4(), d="reject", p=None, k="k3") == "42501"
    assert await state_of(db, w["front_desk"], call, r=req, d="approve", p=w["patient"], k="k4") == [("approved",)]
    assert (await db.execute(text("select account_id from mpi.patients where id = :i"), {"i": w["patient"]})).scalar() == w["claimant"]
    assert (await db.execute(text("select status, resolved_by, resolved_patient_id from mpi.claim_requests where id = :r"), {"r": req})).one() == ("resolved", w["front_desk"], w["patient"])
    assert (await db.execute(text("select count(*) from audit.log where action = 'portal.claim_approve' and idempotency_key = :k"), {"k": f"sys:portal-claim-resolve:{req}"})).scalar() == 1
    assert await state_of(db, w["front_desk"], call, r=req, d="reject", p=None, k="k5") == [("already_resolved",)]  # a repeat changes nothing


async def test_a_rejection_closes_the_request_and_audits_it(db):
    w = await world(db)
    handle = (await db.get_raw_connection()).driver_connection
    await handle.execute("insert into mpi.claim_requests (clinic_id, origin, account_id) values ($1, 'test', $2)", w["clinic"], w["claimant"])
    req = await handle.fetchval("select id from mpi.claim_requests where account_id = $1", w["claimant"])
    assert await state_of(db, w["front_desk"], "select mpi.resolve_claim_request(:r, 'reject', null, 'k')", r=req) == [("rejected",)]
    assert (await db.execute(text("select status from mpi.claim_requests where id = :r"), {"r": req})).scalar() == "rejected"
    assert (await db.execute(text("select count(*) from audit.log where action = 'portal.claim_reject' and idempotency_key = :k"), {"k": f"sys:portal-claim-resolve:{req}"})).scalar() == 1
    assert (await db.execute(text("select count(*) from mpi.patients where account_id = :a"), {"a": w["claimant"]})).scalar() == 0


# ---- consent notices are published only by migration or the owner script (decision 3 of 2026-11-08) -------------------------------------------------------------------------------------------------------


async def test_no_application_role_can_publish_a_consent_notice(db):
    w = await world(db)
    assert (await db.execute(text("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'iam' and p.proname = 'publish_consent_notice'"))).scalar() == 0
    ins = "insert into iam.consent_notices (purpose, version, body) values ('care_delivery', 950, 'x')"
    for who in ("admin", "admin2", "doctor", "nurse", "front_desk", "worker"):
        assert await state_of(db, w[who], ins) == "42501", who


# ---- reads need a verified second factor; the worker is exempt (decisions 8 and 11) ---------------------------------------------------------------------------------------------------------------------


async def test_clinical_reads_need_aal2_for_staff_and_the_worker_reads_but_never_inserts(db):
    w = await world(db)
    handle = (await db.get_raw_connection()).driver_connection
    rid = await handle.fetchval(
        "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, idempotency_key) values ($1, 'test', $2, 'DEMO-T1.a', '{}'::jsonb, true, now(), $3, 'r') returning id",
        w["clinic"], w["patient"], w["nurse"],
    )
    plan_id = uuid.uuid4()
    await handle.execute("insert into ops.care_plans (id, clinic_id, origin, patient_id, plan_ct, plan_key_version, created_by, idempotency_key) values ($1, $2, 'test', $3, '\\x01'::bytea, 1, $4, 'cp')", plan_id, w["clinic"], w["patient"], w["doctor"])
    q = "select count(*) from clinical.results where clinic_id = :c"
    qp = "select count(*) from ops.care_plans where clinic_id = :c"
    for who in ("nurse", "doctor"):
        assert await state_of(db, w[who], q, c=w["clinic"]) == [(1,)]
        assert await state_of(db, w[who], q, claims=AAL1, c=w["clinic"]) == [(0,)], who  # no verified second factor: nothing is visible
        assert await state_of(db, w[who], qp, claims=AAL1, c=w["clinic"]) == [(0,)], who
    assert await state_of(db, w["worker"], q, claims={}, c=w["clinic"]) == [(0,)]  # nothing points to the result: the worker cannot read it (decision 4 of 2026-11-08)
    await handle.execute("insert into ops.outbox (clinic_id, origin, event_type, payload, idempotency_key) values ($1, 'test', 'care_plan_result', jsonb_build_object('result_id', $2::text), 'ob-1')", w["clinic"], str(rid))
    assert await state_of(db, w["worker"], q, claims={}, c=w["clinic"]) == [(0,)]  # the permanent outbox event alone is NOT enough
    await handle.execute(
        "insert into ops.queue (clinic_id, origin, task_type, payload, state, attempt_count, claimed_at, claimed_by, idempotency_key) values ($1, 'test', 'care_plan_result', jsonb_build_object('result_id', $2::text), 'claimed', 1, now(), 'w', 'q-1')",
        w["clinic"], str(rid),
    )
    other = await handle.fetchval(
        "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, idempotency_key) values ($1, 'test', $2, 'DEMO-T9.z', '{}'::jsonb, true, now(), $3, 'r2') returning id",
        w["clinic"], w["patient"], w["nurse"],
    )
    assert await state_of(db, w["worker"], q, claims={}, c=w["clinic"]) == [(1,)]  # the CLAIMED job names ONE result: only that one is readable
    assert await state_of(db, w["worker"], "select id from clinical.results where id = :i", claims={}, i=other) == []
    assert await state_of(db, w["worker"], qp, claims={}, c=w["clinic"]) == [(1,)]
    assert await state_of(db, w["front_desk"], q, c=w["clinic"]) == [(0,)] and await state_of(db, w["admin"], qp, c=w["clinic"]) == [(0,)]
    ins = "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, idempotency_key) values (:c, 'test', :p, 'X-1', '{}'::jsonb, true, now(), :by, 'w1')"
    assert await state_of(db, w["worker"], ins, claims={}, c=w["clinic"], p=w["patient"], by=w["worker"]) == "42501"
    assert await state_of(db, w["worker"], "update ops.care_plans set plan_ct = '\\x02'::bytea", claims={}) == "42501"
    assert rid is not None


# ---- guards the second review asked for ----------------------------------------------------------------------------------------------------------------------------------------------------------------------


async def make_active_plan(db, w, comps):
    async with as_user(db, w["doctor"]) as c:
        plan_id, _ = await cj.create_plan(c, VAULT, clinic_id=w["clinic"], origin="test", patient_id=w["patient"], plan_text="DEMO plan text", created_by=w["doctor"], key_version=1, idempotency_key=f"g-{uuid.uuid4()}")
        runner = comps.runner(c, clinic_id=w["clinic"], origin="test")
        await runner.start(clinic_id=w["clinic"], plan_id=plan_id, patient_id=w["patient"], origin="test")
        await runner.confirm(plan_id, "approve")
    return plan_id


def components(monkeypatch, durable=False):
    monkeypatch.setenv("FEATURE_CARE_JOURNEY", "1")
    from langgraph.checkpoint.memory import InMemorySaver

    return cj.CareJourneyComponents(VAULT, RedFlagEngine(), lambda t: _async(GOOD), "standard", "doctor", None if durable else InMemorySaver(), durable=durable, screener=Clear())


async def test_a_wait_step_cannot_be_closed_without_evidence_a_plan_cannot_complete_with_an_open_wait_and_a_claim_needs_an_open_wait(db, monkeypatch):
    w = await world(db)
    plan_id = await make_active_plan(db, w, components(monkeypatch))
    assert await state_of(db, w["nurse"], "update ops.care_plan_steps set status = 'closed' where plan_id = :p and step_key = 'demo_test'", p=plan_id) == "23001"  # a wait step closes only as met or not_met
    assert await state_of(db, w["nurse"], "update ops.care_plan_waits set open = false where plan_id = :p", p=plan_id) == 1  # (closing a wait alone is allowed; the step stays open)
    assert await state_of(db, w["doctor"], "update ops.care_plans set status = 'completed' where id = :p", p=plan_id) == "23001"  # the result it waits for is still open
    handle = (await db.get_raw_connection()).driver_connection
    rid = await handle.fetchval(
        "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, recorded_by, idempotency_key) values ($1, 'test', $2, 'DEMO-T1.c', '{}'::jsonb, true, now(), $3, 'c1') returning id",
        w["clinic"], w["patient"], w["nurse"],
    )
    assert await state_of(db, w["nurse"], "update ops.care_plan_waits set claimed_result_id = :r where plan_id = :p", r=rid, p=plan_id) == "23001"  # the wait is no longer open
    assert await state_of(db, w["nurse"], "update ops.care_plans set status = 'held' where id = :p", p=plan_id) == 1


async def test_a_wait_must_carry_its_steps_prefix_and_belong_to_a_result_step(db, monkeypatch):
    w = await world(db)
    comps = components(monkeypatch)
    plan_id = await make_active_plan(db, w, comps)
    base = "insert into ops.care_plan_waits (clinic_id, plan_id, step_key, wait_key, patient_id, prefix, idempotency_key) values (:c, :p, :s, :w, :pt, :px, :k)"
    assert await state_of(db, w["doctor"], base, c=w["clinic"], p=plan_id, s="demo_test", w="x1", pt=w["patient"], px="WRONG", k="x1") == "23001"
    assert await state_of(db, w["doctor"], base, c=w["clinic"], p=plan_id, s="book_review", w="x2", pt=w["patient"], px="DEMO-T1", k="x2") == "23001"  # a task step has no wait


# ---- the outbox-woken handler, as the worker identity ----------------------------------------------------------------------------------------------------------------------------------------------------


async def test_a_recorded_result_wakes_its_plan_through_the_outbox_job_as_the_worker(db, monkeypatch):
    w = await world(db)
    comps = components(monkeypatch, durable=True)
    plan_id = await make_active_plan(db, w, comps)  # started and confirmed by the doctor over the durable checkpoint tables
    async with as_user(db, w["nurse"]) as c:
        rid, _ = await cj.record_result(c, clinic_id=w["clinic"], origin="test", patient_id=w["patient"], code="DEMO-T1.a", measures={"demo_value": 5.0}, verified=True, valid_from=VF, valid_to=None, supersedes_id=None, recorded_by=w["nurse"], idempotency_key="wk")
        await PostgresQueue().record_event(c, w["clinic"], "test", cj.TASK_TYPE, {"result_id": str(rid)}, f"care-plan-result:{rid}")  # what the route does, in the same transaction
    assert (await db.execute(text("select count(*) from ops.outbox where event_type = :t and idempotency_key = :k"), {"t": cj.TASK_TYPE, "k": f"care-plan-result:{rid}"})).scalar() == 1

    lock = asyncio.Lock()  # the graph and its checkpoint saver share this one test connection: serialise the uses (production opens a connection per use)

    @asynccontextmanager
    async def worker_factory():
        async with lock, as_user(db, w["worker"], claims={}) as c:
            yield c

    handler = cj.care_plan_result_handler(comps, session_for=lambda engine, account: worker_factory)
    job = SimpleNamespace(clinic_id=w["clinic"], origin="test", payload={"result_id": str(rid)})
    await handler(SimpleNamespace(job=job, engine=None, worker_account_id=w["worker"]))
    step = (await db.execute(text("select status, evidence_result_id from ops.care_plan_steps where plan_id = :p and step_key = 'demo_test'"), {"p": plan_id})).one()
    assert tuple(step) == ("met", rid)  # closed by the worker, citing the result
    assert (await db.execute(text("select count(*) from agents.review_tasks where kind = 'care_plan_referral_proposal' and source_message_id = :s"), {"s": f"plan:{plan_id}"})).scalar() == 1
    await handler(SimpleNamespace(job=job, engine=None, worker_account_id=w["worker"]))  # a second run does nothing more (the plan is no longer at a result pause)
    assert (await db.execute(text("select count(*) from agents.review_tasks where kind = 'care_plan_referral_proposal' and source_message_id = :s"), {"s": f"plan:{plan_id}"})).scalar() == 1
    bad = SimpleNamespace(job=SimpleNamespace(clinic_id=w["other"], origin="test", payload={"result_id": str(rid)}), engine=None, worker_account_id=w["worker"])
    with pytest.raises(Exception, match="not in the job"):
        await handler(bad)  # a job addressed to another clinic is refused, not run


# ---- the two-person switch to autonomous (decision 7 of 2026-11-08) -----------------------------------------------------------------------------------------------------------------------------------------


async def test_autonomous_needs_two_different_admins_and_only_the_approval_function_can_write_it(db):
    w = await world(db)
    propose = "insert into ops.autonomy_proposals (clinic_id, origin, proposed_by, idempotency_key) values (:c, 'test', :by, :k)"
    direct = "insert into ops.autonomy_changes (clinic_id, origin, mode, changed_by, idempotency_key) values (:c, 'test', 'autonomous', :by, :k)"
    assert await state_of(db, w["admin"], direct, c=w["clinic"], by=w["admin"], k="d1") == "42501"  # a single admin can never write it
    assert await state_of(db, w["admin"], propose, claims=AAL1, c=w["clinic"], by=w["admin"], k="p0") == "42501"  # a verified second factor
    for who in ("nurse", "doctor", "front_desk", "worker"):
        assert await state_of(db, w[who], propose, c=w["clinic"], by=w[who], k=f"p-{who}") == "42501", who
    assert await state_of(db, w["admin"], propose, c=w["clinic"], by=w["admin2"], k="pz") == "42501"  # only as yourself
    assert await state_of(db, w["admin"], propose, c=w["clinic"], by=w["admin"], k="p1") == 1
    assert await state_of(db, w["admin2"], propose, c=w["clinic"], by=w["admin2"], k="p2") == "23505"  # one pending proposal per clinic
    prop = (await state_of(db, w["admin"], "select id from ops.autonomy_proposals where clinic_id = :c", c=w["clinic"]))[0][0]
    resolve = "select ops.resolve_autonomy_proposal(:i, :d)"
    assert await state_of(db, w["admin"], resolve, i=prop, d="approve") == [("same_person",)]  # the proposer cannot approve their own
    for who in ("nurse", "doctor", "front_desk", "worker"):
        assert await state_of(db, w[who], resolve, i=prop, d="approve") == "42501", who
    assert await state_of(db, w["admin2"], resolve, claims=AAL1, i=prop, d="approve") == "42501"
    assert (await db.execute(text("select count(*) from ops.autonomy_changes where clinic_id = :c and mode = 'autonomous'"), {"c": w["clinic"]})).scalar() == 0
    assert await state_of(db, w["admin2"], resolve, i=prop, d="approve") == [("approved",)]
    assert (await db.execute(text("select changed_by from ops.autonomy_changes where clinic_id = :c and mode = 'autonomous'"), {"c": w["clinic"]})).scalar() == w["admin2"]
    assert await state_of(db, w["admin2"], resolve, i=prop, d="approve") == [("approved",)]  # a retry gets the stored outcome and writes nothing more
    assert await state_of(db, w["admin"], resolve, i=prop, d="reject") == [("not_pending",)]
    assert (await db.execute(text("select count(*) from audit.log where action = 'autonomy.proposal_approve' and idempotency_key = :k"), {"k": f"sys:autonomy-proposal:{prop}"})).scalar() == 1
    assert await state_of(db, w["admin"], "insert into ops.autonomy_changes (clinic_id, origin, mode, changed_by, idempotency_key) values (:c, 'test', 'assisted', :by, 'low')", c=w["clinic"], by=w["admin"]) == 1  # lowering is one admin
    assert await state_of(db, w["admin"], "update ops.autonomy_proposals set status = 'pending'") == "42501"  # no application update


async def test_a_proposal_can_be_rejected_by_any_admin_and_expires_after_seven_days(db):
    w = await world(db)
    h = (await db.get_raw_connection()).driver_connection
    await h.execute("insert into ops.autonomy_proposals (clinic_id, origin, proposed_by, idempotency_key) values ($1, 'test', $2, 'e1')", w["clinic"], w["admin"])
    prop = await h.fetchval("select id from ops.autonomy_proposals where clinic_id = $1", w["clinic"])
    assert await state_of(db, w["admin"], "select ops.resolve_autonomy_proposal(:i, 'reject')", i=prop) == [("rejected",)]
    assert (await db.execute(text("select status from ops.autonomy_proposals where id = :i"), {"i": prop})).scalar() == "rejected"
    await h.execute("insert into ops.autonomy_proposals (clinic_id, origin, proposed_by, idempotency_key, created_at) values ($1, 'test', $2, 'e2', now() - interval '8 days')", w["clinic"], w["admin"])
    old = await h.fetchval("select id from ops.autonomy_proposals where idempotency_key = 'e2'")
    assert await state_of(db, w["admin2"], "select ops.resolve_autonomy_proposal(:i, 'approve')", i=old) == [("expired",)]
    assert (await db.execute(text("select count(*) from ops.autonomy_changes where clinic_id = :c and mode = 'autonomous'"), {"c": w["clinic"]})).scalar() == 0
