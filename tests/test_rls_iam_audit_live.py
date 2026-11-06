"""RLS and immutability of migration 0001 (iam + audit), against the real hosted project.

BUILD-SPEC.md §17.1, §18.4, §21. Everything runs inside one transaction that is always rolled back:
if 0001 has not been applied yet it is executed in that transaction first, so this verifies the file
without applying it; once it is applied the same assertions run against the real tables. Either way
the database is left exactly as found. Queries run as the restricted `careflow_app` role (SET LOCAL
ROLE), never as the migrator, so RLS is genuinely exercised. Skipped only when
DATABASE_URL_MIGRATOR is not configured.
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
SQL_0001 = (ROOT / "db" / "migrations" / "0001_initial_schema.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

NINE_EMPTY = ["mpi", "sched", "clinical", "docs", "msg", "kb", "agents", "ai", "ops"]


def _u() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def db():
    """A migrator connection inside a transaction that is always rolled back."""
    dsn = (MIGRATOR or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    tx = conn.transaction()
    await tx.start()
    try:
        # The migrator may not SET ROLE to careflow_app by default; grant that inside this
        # transaction only (rolled back with everything else) so queries can run as the real role.
        await ensure_app_role(conn)
        if await conn.fetchval("select to_regclass('iam.accounts') is null"):
            await conn.execute(SQL_0001)
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def seed(conn):
    """Two clinics; an admin, a nurse and an auditor at A; an admin at B; a suspended nurse at A."""
    a, b = _u(), _u()
    for cid, name in ((a, "Clinic A"), (b, "Clinic B")):
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, $2, 'test')", cid, name)
    people = {}
    for key, clinic, role, status in [
        ("admin_a", a, "admin", "active"),
        ("nurse_a", a, "nurse", "active"),
        ("auditor_a", a, "auditor", "active"),
        ("admin_b", b, "admin", "active"),
        ("suspended_a", a, "nurse", "suspended"),
    ]:
        pid = _u()
        people[key] = pid
        await conn.execute(
            "insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', $2, 'test')", pid, status
        )
        await conn.execute(
            "insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')",
            pid, clinic, role,
        )
    patient = _u()
    await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", patient)
    people["patient"] = patient
    return a, b, people


async def as_app(conn, uid):
    """Become careflow_app with verified claims set transaction-locally (§18.1 mechanism)."""
    await conn.execute("set local role careflow_app")
    claims = json.dumps({"sub": str(uid), "role": "authenticated"}) if uid else ""
    await conn.execute("select set_config('request.jwt.claims', $1, true)", claims)


async def back_to_migrator(conn):
    await conn.execute("reset role")


async def visible(conn, uid, sql):
    await conn.execute("savepoint s")
    try:
        await as_app(conn, uid)
        return await conn.fetch(sql)
    finally:
        await back_to_migrator(conn)
        await conn.execute("rollback to savepoint s")


async def test_all_eleven_schemas_exist_and_nine_have_no_tables(db):
    names = {r["nspname"] for r in await db.fetch("select nspname from pg_namespace")}
    assert {"iam", "audit", *NINE_EMPTY} <= names
    rows = await db.fetch(
        "select schemaname, tablename from pg_tables where schemaname = any($1::text[])", NINE_EMPTY
    )
    found: dict[str, set[str]] = {}
    for r in rows:
        found.setdefault(r["schemaname"], set()).add(r["tablename"])
    # 0001 created these nine empty. Later phases add tables only where their migration says so; any other
    # table appearing here is unplanned, so this list is updated in the same change as the migration.
    assert found == {
        "mpi": {"patients", "identity_tokens", "match_candidates", "merges", "identity_confirmations", "erasure_requests"},  # identity_confirmations: migration 0023; erasure_requests: 0032
        "sched": {"locations", "providers", "visit_types", "slots", "priority_levels", "holds", "bookings", "waitlist", "offer_deliveries"},  # offer_deliveries: 0031
        "ops": {"queue", "outbox", "key_rotation_batches", "spend_ledger", "alerts", "care_recurrence_rules", "care_episodes", "care_episode_rules", "care_barriers", "care_review_samples"},  # spend_ledger: 0016; care_*: 0044
        "kb": {"red_flag_rules"},
        "agents": {"review_tasks", "triage_proposals", "review_decisions", "graph_checkpoints", "graph_checkpoint_blobs", "graph_checkpoint_writes"},  # 0018, 0021
        "ai": {"model_registry"},  # migration 0015
        "docs": {"documents"},  # migration 0017
        "msg": {"threads", "messages", "message_rotation_batches", "screenings", "voice_preferences", "voice_sessions", "voice_transcript_chunks"},  # migrations 0011, 0013 and 0045
    }


async def test_every_table_has_forced_rls(db):
    rows = await db.fetch(
        """select c.relname, c.relrowsecurity, c.relforcerowsecurity from pg_class c
           join pg_namespace n on n.oid = c.relnamespace
           where n.nspname in ('iam', 'audit') and c.relkind = 'r'"""
    )
    assert {r["relname"] for r in rows} == {"clinics", "accounts", "staff_roles", "log"}
    assert all(r["relrowsecurity"] and r["relforcerowsecurity"] for r in rows)


async def test_supabase_api_roles_have_no_access(db):
    for role in ("anon", "authenticated"):
        for table in ("iam.clinics", "iam.accounts", "iam.staff_roles", "audit.log"):
            assert not await db.fetchval("select has_table_privilege($1, $2, 'select')", role, table)


async def test_no_identity_sees_nothing(db):
    await seed(db)
    for uid in (None, _u()):
        for table in ("iam.clinics", "iam.accounts", "iam.staff_roles"):
            assert await visible(db, uid, f"select * from {table}") == []


async def test_staff_see_only_their_own_clinic(db):
    a, b, p = await seed(db)
    assert [r["id"] for r in await visible(db, p["nurse_a"], "select id from iam.clinics")] == [a]
    assert [r["id"] for r in await visible(db, p["admin_b"], "select id from iam.clinics")] == [b]


async def test_nurse_sees_only_own_account_and_grant(db):
    _, _, p = await seed(db)
    assert [r["id"] for r in await visible(db, p["nurse_a"], "select id from iam.accounts")] == [p["nurse_a"]]
    grants = await visible(db, p["nurse_a"], "select account_id from iam.staff_roles")
    assert [r["account_id"] for r in grants] == [p["nurse_a"]]


async def test_clinic_admin_sees_their_clinics_staff_but_not_another_clinics(db):
    _, _, p = await seed(db)
    seen = {r["id"] for r in await visible(db, p["admin_a"], "select id from iam.accounts")}
    assert {p["admin_a"], p["nurse_a"], p["auditor_a"], p["suspended_a"]} <= seen
    assert p["admin_b"] not in seen and p["patient"] not in seen
    grants = await visible(db, p["admin_a"], "select account_id from iam.staff_roles")
    assert p["admin_b"] not in {r["account_id"] for r in grants}


async def test_suspended_staff_gain_no_clinic_access(db):
    _, _, p = await seed(db)
    assert await visible(db, p["suspended_a"], "select id from iam.clinics") == []


async def test_revoked_grant_stops_granting_access(db):
    _, _, p = await seed(db)
    await db.execute("update iam.staff_roles set revoked_at = now() where account_id = $1", p["nurse_a"])
    assert await visible(db, p["nurse_a"], "select id from iam.clinics") == []


async def test_app_role_cannot_write_iam(db):
    _, _, p = await seed(db)
    for stmt in (
        "insert into iam.clinics (name, origin) values ('x', 'test')",
        "update iam.accounts set status = 'active'",
        "delete from iam.staff_roles",
    ):
        await db.execute("savepoint s")
        await as_app(db, p["admin_a"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db.execute(stmt)
        await db.execute("rollback to savepoint s")  # also restores the migrator role


async def _audit_insert(conn, actor, claim_uid, key, clinic=None):
    """Insert as careflow_app. On failure the transaction is aborted; the caller rolls back to its
    own savepoint, which also restores the migrator role."""
    await as_app(conn, claim_uid)
    await conn.execute(
        "insert into audit.log (origin, clinic_id, actor_id, action, idempotency_key) values ('test', $3, $1, 'test.action', $2)",
        actor, key, clinic,
    )
    await back_to_migrator(conn)


async def test_audit_insert_only_as_self_in_the_callers_own_clinic(db):
    """0030 (review M3): the actor is the signed-in caller, the row names a clinic, and the caller holds a live grant AT THAT clinic."""
    a, b, p = await seed(db)
    await _audit_insert(db, p["nurse_a"], p["nurse_a"], "k-self", a)  # own id, own clinic: allowed
    refused = (
        (p["admin_a"], p["nurse_a"], "k-forged", a),  # another person's id
        (None, p["nurse_a"], "k-anon-forge", a),  # no actor while signed in
        (None, None, "k-system", a),  # no identity at all: the application role no longer writes system rows
        (p["nurse_a"], p["nurse_a"], "k-no-clinic", None),  # no clinic
        (p["nurse_a"], p["nurse_a"], "k-other-clinic", b),  # a clinic where the caller holds no grant
        (p["admin_b"], p["admin_b"], "k-b-into-a", a),  # a clinic B admin writing a clinic A row
        (p["suspended_a"], p["suspended_a"], "k-suspended", a),  # a suspended account
        (p["patient"], p["patient"], "k-patient", a),  # a patient account
        (p["auditor_a"], p["auditor_a"], "k-auditor", a),  # an auditor does not write into the trail it reads
    )
    for actor, claim, key, clinic in refused:
        await db.execute("savepoint f")
        with pytest.raises(asyncpg.PostgresError):
            await _audit_insert(db, actor, claim, key, clinic)
        await db.execute("rollback to savepoint f")
    assert await db.fetchval("select count(*) from audit.log where idempotency_key like 'k-%' and idempotency_key <> 'k-self'") == 0


async def test_the_queue_accepts_only_a_clean_new_job_from_a_producer(db):
    """0030 (review M4, writes): a producer cannot insert a job that pretends to be claimed, completed, dead-lettered, retried or errored."""
    a, _, p = await seed(db)
    sql = "insert into ops.queue (clinic_id, origin, task_type, payload, idempotency_key{cols}) values ($1, 'test', 'demo.task', '{{}}'::jsonb, $2{vals})"
    await as_app(db, p["nurse_a"])
    await db.execute(sql.format(cols="", vals=""), a, f"q-clean-{_u()}")  # control: a clean job is accepted
    await back_to_migrator(db)
    pretending = (
        (", state", ", 'claimed'"), (", state", ", 'completed'"), (", attempt_count", ", 3"), (", claimed_by", ", 'worker-1'"),
        (", last_error", ", 'boom'"), (", dead_lettered_at", ", now()"), (", completed_at", ", now()"), (", claimed_at", ", now()"),
    )
    for cols, vals in pretending:
        await db.execute("savepoint q")
        with pytest.raises(asyncpg.PostgresError):
            await as_app(db, p["nurse_a"])
            await db.execute(sql.format(cols=cols, vals=vals), a, f"q-bad-{_u()}")
        await db.execute("rollback to savepoint q")


async def test_audit_idempotency_key_rejects_duplicates(db):
    a, _, p = await seed(db)
    await _audit_insert(db, p["nurse_a"], p["nurse_a"], "k-dup", a)
    await db.execute("savepoint d")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _audit_insert(db, p["nurse_a"], p["nurse_a"], "k-dup", a)
    await db.execute("rollback to savepoint d")


async def test_audit_readable_only_by_auditor_of_that_clinic(db):
    a, b, p = await seed(db)
    await db.execute(
        "insert into audit.log (origin, clinic_id, action, idempotency_key) values ('test', $1, 'x', 'ra'), ('test', $2, 'x', 'rb')",
        a, b,
    )
    assert len(await visible(db, p["auditor_a"], "select * from audit.log")) == 1
    for who in ("admin_a", "nurse_a", "admin_b", "patient"):
        assert await visible(db, p[who], "select * from audit.log") == [], who


async def test_audit_log_cannot_be_edited_even_by_its_owner(db):
    await db.execute("insert into audit.log (origin, action, idempotency_key) values ('test', 'x', 'imm')")
    for stmt in ("update audit.log set action = 'y'", "delete from audit.log", "truncate audit.log"):
        await db.execute("savepoint m")
        with pytest.raises(asyncpg.PostgresError) as exc:
            await db.execute(stmt)
        assert "append-only" in str(exc.value)
        await db.execute("rollback to savepoint m")


async def test_app_role_cannot_edit_audit_log(db):
    _, _, p = await seed(db)
    await db.execute("insert into audit.log (origin, action, idempotency_key) values ('test', 'x', 'imm2')")
    for stmt in ("update audit.log set action = 'y'", "delete from audit.log", "truncate audit.log"):
        await db.execute("savepoint m")
        await as_app(db, p["auditor_a"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db.execute(stmt)
        await db.execute("rollback to savepoint m")  # also restores the migrator role
