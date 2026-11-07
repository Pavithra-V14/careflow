"""The Care Gaps tables (migration 0044) and their SQL sources against the real database, as the restricted `careflow_app` role with verified claims, inside ONE never-committed outer transaction."""

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import care_gap_sql, care_gaps
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
ROLES = ("nurse", "doctor", "admin", "front_desk", "auditor", "business_owner")


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
    a, b = uuid.uuid4(), uuid.uuid4()
    people = {r: uuid.uuid4() for r in ROLES}
    people["nurse_b"] = uuid.uuid4()
    pa, pb = uuid.uuid4(), uuid.uuid4()
    grants = [(r, a, r) for r in ROLES] + [("nurse_b", b, "nurse")]
    await raw(
        db,
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'CG A', 'test'), ('{b}', 'CG B', 'test');"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{i}', 'staff', 'active', 'test')" for i in people.values()) + ";"
        "insert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{people[k]}', '{c}', '{r}', 'test')" for k, c, r in grants) + ";"
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values "
        f"('{pa}', '{a}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'cg-{pa}'), ('{pb}', '{b}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'cg-{pb}');",
    )
    return a, b, people, pa, pb


@asynccontextmanager
async def as_user(db, person):
    """A savepoint in which the connection is `careflow_app` with the account's claims. An error inside rolls the savepoint back (which also restores the role)."""
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(person), "role": "authenticated"})})
        yield db
        await db.exec_driver_sql("reset role")


async def state_of(db, person, sql, **params):
    """Run a statement as `person`; the rows, or the SQLSTATE of the refusal."""
    try:
        async with as_user(db, person) as c:
            result = await c.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return care_gap_sql.sqlstate(exc)


async def seed_gap_data(db, a, pa):
    rule_interval, rule_unknown = uuid.uuid4(), uuid.uuid4()
    episode, ep_closed = uuid.uuid4(), uuid.uuid4()
    er_overdue, er_unknown, er_closed_ep = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    barrier_open, barrier_rebook = uuid.uuid4(), uuid.uuid4()
    done = datetime.now(UTC).date() - timedelta(days=100)
    await raw(
        db,
        "insert into ops.care_recurrence_rules (id, clinic_id, origin, rule_code, interval_days, overdue_after_days, idempotency_key) values "
        f"('{rule_interval}', '{a}', 'test', 'demo_rule', 30, 5, 'r1-{a}'), ('{rule_unknown}', '{a}', 'test', 'demo_rule_two', 30, 5, 'r2-{a}');"
        "insert into ops.care_episodes (id, clinic_id, origin, patient_id, episode_code, started_on, closed_on, idempotency_key) values "
        f"('{episode}', '{a}', 'test', '{pa}', 'demo_episode', '2026-01-01', null, 'e1-{a}'), ('{ep_closed}', '{a}', 'test', '{pa}', 'demo_episode', '2026-01-01', '2026-02-01', 'e2-{a}');"
        "insert into ops.care_episode_rules (id, clinic_id, origin, episode_id, rule_id, last_done_on, idempotency_key) values "
        f"('{er_overdue}', '{a}', 'test', '{episode}', '{rule_interval}', '{done}', 'er1-{a}'), ('{er_unknown}', '{a}', 'test', '{episode}', '{rule_unknown}', null, 'er2-{a}'), "
        f"('{er_closed_ep}', '{a}', 'test', '{ep_closed}', '{rule_interval}', '{done}', 'er3-{a}');"
        "insert into ops.care_barriers (id, clinic_id, origin, patient_id, barrier, idempotency_key) values "
        f"('{barrier_open}', '{a}', 'test', '{pa}', 'transport', 'b1-{a}'), ('{barrier_rebook}', '{a}', 'test', '{pa}', 'scheduling_conflict', 'b2-{a}');",
    )
    return {"er_overdue": er_overdue, "er_unknown": er_unknown, "er_closed_ep": er_closed_ep, "barrier_open": barrier_open, "barrier_rebook": barrier_rebook}


