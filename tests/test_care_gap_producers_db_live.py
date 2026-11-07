"""Migration 0048 against the real database, as the restricted `careflow_app` role inside ONE never-committed outer transaction: the autonomy history, the care-gap producers (episode, rule, last-done),
the review sample (worker insert, nurse recheck) and the narrow worker read of overdue rules."""

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
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.care_gap_sql import sqlstate
from tests.db_helpers import ensure_app_role_sa
from tests.test_rls_triage_live import decision_sql, seed_proposal
from tests.test_rls_triage_live import world as triage_world

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


def aal2():
    return {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 5}]}


@asynccontextmanager
async def as_user(db, person, claims):
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(person), "role": "authenticated", **claims})})
        yield db
        await db.exec_driver_sql("reset role")


async def state_of(db, person, sql, claims=None, **params):
    try:
        async with as_user(db, person, aal2() if claims is None else claims) as c:
            result = await c.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


async def pg(db):
    return (await db.get_raw_connection()).driver_connection


async def test_only_an_admin_with_aal2_appends_to_the_autonomy_history_and_only_shadow_or_assisted(db):
    w = await triage_world(await pg(db))
    p = w.people
    ins = "insert into ops.autonomy_changes (clinic_id, origin, mode, changed_by, idempotency_key) values (:c, 'test', :m, :by, :k)"
    assert await state_of(db, p["admin_a"], ins, c=w.a, m="assisted", by=p["admin_a"], k=f"a-{uuid.uuid4()}") == 1
    assert await state_of(db, p["admin_a"], ins, c=w.a, m="assisted", by=p["admin_a"], k=f"a-{uuid.uuid4()}", claims={"aal": "aal1", "amr": []}) == "42501"  # no verified second factor
    assert await state_of(db, p["admin_a"], ins, c=w.a, m="autonomous", by=p["admin_a"], k=f"a-{uuid.uuid4()}") == "42501"  # the policy refuses it: the switch is the two-person proposal (0053/0054)
    assert await state_of(db, p["admin_a"], ins, c=w.a, m="shadow", by=p["doctor_a"], k=f"a-{uuid.uuid4()}") == "42501"  # only as yourself
    for who in ("nurse_a", "doctor_a", "front_desk_a", "owner_a", "auditor_a", "worker_a", "nurse_b"):
        assert await state_of(db, p[who], ins, c=w.a, m="shadow", by=p[who], k=f"a-{uuid.uuid4()}") == "42501", who
    for who in ("front_desk_a", "nurse_a", "doctor_a", "admin_a", "worker_a"):
        assert (await state_of(db, p[who], "select count(*) from ops.autonomy_changes where clinic_id = :c", c=w.a))[0][0] == 1, who
    for who in ("owner_a", "auditor_a", "nurse_b"):
        assert (await state_of(db, p[who], "select count(*) from ops.autonomy_changes where clinic_id = :c", c=w.a))[0][0] == 0, who
    assert await state_of(db, p["admin_a"], "update ops.autonomy_changes set mode = 'shadow'") == "42501"
    assert await state_of(db, p["admin_a"], "delete from ops.autonomy_changes") == "42501"


