"""The durable Scheduling runs against the real database: front desk's narrow checkpoint access (migration 0037) and the only way front desk can raise a backfill review task, agents.raise_backfill_task
(migration 0038). Everything runs as the restricted `careflow_app` role with a verified account's claims inside ONE never-committed outer transaction."""

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

from tests.db_helpers import ensure_app_role_sa, without_scope

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
REFUSED = "backfill task refused"
TASK = "select agents.raise_backfill_task(:c, :s, :st, :p, :r, :k)"
CHECKPOINT = (
    "insert into agents.graph_checkpoints (clinic_id, origin, thread_id, checkpoint_ns, checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata, idempotency_key) "
    "values (:c, 'test', :t, '', :i, 'msgpack', '\\x01'::bytea, 'json', '\\x02'::bytea, :k)"
)


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
    w.a, w.b, w.slot, w.slot_b = _u(), _u(), _u(), _u()
    roster = [("fd", w.a, "front_desk"), ("nurse", w.a, "nurse"), ("doctor", w.a, "doctor"), ("fd_b", w.b, "front_desk")]
    w.people = {k: _u() for k, _, _ in roster}
    ids = {k: _u() for k in ("prov", "loc", "vt", "prov_b", "loc_b", "vt_b")}
    start = "date_trunc('hour', now()) + interval '500 days'"
    slot = lambda sid, clinic, p, lo, vt: (
        f"insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ('{p}', '{clinic}', 'P', 'test', 'bd-p-{p}');\n"
        f"insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values ('{lo}', '{clinic}', 'L', 'test', 'bd-l-{lo}');\n"
        f"insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values ('{vt}', '{clinic}', 'V', 20, 'test', 'bd-v-{vt}');\n"
        f"insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) "
        f"values ('{sid}', '{clinic}', '{p}', '{lo}', '{vt}', {start}, {start} + interval '20 minutes', 'open', 'test', 'bd-s-{sid}');\n"
    )
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'BD A', 'test'), ('{w.b}', 'BD B', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{i}', 'staff', 'active', 'test')" for i in w.people.values())
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{w.people[k]}', '{c}', '{r}', 'test')" for k, c, r in roster) + ";\n"
        + slot(w.slot, w.a, ids["prov"], ids["loc"], ids["vt"]) + slot(w.slot_b, w.b, ids["prov_b"], ids["loc_b"], ids["vt_b"]),
    )
    return w


async def call(db, uid, sql, **params):
    """Run one statement as `careflow_app` with the account's claims in a savepoint; a refusal rolls the savepoint back (which also restores the role)."""
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(uid), "role": "authenticated"})})
        value = (await db.execute(text(sql), params)).scalar()
        await db.exec_driver_sql("reset role")
        return value


def key(w, status="needs_human", slot=None):
    return f"sched-hold:{slot or w.slot}:{status}"


async def raise_task(db, w, who="fd", *, clinic=None, slot=None, status="needs_human", priority="standard", role="nurse", k=None):
    return await call(db, w.people[who], TASK, c=clinic or w.a, s=slot or w.slot, st=status, p=priority, r=role, k=k or key(w, status, slot))


async def one(db, sql, **params):
    return (await db.execute(text(sql), params)).scalar()


# ---- 0038: the task function -----------------------------------------------------------------------------------------------------------------------------------


async def test_front_desk_raises_one_open_task_with_ids_only_and_a_replay_returns_the_same_one(db):
    w = await world(db)
    task = await raise_task(db, w)
    assert await raise_task(db, w) == task  # the same key: the same task, nothing more written
    row = (await db.execute(text("select kind, priority, assigned_role, status, source_message_id, provenance, clinic_id, origin from agents.review_tasks where id = :t"), {"t": task})).one()
    assert tuple(row[:5]) == ("backfill_needs_human", "standard", "nurse", "open", f"slot:{w.slot}") and row[5] == {"slot_id": str(w.slot), "status": "needs_human"} and row[6] == w.a and row[7] == "test"
    audit = (await db.execute(text("select actor_id, actor_role, action, details, idempotency_key from audit.log where resource_id = :s and action = 'backfill.task_raised'"), {"s": str(w.slot)})).all()
    assert len(audit) == 1 and audit[0][0] == w.people["fd"] and audit[0][1] == "front_desk" and audit[0][4].startswith("sys:")
    assert without_scope(audit[0][3]) == {"status": "needs_human", "task_id": str(task)}


