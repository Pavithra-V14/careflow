"""Migration 0083 against the real database: a bound, active patient lists open future slots of the visit types the OWNER listed, books one, lists their own bookings and cancels a FUTURE booking, only through definer functions. Nothing is
bookable until the owner lists the visit type; a slot held for someone else, a past slot, another clinic's slot, another patient's booking and the owner's per-patient cap are all refused. Inside ONE never-committed outer transaction, as the
restricted `careflow_app` role. Synthetic data only."""

import json
import os
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.care_gap_sql import sqlstate
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
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


async def pg(db):
    return (await db.get_raw_connection()).driver_connection


def claims_for(person):
    return json.dumps({"sub": str(person), "role": "authenticated", "aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 60}]})


async def as_app(db, person, sql, **params):
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims_for(person)})
            result = await db.execute(text(sql), params)
            value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def owner(db, sql, **params):
    result = await db.execute(text(sql), params)
    return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount


async def world(db, cap=2):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other", "fd", "pa1", "pa2", "pa3", "rec1", "rec2", "rec3", "prov", "loc", "vt", "vt2", "oprov", "oloc", "ovt")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'PB-A', 'test'), ($2, 'PB-B', 'test')", ids["clinic"], ids["other"])
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids["fd"])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", ids["fd"], ids["clinic"])
    for k in ("pa1", "pa2", "pa3"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids[k])
    for rec, acct in (("rec1", "pa1"), ("rec2", "pa2")):  # pa3 has no record
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, account_id, origin, idempotency_key) "
            "values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, $3, 'test', $4)",
            ids[rec], ids["clinic"], ids[acct], f"pb-{ids[rec]}",
        )
    for clinic, p, lo, vt in (("clinic", "prov", "loc", "vt"), ("other", "oprov", "oloc", "ovt")):
        await h.execute("insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ($1, $2, 'Provider', 'test', $3)", ids[p], ids[clinic], f"pv-{ids[p]}")
        await h.execute("insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values ($1, $2, 'Room', 'test', $3)", ids[lo], ids[clinic], f"lo-{ids[lo]}")
        await h.execute("insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values ($1, $2, 'Visit', 30, 'test', $3)", ids[vt], ids[clinic], f"vt-{ids[vt]}")
    await h.execute("insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values ($1, $2, 'Not offered', 30, 'test', $3)", ids["vt2"], ids["clinic"], f"vt-{ids['vt2']}")
    await h.execute("insert into sched.patient_bookable_visit_types (clinic_id, visit_type_id, max_future_bookings) values ($1, $2, $3)", ids["clinic"], ids["vt"], cap)
    return ids


_NEXT = [100]


async def slot(db, w, hours=None, vt="vt", prov="prov", clinic="clinic", loc="loc", status="open"):
    """A 30-minute slot. With no `hours` each call gets its own later hour (a provider cannot have two overlapping live slots)."""
    if hours is None:
        _NEXT[0] += 2
        hours = _NEXT[0]
    sid = uuid.uuid4()
    h = await pg(db)
    await h.execute(
        "insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) "
        "values ($1, $2, $3, $4, $5, now() + make_interval(hours => $6), now() + make_interval(hours => $6, mins => 30), $7, 'test', $8)",
        sid, w[clinic], w[prov], w[loc], w[vt], hours, status, f"sl-{sid}",
    )
    return sid


BOOK = "select o_booking_id, o_created from sched.patient_book_slot(:s, :k)"


async def test_only_an_offered_visit_type_is_listed_and_only_for_a_bound_patient_of_the_clinic(db):
    w = await world(db)
    good = await slot(db, w)
    await slot(db, w, vt="vt2")  # a visit type the owner did not list
    await slot(db, w, hours=-5)  # already started
    listed = await as_app(db, w["pa1"], "select slot_id from sched.patient_open_slots(:c, 50)", c=w["clinic"])
    assert listed == [(good,)]
    assert await as_app(db, w["pa3"], "select slot_id from sched.patient_open_slots(:c, 50)", c=w["clinic"]) == "42501"  # no record there
    assert await as_app(db, w["pa1"], "select slot_id from sched.patient_open_slots(:c, 50)", c=w["other"]) == "42501"
    assert await as_app(db, w["fd"], "select slot_id from sched.patient_open_slots(:c, 50)", c=w["clinic"]) == "42501"
    assert await as_app(db, w["pa1"], "select slot_id from sched.patient_open_slots(:c, 0)", c=w["clinic"]) == "42501"


