"""Migration 0020, against the real hosted project: referral-hold review tasks (BUILD-SPEC.md §16.2, §17.2, §12.1; CLAUDE.md rules 3 and 8).

One transaction that is always rolled back: if 0020 is not applied yet it is executed in that transaction first (it needs 0007 and 0013), so this verifies the file without applying it.
Queries run as the restricted `careflow_app` role (SET LOCAL ROLE). Nothing is committed.
"""

import json
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values

from tests.db_helpers import ensure_app_role

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
SQL_0020 = (ROOT / "db" / "migrations" / "0020_referral_hold_review_tasks.sql").read_text(encoding="utf-8")

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


def _u() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def db():
    dsn = (MIGRATOR or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    tx = conn.transaction()
    await tx.start()
    try:
        await ensure_app_role(conn)
        applied = await conn.fetchval("select count(*) from pg_policies where schemaname = 'agents' and tablename = 'review_tasks' and qual like '%referral_hold%'")
        if not applied:
            await conn.execute(SQL_0020)
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def as_app(conn, uid, sql, *, fetch=False):
    await conn.execute("savepoint as_app")
    try:
        await conn.execute("set local role careflow_app")
        claims = json.dumps({"sub": str(uid), "role": "authenticated"}) if uid else ""
        await conn.execute("select set_config('request.jwt.claims', $1, true)", claims)
        return await conn.fetch(sql) if fetch else await conn.execute(sql)
    except asyncpg.PostgresError as exc:
        return exc.sqlstate
    finally:
        await conn.execute("rollback to savepoint as_app")


class World:
    pass


async def world(conn) -> World:
    w = World()
    w.a, w.b = _u(), _u()
    for cid in (w.a, w.b):
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, 'Hold test', 'test')", cid)
    w.people = {}
    roster = [
        ("worker_a", w.a, "queue_worker"), ("nurse_a", w.a, "nurse"), ("doctor_a", w.a, "doctor"), ("fd_a", w.a, "front_desk"), ("fd_a2", w.a, "front_desk"),
        ("admin_a", w.a, "admin"), ("auditor_a", w.a, "auditor"), ("fd_b", w.b, "front_desk"), ("nurse_b", w.b, "nurse"),
    ]
    for key, clinic, role in roster:
        w.people[key] = _u()
        await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", w.people[key])
        await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", w.people[key], clinic, role)
    w.tasks = {}
    for name, kind, role, priority in (
        ("hold_fd", "referral_hold", "front_desk", "standard"), ("hold_nurse", "referral_hold", "nurse", "standard"),
        ("red_flag", "red_flag", "nurse", "immediate"), ("triage", "triage_proposal", "nurse", "standard"), ("unmerge", "unmerge_review", "front_desk", "standard"),
    ):
        w.tasks[name] = _u()
        await conn.execute(
            "insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
            "values ($1, $2, 'test', $3, $4, $5, 'document:x', '{}'::jsonb, $6)", w.tasks[name], w.a, kind, priority, role, f"t-{name}-{w.tasks[name]}",
        )
    return w


def hold_insert(w, role, key):
    return (
        "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
        f"values ('{w.a}', 'test', 'referral_hold', 'standard', '{role}', 'document:y', '{{\"reasons\": [\"dob_missing\"]}}'::jsonb, '{key}')"
    )


def resolve(task, by):
    return (
        f"update agents.review_tasks set status = 'resolved', acknowledged_by = '{by}', acknowledged_at = now(), resolved_at = now() where id = '{task}'"
    )


async def kinds_seen(db, w, who):
    rows = await as_app(db, w.people[who], "select kind, assigned_role from agents.review_tasks", fetch=True)
    return sorted((r["kind"], r["assigned_role"]) for r in rows)


async def test_front_desk_sees_only_its_own_referral_holds_plus_unmerge_reviews(db):
    w = await world(db)
    assert await kinds_seen(db, w, "fd_a") == [("referral_hold", "front_desk"), ("unmerge_review", "front_desk")]
    assert await kinds_seen(db, w, "fd_a2") == [("referral_hold", "front_desk"), ("unmerge_review", "front_desk")]


