"""The model registry, migration 0015 (ai.model_registry), against the real hosted project (BUILD-SPEC.md §2, §5.1, §18.4, §21; CLAUDE.md rule 8).

Everything runs in one transaction that is always rolled back: if 0015 has not been applied yet it is executed in that transaction first, so
this verifies the file without applying it. Queries run as the restricted `careflow_app` role (SET LOCAL ROLE), never as the migrator, so
RLS is genuinely exercised. Nothing is committed; the table is append-only anyway.

One parity check ties the database to the Python gateway (careflow/gateway.py): the SQL "pinned model id" check agrees with ModelRow on a
list of sample ids, so the two cannot drift apart. (The SQL-text checks that need no database are in tests/test_ai_registry_sql.py.)
"""

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values

from careflow.gateway import ModelRow

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
SQL_0015 = (ROOT / "db" / "migrations" / "0015_ai_model_registry.sql").read_text(encoding="utf-8")

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


def _u() -> uuid.UUID:
    return uuid.uuid4()


# ---- live ------------------------------------------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    dsn = (MIGRATOR or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("grant careflow_app to current_user with set true")
        if await conn.fetchval("select to_regclass('ai.model_registry') is null"):
            await conn.execute(SQL_0015)
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


INSERT = (
    "insert into ai.model_registry (role, provider, model_id, data_policy, priority, enabled, unit, price_in_per_1k, price_out_per_1k, "
    "shadow_in_per_1k, shadow_out_per_1k, approved_by, approved_at, origin, idempotency_key) values "
    "({role}, {provider}, {model}, {policy}, {priority}, {enabled}, {unit}, 0, 0, 0.003, 0.015, {approver}, {approved_at}, {origin}, {key})"
)


def insert_sql(*, role="decision", provider="p", model="m-1", policy="synthetic_only", priority=1, enabled="false", unit="text_tokens",
               approver="null", approved_at="null", origin="'test'", key=None):
    q = lambda v: v if str(v).startswith("'") or str(v) in {"null", "true", "false"} or str(v).isdigit() else f"'{v}'"
    return INSERT.format(role=q(role), provider=q(provider), model=q(model), policy=q(policy), priority=priority, enabled=enabled,
                         unit=q(unit), approver=approver, approved_at=approved_at, origin=origin, key=q(key or f"k-{_u()}"))


async def outcome(conn, sql):
    """The SQLSTATE of running `sql` as the migrator in a savepoint, or None if it worked."""
    await conn.execute("savepoint attempt")
    try:
        await conn.execute(sql)
    except asyncpg.PostgresError as exc:
        await conn.execute("rollback to savepoint attempt")
        return exc.sqlstate
    await conn.execute("release savepoint attempt")
    return None


async def as_app(conn, uid, sql, *, fetch=False):
    """Run `sql` as careflow_app with `uid`'s claims in a savepoint (None = no identity). Returns rows, or the SQLSTATE of the failure."""
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


async def seed_people(conn):
    clinic_a, clinic_b = _u(), _u()
    for cid in (clinic_a, clinic_b):
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, 'Registry test', 'test')", cid)
    people = {}
    for key, clinic, role in [("admin_a", clinic_a, "admin"), ("worker_b", clinic_b, "queue_worker"), ("nurse_a", clinic_a, "nurse"),
                              ("doctor_a", clinic_a, "doctor"), ("front_desk_a", clinic_a, "front_desk"), ("owner_a", clinic_a, "business_owner"),
                              ("auditor_a", clinic_a, "auditor")]:
        people[key] = _u()
        await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", people[key])
        await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", people[key], clinic, role)
    people["patient"] = _u()
    await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", people["patient"])
    return people


async def test_forced_rls_and_the_app_role_has_select_and_nothing_else(db):
    row = await db.fetchrow("select relrowsecurity, relforcerowsecurity from pg_class where oid = 'ai.model_registry'::regclass")
    assert row["relrowsecurity"] and row["relforcerowsecurity"]
    for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await db.fetchval("select has_table_privilege('careflow_app', 'ai.model_registry', $1)", priv), priv
    assert await db.fetchval("select has_table_privilege('careflow_app', 'ai.model_registry', 'SELECT')")
    for priv in ("INSERT", "UPDATE", "REFERENCES"):  # a column-level grant would not show in has_table_privilege
        assert not await db.fetchval("select has_any_column_privilege('careflow_app', 'ai.model_registry', $1)", priv), priv
    for role in ("anon", "authenticated", "service_role"):
        if await db.fetchval("select exists (select 1 from pg_roles where rolname = $1)", role):
            assert not await db.fetchval("select has_table_privilege($1, 'ai.model_registry', 'SELECT')", role), role


async def test_only_an_admin_or_a_queue_worker_can_read_the_registry(db):
    people = await seed_people(db)
    await db.execute(insert_sql(model="m-read"))
    for who in ("admin_a", "worker_b"):
        assert len(await as_app(db, people[who], "select id from ai.model_registry where model_id = 'm-read'", fetch=True)) == 1, who
    for who in ("nurse_a", "doctor_a", "front_desk_a", "owner_a", "auditor_a", "patient", None):
        got = await as_app(db, people[who] if who else None, "select id from ai.model_registry where model_id = 'm-read'", fetch=True)
        assert got == [], who


async def test_the_application_role_cannot_write_the_registry_whoever_it_is(db):
    people = await seed_people(db)
    await db.execute(insert_sql(model="m-write"))
    for who in ("admin_a", "worker_b"):
        assert await as_app(db, people[who], insert_sql(model="m-other")) == "42501", who
        assert await as_app(db, people[who], "update ai.model_registry set enabled = true where model_id = 'm-write'") == "42501", who
        assert await as_app(db, people[who], "delete from ai.model_registry where model_id = 'm-write'") == "42501", who