async def test_a_patient_books_once_replays_and_cannot_book_what_is_not_offered_or_elsewhere_or_past(db):
    w = await world(db)
    s1 = await slot(db, w)
    first = await as_app(db, w["pa1"], BOOK, s=s1, k="b1")
    assert isinstance(first, list) and first[0][1] is True, first
    assert await as_app(db, w["pa1"], BOOK, s=s1, k="b1") == [(first[0][0], False)]  # a replay answers the same booking
    assert (await owner(db, "select status from sched.slots where id = :s", s=s1)) == [("booked",)]
    assert await as_app(db, w["pa2"], BOOK, s=s1, k="b2") == "55006"  # taken
    assert await as_app(db, w["pa1"], BOOK, s=await slot(db, w), k="b1") == "23505"  # the key was used for another slot
    assert await as_app(db, w["pa1"], BOOK, s=await slot(db, w, vt="vt2"), k="b3") == "42501"  # not offered
    assert await as_app(db, w["pa1"], BOOK, s=await slot(db, w, vt="ovt", prov="oprov", clinic="other", loc="oloc"), k="b4") == "42501"  # another clinic
    assert await as_app(db, w["pa1"], BOOK, s=await slot(db, w, hours=-2), k="b5") == "55006"  # started
    assert await as_app(db, w["pa3"], BOOK, s=await slot(db, w), k="b6") == "42501"  # no record
    assert await as_app(db, w["pa1"], BOOK, s=uuid.uuid4(), k="b7") == "42501"  # no such slot: the same answer


async def test_the_owners_cap_stops_a_further_future_booking_and_a_cancel_gives_it_back(db):
    w = await world(db, cap=1)
    s1, s2 = await slot(db, w, 24), await slot(db, w, 48)
    booked = await as_app(db, w["pa1"], BOOK, s=s1, k="c1")
    assert isinstance(booked, list), booked
    assert await as_app(db, w["pa1"], BOOK, s=s2, k="c2") == "54000"
    assert await as_app(db, w["pa1"], "select o_changed from sched.patient_cancel_booking(:b, 'x1')", b=booked[0][0]) == [(True,)]
    assert isinstance(await as_app(db, w["pa1"], BOOK, s=s2, k="c3"), list)


async def test_a_held_slot_is_bookable_only_by_the_patient_it_is_held_for_and_an_expired_hold_is_settled(db):
    w = await world(db)
    held = await slot(db, w, status="held")
    await owner(db, "insert into sched.holds (clinic_id, slot_id, patient_id, expires_at, origin, idempotency_key) values (:c, :s, :p, clock_timestamp() + interval '1 hour', 'test', :k)", c=w["clinic"], s=held, p=w["rec2"], k=f"h-{held}")
    assert await as_app(db, w["pa1"], BOOK, s=held, k="h1") == "55006"  # held for another patient
    got = await as_app(db, w["pa2"], BOOK, s=held, k="h2")
    assert isinstance(got, list)
    assert (await owner(db, "select status from sched.holds where slot_id = :s", s=held)) == [("converted",)]
    # a hold for nobody is not bookable by a patient
    nobody = await slot(db, w, status="held")
    await owner(db, "insert into sched.holds (clinic_id, slot_id, patient_id, expires_at, origin, idempotency_key) values (:c, :s, null, clock_timestamp() + interval '1 hour', 'test', :k)", c=w["clinic"], s=nobody, k=f"h-{nobody}")
    assert await as_app(db, w["pa1"], BOOK, s=nobody, k="h3") == "55006"


