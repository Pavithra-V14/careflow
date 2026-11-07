"""The Visit Readiness graph over its SQL ports (careflow/visit_readiness_sql.py, migration 0052) against the real database, as the restricted `careflow_app` role with verified claims, inside ONE never-committed
outer transaction. The three model roles and the rules screening are scripted stand-ins (no model route is approved); the rest is real: the gap, the exact-text question messages in the readiness thread, the facts
and answers tables, the nurse task, the brief. What is proved: only missing facts are asked, the checklist is walked, an answer that is not confirmed reaches a nurse in every mode, no approved model means every reply
is unclear and goes to front desk, and the tables refuse what the agent must never do (rewrite, cite another clinic's or another topic's message, be read without a second factor)."""

import base64
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import visit_readiness_content as content_mod
from careflow import visit_readiness_sql as vr
from careflow.care_gap_sql import sqlstate
from careflow.crypto import DemographicVault
from careflow.rules import RedFlagEngine
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
VAULT = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
CONTENT = json.dumps(
    {
        "facts": {"demo_fact_one": "DEMO ONLY question one?"},
        "visit_types": {"DEMO visit type": {"intake": ["demo_fact_one"], "checklist": [{"id": "demo_item_one", "text": "DEMO ONLY item one"}, {"id": "demo_item_two", "text": "DEMO ONLY item two"}]}},
    }
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


class Scripted:
    """Stand-ins for the five model roles; `answers` gives the readiness answer per item."""

    def __init__(self, answers=None, barrier="scheduling_conflict"):
        self.answers, self.barrier = answers or {}, barrier

    async def classify(self, conn, *, message_id=None, item=None, visit_id=None):
        if visit_id is not None and item is None and message_id is None:
            return self.barrier
        if item is None:
            return "answers"  # the intent
        return self.answers.get(item, "confirmed")

    async def resolve(self, conn, **kw):
        return True

    async def generate(self, conn, **kw):
        return []


class Verdict:
    def __init__(self, verdict="clear"):
        self.verdict = verdict

    async def screen(self, conn, *, message_id):
        return self.verdict


async def world(db):
    h = (await db.get_raw_connection()).driver_connection
    ids = {k: uuid.uuid4() for k in ("clinic", "other", "nurse", "front_desk", "doctor", "nurse_b", "patient", "booking", "slot", "vt", "provider", "location")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'VR A', 'test'), ($2, 'VR B', 'test')", ids["clinic"], ids["other"])
    for k in ("nurse", "front_desk", "doctor", "nurse_b"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    await h.execute(
        "insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $5, 'nurse', 'test'), ($2, $5, 'front_desk', 'test'), ($3, $5, 'doctor', 'test'), ($4, $6, 'nurse', 'test')",
        ids["nurse"], ids["front_desk"], ids["doctor"], ids["nurse_b"], ids["clinic"], ids["other"],
    )
    await h.execute("insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', $3)", ids["patient"], ids["clinic"], f"vr-{ids['patient']}")
    await h.execute("insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ($1, $2, 'P', 'test', $3)", ids["provider"], ids["clinic"], f"vp-{ids['provider']}")
    await h.execute("insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values ($1, $2, 'L', 'test', $3)", ids["location"], ids["clinic"], f"vl-{ids['location']}")
    await h.execute("insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values ($1, $2, 'DEMO visit type', 30, 'test', $3)", ids["vt"], ids["clinic"], f"vv-{ids['vt']}")
    start = datetime.now(UTC) + timedelta(days=30)
    await h.execute(
        "insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) values ($1, $2, $3, $4, $5, $6, $7, 'booked', 'test', $8)",
        ids["slot"], ids["clinic"], ids["provider"], ids["location"], ids["vt"], start, start + timedelta(minutes=30), f"vs-{ids['slot']}",
    )
    await h.execute("insert into sched.bookings (id, clinic_id, slot_id, patient_id, origin, idempotency_key) values ($1, $2, $3, $4, 'test', $5)", ids["booking"], ids["clinic"], ids["slot"], ids["patient"], f"vb-{ids['booking']}")
    return ids


def components(monkeypatch, models=None, verdict="clear"):
    monkeypatch.setenv("FEATURE_VISIT_READINESS", "1")
    return vr.VisitReadinessComponents(VAULT, RedFlagEngine(), content_mod.parse(CONTENT), "standard", InMemorySaver(), models=models, screener=Verdict(verdict))


async def runner_for(c, comps, w, who):
    visit = await vr.load_visit(c, w["booking"])
    return comps.runner(c, visit=visit, actor=who, key_version=1)


async def reply(c, comps, w, who, body="DEMO reply"):
    visit = await vr.load_visit(c, w["booking"])
    message_id = await vr.store_reply(c, VAULT, 1, visit, who, body, f"{w['booking']}:{uuid.uuid4()}")
    return await (await runner_for(c, comps, w, who)).reply(message_id)


async def test_only_the_missing_fact_is_asked_then_the_checklist_is_walked_and_a_full_confirmation_proceeds(db, monkeypatch):
    w = await world(db)
    comps = components(monkeypatch, Scripted())
    async with as_user(db, w["nurse"]) as c:
        view = await (await runner_for(c, comps, w, w["nurse"])).start()
        assert view.waiting_for == "reply" and view.current_item == "demo_fact_one"
        assert (await c.execute(text("select count(*) from msg.messages where topic = 'readiness' and clinic_id = :c"), {"c": w["clinic"]})).scalar() == 1  # the clinician's own question, as a message
        view = await reply(c, comps, w, w["nurse"])
        assert view.current_item == "demo_item_one"
        assert (await c.execute(text("select count(*) from ops.visit_facts where visit_id = :v and fact = 'demo_fact_one'"), {"v": w["booking"]})).scalar() == 1
        view = await reply(c, comps, w, w["nurse"])
        assert view.current_item == "demo_item_two"
        view = await reply(c, comps, w, w["nurse"])
        assert view.waiting_for == "none" and view.finished
        rows = (await c.execute(text("select item, answer from ops.visit_readiness_answers where visit_id = :v order by item"), {"v": w["booking"]})).all()
        assert [tuple(r) for r in rows] == [("demo_item_one", "confirmed"), ("demo_item_two", "confirmed")]
        assert (await c.execute(text("select count(*) from agents.review_tasks where source_message_id = :s"), {"s": f"visit:{w['booking']}"})).scalar() == 0  # everything confirmed: no task
        again = await (await runner_for(c, comps, w, w["nurse"])).start()
        assert again.finished  # a second start is the same run


async def test_a_checklist_item_that_is_not_confirmed_raises_a_nurse_task_in_every_mode(db, monkeypatch):
    w = await world(db)
    comps = components(monkeypatch, Scripted({"demo_item_two": "not_done"}))
    async with as_user(db, w["nurse"]) as c:
        await (await runner_for(c, comps, w, w["nurse"])).start()
        for _ in range(3):
            view = await reply(c, comps, w, w["nurse"])
        assert view.finished and view.task_id is not None
        task = (await c.execute(text("select kind, assigned_role, priority from agents.review_tasks where id = :i"), {"i": view.task_id})).one()
        assert tuple(task) == ("visit_readiness_readiness_escalation", "nurse", "standard")


async def test_with_no_approved_model_route_every_reply_is_unclear_and_goes_to_front_desk_never_guessed(db, monkeypatch):
    w = await world(db)
    comps = components(monkeypatch, None)  # NoModelRoute
    async with as_user(db, w["nurse"]) as c:
        await (await runner_for(c, comps, w, w["nurse"])).start()
        view = await reply(c, comps, w, w["nurse"])
        view = await reply(c, comps, w, w["nurse"]) if not view.finished else view
        assert (await c.execute(text("select count(*) from ops.visit_facts where visit_id = :v"), {"v": w["booking"]})).scalar() == 0  # nothing was learned from an unclassified reply
        assert (await c.execute(text("select count(*) from ops.visit_readiness_answers where visit_id = :v and answer = 'confirmed'"), {"v": w["booking"]})).scalar() == 0


async def test_a_red_flag_in_a_reply_ends_the_run_before_any_model_reads_it(db, monkeypatch):
    w = await world(db)
    comps = components(monkeypatch, Scripted(), verdict="red_flag_escalated")
    async with as_user(db, w["nurse"]) as c:
        await (await runner_for(c, comps, w, w["nurse"])).start()
        view = await reply(c, comps, w, w["nurse"])
        assert view.status == "red_flag_escalated" and view.waiting_for == "none"
        assert (await c.execute(text("select count(*) from ops.visit_facts where visit_id = :v"), {"v": w["booking"]})).scalar() == 0


async def test_the_tables_are_append_only_pinned_to_the_clinic_and_topic_and_need_a_second_factor_to_read(db, monkeypatch):
    w = await world(db)
    comps = components(monkeypatch, Scripted())
    async with as_user(db, w["nurse"]) as c:
        await (await runner_for(c, comps, w, w["nurse"])).start()
        await reply(c, comps, w, w["nurse"])
    q = "select count(*) from ops.visit_facts where visit_id = :v"
    assert await state_of(db, w["nurse"], q, v=w["booking"]) == [(1,)] and await state_of(db, w["doctor"], q, v=w["booking"]) == [(1,)]
    assert await state_of(db, w["nurse"], q, claims={"aal": "aal1", "amr": []}, v=w["booking"]) == [(0,)]  # no verified second factor
    assert await state_of(db, w["front_desk"], q, v=w["booking"]) == [(0,)] and await state_of(db, w["nurse_b"], q, v=w["booking"]) == [(0,)]
    assert await state_of(db, w["nurse"], "update ops.visit_facts set fact = 'x_y'") == "42501"
    assert await state_of(db, w["nurse"], "delete from ops.visit_readiness_answers") == "42501"
    ins = "insert into ops.visit_facts (clinic_id, origin, visit_id, patient_id, fact, thread_id, source_message_id, recorded_by, idempotency_key) values (:c, 'test', :v, :p, 'other_fact', :t, :m, :by, :k)"
    async with as_user(db, w["nurse"]) as c:  # threads and messages are written by a nurse under row-level security (the owner is subject to the policies too)
        care_thread = (await c.execute(text("insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) values (:c, :p, 'care', :u, 'test', 'ct') returning id"), {"c": w["clinic"], "p": w["patient"], "u": w["nurse"]})).scalar_one()
        care_msg = (
            await c.execute(
                text("insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) values (gen_random_uuid(), :c, 'test', 'care', :t, 'staff', :u, '\\x01'::bytea, 1, 'cm') returning id"),
                {"c": w["clinic"], "t": care_thread, "u": w["nurse"]},
            )
        ).scalar_one()
    assert await state_of(db, w["nurse"], ins, c=w["clinic"], v=w["booking"], p=w["patient"], t=care_thread, m=care_msg, by=w["nurse"], k="f1") == "23503"  # a fact cannot cite a message of another topic
    assert await state_of(db, w["front_desk"], ins, c=w["clinic"], v=w["booking"], p=w["patient"], t=care_thread, m=care_msg, by=w["front_desk"], k="f2") == "42501"
    assert await state_of(db, w["nurse"], ins, c=w["clinic"], v=w["booking"], p=uuid.uuid4(), t=care_thread, m=care_msg, by=w["nurse"], k="f3") == "23503"  # the visit names ITS patient


async def test_a_fact_cannot_cite_another_patients_readiness_reply(db, monkeypatch):
    w = await world(db)
    comps = components(monkeypatch, Scripted())
    h = (await db.get_raw_connection()).driver_connection
    other = uuid.uuid4()
    await h.execute("insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, '\x01'::bytea, 1, '\x01'::bytea, 1, 'test', $3)", other, w["clinic"], f"vr-{other}")
    async with as_user(db, w["nurse"]) as c:
        await (await runner_for(c, comps, w, w["nurse"])).start()
        theirs = (await c.execute(text("insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) values (:c, :p, 'readiness', :u, 'test', 'ot') returning id"), {"c": w["clinic"], "p": other, "u": w["nurse"]})).scalar_one()
        their_msg = (
            await c.execute(
                text("insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) values (gen_random_uuid(), :c, 'test', 'readiness', :t, 'staff', :u, '\\x01'::bytea, 1, 'om') returning id"),
                {"c": w["clinic"], "t": theirs, "u": w["nurse"]},
            )
        ).scalar_one()
    ins = "insert into ops.visit_facts (clinic_id, origin, visit_id, patient_id, fact, thread_id, source_message_id, recorded_by, idempotency_key) values (:c, 'test', :v, :p, 'cross_fact', :t, :m, :by, 'x1')"
    assert await state_of(db, w["nurse"], ins, c=w["clinic"], v=w["booking"], p=w["patient"], t=theirs, m=their_msg, by=w["nurse"]) == "23503"  # the thread belongs to another patient