async def test_nurse_and_doctor_with_aal2_create_episodes_rules_and_record_done_and_nobody_else_can(db):
    w = await triage_world(await pg(db))
    p = w.people
    rule = "insert into ops.care_recurrence_rules (clinic_id, origin, rule_code, interval_days, overdue_after_days, idempotency_key) values (:c, 'test', :code, 30, 5, :k) returning id"
    ep = "insert into ops.care_episodes (clinic_id, origin, patient_id, episode_code, started_on, idempotency_key) values (:c, 'test', :p, 'demo_episode', current_date, :k) returning id"
    rule_id = (await state_of(db, p["nurse_a"], rule, c=w.a, code="demo_rule", k="r1"))[0][0]
    assert await state_of(db, p["nurse_a"], rule, c=w.a, code="demo_rule", k="r2") == "23505"  # one rule per code per clinic
    assert await state_of(db, p["nurse_a"], rule, c=w.a, code="demo_rule_two", k="r3", claims={"aal": "aal1", "amr": []}) == "42501"
    for who in ("admin_a", "front_desk_a", "owner_a", "auditor_a", "worker_a", "nurse_b"):
        assert await state_of(db, p[who], rule, c=w.a, code="x_rule", k=f"x-{who}") == "42501", who
    ep_id = (await state_of(db, p["doctor_a"], ep, c=w.a, p=w.patient_a, k="e1"))[0][0]
    assert await state_of(db, p["doctor_a"], ep, c=w.a, p=w.patient_b, k="e2") == "23503"  # a patient of another clinic
    link = "insert into ops.care_episode_rules (clinic_id, origin, episode_id, rule_id, idempotency_key) values (:c, 'test', :e, :r, :k) returning id"
    er = (await state_of(db, p["nurse_a"], link, c=w.a, e=ep_id, r=rule_id, k="l1"))[0][0]
    assert await state_of(db, p["nurse_a"], link, c=w.a, e=ep_id, r=rule_id, k="l2") == "23505"  # one rule per episode
    today = datetime.now(UTC).date()
    done = "update ops.care_episode_rules set last_done_on = :d where id = :i"
    assert await state_of(db, p["nurse_a"], done, d=today - timedelta(days=10), i=er) == 1
    assert await state_of(db, p["doctor_a"], done, d=today - timedelta(days=5), i=er) == 1  # forward
    assert await state_of(db, p["nurse_a"], done, d=today - timedelta(days=9), i=er) == "23001"  # never backwards
    assert await state_of(db, p["nurse_a"], done, d=today + timedelta(days=1), i=er) == "23001"  # never into the future
    assert await state_of(db, p["nurse_a"], done, d=today, i=er, claims={"aal": "aal1", "amr": []}) == 0  # the restrictive policy hides the row from the update: nothing changes
    assert await state_of(db, p["admin_a"], done, d=today, i=er) == 0 and await state_of(db, p["nurse_b"], done, d=today, i=er) == 0
    assert await state_of(db, p["nurse_a"], "update ops.care_episode_rules set rule_id = rule_id") == "42501"  # no other column
    assert await state_of(db, p["nurse_a"], "delete from ops.care_episode_rules") == "42501"


async def test_the_worker_reads_overdue_rules_through_the_narrow_function_only(db):
    w = await triage_world(await pg(db))
    p = w.people
    old = datetime.now(UTC).date() - timedelta(days=100)
    handle = await pg(db)
    rule, episode, link = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await handle.execute(
        "insert into ops.care_recurrence_rules (id, clinic_id, origin, rule_code, interval_days, overdue_after_days, idempotency_key) values ($1, $2, 'test', 'w_rule', 30, 5, $3)", rule, w.a, f"wr-{rule}"
    )
    await handle.execute("insert into ops.care_episodes (id, clinic_id, origin, patient_id, episode_code, started_on, idempotency_key) values ($1, $2, 'test', $3, 'w_ep', $4, $5)", episode, w.a, w.patient_a, old, f"we-{episode}")
    await handle.execute("insert into ops.care_episode_rules (id, clinic_id, origin, episode_id, rule_id, last_done_on, idempotency_key) values ($1, $2, 'test', $3, $4, $5, $6)", link, w.a, episode, rule, old, f"wl-{link}")
    rows = await state_of(db, p["worker_a"], "select episode_rule_id, last_done_on, interval_days, overdue_after_days from ops.care_gap_rules_for_worker(:c)", claims={}, c=w.a)
    assert rows == [(link, old, 30, 5)]
    for who in ("nurse_a", "admin_a", "front_desk_a", "worker_b"):
        assert await state_of(db, p[who], "select * from ops.care_gap_rules_for_worker(:c)", c=w.a) == "42501", who
    assert (await state_of(db, p["worker_a"], "select count(*) from ops.care_episode_rules where true", claims={}))[0][0] == 0  # no table policy for the worker
    assert (await state_of(db, p["worker_a"], "select count(*) from ops.care_episodes where true", claims={}))[0][0] == 0