async def test_nurse_doctor_and_worker_see_every_task_of_the_clinic_and_nobody_else_sees_holds(db):
    w = await world(db)
    everything = sorted([("referral_hold", "front_desk"), ("referral_hold", "nurse"), ("red_flag", "nurse"), ("triage_proposal", "nurse"), ("unmerge_review", "front_desk")])
    for who in ("nurse_a", "doctor_a", "worker_a"):
        assert await kinds_seen(db, w, who) == everything, who
    assert await kinds_seen(db, w, "admin_a") == [("unmerge_review", "front_desk")]  # unchanged: admin sees unmerge reviews only
    for who in ("auditor_a", "fd_b", "nurse_b"):
        assert await kinds_seen(db, w, who) == [], who
    assert await as_app(db, None, "select 1 from agents.review_tasks", fetch=True) == []


async def test_a_nurse_assigned_hold_the_red_flag_and_the_triage_task_stay_invisible_to_front_desk(db):
    w = await world(db)
    ids = {r["id"] for r in await as_app(db, w.people["fd_a"], "select id from agents.review_tasks", fetch=True)}
    assert w.tasks["hold_nurse"] not in ids and w.tasks["red_flag"] not in ids and w.tasks["triage"] not in ids and w.tasks["hold_fd"] in ids


async def test_front_desk_can_resolve_its_own_hold_only_as_themselves_and_not_a_nurse_hold(db):
    w = await world(db)
    p = w.people
    assert await as_app(db, p["fd_a"], resolve(w.tasks["hold_fd"], p["fd_a"])) == "UPDATE 1"
    assert await as_app(db, p["fd_a"], resolve(w.tasks["hold_fd"], p["fd_a2"])) == "42501"  # naming somebody else as the one who resolved it
    assert await as_app(db, p["fd_a"], resolve(w.tasks["hold_nurse"], p["fd_a"])) == "UPDATE 0"  # not theirs: the row is not even visible to update
    assert await as_app(db, p["fd_a"], resolve(w.tasks["red_flag"], p["fd_a"])) == "UPDATE 0"
    assert await as_app(db, p["fd_b"], resolve(w.tasks["hold_fd"], p["fd_b"])) == "UPDATE 0"  # another clinic
    assert await as_app(db, p["admin_a"], resolve(w.tasks["hold_fd"], p["admin_a"])) == "UPDATE 0"


async def test_nurse_and_doctor_can_still_resolve_any_task_as_themselves(db):
    w = await world(db)
    p = w.people
    for who, task in (("nurse_a", "hold_nurse"), ("doctor_a", "hold_fd"), ("nurse_a", "red_flag")):
        assert await as_app(db, p[who], resolve(w.tasks[task], p[who])) == "UPDATE 1", (who, task)
    assert await as_app(db, p["nurse_a"], resolve(w.tasks["hold_nurse"], p["doctor_a"])) == "42501"


async def test_the_worker_can_raise_holds_for_front_desk_and_nurse_and_front_desk_cannot_raise_one(db):
    w = await world(db)
    p = w.people
    assert await as_app(db, p["worker_a"], hold_insert(w, "front_desk", "h-1")) == "INSERT 0 1"
    assert await as_app(db, p["worker_a"], hold_insert(w, "nurse", "h-2")) == "INSERT 0 1"
    assert await as_app(db, p["fd_a"], hold_insert(w, "front_desk", "h-3")) == "42501"  # insert is unchanged: front desk may insert unmerge reviews only
    assert await as_app(db, p["admin_a"], hold_insert(w, "nurse", "h-4")) == "42501"  # admin may insert unmerge reviews only


async def test_the_policy_set_keeps_its_three_names_and_the_new_clause_is_present(db):
    rows = await db.fetch("select policyname, qual, with_check from pg_policies where schemaname = 'agents' and tablename = 'review_tasks' order by policyname")
    assert [r["policyname"] for r in rows] == ["review_tasks_insert", "review_tasks_select", "review_tasks_update"]
    by = {r["policyname"]: r for r in rows}
    assert "referral_hold" in by["review_tasks_select"]["qual"] and "referral_hold" in by["review_tasks_update"]["qual"]
    assert "referral_hold" not in (by["review_tasks_insert"]["with_check"] or "")