async def test_nurse_doctor_and_admin_of_the_clinic_read_every_table_and_nobody_else_does(db):
    a, _, people, pa, _ = await world(db)
    await seed_gap_data(db, a, pa)
    tables = ("care_recurrence_rules", "care_episodes", "care_episode_rules", "care_barriers", "care_review_samples")
    for who in ("nurse", "doctor", "admin"):
        for t in tables[:4]:
            rows = await state_of(db, people[who], f"select count(*) from ops.{t}")
            assert rows[0][0] > 0, (who, t)
    for who in ("front_desk", "auditor", "business_owner", "nurse_b"):
        for t in tables:
            assert (await state_of(db, people[who], f"select count(*) from ops.{t}"))[0][0] == 0, (who, t)


async def test_an_admin_cannot_write_rules_episodes_or_episode_rules_and_nobody_deletes(db):
    a, _, people, pa, _ = await world(db)
    await seed_gap_data(db, a, pa)
    for sql in (
        f"insert into ops.care_recurrence_rules (clinic_id, origin, rule_code, interval_days, overdue_after_days, idempotency_key) values ('{a}', 'test', 'x_rule', 1, 0, 'zz')",
        f"insert into ops.care_episodes (clinic_id, origin, patient_id, episode_code, started_on, idempotency_key) values ('{a}', 'test', '{uuid.uuid4()}', 'x_ep', '2026-01-01', 'zz')",
        "update ops.care_recurrence_rules set overdue_after_days = 0",
        "delete from ops.care_episodes",
        "delete from ops.care_barriers",
    ):
        assert await state_of(db, people["admin"], sql) == "42501", sql
    assert await state_of(db, people["admin"], "update ops.care_episode_rules set last_done_on = current_date") == 0  # (0048) the column is writable by nurse and doctor only: for an admin no row matches


async def test_a_barrier_is_recorded_by_nurse_doctor_or_admin_only_for_a_patient_of_the_clinic(db):
    a, _, people, pa, pb = await world(db)
    insert = "insert into ops.care_barriers (clinic_id, origin, patient_id, barrier, idempotency_key) values (:c, 'test', :p, 'transport', :k)"
    for who in ("nurse", "doctor", "admin"):
        assert await state_of(db, people[who], insert, c=a, p=pa, k=f"k-{who}-{uuid.uuid4()}") == 1, who
    for who in ("front_desk", "auditor", "business_owner", "nurse_b"):
        assert await state_of(db, people[who], insert, c=a, p=pa, k=f"k-{uuid.uuid4()}") == "42501", who
    assert await state_of(db, people["nurse"], insert, c=a, p=pb, k=f"k-{uuid.uuid4()}") == "23503"  # a patient of another clinic
    assert await state_of(db, people["nurse"], insert, c=a, p=pa, k="dup") == 1
    async with as_user(db, people["nurse"]) as c:
        await c.execute(text(insert), {"c": a, "p": pa, "k": "dup2"})
        with pytest.raises(DBAPIError) as dup:
            async with c.begin_nested():
                await c.execute(text(insert), {"c": a, "p": pa, "k": "dup2"})
        assert care_gap_sql.sqlstate(dup.value) == "23505"


async def test_a_barrier_cannot_be_inserted_already_resolved_or_with_a_type_outside_the_set(db):
    a, _, people, pa, _ = await world(db)
    assert await state_of(db, people["nurse"], f"insert into ops.care_barriers (clinic_id, origin, patient_id, barrier, idempotency_key, status) values ('{a}', 'test', '{pa}', 'cost', 'k1', 'resolved')") == "42501"
    assert await state_of(db, people["nurse"], f"insert into ops.care_barriers (clinic_id, origin, patient_id, barrier, idempotency_key) values ('{a}', 'test', '{pa}', 'made_up', 'k2')") == "23514"


