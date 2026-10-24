"""Migration 0016, against the real hosted project: the append-only spend ledger (ops.spend_ledger) and the database-enforced approver of a
phi_approved model (ai.model_registry). BUILD-SPEC.md §5.1, §6.1, §15.2, §18.4, §21; CLAUDE.md rule 8.

One transaction that is always rolled back: if 0015 / 0016 are not applied yet they are executed in that transaction first, so this verifies the
files without applying them. Queries run as the restricted `careflow_app` role (SET LOCAL ROLE), never as the migrator. Nothing is committed.
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
SQL_0015 = (ROOT / "db" / "migrations" / "0015_ai_model_registry.sql").read_text(encoding="utf-8")
SQL_0016 = (ROOT / "db" / "migrations" / "0016_spend_ledger_and_registry_approval.sql").read_text(encoding="utf-8")

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
        if await conn.fetchval("select to_regclass('ai.model_registry') is null"):
            await conn.execute(SQL_0015)
        if await conn.fetchval("select to_regclass('ops.spend_ledger') is null"):
            await conn.execute(SQL_0016)
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def outcome(conn, sql, *args):
    """The SQLSTATE of running `sql` as the migrator in a savepoint, or None if it worked."""
    await conn.execute("savepoint attempt")
    try:
        await conn.execute(sql, *args)
    except asyncpg.PostgresError as exc:
        await conn.execute("rollback to savepoint attempt")
        return exc.sqlstate
    await conn.execute("release savepoint attempt")
    return None


async def as_app(conn, uid, sql, *, fetch=False):
    """Run `sql` as careflow_app with `uid`'s claims in a savepoint. Returns the rows, or the SQLSTATE of the failure."""
    await conn.execute("savepoint as_app")
    try:
        await conn.execute("set local role careflow_app")
        await conn.execute("select set_config('request.jwt.claims', $1, true)", json.dumps({"sub": str(uid), "role": "authenticated"}) if uid else "")
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
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, 'Spend test', 'test')", cid)
    w.people = {}

    async def person(key, clinic, role, *, status="active", kind="staff", revoked=False):
        pid = _u()
        w.people[key] = pid
        await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, $2, $3, 'test')", pid, kind, status)
        if role:
            await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", pid, clinic, role)
            if revoked:  # in SQL, so the database clock decides (revoked_at must not precede granted_at)
                await conn.execute("update iam.staff_roles set revoked_at = now() + interval '1 second' where account_id = $1", pid)

    for key, clinic, role in [("worker_a", w.a, "queue_worker"), ("admin_a", w.a, "admin"), ("owner_a", w.a, "business_owner"),
                              ("auditor_a", w.a, "auditor"), ("nurse_a", w.a, "nurse"), ("doctor_a", w.a, "doctor"),
                              ("front_desk_a", w.a, "front_desk"), ("admin_b", w.b, "admin"), ("worker_b", w.b, "queue_worker")]:
        await person(key, clinic, role)
    await person("doctor_b", w.b, "doctor")
    await person("suspended_doctor", w.a, "doctor", status="suspended")
    await person("revoked_admin", w.a, "admin", revoked=True)
    await person("patient", None, None, kind="patient")
    return w


# =====================================================================================================================================
# PART A: ops.spend_ledger
# =====================================================================================================================================

LEDGER = ("insert into ops.spend_ledger (clinic_id, role, provider, model_id, call_class, usd_actual, usd_shadow, origin, idempotency_key) "
          "values ('{clinic}', '{role}', 'p', 'm-1', '{cls}', {actual}, {shadow}, {origin}, '{key}')")


def ledger_sql(clinic, *, role="decision", cls="ordinary", actual="0.5", shadow="0.7", origin="'test'", key=None):
    return LEDGER.format(clinic=clinic, role=role, cls=cls, actual=actual, shadow=shadow, origin=origin, key=key or f"k-{_u()}")


