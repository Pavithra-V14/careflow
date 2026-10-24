"""Migration 0021 and the Postgres checkpointer against the real hosted project (BUILD-SPEC.md §7.2, §18.4; CLAUDE.md rules 5, 8).

One never-committed outer transaction; each test in its own savepoint. If 0021 is not applied yet it is executed inside it (after 0018, whose agents.append_only() it uses, if that is
missing too), so the file is verified without being applied. Row-level security is genuinely in the path: the probes and the saver run as the restricted `careflow_app` role with a
verified account's claims (SET LOCAL ROLE). Then the SAME small graph that tests/test_checkpointer.py runs against LangGraph's InMemorySaver runs against the real tables.
"""

import asyncio
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.checkpointer import PostgresCheckpointSaver, SqlCheckpointStore
from careflow.db import set_request_claims
from tests.db_helpers import ensure_app_role_sa
from tests.test_checkpointer import checkpoint, normalise, small_graph

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
SQL = {n: (ROOT / "db" / "migrations" / f).read_text(encoding="utf-8") for n, f in (("0018", "0018_agents_triage_proposals_and_decisions.sql"), ("0021", "0021_agents_graph_checkpoints.sql"))}

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


def _u() -> uuid.UUID:
    return uuid.uuid4()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await ensure_app_role_sa(conn)
        if (await conn.execute(text("select to_regprocedure('agents.append_only()') is null"))).scalar_one():
            await raw(conn, SQL["0018"])
        if (await conn.execute(text("select to_regclass('agents.graph_checkpoints') is null"))).scalar_one():
            await raw(conn, SQL["0021"])
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


class World:
    pass


async def world(conn) -> World:
    w = World()
    w.a, w.b = _u(), _u()
    roster = [
        ("worker_a", w.a, "queue_worker"), ("nurse_a", w.a, "nurse"), ("doctor_a", w.a, "doctor"), ("fd_a", w.a, "front_desk"), ("admin_a", w.a, "admin"),
        ("auditor_a", w.a, "auditor"), ("owner_a", w.a, "business_owner"), ("worker_b", w.b, "queue_worker"),
    ]
    w.people = {k: _u() for k, _, _ in roster}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'Ckpt A', 'test'), ('{w.b}', 'Ckpt B', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{w.people[k]}', 'staff', 'active', 'test')" for k, _, _ in roster)
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{w.people[k]}', '{c}', '{r}', 'test')" for k, c, r in roster) + ";",
    )
    return w


def only(sql, params):
    used = set(re.findall(r"(?<!:):([a-z_]+)", sql))
    return {k: v for k, v in params.items() if k in used}


async def attempt(conn, uid, sql, **params):
    """One statement as `uid` in a savepoint: its rowcount, or the SQLSTATE. No `reset role` after an error (the savepoint rollback undoes SET LOCAL ROLE)."""
    try:
        async with conn.begin_nested():
            await conn.exec_driver_sql("set local role careflow_app")
            await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
            rows = (await conn.execute(text(sql), only(sql, params))).rowcount
            await conn.exec_driver_sql("reset role")
            return rows
    except DBAPIError as exc:
        return getattr(exc.orig, "sqlstate", None)


async def count_as(conn, uid, table, thread):
    async with conn.begin_nested():
        await conn.exec_driver_sql("set local role careflow_app")
        await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        n = (await conn.execute(text(f"select count(*) from {table} where thread_id = :t"), {"t": thread})).scalar_one()
        await conn.exec_driver_sql("reset role")
        return n


async def owner(conn, sql, **params):
    """The same, as the owner (the migrator connection): the trigger and constraint layer, with row-level security out of the way."""
    try:
        async with conn.begin_nested():
            return (await conn.execute(text(sql), only(sql, params))).rowcount
    except DBAPIError as exc:
        return getattr(exc.orig, "sqlstate", None)


CP = (
    "insert into agents.graph_checkpoints (clinic_id, origin, thread_id, checkpoint_ns, checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata, idempotency_key) "
    "values (:c, :o, :t, '', :id, 'msgpack', '\\x01'::bytea, 'json', '\\x02'::bytea, :k)"
)
BLOB = (
    "insert into agents.graph_checkpoint_blobs (clinic_id, origin, thread_id, checkpoint_ns, channel, version, blob_type, blob, idempotency_key) "
    "values (:c, :o, :t, '', 'x', :v, 'msgpack', '\\x01'::bytea, :k)"
)
WRITE = (
    "insert into agents.graph_checkpoint_writes (clinic_id, origin, thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, write_type, write_blob, idempotency_key) "
    "values (:c, :o, :t, '', 'cp-1', 'task', :idx, 'ch', 'msgpack', '\\x01'::bytea, :k)"
)


