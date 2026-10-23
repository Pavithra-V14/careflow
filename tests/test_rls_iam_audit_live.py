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
        await conn.execute("grant careflow_app to current_user with set true")
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
        "mpi": {"patients", "identity_tokens", "match_candidates", "merges"},
        "sched": {"locations", "providers", "visit_types", "slots", "priority_levels", "holds", "bookings", "waitlist"},
        "ops": {"queue", "outbox", "key_rotation_batches"},
        "kb": {"red_flag_rules"},
        "agents": {"review_tasks"},
        "msg": {"threads", "messages", "message_rotation_batches", "screenings"},  # migrations 0011 and 0013
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


async def _audit_insert(conn, actor, claim_uid, key):
    """Insert as careflow_app. On failure the transaction is aborted; the caller rolls back to its
    own savepoint, which also restores the migrator role."""
    await as_app(conn, claim_uid)
    await conn.execute(
        "insert into audit.log (origin, actor_id, action, idempotency_key) values ('test', $1, 'test.action', $2)",
        actor, key,
    )
    await back_to_migrator(conn)


async def test_audit_insert_only_as_self(db):
    _, _, p = await seed(db)
    await _audit_insert(db, p["nurse_a"], p["nurse_a"], "k-self")  # own id: allowed
    await _audit_insert(db, None, None, "k-system")  # no identity, system row: allowed
    for actor, claim, key in ((p["admin_a"], p["nurse_a"], "k-forged"), (None, p["nurse_a"], "k-anon-forge")):
        await db.execute("savepoint f")
        with pytest.raises(asyncpg.PostgresError):
            await _audit_insert(db, actor, claim, key)
        await db.execute("rollback to savepoint f")


async def test_audit_idempotency_key_rejects_duplicates(db):
    _, _, p = await seed(db)
    await _audit_insert(db, p["nurse_a"], p["nurse_a"], "k-dup")
    await db.execute("savepoint d")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _audit_insert(db, p["nurse_a"], p["nurse_a"], "k-dup")
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