async def test_spend_ledger_has_forced_rls_and_only_the_privileges_it_needs(db):
    row = await db.fetchrow("select relrowsecurity, relforcerowsecurity from pg_class where oid = 'ops.spend_ledger'::regclass")
    assert row["relrowsecurity"] and row["relforcerowsecurity"]
    for priv in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await db.fetchval("select has_table_privilege('careflow_app', 'ops.spend_ledger', $1)", priv), priv
    assert await db.fetchval("select has_table_privilege('careflow_app', 'ops.spend_ledger', 'SELECT')")
    for column in ("clinic_id", "role", "provider", "model_id", "call_class", "usd_actual", "usd_shadow", "origin", "idempotency_key"):
        assert await db.fetchval("select has_column_privilege('careflow_app', 'ops.spend_ledger', $1, 'INSERT')", column), column
    for column in ("id", "day", "created_at"):  # always from the database
        assert not await db.fetchval("select has_column_privilege('careflow_app', 'ops.spend_ledger', $1, 'INSERT')", column), column
    for role in ("anon", "authenticated", "service_role"):
        if await db.fetchval("select exists (select 1 from pg_roles where rolname = $1)", role):
            assert not await db.fetchval("select has_table_privilege($1, 'ops.spend_ledger', 'SELECT')", role), role


async def test_only_a_queue_worker_of_the_clinic_can_insert(db):
    w = await world(db)
    assert await as_app(db, w.people["worker_a"], ledger_sql(w.a)) == "INSERT 0 1"
    for who in ("admin_a", "owner_a", "auditor_a", "nurse_a", "doctor_a", "front_desk_a", "patient", None):
        assert await as_app(db, w.people[who] if who else None, ledger_sql(w.a)) == "42501", who
    assert await as_app(db, w.people["worker_b"], ledger_sql(w.a)) == "42501"  # another clinic's worker
    assert await as_app(db, w.people["worker_a"], ledger_sql(w.b)) == "42501"  # a worker cannot file spend under another clinic


async def test_a_suspended_or_revoked_worker_cannot_insert_and_the_migrator_bypasses_rls(db):
    w = await world(db)
    await db.execute("update iam.accounts set status = 'suspended' where id = $1", w.people["worker_a"])
    assert await as_app(db, w.people["worker_a"], ledger_sql(w.a)) == "42501"
    await db.execute("update iam.accounts set status = 'active' where id = $1", w.people["worker_a"])
    await db.execute("update iam.staff_roles set revoked_at = now() + interval '1 second' where account_id = $1", w.people["worker_a"])
    assert await as_app(db, w.people["worker_a"], ledger_sql(w.a)) == "42501"
    # The approval trigger and has_clinic_role read forced-RLS tables as the owner: that holds only because the owner bypasses RLS.
    assert await db.fetchval("select rolbypassrls from pg_roles where rolname = current_user")


async def test_the_day_comes_from_the_database_clock_in_india_time_and_cannot_be_chosen(db):
    w = await world(db)
    await db.execute(ledger_sql(w.a))
    day, expected = await db.fetchval("select day from ops.spend_ledger where clinic_id = $1", w.a), await db.fetchval("select (clock_timestamp() at time zone 'Asia/Kolkata')::date")
    assert day == expected
    forged = ("insert into ops.spend_ledger (clinic_id, day, role, provider, model_id, call_class, usd_actual, usd_shadow, origin, idempotency_key) "
              f"values ('{w.a}', '2000-01-01', 'decision', 'p', 'm', 'ordinary', 1, 1, 'test', 'forged-{_u()}')")
    assert await as_app(db, w.people["worker_a"], forged) == "42501"  # no INSERT privilege on the column