def args(w, origin="test", **over):
    n = _u().hex
    return {"c": w.a, "o": origin, "t": f"t-{n}", "id": f"cp-{n}", "k": f"k-{n}", "v": f"v-{n}", "idx": 0, **over}


async def test_forced_rls_no_delete_truncate_or_references_and_update_only_on_four_columns_of_writes(db):
    for table in ("agents.graph_checkpoints", "agents.graph_checkpoint_blobs", "agents.graph_checkpoint_writes"):
        row = (await db.execute(text("select relrowsecurity, relforcerowsecurity from pg_class where oid = cast(:t as regclass)"), {"t": table})).first()
        assert row.relrowsecurity and row.relforcerowsecurity, table
        for priv in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not (await db.execute(text("select has_table_privilege('careflow_app', :t, :p)"), {"t": table, "p": priv})).scalar_one(), (table, priv)
        assert (await db.execute(text("select has_table_privilege('careflow_app', :t, 'SELECT')"), {"t": table})).scalar_one()
        updatable = (await db.execute(text("select has_any_column_privilege('careflow_app', :t, 'UPDATE')"), {"t": table})).scalar_one()
        assert updatable == (table == "agents.graph_checkpoint_writes"), table
        for role in ("anon", "authenticated", "service_role"):
            if (await db.execute(text("select exists (select 1 from pg_roles where rolname = :r)"), {"r": role})).scalar_one():
                assert not (await db.execute(text("select has_table_privilege(:r, :t, 'SELECT')"), {"r": role, "t": table})).scalar_one(), (role, table)


async def test_only_the_worker_nurse_and_doctor_of_the_clinic_can_write_and_read_every_table(db):
    w = await world(db)
    for who in ("worker_a", "nurse_a", "doctor_a"):
        for sql in (CP, BLOB, WRITE):
            assert await attempt(db, w.people[who], sql, **args(w)) == 1, (who, sql[:50])
    for who in ("fd_a", "admin_a", "auditor_a", "owner_a", "worker_b"):
        for sql in (CP, BLOB, WRITE):
            assert await attempt(db, w.people[who], sql, **args(w)) == "42501", (who, sql[:50])
    known = args(w, t="known-thread")
    for sql in (CP, BLOB, WRITE):
        assert await owner(db, sql, **known) == 1
    for table in ("agents.graph_checkpoints", "agents.graph_checkpoint_blobs", "agents.graph_checkpoint_writes"):
        for who in ("worker_a", "nurse_a", "doctor_a"):
            assert await count_as(db, w.people[who], table, "known-thread") == 1, (who, table)
        for who in ("fd_a", "admin_a", "auditor_a", "owner_a", "worker_b"):
            assert await count_as(db, w.people[who], table, "known-thread") == 0, (who, table)


async def test_the_owner_cannot_edit_the_history_and_nobody_can_delete_a_write(db):
    w = await world(db)
    a = args(w)
    for sql in (CP, BLOB, WRITE):
        assert await owner(db, sql, **a) == 1
    for stmt in (
        "update agents.graph_checkpoints set origin = 'live'", "delete from agents.graph_checkpoints", "truncate agents.graph_checkpoints cascade",
        "update agents.graph_checkpoint_blobs set blob = '\\x03'::bytea", "delete from agents.graph_checkpoint_blobs", "truncate agents.graph_checkpoint_blobs",
        "delete from agents.graph_checkpoint_writes", "truncate agents.graph_checkpoint_writes",
        "update agents.graph_checkpoint_writes set write_blob = '\\x03'::bytea where idx >= 0",  # a regular write is final even for the owner
    ):
        assert await owner(db, stmt) == "23001", stmt  # restrict_violation from agents.append_only()