async def test_a_patient_sees_only_their_own_bookings_and_cancels_only_a_future_own_one(db):
    w = await world(db)
    s1 = await slot(db, w, 24)
    booked = await as_app(db, w["pa1"], BOOK, s=s1, k="m1")
    assert isinstance(booked, list), booked
    bid = booked[0][0]
    mine = await as_app(db, w["pa1"], "select booking_id, status, cancellable from sched.patient_bookings()")
    assert mine == [(bid, "confirmed", True)]
    assert await as_app(db, w["pa2"], "select booking_id from sched.patient_bookings()") == []
    assert await as_app(db, w["pa2"], "select o_changed from sched.patient_cancel_booking(:b, 'z')", b=bid) == "42501"  # not theirs: the same as missing
    assert await as_app(db, w["pa1"], "select o_changed from sched.patient_cancel_booking(:b, 'z')", b=uuid.uuid4()) == "42501"
    assert await as_app(db, w["pa1"], "select o_changed from sched.patient_cancel_booking(:b, 'z1')", b=bid) == [(True,)]
    assert await as_app(db, w["pa1"], "select o_changed from sched.patient_cancel_booking(:b, 'z1')", b=bid) == [(False,)]  # a replay
    assert await as_app(db, w["pa1"], "select o_changed from sched.patient_cancel_booking(:b, 'other')", b=bid) == "55000"  # already cancelled by another request
    assert (await owner(db, "select status from sched.slots where id = :s", s=s1)) == [("open",)]
    # a booking whose slot has started cannot be cancelled
    past = await slot(db, w, hours=-3)
    await owner(db, "insert into sched.bookings (clinic_id, slot_id, patient_id, origin, idempotency_key) values (:c, :s, :p, 'test', 'past-1')", c=w["clinic"], s=past, p=w["rec1"])
    await owner(db, "update sched.slots set status = 'booked' where id = :s", s=past)
    pb = (await owner(db, "select id from sched.bookings where slot_id = :s", s=past))[0][0]
    assert await as_app(db, w["pa1"], "select o_changed from sched.patient_cancel_booking(:b, 'p1')", b=pb) == "55000"


async def test_the_patient_has_no_table_access_and_the_bookable_list_is_not_theirs(db):
    w = await world(db)
    await slot(db, w)
    assert await as_app(db, w["pa1"], "select count(*) from sched.slots where clinic_id = :c", c=w["clinic"]) == [(0,)]
    assert await as_app(db, w["pa1"], "select count(*) from sched.bookings where clinic_id = :c", c=w["clinic"]) == [(0,)]
    assert await as_app(db, w["pa1"], "select count(*) from sched.patient_bookable_visit_types where clinic_id = :c", c=w["clinic"]) == "42501"
    assert await as_app(db, w["pa1"], "insert into sched.patient_bookable_visit_types (clinic_id, visit_type_id, max_future_bookings) values (:c, :v, 5)", c=w["clinic"], v=w["vt2"]) == "42501"


async def test_an_expired_hold_is_settled_for_the_next_booker_and_a_replay_after_cancel_is_not_answered_as_booked(db):
    w = await world(db)
    held = await slot(db, w, status="held")
    await owner(db, "insert into sched.holds (clinic_id, slot_id, patient_id, expires_at, origin, idempotency_key) values (:c, :s, :p, clock_timestamp() + interval '1 hour', 'test', :k)", c=w["clinic"], s=held, p=w["rec2"], k=f"x-{held}")
    await owner(db, "update sched.holds set expires_at = clock_timestamp() - interval '1 second' where slot_id = :s", s=held)  # the insert trigger forbids a past expiry, so it lapses afterwards
    got = await as_app(db, w["pa1"], BOOK, s=held, k="e1")
    assert isinstance(got, list), got
    assert (await owner(db, "select status from sched.holds where slot_id = :s", s=held)) == [("expired",)]
    assert await as_app(db, w["pa1"], "select o_changed from sched.patient_cancel_booking(:b, 'e2')", b=got[0][0]) == [(True,)]
    assert await as_app(db, w["pa1"], BOOK, s=held, k="e1") == "23505"  # the same key after a cancel: not "booked"


async def test_an_inactive_visit_type_is_not_bookable_and_the_cap_counts_only_future_confirmed_bookings_of_the_type(db):
    w = await world(db, cap=1)
    s1 = await slot(db, w)
    past = await slot(db, w, hours=-6)
    await owner(db, "insert into sched.bookings (clinic_id, slot_id, patient_id, origin, idempotency_key) values (:c, :s, :p, 'test', 'old-1')", c=w["clinic"], s=past, p=w["rec1"])
    assert isinstance(await as_app(db, w["pa1"], BOOK, s=s1, k="i1"), list)  # a past booking does not count against the cap
    await owner(db, "update sched.visit_types set active = false where id = :v", v=w["vt"])
    assert await as_app(db, w["pa2"], BOOK, s=await slot(db, w), k="i2") == "42501"
