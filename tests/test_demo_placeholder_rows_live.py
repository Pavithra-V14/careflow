"""Migration 0096 against the real database (inside ONE never-committed transaction): the label column on each owner-managed table, and `ops.demo_placeholder_rows()` that the startup check calls.

The function counts labelled rows across the whole database (other test files commit their own), so every count here is compared as "at least what this test added", never as an exact total.
"""

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
LABEL = "DEMO PLACEHOLDER, not approved by the clinic owner"

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


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


async def counts(conn, *, as_app=False):
    if as_app:
        await conn.exec_driver_sql("set local role careflow_app")
    try:
        return {r.o_source: int(r.o_rows) for r in (await conn.execute(text("select o_source, o_rows from ops.demo_placeholder_rows()"))).all()}
    finally:
        if as_app:
            await conn.exec_driver_sql("reset role")


async def test_labelled_rows_are_counted_per_source_unlabelled_rows_are_not_and_the_app_role_sees_counts_only(db):
    before = await counts(db)
    clinic, visit_type = uuid.uuid4(), uuid.uuid4()
    await db.execute(text("insert into iam.clinics (id, name, origin) values (:c, 'PH A', 'test')"), {"c": clinic})
    await db.execute(text("insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values (:v, :c, 'V', 30, 'test', :k)"), {"v": visit_type, "c": clinic, "k": f"ph-{visit_type}"})
    p = {"c": clinic, "l": LABEL, "v": visit_type}
    await db.execute(text("insert into iam.break_glass_reasons (clinic_id, code, provenance) values (:c, 'emergency_care', :l), (:c, 'unlabelled_word', null)"), p)
    await db.execute(text("insert into iam.break_glass_settings (clinic_id, max_minutes, max_reopens, provenance) values (:c, 60, 3, :l)"), p)
    await db.execute(text("insert into iam.public_grievance_limits (clinic_id, max_per_hour, provenance) values (:c, 20, :l)"), p)
    await db.execute(text("insert into docs.canary_settings (clinic_id, max_per_day, provenance) values (:c, 5, :l)"), p)
    await db.execute(text("insert into ops.spend_limits (clinic_id, max_usd_per_call, provenance) values (:c, 0.05, :l)"), p)
    await db.execute(text("insert into sched.patient_bookable_visit_types (clinic_id, visit_type_id, max_future_bookings, provenance) values (:c, :v, 2, :l)"), p)
    await db.execute(text("insert into iam.family_link_rules (clinic_id, link_type, min_dependant_age_years, max_dependant_age_years, requires_dependant_consent, provenance) values (:c, 'guardian_of_minor', 0, 9, true, :l)"), p)
    after = await counts(db)
    assert set(after) == {"iam.break_glass_reasons", "iam.break_glass_settings", "iam.public_grievance_limits", "docs.canary_settings", "ops.spend_limits", "sched.patient_bookable_visit_types",
                          "iam.family_link_rules", "iam.consent_notices"}
    for source in after:
        if source != "iam.consent_notices":
            assert after[source] >= before[source] + 1, source
    assert after["iam.break_glass_reasons"] - before["iam.break_glass_reasons"] == 1  # the unlabelled word is not counted
    seen_by_the_app = await counts(db, as_app=True)
    assert seen_by_the_app == after  # the application role can run it, and gets the same counts and nothing else


async def test_the_application_role_still_cannot_read_or_write_the_owner_managed_tables_or_see_the_label(db):
    await db.exec_driver_sql("set local role careflow_app")
    try:
        for table in ("iam.break_glass_reasons", "iam.break_glass_settings", "iam.public_grievance_limits", "docs.canary_settings", "ops.spend_limits", "sched.patient_bookable_visit_types"):
            async with db.begin_nested():
                with pytest.raises(DBAPIError) as info:
                    await db.execute(text(f"select provenance from {table}"))
                assert getattr(info.value.orig, "sqlstate", None) == "42501", table
    finally:
        await db.exec_driver_sql("reset role")


async def test_a_label_longer_than_the_column_allows_is_refused(db):
    clinic = uuid.uuid4()
    await db.execute(text("insert into iam.clinics (id, name, origin) values (:c, 'PH B', 'test')"), {"c": clinic})
    with pytest.raises(DBAPIError) as info:
        async with db.begin_nested():
            await db.execute(text("insert into ops.spend_limits (clinic_id, max_usd_per_call, provenance) values (:c, 0.05, :l)"), {"c": clinic, "l": "x" * 201})
    assert getattr(info.value.orig, "sqlstate", None) == "23514"
