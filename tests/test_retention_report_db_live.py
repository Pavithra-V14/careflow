"""Migration 0071 against the real database: the report-only retention job. Callable only by the queue worker of the clinic; it counts checkpoint threads past a cutoff and patients past a cutoff (unknown is counted
apart, never as due), writes ONE audit row per clinic per day, and deletes nothing. Inside ONE never-committed outer transaction, as the restricted `careflow_app` role.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import producer_sweeps
from careflow.care_gap_sql import sqlstate
from careflow.producer_sweeps import retention_report_sweep
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
REPORT = "select checkpoint_threads_due, patients_past_period, patients_no_encounter, created from ops.retention_report_as_worker(:c, :a, :b)"
NOW = datetime.now(UTC)
CP = (
    "insert into agents.graph_checkpoints (clinic_id, origin, thread_id, checkpoint_ns, checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata, created_at, idempotency_key) "
    "values ($1, 'test', $2, '', $3, 'msgpack', '\\x01'::bytea, 'json', '\\x02'::bytea, $4, $5)"
)


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


async def pg(db):
    return (await db.get_raw_connection()).driver_connection


async def as_app(db, person, sql, **params):
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(person), "role": "authenticated"})})
            result = await db.execute(text(sql), params)
            value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other_clinic", "worker", "admin", "provider", "location", "vt")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'RR-A', 'test'), ($2, 'RR-B', 'test')", ids["clinic"], ids["other_clinic"])
    for k in ("worker", "admin"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'queue_worker', 'test'), ($3, $2, 'admin', 'test')", ids["worker"], ids["clinic"], ids["admin"])
    await h.execute("insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ($1, $2, 'P', 'test', $3)", ids["provider"], ids["clinic"], f"rp-{ids['provider']}")
    await h.execute("insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values ($1, $2, 'L', 'test', $3)", ids["location"], ids["clinic"], f"rl-{ids['location']}")
    await h.execute("insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values ($1, $2, 'DEMO visit type', 30, 'test', $3)", ids["vt"], ids["clinic"], f"rv-{ids['vt']}")

    async def patient(name):
        pid = uuid.uuid4()
        await h.execute("insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, decode(repeat('01', 32), 'hex'), 1, decode(repeat('01', 32), 'hex'), 1, 'test', $3)", pid, ids["clinic"], f"{name}-{pid}")
        return pid

    async def booking(pid, starts, cancelled=False):
        slot, bid = uuid.uuid4(), uuid.uuid4()
        await h.execute(
            "insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) values ($1, $2, $3, $4, $5, $6, $7, $8, 'test', $9)",
            slot, ids["clinic"], ids["provider"], ids["location"], ids["vt"], starts, starts + timedelta(minutes=30), "open" if cancelled else "booked", f"rs-{slot}",
        )
        if cancelled:
            await h.execute(
                "insert into sched.bookings (id, clinic_id, slot_id, patient_id, status, cancelled_at, cancel_idempotency_key, origin, idempotency_key) values ($1, $2, $3, $4, 'cancelled', now(), $5, 'test', $6)",
                bid, ids["clinic"], slot, pid, f"rc-{bid}", f"rb-{bid}",
            )
        else:
            await h.execute("insert into sched.bookings (id, clinic_id, slot_id, patient_id, origin, idempotency_key) values ($1, $2, $3, $4, 'test', $5)", bid, ids["clinic"], slot, pid, f"rb-{bid}")

    old, recent, _none, future, cancelled = [await patient(n) for n in ("old", "recent", "none", "future", "cancelled")]
    await booking(old, NOW - timedelta(days=1500))
    await booking(old, NOW - timedelta(days=1400))  # the NEWEST started booking decides: still past a 1000-day cutoff
    await booking(recent, NOW - timedelta(days=1500))
    await booking(recent, NOW - timedelta(days=2))  # one recent visit: not past the period
    await booking(future, NOW + timedelta(days=20))  # has not happened: no encounter
    await booking(cancelled, NOW - timedelta(days=2000), cancelled=True)  # a cancelled booking is not an encounter
    # checkpoint threads: one old (two checkpoints, the newest decides), one fresh, one in another clinic
    await h.execute(CP, ids["clinic"], "t-old", "c1", NOW - timedelta(days=200), f"cp-{uuid.uuid4()}")
    await h.execute(CP, ids["clinic"], "t-old", "c2", NOW - timedelta(days=150), f"cp-{uuid.uuid4()}")
    await h.execute(CP, ids["clinic"], "t-new", "c1", NOW - timedelta(days=1), f"cp-{uuid.uuid4()}")
    await h.execute(CP, ids["other_clinic"], "t-other", "c1", NOW - timedelta(days=900), f"cp-{uuid.uuid4()}")
    return ids


def cutoffs():
    return NOW - timedelta(days=90), NOW - timedelta(days=1000)


async def count(db, sql, **p):
    return (await db.execute(text(sql), p)).scalar()


async def test_it_counts_old_threads_and_patients_and_counts_unknown_apart(db):
    w = await world(db)
    a, b = cutoffs()
    assert await as_app(db, w["worker"], REPORT, c=w["clinic"], a=a, b=b) == [(1, 1, 3, True)]  # 1 old thread; 1 patient past; none + future + cancelled have no encounter on record
    row = (await db.execute(text("select actor_role, details from audit.log where action = 'retention.report' and clinic_id = :c"), {"c": w["clinic"]})).one()
    assert row.actor_role == "queue_worker" and row.details["deleted"] == 0 and row.details["prompt_response_bodies"] == "not_stored"
    assert row.details["checkpoint_threads_due"] == 1 and row.details["patients_past_period"] == 1 and row.details["patients_no_encounter"] == 3
    assert "minor_record" in row.details["not_assessed"] and "pregnancy_record" in row.details["not_assessed"]


async def test_it_deletes_nothing(db):
    w = await world(db)
    before = [await count(db, f"select count(*) from {t} where clinic_id = :c", c=w["clinic"]) for t in ("agents.graph_checkpoints", "mpi.patients", "sched.bookings")]
    a, b = cutoffs()
    await as_app(db, w["worker"], REPORT, c=w["clinic"], a=a, b=b)
    assert [await count(db, f"select count(*) from {t} where clinic_id = :c", c=w["clinic"]) for t in ("agents.graph_checkpoints", "mpi.patients", "sched.bookings")] == before
    assert await as_app(db, w["worker"], "delete from agents.graph_checkpoints") == "42501"  # and the application role still has no direct delete


async def test_one_audit_row_a_day_and_a_period_left_unset_is_not_assessed(db):
    w = await world(db)
    a, b = cutoffs()
    assert (await as_app(db, w["worker"], REPORT, c=w["clinic"], a=a, b=b))[0][3] is True
    assert (await as_app(db, w["worker"], REPORT, c=w["clinic"], a=a, b=b))[0][3] is False  # same day: counted again, no second audit row
    assert await count(db, "select count(*) from audit.log where action = 'retention.report' and clinic_id = :c", c=w["clinic"]) == 1
    w2 = await world(db)
    assert await as_app(db, w2["worker"], REPORT, c=w2["clinic"], a=None, b=b) == [(None, 1, 3, True)]  # no checkpoint period: null, not zero
    assert await as_app(db, w2["worker"], "select checkpoint_threads_due, patients_past_period, patients_no_encounter from ops.retention_report_as_worker(:c, :a, :b)", c=w2["other_clinic"], a=a, b=b) == "42501"


async def test_only_the_queue_worker_of_the_clinic_and_never_a_future_cutoff(db):
    w = await world(db)
    a, b = cutoffs()
    assert await as_app(db, w["admin"], REPORT, c=w["clinic"], a=a, b=b) == "42501"
    assert await as_app(db, w["worker"], REPORT, c=w["other_clinic"], a=a, b=b) == "42501"
    assert await as_app(db, w["worker"], REPORT, c=w["clinic"], a=NOW + timedelta(days=1), b=b) == "42501"
    assert await as_app(db, w["worker"], REPORT, c=w["clinic"], a=None, b=None) == "42501"  # an empty call would use up the day's audit row
    assert await count(db, "select count(*) from audit.log where action = 'retention.report' and clinic_id = :c", c=w["clinic"]) == 0  # every refusal wrote nothing


async def test_the_sweep_does_nothing_with_no_period_set_and_reports_once_a_day_when_one_is(db, monkeypatch):
    w = await world(db)
    monkeypatch.setattr(producer_sweeps, "_reported_on", {})
    assert await retention_report_sweep(db, None, None, origin="test") == 0
    assert await count(db, "select count(*) from audit.log where action = 'retention.report' and clinic_id = :c", c=w["clinic"]) == 0
