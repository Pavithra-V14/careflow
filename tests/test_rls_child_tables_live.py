"""Clinic isolation for CHILD tables: rows that point at a parent in another clinic (BUILD-SPEC.md §17.1, §18.4, §21 "a clinic cannot read or
write another clinic's rows, including child tables with no direct clinic column"; CLAUDE.md rule 8). Live, read-only on the catalog; the
data tests run in a transaction that is always rolled back (nothing is committed).

WHAT THIS FILE GUARDS. Every table that holds clinic-scoped data carries `clinic_id` itself; the only tables without it are `iam.clinics`
(the clinic row itself) and `iam.accounts` (a person, not a clinic's data); their cross-clinic behaviour is covered in
tests/test_rls_iam_audit_live.py. So the spec's "child table with no clinic column" does not exist, and the realistic risk is a child row
whose foreign key to its parent does NOT pin the clinic, so the child could point at another clinic's parent. Composite foreign keys
(child_id, clinic_id) -> parent (id, clinic_id) close that.

History: until migration 0014 three single-column foreign keys left it open (sched.bookings.hold_id, mpi.merges.candidate_id,
ops.outbox.job_id). 0014 replaced them, and KNOWN_GAPS is now EMPTY. The structural test requires it to stay exactly right: a NEW foreign
key between clinic-scoped tables that does not carry clinic_id on both sides fails the build until someone reviews it (and either pins
it or lists it in KNOWN_GAPS with the reason).
"""

import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

APP_SCHEMAS = ["iam", "mpi", "sched", "clinical", "docs", "msg", "kb", "agents", "ai", "ops", "audit"]  # not the migration ledger
NO_CLINIC_COLUMN = {"iam.clinics", "iam.accounts"}
# (child table, parent table): a foreign key between two clinic-scoped tables that does not carry clinic_id on both sides. Empty since 0014.
KNOWN_GAPS: set[tuple[str, str]] = set()


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
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def fails(conn, sql):
    """Run `sql` in a savepoint. Returns the SQLSTATE of the error, or None if it succeeded (and is rolled back with the test)."""
    await conn.execute("savepoint attempt")
    try:
        await conn.execute(sql)
    except asyncpg.PostgresError as exc:
        await conn.execute("rollback to savepoint attempt")
        return exc.sqlstate
    await conn.execute("release savepoint attempt")
    return None


# ---- structural: which tables have no clinic_id, and which foreign keys do not pin it -----------------------------------------------


async def clinic_scoped_tables(conn) -> tuple[set[str], set[str]]:
    rows = await conn.fetch(
        """select n.nspname || '.' || c.relname as t,
                  exists (select 1 from pg_attribute a
                          where a.attrelid = c.oid and a.attname = 'clinic_id' and a.attnum > 0 and not a.attisdropped) as has_clinic
           from pg_class c join pg_namespace n on n.oid = c.relnamespace
           where c.relkind in ('r', 'p') and n.nspname = any($1::text[])""",
        APP_SCHEMAS,
    )
    return {r["t"] for r in rows if r["has_clinic"]}, {r["t"] for r in rows if not r["has_clinic"]}


async def test_every_application_table_carries_clinic_id_except_the_two_listed(db):
    scoped, unscoped = await clinic_scoped_tables(db)
    assert scoped, "found no clinic-scoped tables: the schema list or the catalog query is wrong"
    assert unscoped == NO_CLINIC_COLUMN, (
        f"a table without a clinic_id column appeared or disappeared: {sorted(unscoped ^ NO_CLINIC_COLUMN)}. A clinic-scoped child table "
        "with no clinic_id needs its own cross-clinic read/write test here before it is added to NO_CLINIC_COLUMN."
    )


async def test_foreign_keys_between_clinic_scoped_tables_pin_the_clinic_except_the_known_gaps(db):
    scoped, _ = await clinic_scoped_tables(db)
    rows = await db.fetch(
        """select c.conrelid::regclass::text as child, c.confrelid::regclass::text as parent,
                  (select array_agg(a.attname::text order by k.ord) from unnest(c.conkey) with ordinality as k(attnum, ord)
                     join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum) as child_cols,
                  (select array_agg(a.attname::text order by k.ord) from unnest(c.confkey) with ordinality as k(attnum, ord)
                     join pg_attribute a on a.attrelid = c.confrelid and a.attnum = k.attnum) as parent_cols
           from pg_constraint c join pg_namespace n on n.oid = c.connamespace
           where c.contype = 'f' and n.nspname = any($1::text[])""",
        APP_SCHEMAS,
    )
    assert rows, "found no foreign keys: the catalog query is wrong"
    gaps = {
        (r["child"], r["parent"])
        for r in rows
        if r["child"] in scoped and r["parent"] in scoped and not ("clinic_id" in r["child_cols"] and "clinic_id" in r["parent_cols"])
    }
    assert gaps == KNOWN_GAPS, (
        f"unreviewed unpinned foreign keys: {sorted(gaps - KNOWN_GAPS)}; fixed since the list was written (remove them from "
        f"KNOWN_GAPS): {sorted(KNOWN_GAPS - gaps)}"
    )