@pytest.mark.parametrize("who", ["nurse", "doctor", "fd_b"])
async def test_only_front_desk_of_the_named_clinic_may_raise_it(db, who):
    w = await world(db)
    with pytest.raises(Exception, match=REFUSED):
        await raise_task(db, w, who)
    assert await one(db, "select count(*) from agents.review_tasks where kind = 'backfill_needs_human' and clinic_id = :c", c=w.a) == 0


async def test_every_input_is_closed_a_foreign_slot_a_wrong_word_or_a_wrong_key_is_the_one_fixed_refusal(db):
    w = await world(db)
    bad = [
        {"slot": w.slot_b},  # a slot of another clinic
        {"slot": _u()},  # no such slot
        {"status": "red_flag"},  # not one of the three words
        {"priority": "urgent"},
        {"role": "admin"},
        {"role": "front_desk"},  # front desk could not see or resolve it
        {"k": "sched-hold:other:needs_human"},  # the key must be exactly the graph's key
    ]
    for kwargs in bad:
        with pytest.raises(Exception, match=REFUSED):
            await raise_task(db, w, **kwargs)
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people["fd"], TASK, c=None, s=w.slot, st="needs_human", p="standard", r="nurse", k=key(w))
    assert await one(db, "select count(*) from agents.review_tasks where kind = 'backfill_needs_human'") == 0


async def test_a_call_with_no_identity_is_refused_and_the_application_role_has_no_table_right_to_insert_a_task(db):
    w = await world(db)
    with pytest.raises(Exception, match=REFUSED):
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', '', true)"))
            await db.execute(text(TASK), {"c": w.a, "s": w.slot, "st": "needs_human", "p": "standard", "r": "front_desk", "k": key(w)})
    with pytest.raises(Exception, match="row-level security|permission denied"):  # front desk still has no table-wide insert policy for this kind
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(w.people["fd"]), "role": "authenticated"})})
            await db.execute(
                text(
                    "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
                    "values (:c, 'test', 'backfill_needs_human', 'standard', 'front_desk', 'x', '{}'::jsonb, 'direct')"
                ),
                {"c": w.a},
            )


async def test_a_different_status_is_a_different_task_for_the_same_slot(db):
    w = await world(db)
    first = await raise_task(db, w, status="needs_human")
    second = await raise_task(db, w, status="booking_needs_human")
    assert first != second


# ---- 0037: front desk's checkpoints ----------------------------------------------------------------------------------------------------------------------------


async def test_front_desk_writes_and_reads_scheduling_checkpoints_of_its_clinic_only(db):
    w = await world(db)
    thread = f"scheduling:{w.slot}"
    assert await call(db, w.people["fd"], CHECKPOINT + " returning 1", c=w.a, t=thread, i="c1", k=f"cp-{_u()}") == 1
    assert await one(db, "select count(*) from agents.graph_checkpoints where thread_id = :t", t=thread) == 1
    seen = await call(db, w.people["fd"], "select count(*) from agents.graph_checkpoints where thread_id = :t", t=thread)
    assert seen == 1
    assert await call(db, w.people["fd_b"], "select count(*) from agents.graph_checkpoints where thread_id = :t", t=thread) == 0  # another clinic sees nothing
    with pytest.raises(Exception, match="row-level security"):  # and cannot write into this clinic
        await call(db, w.people["fd_b"], CHECKPOINT + " returning 1", c=w.a, t=thread, i="c2", k=f"cp-{_u()}")


@pytest.mark.parametrize("thread", ["front_door:abc", "other:abc", "scheduling", "xscheduling:abc"])
async def test_front_desk_cannot_touch_any_other_graphs_thread(db, thread):
    w = await world(db)
    with pytest.raises(Exception, match="row-level security"):
        await call(db, w.people["fd"], CHECKPOINT + " returning 1", c=w.a, t=thread, i="c1", k=f"cp-{_u()}")
    await raw(db, f"insert into agents.graph_checkpoints (clinic_id, origin, thread_id, checkpoint_ns, checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata, idempotency_key) values ('{w.a}', 'test', '{thread}', '', 'c9', 'msgpack', '\\x01', 'json', '\\x02', 'cp-{_u()}')")
    assert await call(db, w.people["fd"], "select count(*) from agents.graph_checkpoints where thread_id = :t", t=thread) == 0  # it cannot even see it


async def test_the_append_only_history_still_holds_for_front_desk(db):
    w = await world(db)
    thread = f"scheduling:{w.slot}"
    await call(db, w.people["fd"], CHECKPOINT + " returning 1", c=w.a, t=thread, i="c1", k=f"cp-{_u()}")
    for sql in ("update agents.graph_checkpoints set checkpoint_type = 'x' where thread_id = :t", "delete from agents.graph_checkpoints where thread_id = :t"):
        with pytest.raises(Exception, match="permission denied|append-only|row-level"):
            await call(db, w.people["fd"], sql + " returning 1", t=thread)


