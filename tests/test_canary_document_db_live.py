"""docs.register_canary_document (migrations 0042, 0043 and 0047; the cap is the owner-managed docs.canary_settings row) against the real database, as the restricted `careflow_app` role inside ONE never-committed outer transaction."""

import json
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
REFUSED = "canary document refused"
CALL = "select docs.register_canary_document(:c, :d, :k)"


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


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


async def world(db):
    """Two clinics; a queue worker and a front-desk person in A, a worker in B. Created as the owner."""
    a, b = uuid.uuid4(), uuid.uuid4()
    roster = [("worker", a, "queue_worker"), ("fd", a, "front_desk"), ("worker_b", b, "queue_worker")]
    people = {name: uuid.uuid4() for name, _, _ in roster}
    await raw(
        db,
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'CD A', 'test'), ('{b}', 'CD B', 'test');"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{i}', 'staff', 'active', 'test')" for i in people.values()) + ";"
        "insert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{people[k]}', '{c}', '{r}', 'test')" for k, c, r in roster) + ";"
        f"insert into docs.canary_settings (clinic_id, max_per_day) values ('{a}', 20), ('{b}', 20);",  # 20 = the labelled DEMO placeholder; tests only
    )
    return a, b, people


async def call_as(db, person, sql, **params):
    """One statement as `careflow_app` with the account's claims in a savepoint; a refusal rolls the savepoint back (which also restores the role)."""
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(person), "role": "authenticated"})})
        value = (await db.execute(text(sql), params)).scalar()
        await db.exec_driver_sql("reset role")
        return value


async def test_only_a_queue_worker_of_the_clinic_can_register_a_canary_document_and_it_is_origin_canary(db):
    a, b, people = await world(db)
    doc = uuid.uuid4()
    key = f"canary:{doc}"
    assert await call_as(db, people["worker"], CALL, c=a, d=doc, k=key) == doc
    row = (await db.execute(text("select origin, kind, registered_by from docs.documents where id = :d"), {"d": doc})).one()
    assert tuple(row) == ("canary", "referral", people["worker"])
    for who, clinic in (("fd", a), ("worker_b", a), ("worker", b)):  # a VALID id and key each time, so only the role / clinic check can be what refuses
        other = uuid.uuid4()
        with pytest.raises(Exception, match=REFUSED):
            await call_as(db, people[who], CALL, c=clinic, d=other, k=f"canary:{other}")
        assert (await db.execute(text("select count(*) from docs.documents where id = :d"), {"d": other})).scalar() == 0
    row2 = (await db.execute(text("select size_bytes, sha256, content_type, storage_path from docs.documents where id = :d"), {"d": doc})).one()
    assert tuple(row2) == (1, "0" * 64, "application/pdf", f"clinic/{a}/doc/{doc}")
    with pytest.raises(Exception, match=REFUSED):  # nobody signed in
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', '', true)"))
            await db.execute(text(CALL), {"c": a, "d": uuid.uuid4(), "k": "canary:x", "m": 20})


async def test_the_key_must_be_bound_to_the_document_id_and_a_replay_writes_nothing_more(db):
    a, _, people = await world(db)
    doc = uuid.uuid4()
    with pytest.raises(Exception, match=REFUSED):
        await call_as(db, people["worker"], CALL, c=a, d=doc, k="canary:somebody-else")
    assert await call_as(db, people["worker"], CALL, c=a, d=doc, k=f"canary:{doc}") == doc
    assert await call_as(db, people["worker"], CALL, c=a, d=doc, k=f"canary:{doc}") == doc
    audit = (await db.execute(text("select count(*), min(idempotency_key) from audit.log where resource_id = :d and action = 'canary.injected'"), {"d": str(doc)})).one()
    assert audit[0] == 1 and audit[1] == f"sys:canary-document:{doc}"


