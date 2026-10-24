"""Migration 0018, against the real hosted project: agents.triage_proposals and agents.review_decisions (BUILD-SPEC.md §12; CLAUDE.md rules 8 and 9).

One transaction that is always rolled back: if 0018 is not applied yet it is executed in that transaction first (it needs 0017 and 0013 to exist), so this verifies the file
without applying it. Queries run as the restricted `careflow_app` role (SET LOCAL ROLE). Nothing is committed.
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
SQL_0018 = (ROOT / "db" / "migrations" / "0018_agents_triage_proposals_and_decisions.sql").read_text(encoding="utf-8")

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
SHA = "a" * 64


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
        if await conn.fetchval("select to_regclass('agents.triage_proposals') is null"):
            await conn.execute(SQL_0018)
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def outcome(conn, sql):
    await conn.execute("savepoint attempt")
    try:
        await conn.execute(sql)
    except asyncpg.PostgresError as exc:
        await conn.execute("rollback to savepoint attempt")
        return exc.sqlstate
    await conn.execute("release savepoint attempt")
    return None


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
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, 'Triage test', 'test')", cid)
    w.people = {}
    roster = [
        ("front_desk_a", w.a, "front_desk"), ("nurse_a", w.a, "nurse"), ("doctor_a", w.a, "doctor"), ("worker_a", w.a, "queue_worker"),
        ("admin_a", w.a, "admin"), ("owner_a", w.a, "business_owner"), ("auditor_a", w.a, "auditor"),
        ("nurse_b", w.b, "nurse"), ("worker_b", w.b, "queue_worker"), ("front_desk_b", w.b, "front_desk"),
    ]
    for key, clinic, role in roster:
        w.people[key] = _u()
        await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", w.people[key])
        await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", w.people[key], clinic, role)
    w.patient_a, w.patient_b = _u(), _u()
    for pid, cid in ((w.patient_a, w.a), (w.patient_b, w.b)):
        await conn.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
            "values ($1, $2, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', $3)", pid, cid, f"pat-{pid}",
        )
    w.doc_a, w.doc_b = _u(), _u()
    for did, cid, by in ((w.doc_a, w.a, w.people["front_desk_a"]), (w.doc_b, w.b, w.people["front_desk_b"])):
        await conn.execute(
            "insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) "
            "values ($1, $2, 'test', 'referral', $3, 'application/pdf', 1000, $4, $5, $6)", did, cid, f"clinic/{cid}/doc/{did}", SHA, by, f"d-{did}",
        )
    w.task_a, w.task_b = _u(), _u()
    for tid, cid in ((w.task_a, w.a), (w.task_b, w.b)):
        await conn.execute(
            "insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
            "values ($1, $2, 'test', 'triage_proposal', 'standard', 'nurse', 'document:x', '{}'::jsonb, $3)", tid, cid, f"t-{tid}",
        )
    return w


def proposal_sql(w, *, clinic=None, doc=None, patient=None, task=None, pid=None, candidates="'[]'", abstained="true", mode="assisted", key=None, origin="'test'"):
    pid = pid or _u()
    return (
        "insert into agents.triage_proposals (id, clinic_id, origin, document_id, patient_id, candidates, abstained, mode, review_task_id, idempotency_key) values "
        f"('{pid}', '{clinic or w.a}', {origin}, '{doc or w.doc_a}', '{patient or w.patient_a}', {candidates}::jsonb, {abstained}, '{mode}', '{task or w.task_a}', '{key or f'k-{_u()}'}')"
    )


async def seed_proposal(db, w, **kw):
    pid = _u()
    await db.execute(proposal_sql(w, pid=pid, **kw))
    return pid


def decision_sql(clinic, proposal, by, *, decision="approve", key=None):
    return (
        "insert into agents.review_decisions (clinic_id, origin, proposal_id, decision, decided_by, idempotency_key) values "
        f"('{clinic}', 'test', '{proposal}', '{decision}', '{by}', '{key or f'k-{_u()}'}')"
    )


async def test_forced_rls_append_only_and_column_limited_inserts(db):
    for table in ("agents.triage_proposals", "agents.review_decisions"):
        row = await db.fetchrow("select relrowsecurity, relforcerowsecurity from pg_class where oid = $1::regclass", table)
        assert row["relrowsecurity"] and row["relforcerowsecurity"], table
        for priv in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not await db.fetchval("select has_table_privilege('careflow_app', $1, $2)", table, priv), (table, priv)
        assert not await db.fetchval("select has_any_column_privilege('careflow_app', $1, 'UPDATE')", table), table  # a column-level grant would not show above
        assert await db.fetchval("select has_table_privilege('careflow_app', $1, 'SELECT')", table)
        assert not await db.fetchval("select has_column_privilege('careflow_app', $1, 'created_at', 'INSERT')", table)
        for role in ("anon", "authenticated", "service_role"):
            if await db.fetchval("select exists (select 1 from pg_roles where rolname = $1)", role):
                assert not await db.fetchval("select has_table_privilege($1, $2, 'SELECT')", role, table), (role, table)


async def test_the_owner_cannot_edit_either_table_either(db):
    w = await world(db)
    pid = await seed_proposal(db, w)
    await db.execute(decision_sql(w.a, pid, w.people["nurse_a"]))
    for stmt in (
        "update agents.triage_proposals set abstained = false", "delete from agents.triage_proposals", "truncate agents.triage_proposals cascade",
        "update agents.review_decisions set decision = 'reject'", "delete from agents.review_decisions", "truncate agents.review_decisions",
    ):
        assert await outcome(db, stmt) == "23001", stmt  # restrict_violation


async def test_only_the_worker_nurse_and_doctor_of_the_clinic_can_write_a_proposal(db):
    w = await world(db)
    p = w.people
    for who in ("worker_a", "nurse_a"):
        assert await as_app(db, p[who], proposal_sql(w, key=f"k-{who}", doc=w.doc_a)) == "INSERT 0 1", who
    for who in ("front_desk_a", "admin_a", "owner_a", "auditor_a", "nurse_b", "worker_b", "front_desk_b"):
        assert await as_app(db, p[who], proposal_sql(w)) == "42501", who
    assert await as_app(db, None, proposal_sql(w)) == "42501"


async def test_a_proposal_cannot_point_at_another_clinics_document_patient_or_task_and_origin_is_pinned(db):
    w = await world(db)
    assert await outcome(db, proposal_sql(w)) is None  # control
    assert await outcome(db, proposal_sql(w, doc=w.doc_b)) == "23503"
    assert await outcome(db, proposal_sql(w, patient=w.patient_b)) == "23503"
    assert await outcome(db, proposal_sql(w, task=w.task_b)) == "23503"
    assert await outcome(db, proposal_sql(w, origin="'live'")) == "23503"  # patient and task are origin 'test'
    assert await outcome(db, proposal_sql(w, origin="null")) == "23502"


async def test_proposal_constraints_one_per_document_key_per_clinic_abstained_means_no_candidates(db):
    w = await world(db)
    assert await outcome(db, proposal_sql(w, key="same")) is None
    assert await outcome(db, proposal_sql(w, key="same")) == "23505"
    assert await outcome(db, proposal_sql(w, key="other")) == "23505"  # a second proposal for the same document
    assert await outcome(db, proposal_sql(w, abstained="true", candidates="'[{\"card_id\": \"c\", \"version\": 1}]'", doc=w.doc_a)) == "23514"  # CHECK constraints are evaluated before any unique key
    w2 = await world(db)
    assert await outcome(db, proposal_sql(w2, abstained="false", candidates="'[{\"card_id\": \"c\", \"version\": 1}]'")) is None
    assert await outcome(db, proposal_sql(w2, candidates="'{}'", doc=w2.doc_a, task=w2.task_a)) == "23514"
    assert await outcome(db, proposal_sql(w2, mode="fully_automatic")) == "23514"


async def test_there_is_no_column_that_can_hold_a_priority_or_card_content(db):
    cols = {r["column_name"] for r in await db.fetch("select column_name from information_schema.columns where table_schema = 'agents' and table_name = 'triage_proposals'")}
    assert not {c for c in cols if "priority" in c or "title" in c or "text" in c or "note" in c}


async def test_only_nurse_doctor_and_worker_of_the_clinic_read_proposals(db):
    w = await world(db)
    p = w.people
    await db.execute(proposal_sql(w))
    for who in ("nurse_a", "doctor_a", "worker_a"):
        got = await as_app(db, p[who], "select clinic_id from agents.triage_proposals", fetch=True)
        assert {r["clinic_id"] for r in got} == {w.a}, who
    for who in ("front_desk_a", "admin_a", "owner_a", "auditor_a", "nurse_b", "worker_b", "front_desk_b"):
        assert await as_app(db, p[who], "select 1 from agents.triage_proposals", fetch=True) == [], who
    assert await as_app(db, None, "select 1 from agents.triage_proposals", fetch=True) == []


async def test_only_a_nurse_or_doctor_of_the_clinic_can_record_a_decision_and_only_as_themselves(db):
    w = await world(db)
    p = w.people
    pid = await seed_proposal(db, w)
    assert await as_app(db, p["nurse_a"], decision_sql(w.a, pid, p["nurse_a"])) == "INSERT 0 1"
    for who in ("worker_a", "front_desk_a", "admin_a", "owner_a", "auditor_a", "nurse_b"):
        assert await as_app(db, p[who], decision_sql(w.a, pid, p[who])) == "42501", who
    assert await as_app(db, p["nurse_a"], decision_sql(w.a, pid, p["doctor_a"])) == "42501"  # naming somebody else as the decider
    assert await as_app(db, None, decision_sql(w.a, pid, p["nurse_a"])) == "42501"


async def test_a_decision_always_names_a_human_there_is_no_automatic_or_timeout_row(db):
    w = await world(db)
    pid = await seed_proposal(db, w)
    sql = f"insert into agents.review_decisions (clinic_id, origin, proposal_id, decision, idempotency_key) values ('{w.a}', 'test', '{pid}', 'approve', 'auto-1')"
    assert await outcome(db, sql) == "23502"  # decided_by is NOT NULL
    assert await outcome(db, decision_sql(w.a, pid, w.people["nurse_a"], decision="approve_by_timeout")) == "23514"
    cols = {r["column_name"] for r in await db.fetch("select column_name from information_schema.columns where table_schema = 'agents' and table_name = 'review_decisions'")}
    assert "decision_source" not in cols and "auto" not in " ".join(cols)


async def test_one_decision_per_proposal_a_decision_cannot_cross_clinics_and_the_key_is_per_clinic(db):
    w = await world(db)
    pid = await seed_proposal(db, w)
    assert await outcome(db, decision_sql(w.a, pid, w.people["nurse_a"], key="d1")) is None
    assert await outcome(db, decision_sql(w.a, pid, w.people["doctor_a"], key="d2")) == "23505"  # a second decision on the same proposal
    assert await outcome(db, decision_sql(w.b, pid, w.people["nurse_b"])) == "23503"  # clinic B cannot decide clinic A's proposal


async def test_only_nurse_doctor_and_worker_read_decisions(db):
    w = await world(db)
    p = w.people
    pid = await seed_proposal(db, w)
    await db.execute(decision_sql(w.a, pid, p["nurse_a"]))
    for who in ("nurse_a", "doctor_a", "worker_a"):
        assert len(await as_app(db, p[who], "select 1 from agents.review_decisions", fetch=True)) == 1, who
    for who in ("front_desk_a", "admin_a", "owner_a", "auditor_a", "nurse_b", "worker_b"):
        assert await as_app(db, p[who], "select 1 from agents.review_decisions", fetch=True) == [], who


async def test_a_decision_cannot_carry_a_different_origin_than_its_proposal(db):
    w = await world(db)
    pid = await seed_proposal(db, w)  # origin 'test'
    sql = (
        "insert into agents.review_decisions (clinic_id, origin, proposal_id, decision, decided_by, idempotency_key) values "
        f"('{w.a}', 'live', '{pid}', 'approve', '{w.people['nurse_a']}', 'o-1')"
    )
    assert await outcome(db, sql) == "23503"


async def test_a_patient_account_a_revoked_grant_and_a_suspended_account_get_nothing_and_a_doctor_is_a_valid_decider(db):
    w = await world(db)
    p = w.people
    pid = await seed_proposal(db, w)
    patient_like, revoked, suspended = _u(), _u(), _u()
    for acct, status, kind in ((patient_like, "active", "patient"), (revoked, "active", "staff"), (suspended, "suspended", "staff")):
        await db.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, $2, $3, 'test')", acct, kind, status)
        await db.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", acct, w.a)
    await db.execute("update iam.staff_roles set revoked_at = now() where account_id = $1", revoked)
    for who in (patient_like, revoked, suspended):
        assert await as_app(db, who, decision_sql(w.a, pid, who)) == "42501", who
        assert await as_app(db, who, "select 1 from agents.triage_proposals", fetch=True) == [], who
    assert await as_app(db, p["doctor_a"], decision_sql(w.a, pid, p["doctor_a"])) == "INSERT 0 1"
    assert len(await as_app(db, p["doctor_a"], "select 1 from agents.triage_proposals", fetch=True)) == 1