async def test_the_worker_inserts_a_sample_for_a_decision_of_its_clinic_and_a_nurse_rechecks_it_once(db):
    handle = await pg(db)
    w = await triage_world(handle)
    p = w.people
    proposal = await seed_proposal(handle, w)
    await handle.execute(decision_sql(w.a, proposal, p["nurse_a"]))
    decision = await handle.fetchval("select id from agents.review_decisions where proposal_id = $1", proposal)
    ins = "insert into ops.care_review_samples (clinic_id, origin, decision_id, idempotency_key) values (:c, 'test', :d, :k)"
    assert await state_of(db, p["worker_b"], ins, claims={}, c=w.b, d=decision, k="s3") == "23503"  # the clinic is pinned to the decision's own
    assert await state_of(db, p["worker_a"], ins, claims={}, c=w.a, d=decision, k="s1") == 1
    assert await state_of(db, p["worker_a"], ins, claims={}, c=w.a, d=decision, k="s2") == "23505"  # one sample per decision
    assert await state_of(db, p["worker_a"], ins, claims={}, c=w.a, d=uuid.uuid4(), k="s4") == "23503"
    for who in ("nurse_a", "doctor_a", "admin_a", "front_desk_a"):
        assert await state_of(db, p[who], ins, c=w.a, d=decision, k=f"s-{who}") == "42501", who
    sample = (await state_of(db, p["nurse_a"], "select id from ops.care_review_samples where decision_id = :d", d=decision))[0][0]
    recheck = "update ops.care_review_samples set rechecked_by = :by where id = :i"
    assert await state_of(db, p["doctor_a"], recheck, by=p["admin_a"], i=sample) == "42501"  # only as yourself (the new row fails the policy check)
    assert await state_of(db, p["doctor_a"], recheck, by=p["doctor_a"], i=sample, claims={"aal": "aal1", "amr": []}) == 0
    assert await state_of(db, p["front_desk_a"], recheck, by=p["front_desk_a"], i=sample) == 0
    assert await state_of(db, p["nurse_a"], recheck, by=p["nurse_a"], i=sample) == "23001"  # the nurse made the decision: someone else must re-check it (decision 7)
    async with as_user(db, p["doctor_a"], aal2()) as c:
        assert (await c.execute(text(recheck), {"by": p["doctor_a"], "i": sample})).rowcount == 1
        assert (await c.execute(text("select rechecked_at is not null from ops.care_review_samples where id = :i"), {"i": sample})).scalar() is True  # the database's clock
    assert await state_of(db, p["nurse_a"], recheck, by=p["nurse_a"], i=sample) == 0  # re-checked once; the update policy no longer matches


async def test_the_overdue_sweep_raises_one_alert_per_overdue_rule_through_the_narrow_read_and_a_second_pass_adds_none(db):
    from careflow.producer_sweeps import care_gap_overdue_sweep

    handle = await pg(db)
    w = await triage_world(handle)
    old = datetime.now(UTC).date() - timedelta(days=100)
    rule, episode, link, fresh = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await handle.execute("insert into ops.care_recurrence_rules (id, clinic_id, origin, rule_code, interval_days, overdue_after_days, idempotency_key) values ($1, $2, 'test', 's_rule', 30, 5, $3)", rule, w.a, f"sr-{rule}")
    await handle.execute("insert into ops.care_episodes (id, clinic_id, origin, patient_id, episode_code, started_on, idempotency_key) values ($1, $2, 'test', $3, 's_ep', $4, $5)", episode, w.a, w.patient_a, old, f"se-{episode}")
    await handle.execute("insert into ops.care_episode_rules (id, clinic_id, origin, episode_id, rule_id, last_done_on, idempotency_key) values ($1, $2, 'test', $3, $4, $5, $6)", link, w.a, episode, rule, old, f"sl-{link}")
    rule2 = uuid.uuid4()
    await handle.execute("insert into ops.care_recurrence_rules (id, clinic_id, origin, rule_code, interval_days, overdue_after_days, idempotency_key) values ($1, $2, 'test', 's_rule2', 30, 5, $3)", rule2, w.a, f"sr-{rule2}")
    await handle.execute("insert into ops.care_episode_rules (id, clinic_id, origin, episode_id, rule_id, last_done_on, idempotency_key) values ($1, $2, 'test', $3, $4, current_date, $5)", fresh, w.a, episode, rule2, f"sl-{fresh}")
    async with as_user(db, w.people["worker_a"], {}) as c:
        assert await care_gap_overdue_sweep(c, 1, origin="test") == 1  # the overdue one only; the recently done one is not overdue
        assert await care_gap_overdue_sweep(c, 1, origin="test") == 1  # attempted again: the key makes it a no-op
        assert await care_gap_overdue_sweep(c, None, origin="test") == 0  # no alert age configured: nothing raised
    rows = await handle.fetch("select class, audience_role, ref from ops.alerts where clinic_id = $1 and class = 'task_overdue'", w.a)
    assert len(rows) == 1 and rows[0]["audience_role"] == "nurse" and rows[0]["ref"] == str(link)