async def test_the_function_is_executable_by_the_application_role_only(db):
    for role, expected in (("public", False), ("careflow_app", True), ("anon", False), ("authenticated", False), ("service_role", False)):
        exists = (await db.execute(text("select exists (select 1 from pg_roles where rolname = :r) or :r = 'public'"), {"r": role})).scalar()
        if not exists:
            continue
        granted = (await db.execute(text("select has_function_privilege(:r, 'docs.register_canary_document(uuid, uuid, text)', 'execute')"), {"r": role})).scalar()
        assert granted is expected, role


async def test_a_replay_adds_no_second_row_and_a_key_held_by_a_real_document_is_refused(db):
    a, _, people = await world(db)
    doc = uuid.uuid4()
    for _ in range(2):
        await call_as(db, people["worker"], CALL, c=a, d=doc, k=f"canary:{doc}")
    assert (await db.execute(text("select count(*) from docs.documents where clinic_id = :c and idempotency_key = :k"), {"c": a, "k": f"canary:{doc}"})).scalar() == 1
    real = uuid.uuid4()
    await raw(db, f"insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) values ('{real}', '{a}', 'test', 'referral', 'clinic/{a}/doc/{real}', 'application/pdf', 1, '{'1' * 64}', '{people['fd']}', 'canary:{real}')")
    with pytest.raises(Exception, match=REFUSED):  # the key belongs to a non-canary document: not returned, not overwritten
        await call_as(db, people["worker"], CALL, c=a, d=real, k=f"canary:{real}")


async def set_cap(db, clinic, n):
    await raw(db, f"update docs.canary_settings set max_per_day = {n} where clinic_id = '{clinic}'")


async def test_the_daily_cap_refuses_a_new_document_but_never_a_replay(db):
    a, _, people = await world(db)
    await set_cap(db, a, 2)
    docs = [uuid.uuid4() for _ in range(3)]
    for d in docs[:2]:
        assert await call_as(db, people["worker"], CALL, c=a, d=d, k=f"canary:{d}") == d
    with pytest.raises(Exception, match=REFUSED):  # the third new document today exceeds a cap of 2
        await call_as(db, people["worker"], CALL, c=a, d=docs[2], k=f"canary:{docs[2]}")
    assert await call_as(db, people["worker"], CALL, c=a, d=docs[0], k=f"canary:{docs[0]}") == docs[0]  # a replay is still answered


async def test_a_clinic_with_no_cap_row_gets_no_canary_and_the_application_cannot_touch_the_setting(db):
    a, _, people = await world(db)
    await raw(db, f"delete from docs.canary_settings where clinic_id = '{a}'")
    d = uuid.uuid4()
    with pytest.raises(Exception, match=REFUSED):  # a required setting with no default: unset means nothing is injected
        await call_as(db, people["worker"], CALL, c=a, d=d, k=f"canary:{d}")
    for sql in ("select count(*) from docs.canary_settings where true", "insert into docs.canary_settings (clinic_id, max_per_day) values (:c, 1000)", "update docs.canary_settings set max_per_day = 1000"):
        with pytest.raises(Exception, match="permission denied"):
            await call_as(db, people["worker"], sql, c=a)


async def test_the_cap_counts_only_this_clinic_and_only_canary_documents(db):
    a, b, people = await world(db)
    await set_cap(db, a, 1)
    await set_cap(db, b, 1)
    worker_b_doc = uuid.uuid4()
    assert await call_as(db, people["worker_b"], CALL, c=b, d=worker_b_doc, k=f"canary:{worker_b_doc}") == worker_b_doc
    d = uuid.uuid4()
    assert await call_as(db, people["worker"], CALL, c=a, d=d, k=f"canary:{d}") == d  # clinic B's canary does not use clinic A's day


async def test_the_old_functions_are_gone(db):
    n = (await db.execute(text("select count(*) from pg_proc p join pg_namespace s on s.oid = p.pronamespace where s.nspname = 'docs' and p.proname = 'register_canary_document' and p.pronargs <> 3"))).scalar()
    assert n == 0