async def test_only_the_special_writes_can_be_replaced_and_the_app_role_cannot_change_keys_or_origin(db):
    w = await world(db)
    p = w.people["worker_a"]
    assert await owner(db, WRITE, **args(w, t="t-w", idx=0, k="k-reg")) == 1
    assert await owner(db, WRITE, **args(w, t="t-w", idx=-3, k="k-spec")) == 1
    change = "update agents.graph_checkpoint_writes set write_blob = '\\x09'::bytea where thread_id = 't-w' and idx = :idx"
    assert await attempt(db, p, change, idx=0) == 0  # a regular write is never replaced (RLS filters the row)
    assert await attempt(db, p, change, idx=-3) == 1  # a special one is
    for column, value in (("origin", "'live'"), ("clinic_id", f"'{w.b}'"), ("idx", "-9"), ("idempotency_key", "'x'"), ("checkpoint_id", "'cp-2'")):
        assert await attempt(db, p, f"update agents.graph_checkpoint_writes set {column} = {value} where thread_id = 't-w' and idx = -3") == "42501", column
    assert await attempt(db, w.people["fd_a"], change, idx=-3) == 0  # front desk sees no row to update


async def test_origin_has_no_default_ids_are_per_clinic_and_a_second_row_for_the_same_checkpoint_is_refused(db):
    w = await world(db)
    a = args(w)
    assert await owner(db, CP, **a) == 1
    assert await owner(db, CP, **{**a, "k": "a-different-key"}) == "23505"  # the same checkpoint again: the primary key
    assert await owner(db, CP, **{**a, "id": "cp-other"}) == "23505"  # a second checkpoint under the SAME idempotency key in this clinic
    assert await owner(db, CP, **{**a, "c": w.b}) == 1  # another clinic may use the same ids and keys
    assert await owner(db, CP, **{**args(w), "o": "prod"}) == "23514"
    assert await owner(db, CP, **{**args(w), "o": None}) == "23502"


# ---- the saver on the real tables ------------------------------------------------------------------------------------------------------------


def make_session(conn, uid):
    lock = asyncio.Lock()

    @asynccontextmanager
    async def session():
        async with lock, conn.begin_nested():
            await conn.exec_driver_sql("set local role careflow_app")
            await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
            yield conn
            await conn.exec_driver_sql("reset role")

    return session


def cfg(w, thread, **over):
    return {"configurable": {"thread_id": thread, "checkpoint_ns": "", "clinic_id": str(w.a), "origin": "test", "__pregel_durability": "sync", **over}}


async def run_small(saver, config):
    graph = small_graph(saver)
    await graph.ainvoke({"a": 0}, config)
    paused = normalise(await graph.aget_state(config))
    await graph.ainvoke(Command(resume="yes"), config)
    return paused, normalise(await graph.aget_state(config)), [normalise(s) async for s in graph.aget_state_history(config)]


async def test_the_same_graph_gives_the_same_results_on_the_real_tables_as_on_langgraphs_own_saver(db):
    w = await world(db)
    thread = f"conf-{_u()}"
    ours = PostgresCheckpointSaver(SqlCheckpointStore(make_session(db, w.people["worker_a"])))
    mine = await run_small(ours, cfg(w, thread))
    theirs = await run_small(InMemorySaver(), {"configurable": {"thread_id": thread}})
    assert mine == theirs
    assert (await db.execute(text("select count(*) from agents.graph_checkpoints where thread_id = :t"), {"t": thread})).scalar_one() == len(mine[2])


async def test_a_crash_after_a_checkpoint_resumes_from_the_stored_rows_with_a_fresh_saver(db):
    w = await world(db)
    thread = f"crash-{_u()}"
    session = make_session(db, w.people["worker_a"])
    first = small_graph(PostgresCheckpointSaver(SqlCheckpointStore(session)))
    await first.ainvoke({"a": 0}, cfg(w, thread))
    del first  # the process is gone; only the rows remain
    second = small_graph(PostgresCheckpointSaver(SqlCheckpointStore(session)))
    assert (await second.aget_state(cfg(w, thread))).next == ("ask",)
    await second.ainvoke(Command(resume="after restart"), cfg(w, thread))
    assert dict((await second.aget_state(cfg(w, thread))).values) == {"a": 11, "answer": "after restart"}