async def test_the_ledger_is_readable_only_by_worker_admin_owner_and_auditor_of_the_clinic(db):
    w = await world(db)
    await db.execute(ledger_sql(w.a, key="read-a"))
    await db.execute(ledger_sql(w.b, key="read-b"))
    for who in ("worker_a", "admin_a", "owner_a", "auditor_a"):
        rows = await as_app(db, w.people[who], "select clinic_id from ops.spend_ledger", fetch=True)
        assert {r["clinic_id"] for r in rows} == {w.a}, who  # only its own clinic
    for who in ("nurse_a", "doctor_a", "front_desk_a", "patient", None):
        assert await as_app(db, w.people[who] if who else None, "select 1 from ops.spend_ledger", fetch=True) == [], who


async def test_the_worker_can_add_up_the_days_spend_for_its_clinic(db):
    w = await world(db)
    for actual in ("0.25", "0.75", "1.5"):
        await db.execute(ledger_sql(w.a, actual=actual))
    await db.execute(ledger_sql(w.b, actual="9"))
    rows = await as_app(db, w.people["worker_a"], "select coalesce(sum(usd_actual), 0) as spent from ops.spend_ledger where day = (clock_timestamp() at time zone 'Asia/Kolkata')::date", fetch=True)
    assert float(rows[0]["spent"]) == pytest.approx(2.5)


async def test_the_ledger_is_append_only_even_for_the_owner(db):
    w = await world(db)
    await db.execute(ledger_sql(w.a))
    assert await outcome(db, "update ops.spend_ledger set usd_actual = 0") == "23001"
    assert await outcome(db, "delete from ops.spend_ledger") == "23001"
    assert await outcome(db, "truncate ops.spend_ledger") == "23001"


@pytest.mark.parametrize(
    ("label", "kwargs", "state"),
    [
        ("a negative actual cost", {"actual": "-0.01"}, "23514"),
        ("a negative shadow cost", {"shadow": "-1"}, "23514"),
        ("an unknown role", {"role": "llm_huge"}, "23514"),
        ("an unknown call class", {"cls": "special"}, "23514"),
        ("no origin", {"origin": "null"}, "23502"),
        ("a bad origin", {"origin": "'prod'"}, "23514"),
    ],
)
async def test_the_ledger_refuses_invalid_rows(db, label, kwargs, state):
    w = await world(db)
    assert await outcome(db, ledger_sql(w.a, **kwargs)) == state, label


async def test_the_ledger_idempotency_key_is_unique_per_clinic_and_a_free_call_is_recorded_with_its_shadow_cost(db):
    w = await world(db)
    assert await outcome(db, ledger_sql(w.a, actual="0", shadow="0.02", key="dup")) is None
    assert await outcome(db, ledger_sql(w.a, key="dup")) == "23505"  # the same clinic cannot reuse a key
    assert await outcome(db, ledger_sql(w.b, key="dup")) is None  # another clinic's worker cannot collide with it, nor learn it exists
    assert await outcome(db, ledger_sql(w.a, cls="safety", key="safety-1")) is None  # the safety class is recorded too


# =====================================================================================================================================
# PART B: the approver of a phi_approved model
# =====================================================================================================================================


def model_sql(*, policy="phi_approved", created_by="null", approved_by="null", approved_at="now()", model=None, key=None):
    return (
        "insert into ai.model_registry (role, provider, model_id, data_policy, priority, unit, price_in_per_1k, price_out_per_1k, shadow_in_per_1k, "
        "shadow_out_per_1k, created_by, approved_by, approved_at, origin, idempotency_key) values "
        f"('decision', 'p', '{model or f'm-{_u().hex[:8]}'}', '{policy}', 1, 'text_tokens', 0, 0, 0, 0, {created_by}, {approved_by}, {approved_at}, 'test', '{key or f'k-{_u()}'}')"
    )


def q(uid):
    return f"'{uid}'"


