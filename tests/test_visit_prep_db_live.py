"""Migration 0089 against the real database: a patient reads and marks the preparation checklist of their OWN confirmed upcoming visit only (everyone else, a cancelled or started visit and an unknown visit are one 42501);
the newest mark per item is the state; a replay is idempotent and a reused key for a different mark is refused; a nurse or doctor of the clinic reads the marks read-only with a verified second factor and nobody
else can; the table cannot be reached directly and is append-only. The CHECKLIST ITEMS are application content (a setting), so the database holds closed item ids only. Synthetic data only."""

import json
import time
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from careflow.care_gap_sql import sqlstate
from tests.test_patient_booking_db_live import (  # noqa: F401  (the harness: one connection, the helpers and the seeded world are reused, not copied)
    MIGRATOR,
    _conn,
    pg,
    slot,
    world,
)

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]

PREP = "select o_visit_type, o_item, o_done from sched.patient_visit_prep(:b)"
MARK = "select o_mark_id, o_created from sched.patient_mark_prep(:b, :i, :d, :k)"
STAFF = "select o_visit_type, o_item, o_done from sched.staff_visit_prep(:b)"


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):  # noqa: F811 - the same name as the imported connection fixture is deliberate: this module wraps it per test
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


def claims(person, *, aal2=True):
    base = {"sub": str(person), "role": "authenticated"}
    if aal2:
        base |= {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 60}]}
    return json.dumps(base)


async def as_app(db, person, sql, *, aal2=True, **params):
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims(person, aal2=aal2)})
            result = await db.execute(text(sql), params)
            value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def owner(db, sql, **params):
    try:
        async with db.begin_nested():
            result = await db.execute(text(sql), params)
            return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
    except DBAPIError as exc:
        return sqlstate(exc)


async def prep_world(db):
    w = await world(db)
    h = await pg(db)
    for k, clinic, role in (("nurse", "clinic", "nurse"), ("nurse_b", "other", "nurse")):
        w[k] = uuid.uuid4()
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", w[k])
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", w[k], w[clinic], role)
    return w


async def booked(db, w, patient="rec1", hours=None, cancelled=False):
    sid = await slot(db, w, hours=hours, status="booked")
    bid = uuid.uuid4()
    h = await pg(db)
    if cancelled:
        await h.execute(
            "insert into sched.bookings (id, clinic_id, slot_id, patient_id, origin, idempotency_key, status, cancelled_at, cancel_idempotency_key) values ($1, $2, $3, $4, 'test', $5, 'cancelled', now(), 'c')",
            bid, w["clinic"], sid, w[patient], f"bk-{bid}",
        )
    else:
        await h.execute("insert into sched.bookings (id, clinic_id, slot_id, patient_id, origin, idempotency_key) values ($1, $2, $3, $4, 'test', $5)", bid, w["clinic"], sid, w[patient], f"bk-{bid}")
    return bid


async def test_a_patient_reads_only_their_own_confirmed_visit_and_every_other_case_is_one_refusal(db):
    w = await prep_world(db)
    mine = await booked(db, w)
    assert await as_app(db, w["pa1"], PREP, b=mine) == [("Visit", None, None)]  # the visit exists and nothing is marked yet
    for who in ("pa2", "pa3", "fd", "nurse", "nurse_b"):
        assert await as_app(db, w[who], PREP, b=mine) == "42501", who
    assert await as_app(db, w["pa1"], PREP, b=uuid.uuid4()) == "42501"
    assert await as_app(db, w["pa1"], PREP, b=await booked(db, w, cancelled=True)) == "42501"  # cancelled
    assert await as_app(db, uuid.uuid4(), PREP, b=mine) == "42501"  # no account at all
    assert await as_app(db, w["pa1"], PREP, b=None) == "42501"


async def test_marks_are_idempotent_the_newest_wins_and_a_reused_key_is_refused(db):
    w = await prep_world(db)
    b = await booked(db, w)
    first = await as_app(db, w["pa1"], MARK, b=b, i="bring_item", d=True, k="k1")
    assert first[0][1] is True
    assert (await as_app(db, w["pa1"], MARK, b=b, i="bring_item", d=True, k="k1"))[0] == (first[0][0], False)  # a replay
    assert await as_app(db, w["pa1"], MARK, b=b, i="bring_item", d=False, k="k1") == "23505"  # the key for a different mark
    assert await as_app(db, w["pa1"], MARK, b=b, i="other_item", d=True, k="k1") == "23505"
    assert (await as_app(db, w["pa1"], MARK, b=b, i="bring_item", d=False, k="k2"))[0][1] is True  # changed their mind
    await as_app(db, w["pa1"], MARK, b=b, i="form_item", d=True, k="k3")
    assert await as_app(db, w["pa1"], PREP, b=b) == [("Visit", "bring_item", False), ("Visit", "form_item", True)]  # the newest mark per item
    assert await owner(db, "select count(*), bool_and(idempotency_key like 'visit-prep:%') from ops.visit_prep_marks where visit_id = :b", b=b) == [(3, True)]
    audit = await owner(db, "select count(*), bool_and(idempotency_key like 'sys:visit-prep:%'), bool_and(actor_role = 'patient') from audit.log where action = 'sched.visit_prep.marked' and resource_id = :b", b=str(b))
    assert audit == [(3, True, True)]  # one audit row per created mark, a server-made key, the item and the flag only