BLOB = (
    "insert into agents.graph_checkpoint_blobs (clinic_id, origin, thread_id, checkpoint_ns, channel, version, blob_type, blob, idempotency_key) "
    "values (:c, 'test', :t, '', 'ch', :v, 'msgpack', '\x01'::bytea, :k)"
)
WRITE = (
    "insert into agents.graph_checkpoint_writes (clinic_id, origin, thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, write_type, write_blob, idempotency_key) "
    "values (:c, 'test', :t, '', 'c1', 'tk', :x, 'ch', 'msgpack', '\x01'::bytea, :k)"
)


async def test_blobs_and_writes_follow_the_same_prefix_and_clinic_rules(db):
    w = await world(db)
    mine, other = f"scheduling:{w.slot}", "front_door:abc"
    assert await call(db, w.people["fd"], BLOB + " returning 1", c=w.a, t=mine, v="1", k=f"b-{_u()}") == 1
    assert await call(db, w.people["fd"], WRITE + " returning 1", c=w.a, t=mine, x=0, k=f"w-{_u()}") == 1
    for sql, params in ((BLOB, {"v": "2"}), (WRITE, {"x": 0})):
        with pytest.raises(Exception, match="row-level security"):
            await call(db, w.people["fd"], sql + " returning 1", c=w.a, t=other, k=f"x-{_u()}", **params)  # another graph's thread
        with pytest.raises(Exception, match="row-level security"):
            await call(db, w.people["fd_b"], sql + " returning 1", c=w.a, t=mine, k=f"x-{_u()}", **params)  # another clinic
    assert await call(db, w.people["fd"], "select count(*) from agents.graph_checkpoint_blobs where thread_id = :t", t=mine) == 1
    assert await call(db, w.people["fd_b"], "select count(*) from agents.graph_checkpoint_blobs where thread_id = :t", t=mine) == 0


async def test_only_the_special_negative_index_writes_of_a_scheduling_thread_can_be_replaced_by_front_desk(db):
    w = await world(db)
    mine = f"scheduling:{w.slot}"
    await call(db, w.people["fd"], WRITE + " returning 1", c=w.a, t=mine, x=-1, k=f"w-{_u()}")
    await call(db, w.people["fd"], WRITE.replace("'tk'", "'tk2'") + " returning 1", c=w.a, t=mine, x=0, k=f"w-{_u()}")
    update = "with u as (update agents.graph_checkpoint_writes set write_blob = '\x09'::bytea where thread_id = :t and idx = :x returning 1) select count(*) from u"
    assert await call(db, w.people["fd"], update, t=mine, x=-1) == 1  # a special entry is replaced
    with pytest.raises(Exception, match="append-only|restrict"):
        await call(db, w.people["fd"], update, t=mine, x=0)  # a regular write is final, for everyone
    assert await call(db, w.people["fd_b"], update, t=mine, x=-1) == 0  # another clinic cannot even see it


async def test_nurse_and_doctor_still_see_the_clinics_checkpoints_and_the_task_is_theirs_to_see(db):
    w = await world(db)
    thread = f"scheduling:{w.slot}"
    await call(db, w.people["fd"], CHECKPOINT + " returning 1", c=w.a, t=thread, i="c1", k=f"cp-{_u()}")
    for who in ("nurse", "doctor"):
        assert await call(db, w.people[who], "select count(*) from agents.graph_checkpoints where thread_id = :t", t=thread) == 1
    task = await raise_task(db, w, role="doctor")
    assert await call(db, w.people["doctor"], "select count(*) from agents.review_tasks where id = :t", t=task) == 1
    assert await call(db, w.people["fd_b"], "select count(*) from agents.review_tasks where id = :t", t=task) == 0


async def test_the_task_function_is_executable_by_the_application_role_only_and_its_audit_row_names_the_clinic(db):
    w = await world(db)
    for role in ("public", "anon", "authenticated", "service_role"):
        granted = await one(
            db, "select case when :r = 'public' or exists (select 1 from pg_roles where rolname = :r) then has_function_privilege(:r, 'agents.raise_backfill_task(uuid, uuid, text, text, text, text)', 'execute') else false end", r=role
        )
        assert granted is False, role
    await raise_task(db, w)
    assert (await db.execute(text("select clinic_id, origin from audit.log where resource_id = :s and action = 'backfill.task_raised'"), {"s": str(w.slot)})).one() == (w.a, "test")