# ---- constraints -------------------------------------------------------------------------------------------------------------------------


async def test_a_valid_row_is_accepted_and_new_rows_are_disabled_by_default(db):
    assert await outcome(db, insert_sql()) is None
    sql = ("insert into ai.model_registry (role, provider, model_id, data_policy, priority, unit, price_in_per_1k, price_out_per_1k, "
           "shadow_in_per_1k, shadow_out_per_1k, origin, idempotency_key) values ('embed', 'p', 'e-1', 'synthetic_only', 1, 'text_tokens', 0, 0, 0, 0, 'test', 'k-default')")
    assert await outcome(db, sql) is None
    assert await db.fetchval("select enabled from ai.model_registry where idempotency_key = 'k-default'") is False


@pytest.mark.parametrize(
    ("label", "kwargs", "state"),
    [
        ("an unknown role", {"role": "llm_huge"}, "23514"),
        ("an unknown data policy", {"policy": "maybe"}, "23514"),
        ("priority zero", {"priority": 0}, "23514"),
        ("a blank provider", {"provider": " "}, "23514"),
        ("an unpinned model id (latest)", {"model": "latest"}, "23514"),
        ("an unpinned model id (suffix)", {"model": "vendor/model@latest"}, "23514"),
        ("an audio unit for a non-voice role", {"unit": "audio_tokens"}, "23514"),
        ("voice_live priced in text tokens", {"role": "voice_live", "unit": "text_tokens"}, "23514"),
        ("phi_approved with no approver", {"policy": "phi_approved"}, "23514"),
        ("an approver without a date", {"approver": "(select id from iam.accounts limit 1)"}, "23514"),
        ("no origin", {"origin": "null"}, "23502"),
        ("a bad origin", {"origin": "'prod'"}, "23514"),
    ],
)
async def test_the_registry_refuses_invalid_rows(db, label, kwargs, state):
    await seed_people(db)  # gives the approver sub-select an account to find
    assert await outcome(db, insert_sql(**kwargs)) == state, label


async def test_phi_approved_needs_a_named_approver_and_a_date(db):
    people = await seed_people(db)
    ok = insert_sql(policy="phi_approved", approver=f"'{people['admin_a']}'", approved_at="now()", model="m-phi")
    assert await outcome(db, ok) is None


async def test_voice_live_rows_use_audio_tokens(db):
    assert await outcome(db, insert_sql(role="voice_live", unit="audio_tokens", model="v-1")) is None


async def test_one_row_per_engine_and_a_unique_idempotency_key(db):
    assert await outcome(db, insert_sql(key="same")) is None
    assert await outcome(db, insert_sql(model="m-other", key="same")) == "23505"  # same key
    assert await outcome(db, insert_sql(key="another")) == "23505"  # same (role, provider, model)


async def test_among_enabled_rows_of_a_role_the_priority_is_unique_but_disabled_rows_may_share_it(db):
    assert await outcome(db, insert_sql(model="a", priority=1, enabled="true")) is None
    assert await outcome(db, insert_sql(model="b", priority=1, enabled="true")) == "23505"
    assert await outcome(db, insert_sql(model="c", priority=1, enabled="false")) is None
    assert await outcome(db, insert_sql(role="embed", model="a", priority=1, enabled="true")) is None  # another role


# ---- immutability ------------------------------------------------------------------------------------------------------------------------


async def test_operational_columns_may_change_but_identity_and_data_policy_may_not(db):
    people = await seed_people(db)
    assert await outcome(db, insert_sql(policy="phi_approved", approver=f"'{people['admin_a']}'", approved_at="now()", model="m-imm")) is None
    for column_change in (
        "enabled = true", "priority = 5", "price_in_per_1k = 0.5", "shadow_out_per_1k = 0.9", "timeout_seconds = 30",
    ):
        assert await outcome(db, f"update ai.model_registry set {column_change} where model_id = 'm-imm'") is None, column_change
    for column_change in (
        "role = 'embed', unit = 'text_tokens'", "provider = 'other'", "model_id = 'other-model'", "data_policy = 'synthetic_only'",
        "approved_by = null, approved_at = null", "approved_at = now() + interval '1 day'", "origin = 'live'", "idempotency_key = 'changed'",
        "created_at = now() + interval '1 day'",
    ):
        assert await outcome(db, f"update ai.model_registry set {column_change} where model_id = 'm-imm'") == "23001", column_change


async def test_nothing_is_ever_deleted_or_truncated_even_by_the_owner(db):
    assert await outcome(db, insert_sql()) is None
    assert await outcome(db, "delete from ai.model_registry where model_id = 'm-1'") == "23001"
    assert await outcome(db, "truncate ai.model_registry") == "23001"


# ---- parity with the Python gateway --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    ["latest", "m-latest", "vendor/model@latest", "m:stable", "default", "x_current", "modernbert-large-2025-06-01", "latest-news-v2",
     "gemini-2.5-flash", "name/with/slashes-v3", "model.default.v2", "m-latest ", " m-1", "m-latest\n", "m\x01-1"],
)
async def test_the_database_and_the_gateway_agree_on_which_model_ids_are_pinned(db, model):
    try:
        ModelRow("decision", "p", model, "synthetic_only", 1, Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        python_accepts = True
    except ValueError:
        python_accepts = False
    sql_accepts = await outcome(db, insert_sql(model=model.replace("'", ""))) is None
    assert python_accepts == sql_accepts, model