async def test_a_mark_is_refused_for_someone_elses_visit_a_started_visit_a_cancelled_one_and_a_bad_item_or_key(db):
    w = await prep_world(db)
    mine = await booked(db, w)
    assert await as_app(db, w["pa2"], MARK, b=mine, i="bring_item", d=True, k="x") == "42501"  # another patient
    for who in ("fd", "nurse"):
        assert await as_app(db, w[who], MARK, b=mine, i="bring_item", d=True, k="x") == "42501", who  # staff do not mark for a patient
    assert await as_app(db, w["pa1"], MARK, b=await booked(db, w, hours=-5), i="bring_item", d=True, k="x") == "42501"  # the visit has started
    assert await as_app(db, w["pa1"], MARK, b=await booked(db, w, cancelled=True), i="bring_item", d=True, k="x") == "42501"
    for item in ("Bad Item", "1starts", "", "x" * 61, "has-dash"):
        assert await as_app(db, w["pa1"], MARK, b=mine, i=item, d=True, k="x") == "42501", item
    assert await as_app(db, w["pa1"], MARK, b=mine, i="bring_item", d=None, k="x") == "42501"
    for key in ("", "   ", "k" * 201):
        assert await as_app(db, w["pa1"], MARK, b=mine, i="bring_item", d=True, k=key) == "42501", key
    assert await owner(db, "select count(*) from ops.visit_prep_marks where visit_id = :b", b=mine) == [(0,)]  # nothing was written by any refusal


async def test_staff_read_the_marks_read_only_with_a_second_factor_and_only_nurse_or_doctor_of_the_clinic(db):
    w = await prep_world(db)
    b = await booked(db, w)
    await as_app(db, w["pa1"], MARK, b=b, i="bring_item", d=True, k="k1")
    assert await as_app(db, w["nurse"], STAFF, b=b) == [("Visit", "bring_item", True)]
    assert await as_app(db, w["nurse"], STAFF, b=b, aal2=False) == "42501"  # no verified second factor
    for who in ("fd", "nurse_b", "pa1", "pa2"):
        assert await as_app(db, w[who], STAFF, b=b) == "42501", who
    assert await as_app(db, w["nurse"], STAFF, b=uuid.uuid4()) == "42501"
    assert await as_app(db, w["nurse"], "select count(*) from ops.visit_prep_marks where clinic_id = :c", c=w["clinic"]) == "42501"  # no direct read, even for staff


async def test_the_table_has_no_direct_access_and_is_append_only_even_for_the_owner(db):
    w = await prep_world(db)
    b = await booked(db, w)
    await as_app(db, w["pa1"], MARK, b=b, i="bring_item", d=True, k="k1")
    for who in ("pa1", "nurse", "fd"):
        assert await as_app(db, w[who], "select count(*) from ops.visit_prep_marks where clinic_id = :c", c=w["clinic"]) == "42501", who
        assert await as_app(db, w[who], "insert into ops.visit_prep_marks (clinic_id, origin, visit_id, patient_id, item, done, marked_by, idempotency_key) values (:c, 'test', :b, :p, 'x', true, :u, 'direct')", c=w["clinic"], b=b, p=w["rec1"], u=w[who]) == "42501", who
    assert await owner(db, "update ops.visit_prep_marks set done = false where visit_id = :b", b=b) == "23001"
    assert await owner(db, "delete from ops.visit_prep_marks where visit_id = :b", b=b) == "23001"
    assert await owner(db, "truncate ops.visit_prep_marks") == "23001"
    flags = await owner(db, "select relrowsecurity, relforcerowsecurity from pg_class where oid = 'ops.visit_prep_marks'::regclass")
    assert flags == [(True, True)]
    assert await owner(db, "select count(*) from pg_policy where polrelid = 'ops.visit_prep_marks'::regclass") == [(0,)]
    for role in ("anon", "authenticated", "service_role"):
        if await owner(db, "select 1 from pg_roles where rolname = :r", r=role):
            for fn in ("sched.patient_visit_prep(uuid)", "sched.patient_mark_prep(uuid, text, boolean, text)", "sched.staff_visit_prep(uuid)"):
                assert await owner(db, "select has_function_privilege(:r, :f, 'execute')", r=role, f=fn) == [(False,)], (role, fn)