async def test_a_phi_approved_model_needs_two_different_active_staff_the_approver_holding_admin_or_doctor(db):
    w = await world(db)
    ok = model_sql(created_by=q(w.people["nurse_a"]), approved_by=q(w.people["doctor_a"]))
    assert await outcome(db, ok) is None
    assert await outcome(db, model_sql(created_by=q(w.people["nurse_a"]), approved_by=q(w.people["admin_a"]))) is None  # admin may approve too
    assert await outcome(db, model_sql(created_by=q(w.people["doctor_b"]), approved_by=q(w.people["admin_a"]))) is None  # another clinic's staff is fine: platform-wide


@pytest.mark.parametrize(
    ("label", "created", "approved", "approved_at"),
    [
        ("no created_by", "null", "doctor_a", "now()"),
        ("the approver is also the creator", "doctor_a", "doctor_a", "now()"),
        ("the approver is a nurse", "admin_a", "nurse_a", "now()"),
        ("the approver is a front desk", "admin_a", "front_desk_a", "now()"),
        ("the approver is a business owner", "admin_a", "owner_a", "now()"),
        ("the approver is a patient account", "admin_a", "patient", "now()"),
        ("the approver is suspended", "admin_a", "suspended_doctor", "now()"),
        ("the approver's grant is revoked", "doctor_a", "revoked_admin", "now()"),
        ("the creator is not staff", "patient", "doctor_a", "now()"),
        ("the creator is suspended", "suspended_doctor", "admin_a", "now()"),
        ("the approval is dated in the future", "nurse_a", "doctor_a", "now() + interval '2 minutes'"),
    ],
)
async def test_a_phi_approved_model_with_an_invalid_approval_is_refused(db, label, created, approved, approved_at):
    w = await world(db)
    sql = model_sql(created_by=q(w.people[created]) if created != "null" else "null", approved_by=q(w.people[approved]), approved_at=approved_at)
    assert await outcome(db, sql) == "23514", label


async def test_the_creator_must_be_the_signed_in_account_when_there_is_one(db):
    w = await world(db)
    await db.execute("select set_config('request.jwt.claims', $1, true)", json.dumps({"sub": str(w.people["admin_a"]), "role": "authenticated"}))
    assert await outcome(db, model_sql(created_by=q(w.people["nurse_a"]), approved_by=q(w.people["doctor_a"]))) == "23514"  # claims say admin_a
    assert await outcome(db, model_sql(created_by=q(w.people["admin_a"]), approved_by=q(w.people["doctor_a"]))) is None


async def test_a_phi_approved_row_with_no_approver_or_no_date_is_refused_by_the_table_check(db):
    w = await world(db)
    assert await outcome(db, model_sql(created_by=q(w.people["nurse_a"]), approved_by="null")) == "23514"
    assert await outcome(db, model_sql(created_by=q(w.people["nurse_a"]), approved_by=q(w.people["doctor_a"]), approved_at="null")) == "23514"


async def test_a_synthetic_only_row_needs_none_of_that(db):
    await world(db)
    assert await outcome(db, model_sql(policy="synthetic_only", approved_at="null")) is None  # no approver, so no date either (the approval is whole or absent)


async def test_created_by_is_immutable_like_the_rest_of_a_rows_identity(db):
    w = await world(db)
    await db.execute(model_sql(created_by=q(w.people["nurse_a"]), approved_by=q(w.people["doctor_a"]), model="m-fixed"))
    assert await outcome(db, f"update ai.model_registry set created_by = '{w.people['admin_a']}' where model_id = 'm-fixed'") == "23001"
    assert await outcome(db, "update ai.model_registry set enabled = true where model_id = 'm-fixed'") is None  # operational columns still change


async def test_the_approval_check_function_is_not_executable_by_the_application_or_api_roles(db):
    for role in ("careflow_app", "anon", "authenticated", "service_role"):
        if await db.fetchval("select exists (select 1 from pg_roles where rolname = $1)", role):
            assert not await db.fetchval("select has_function_privilege($1, 'ai.model_registry_check_approval()', 'EXECUTE')", role), role