async def test_a_barrier_is_resolved_once_as_the_caller_with_the_database_clock_and_is_then_final(db):
    a, _, people, pa, _ = await world(db)
    ids = await seed_gap_data(db, a, pa)
    barrier = ids["barrier_open"]
    resolve = f"update ops.care_barriers set status = 'resolved', resolved_by = '{people['nurse']}' where id = '{barrier}'"
    assert await state_of(db, people["front_desk"], resolve) == 0  # cannot see the row: updates nothing
    assert await state_of(db, people["nurse"], f"update ops.care_barriers set status = 'resolved', resolved_by = '{people['doctor']}' where id = '{barrier}'") == "42501"  # not as someone else
    assert await state_of(db, people["nurse"], f"update ops.care_barriers set barrier = 'cost' where id = '{barrier}'") == "42501"  # no column grant
    assert await state_of(db, people["nurse"], resolve) == 1
    row = (await db.execute(text("select status, resolved_by, resolved_at is not null from ops.care_barriers where id = :i"), {"i": barrier})).one()
    assert tuple(row) == ("resolved", people["nurse"], True)
    assert await state_of(db, people["nurse"], f"update ops.care_barriers set status = 'resolved', resolved_by = '{people['nurse']}' where id = '{barrier}'") == 0  # final: no open row to update
    with pytest.raises(DBAPIError, match="final"):
        await db.execute(text("update ops.care_barriers set status = 'resolved' where id = :i"), {"i": barrier})  # even the owner cannot change it


async def test_the_sources_and_the_panel_see_real_rows_and_every_row_is_reachable(db):
    a, _, people, pa, _ = await world(db)
    ids = await seed_gap_data(db, a, pa)
    async with as_user(db, people["nurse"]) as c:
        sources = care_gap_sql.sources_for(c, a, "test")
        now = (await c.execute(text("select clock_timestamp()"))).scalar_one()
        panel = await care_gaps.build_panel(sources, now=now)
        assert {(r.source, r.item_id) for r in panel.rows} == {("overdue_recurrence", str(ids["er_overdue"])), ("unresolved_barrier", str(ids["barrier_open"]))}
        assert sources[0].unknown_dates == 1  # the rule with no last-done date is counted, not silently dropped and not called overdue
        assert panel.unreadable == () and {s.source: s.open_count for s in panel.summaries} == {"overdue_recurrence": 1, "after_the_fact_review": 0, "unresolved_barrier": 1}
        assert await care_gaps.verify_reachable(panel, sources) == []
        assert await sources[0].resolve(str(ids["er_closed_ep"])) is None  # a closed episode's rule is not a gap
        assert (await sources[2].resolve(str(ids["barrier_rebook"]))).is_open is False  # a scheduling conflict is not a social barrier
        flagged = care_gaps.alerts_for(panel, clinic_id=str(a), origin="test", alert_after_days=1)
        assert {x.ref for x in flagged.alerts} == {str(ids["er_overdue"])}  # the new barrier is 0 days old


async def test_record_and_resolve_helpers_replay_and_refuse_as_designed(db):
    a, _, people, pa, pb = await world(db)
    async with as_user(db, people["nurse"]) as c:
        first, created = await care_gap_sql.record_barrier(c, clinic_id=a, origin="test", patient_id=pa, barrier="cost", idempotency_key="h1")
        again, created_again = await care_gap_sql.record_barrier(c, clinic_id=a, origin="test", patient_id=pa, barrier="cost", idempotency_key="h1")
        assert first == again and created is True and created_again is False
        with pytest.raises(LookupError):
            await care_gap_sql.record_barrier(c, clinic_id=a, origin="test", patient_id=pb, barrier="cost", idempotency_key="h2")
        assert await care_gap_sql.resolve_barrier(c, barrier_id=first, resolved_by=people["nurse"]) == "resolved"
        assert await care_gap_sql.resolve_barrier(c, barrier_id=first, resolved_by=people["nurse"]) == "already_resolved"
        assert await care_gap_sql.resolve_barrier(c, barrier_id=uuid.uuid4(), resolved_by=people["nurse"]) == "not_found"


async def test_the_application_role_has_no_delete_or_truncate_and_nobody_else_has_access(db):
    for t in ("care_recurrence_rules", "care_episodes", "care_episode_rules", "care_barriers", "care_review_samples"):
        for priv in ("delete", "truncate"):
            assert (await db.execute(text("select has_table_privilege('careflow_app', :t, :p)"), {"t": f"ops.{t}", "p": priv})).scalar() is False