# ---- behavioural: a child row cannot point at another clinic's parent, where the foreign key pins the clinic ---------------------------


class World:
    pass


async def world(conn) -> World:
    w = World()
    w.a, w.b = _u(), _u()
    w.patient_a, w.patient_b = _u(), _u()
    w.provider_a, w.provider_b, w.location_a, w.location_b, w.vt_a, w.vt_b = (_u() for _ in range(6))
    w.slot_a, w.slot_b, w.hold_a = _u(), _u(), _u()
    w.slot_a2, w.slot_b2 = _u(), _u()  # no hold on these: the cross-clinic probes must not trip the one-active-hold-per-slot unique index first
    w.patient_a2, w.patient_b2, w.candidate_a, w.candidate_b, w.acct, w.job_a = (_u() for _ in range(6))
    pat = lambda pid, c: f"('{pid}', '{c}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'pat-{pid}')"
    slot = lambda sid, c, prov, loc, vt, hour: (
        f"('{sid}', '{c}', '{prov}', '{loc}', '{vt}', '2035-02-03 {hour}:00+00', '2035-02-03 {hour}:30+00', 'test', 'slot-{sid}')"
    )
    await conn.execute(
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'Clinic A', 'test'), ('{w.b}', 'Clinic B', 'test');\n"
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values "
        f"{pat(w.patient_a, w.a)}, {pat(w.patient_b, w.b)}, {pat(w.patient_a2, w.a)}, {pat(w.patient_b2, w.b)};\n"
        "insert into mpi.match_candidates (id, clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, origin, idempotency_key) values "
        f"('{w.candidate_a}', '{w.a}', '{w.patient_a2}', '{w.patient_a}', array['phone'], 'near_match', 'test', 'c-{w.candidate_a}'), "
        f"('{w.candidate_b}', '{w.b}', '{w.patient_b2}', '{w.patient_b}', array['phone'], 'near_match', 'test', 'c-{w.candidate_b}');\n"
        f"insert into iam.accounts (id, account_type, status, origin) values ('{w.acct}', 'staff', 'active', 'test');\n"
        "insert into ops.queue (id, clinic_id, origin, task_type, payload, idempotency_key) values "
        f"('{w.job_a}', '{w.a}', 'test', 'demo.task', '{{}}'::jsonb, 'q-{w.job_a}');\n"
        "insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values "
        f"('{w.provider_a}', '{w.a}', 'Dr A', 'test', 'p-{w.provider_a}'), ('{w.provider_b}', '{w.b}', 'Dr B', 'test', 'p-{w.provider_b}');\n"
        "insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values "
        f"('{w.location_a}', '{w.a}', 'Room A', 'test', 'l-{w.location_a}'), ('{w.location_b}', '{w.b}', 'Room B', 'test', 'l-{w.location_b}');\n"
        "insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values "
        f"('{w.vt_a}', '{w.a}', 'Review', 30, 'test', 'v-{w.vt_a}'), ('{w.vt_b}', '{w.b}', 'Review', 30, 'test', 'v-{w.vt_b}');\n"
        "insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, origin, idempotency_key) values "
        f"{slot(w.slot_a, w.a, w.provider_a, w.location_a, w.vt_a, '09')}, {slot(w.slot_b, w.b, w.provider_b, w.location_b, w.vt_b, '09')}, "
        f"{slot(w.slot_a2, w.a, w.provider_a, w.location_a, w.vt_a, '10')}, {slot(w.slot_b2, w.b, w.provider_b, w.location_b, w.vt_b, '10')};\n"
        "insert into sched.holds (id, clinic_id, slot_id, patient_id, expires_at, origin, idempotency_key) values "
        f"('{w.hold_a}', '{w.a}', '{w.slot_a}', '{w.patient_a}', now() + interval '1 hour', 'test', 'h-{w.hold_a}');"
    )
    return w