async def test_another_clinics_worker_sees_nothing_and_cannot_write_into_this_clinic_and_front_desk_cannot_write(db):
    w = await world(db)
    thread = f"iso-{_u()}"
    ours = PostgresCheckpointSaver(SqlCheckpointStore(make_session(db, w.people["worker_a"])))
    await small_graph(ours).ainvoke({"a": 0}, cfg(w, thread))
    foreign = PostgresCheckpointSaver(SqlCheckpointStore(make_session(db, w.people["worker_b"])))
    assert await foreign.aget_tuple(cfg(w, thread)) is None and [t async for t in foreign.alist(cfg(w, thread))] == []
    c = checkpoint({"x": 1})
    with pytest.raises(DBAPIError):
        await foreign.aput(cfg(w, thread), c, {"step": 0}, c["channel_versions"])  # clinic A's id, clinic B's worker: row-level security refuses
    desk = PostgresCheckpointSaver(SqlCheckpointStore(make_session(db, w.people["fd_a"])))
    with pytest.raises(DBAPIError):
        await desk.aput(cfg(w, f"desk-{_u()}"), c, {"step": 0}, c["channel_versions"])


async def test_the_saver_s_upsert_replaces_a_special_write_and_keeps_a_regular_one_under_row_level_security(db):
    from careflow.checkpointer import Scope

    w = await world(db)
    store = SqlCheckpointStore(make_session(db, w.people["worker_a"]))
    scope, thread = Scope(w.a, "test"), f"upsert-{_u()}"
    await store.put_writes(scope, thread, "", "cp-1", "task", "", [(-3, "__interrupt__", ("msgpack", b"first")), (0, "a", ("msgpack", b"first"))])
    await store.put_writes(scope, thread, "", "cp-1", "task", "", [(-3, "__interrupt__", ("msgpack", b"second")), (0, "a", ("msgpack", b"second"))])
    got = {x.idx: x.value[1] for x in await store.get_writes(w.a, thread, "", "cp-1")}
    assert got == {-3: b"second", 0: b"first"}


async def test_a_put_is_one_transaction_a_failing_checkpoint_row_leaves_no_blob_behind(db):
    from careflow.checkpointer import Scope

    w = await world(db)
    store = SqlCheckpointStore(make_session(db, w.people["worker_a"]))
    thread = f"atomic-{_u()}"
    with pytest.raises(DBAPIError):  # a parent id longer than 100 characters violates the checkpoint row's CHECK, AFTER the blob insert succeeded
        await store.put_checkpoint(Scope(w.a, "test"), thread, "", "cp-1", "p" * 101, ("msgpack", b"c"), ("json", b"m"), [("x", "v1", ("msgpack", b"blob"))])
    assert (await db.execute(text("select count(*) from agents.graph_checkpoint_blobs where thread_id = :t"), {"t": thread})).scalar_one() == 0
    assert (await db.execute(text("select count(*) from agents.graph_checkpoints where thread_id = :t"), {"t": thread})).scalar_one() == 0


async def test_a_worker_serving_two_clinics_gets_each_clinics_own_rows_for_the_same_thread_id(db):
    w = await world(db)
    both = _u()
    await raw(db, f"insert into iam.accounts (id, account_type, status, origin) values ('{both}', 'staff', 'active', 'test');\n"
              f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values ('{both}', '{w.a}', 'queue_worker', 'test'), ('{both}', '{w.b}', 'queue_worker', 'test');")
    saver = PostgresCheckpointSaver(SqlCheckpointStore(make_session(db, both)))
    thread, c_a, c_b = f"shared-{_u()}", checkpoint({"x": "A"}), checkpoint({"x": "B"})
    await saver.aput(cfg(w, thread), c_a, {"step": 0}, c_a["channel_versions"])
    await saver.aput(cfg(w, thread, clinic_id=str(w.b)), c_b, {"step": 0}, c_b["channel_versions"])
    late = checkpoint({"x": "poison"})
    late["id"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    await saver.aput(cfg(w, thread, clinic_id=str(w.b)), late, {"step": 1}, late["channel_versions"])
    got_a = await saver.aget_tuple(cfg(w, thread))
    got_b = await saver.aget_tuple(cfg(w, thread, clinic_id=str(w.b)))
    assert got_a.checkpoint["channel_values"] == {"x": "A"} and got_a.config["configurable"]["checkpoint_id"] == c_a["id"]
    assert got_b.checkpoint["channel_values"] == {"x": "poison"}  # B's own latest; A never sees it
    assert [t.config["configurable"]["checkpoint_id"] async for t in saver.alist(cfg(w, thread))] == [c_a["id"]]
