"""Migration 0017, against the real hosted project: docs.documents (BUILD-SPEC.md §10.4, §11.5, §17.3, §18.4; CLAUDE.md rule 8).

One transaction that is always rolled back: if 0017 is not applied yet it is executed in that transaction first, so this verifies the file
without applying it. Queries run as the restricted `careflow_app` role (SET LOCAL ROLE). Nothing is committed (the table is append-only anyway).
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
SQL_0017 = (ROOT / "db" / "migrations" / "0017_docs_documents.sql").read_text(encoding="utf-8")

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
        if await conn.fetchval("select to_regclass('docs.documents') is null"):
            await conn.execute(SQL_0017)
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
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, 'Docs test', 'test')", cid)
    w.people = {}
    roster = [
        ("front_desk_a", w.a, "front_desk"), ("nurse_a", w.a, "nurse"), ("doctor_a", w.a, "doctor"), ("worker_a", w.a, "queue_worker"),
        ("admin_a", w.a, "admin"), ("owner_a", w.a, "business_owner"), ("auditor_a", w.a, "auditor"),
        ("front_desk_b", w.b, "front_desk"), ("doctor_b", w.b, "doctor"),
    ]
    for key, clinic, role in roster:
        w.people[key] = _u()
        await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", w.people[key])
        await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", w.people[key], clinic, role)
    w.people["patient"] = _u()
    await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", w.people["patient"])
    return w


def doc_sql(clinic, by, *, doc=None, path=None, ctype="application/pdf", size=1000, sha=SHA, kind="referral", origin="'test'", key=None):
    doc = doc or _u()
    path = path or f"clinic/{clinic}/doc/{doc}"
    return (
        "insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) "
        f"values ('{doc}', '{clinic}', {origin}, '{kind}', '{path}', '{ctype}', {size}, '{sha}', '{by}', '{key or f'k-{_u()}'}')"
    )


async def test_forced_rls_and_the_app_role_has_select_and_a_column_limited_insert_only(db):
    row = await db.fetchrow("select relrowsecurity, relforcerowsecurity from pg_class where oid = 'docs.documents'::regclass")
    assert row["relrowsecurity"] and row["relforcerowsecurity"]
    for priv in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not await db.fetchval("select has_table_privilege('careflow_app', 'docs.documents', $1)", priv), priv
    assert await db.fetchval("select has_table_privilege('careflow_app', 'docs.documents', 'SELECT')")
    assert not await db.fetchval("select has_column_privilege('careflow_app', 'docs.documents', 'created_at', 'INSERT')")
    assert not await db.fetchval("select has_column_privilege('careflow_app', 'docs.documents', 'status', 'INSERT')")
    for role in ("anon", "authenticated", "service_role"):
        if await db.fetchval("select exists (select 1 from pg_roles where rolname = $1)", role):
            assert not await db.fetchval("select has_table_privilege($1, 'docs.documents', 'SELECT')", role), role


async def test_only_front_desk_and_nurse_of_the_clinic_can_register_and_only_as_themselves(db):
    w = await world(db)
    p = w.people
    assert await as_app(db, p["front_desk_a"], doc_sql(w.a, p["front_desk_a"])) == "INSERT 0 1"
    assert await as_app(db, p["nurse_a"], doc_sql(w.a, p["nurse_a"])) == "INSERT 0 1"
    for who in ("doctor_a", "worker_a", "admin_a", "owner_a", "auditor_a", "patient", "front_desk_b"):
        assert await as_app(db, p[who], doc_sql(w.a, p[who])) == "42501", who
    assert await as_app(db, p["front_desk_a"], doc_sql(w.a, p["nurse_a"])) == "42501"  # naming somebody else
    assert await as_app(db, None, doc_sql(w.a, p["front_desk_a"])) == "42501"


async def test_only_clinical_staff_and_the_worker_of_the_clinic_can_read_and_only_their_clinic(db):
    w = await world(db)
    p = w.people
    await db.execute(doc_sql(w.a, p["front_desk_a"]))
    await db.execute(doc_sql(w.b, p["front_desk_b"]))
    for who in ("front_desk_a", "nurse_a", "doctor_a", "worker_a"):
        rows = await as_app(db, p[who], "select clinic_id from docs.documents", fetch=True)
        assert {r["clinic_id"] for r in rows} == {w.a}, who
    rows_b = await as_app(db, p["doctor_b"], "select clinic_id from docs.documents", fetch=True)
    assert {r["clinic_id"] for r in rows_b} == {w.b}  # clinic B's doctor sees clinic B's document only
    for who in ("admin_a", "owner_a", "auditor_a", "patient"):
        assert await as_app(db, p[who], "select 1 from docs.documents", fetch=True) == [], who
    assert await as_app(db, None, "select 1 from docs.documents", fetch=True) == []


async def test_the_application_role_cannot_update_or_delete(db):
    w = await world(db)
    await db.execute(doc_sql(w.a, w.people["front_desk_a"]))
    for who in ("front_desk_a", "nurse_a", "doctor_a", "worker_a"):
        assert await as_app(db, w.people[who], "update docs.documents set kind = 'other'") == "42501", who
        assert await as_app(db, w.people[who], "delete from docs.documents") == "42501", who


async def test_the_table_is_append_only_even_for_the_owner(db):
    w = await world(db)
    await db.execute(doc_sql(w.a, w.people["front_desk_a"]))
    assert await outcome(db, "update docs.documents set kind = 'other'") == "23001"
    assert await outcome(db, "delete from docs.documents") == "23001"
    assert await outcome(db, "truncate docs.documents cascade") == "23001"  # since 0018 a foreign key references this table, so a plain TRUNCATE is refused earlier (0A000); CASCADE reaches the trigger


@pytest.mark.parametrize(
    ("label", "kwargs", "state"),
    [
        ("an unknown kind", {"kind": "selfie"}, "23514"),
        ("a type outside the allowlist", {"ctype": "text/html"}, "23514"),
        ("an executable", {"ctype": "application/x-msdownload"}, "23514"),
        ("a zero size", {"size": 0}, "23514"),
        ("a size over the hard ceiling", {"size": 104857601}, "23514"),
        ("a malformed hash", {"sha": "xyz"}, "23514"),
        ("an upper-case hash", {"sha": "A" * 64}, "23514"),
        ("no origin", {"origin": "null"}, "23502"),
        ("a bad origin", {"origin": "'prod'"}, "23514"),
        ("a path that is not this row's own", {"path": "clinic/other/doc/x"}, "23514"),
        ("a path under a client-chosen name", {"path": "uploads/../../etc/passwd"}, "23514"),
    ],
)
async def test_the_table_refuses_invalid_rows(db, label, kwargs, state):
    w = await world(db)
    assert await outcome(db, doc_sql(w.a, w.people["front_desk_a"], **kwargs)) == state, label


async def test_a_row_cannot_point_at_another_clinics_object(db):
    w = await world(db)
    doc = _u()
    assert await outcome(db, doc_sql(w.a, w.people["front_desk_a"], doc=doc, path=f"clinic/{w.b}/doc/{doc}")) == "23514"


async def test_the_idempotency_key_is_unique_per_clinic_not_across_clinics(db):
    w = await world(db)
    assert await outcome(db, doc_sql(w.a, w.people["front_desk_a"], key="same")) is None
    assert await outcome(db, doc_sql(w.a, w.people["front_desk_a"], key="same")) == "23505"
    assert await outcome(db, doc_sql(w.b, w.people["front_desk_b"], key="same")) is None  # another clinic cannot collide with, or probe, it


async def test_the_status_is_registered_and_comes_from_the_database(db):
    w = await world(db)
    await db.execute(doc_sql(w.a, w.people["front_desk_a"]))
    assert await db.fetchval("select status from docs.documents where clinic_id = $1", w.a) == "registered"
    doc = _u()
    forged = (
        "insert into docs.documents (id, clinic_id, origin, kind, status, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) "
        f"values ('{doc}', '{w.a}', 'test', 'referral', 'registered', 'clinic/{w.a}/doc/{doc}', 'application/pdf', 10, '{SHA}', '{w.people['front_desk_a']}', 'forged-{_u()}')"
    )
    assert await as_app(db, w.people["front_desk_a"], forged) == "42501"  # no INSERT privilege on the status column