def booking(clinic, slot, patient, hold="null"):
    hold_sql = f"'{hold}'" if hold != "null" else "null"
    return (
        "insert into sched.bookings (clinic_id, slot_id, patient_id, hold_id, origin, idempotency_key) "
        f"values ('{clinic}', '{slot}', '{patient}', {hold_sql}, 'test', 'b-{_u()}')"
    )


async def test_the_control_a_booking_wholly_inside_one_clinic_is_accepted(db):
    w = await world(db)
    assert await fails(db, booking(w.b, w.slot_b, w.patient_b)) is None


async def test_a_booking_cannot_point_at_another_clinics_slot_or_patient(db):
    w = await world(db)
    assert await fails(db, booking(w.b, w.slot_a, w.patient_b)) == "23503"  # clinic B's booking on clinic A's slot
    assert await fails(db, booking(w.b, w.slot_b, w.patient_a)) == "23503"  # clinic B's booking for clinic A's patient


async def test_a_hold_cannot_point_at_another_clinics_slot_or_patient(db):
    w = await world(db)
    hold = lambda slot, patient: (
        "insert into sched.holds (clinic_id, slot_id, patient_id, expires_at, origin, idempotency_key) "
        f"values ('{w.b}', '{slot}', '{patient}', now() + interval '1 hour', 'test', 'h-{_u()}')"
    )
    assert await fails(db, hold(w.slot_b, w.patient_b)) is None  # control
    # The probes use slots that have NO active hold. (An earlier version probed slot_a, which already carries hold_a, and slot_b, which the
    # control above just took: the one-active-hold-per-slot unique index fires FIRST with 23505, so the test never reached the foreign key.)
    assert await fails(db, hold(w.slot_a2, w.patient_b)) == "23503"  # clinic B's hold on clinic A's slot
    assert await fails(db, hold(w.slot_b2, w.patient_a)) == "23503"  # clinic B's hold for clinic A's patient


async def test_a_slot_cannot_use_another_clinics_provider_location_or_visit_type(db):
    w = await world(db)
    slot = lambda prov, loc, vt, hour: (
        "insert into sched.slots (clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, origin, idempotency_key) "
        f"values ('{w.b}', '{prov}', '{loc}', '{vt}', '2035-02-04 {hour}:00+00', '2035-02-04 {hour}:30+00', 'test', 's-{_u()}')"
    )
    assert await fails(db, slot(w.provider_b, w.location_b, w.vt_b, "09")) is None  # control
    assert await fails(db, slot(w.provider_a, w.location_b, w.vt_b, "10")) == "23503"
    assert await fails(db, slot(w.provider_b, w.location_a, w.vt_b, "11")) == "23503"
    assert await fails(db, slot(w.provider_b, w.location_b, w.vt_a, "12")) == "23503"


async def test_a_booking_cannot_point_at_another_clinics_hold(db):
    """Was a documented gap until 0014 (bookings.hold_id was a single-column key). The control is B's own booking with no hold."""
    w = await world(db)
    assert await fails(db, booking(w.b, w.slot_b, w.patient_b)) is None
    assert await fails(db, booking(w.b, w.slot_b, w.patient_b, hold=w.hold_a)) == "23503"


def merge(w, clinic, master, subordinate, candidate):
    return (
        "insert into mpi.merges (clinic_id, master_patient_id, subordinate_patient_id, candidate_id, approved_by, origin, idempotency_key) "
        f"values ('{clinic}', '{master}', '{subordinate}', '{candidate}', '{w.acct}', 'test', 'm-{_u()}')"
    )


async def test_a_merge_cannot_point_at_another_clinics_candidate(db):
    w = await world(db)
    assert await fails(db, merge(w, w.b, w.patient_b, w.patient_b2, w.candidate_a)) == "23503"  # B's merge, A's review row
    assert await fails(db, merge(w, w.b, w.patient_b, w.patient_b2, w.candidate_b)) is None  # control: B's own review row


def outbox(w, clinic, job):
    return (
        "insert into ops.outbox (clinic_id, origin, event_type, payload, dispatched_at, job_id, idempotency_key) "
        f"values ('{clinic}', 'test', 'demo.event', '{{}}'::jsonb, now(), '{job}', 'o-{_u()}')"
    )


async def test_an_outbox_event_cannot_point_at_another_clinics_job(db):
    w = await world(db)
    assert await fails(db, outbox(w, w.b, w.job_a)) == "23503"  # B's event, A's job
    assert await fails(db, outbox(w, w.a, w.job_a)) is None  # control: A's own job
