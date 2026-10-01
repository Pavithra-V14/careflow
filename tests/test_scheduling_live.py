"""SlotManager against the real hosted project (careflow/scheduling.py; BUILD-SPEC.md §17.1, §17.5, §12.1;
CLAUDE.md rules 4, 5, 8).

Every call goes through the real manager as the restricted `careflow_app` role with verified claims set
per transaction, over SQLAlchemy Core async. All tests share one connection and one never-committed
outer transaction, each in its own savepoint; migration 0004 is executed inside that outer transaction
if it is not applied yet, so the file is verified without being applied. Nothing is ever committed.

Priority levels in these tests are synthetic (`tier_a`, `tier_b`, `tier_c`, origin 'test'). They carry no
clinical meaning; the real tiers are clinician-authored data and appear nowhere in code or tests.
"""

import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import scheduling as sch
from careflow.db import set_request_claims
from careflow.scheduling import SlotManager

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
SQL_0004 = (ROOT / "db" / "migrations" / "0004_scheduling_operations.sql").read_text(encoding="utf-8")
SQL_0005 = (ROOT / "db" / "migrations" / "0005_audit_doctor_signoff_security_fix.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _key() -> str:
    return f"k-{_u()}"


@pytest.fixture(scope="module")
def mgr():
    return SlotManager()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(
        make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0}
    )
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await conn.exec_driver_sql("grant careflow_app to current_user with set true")
        if (await conn.execute(text("select to_regclass('sched.holds') is null"))).scalar_one():
            await raw(conn, SQL_0004)
        # 0005 narrows the app role's UPDATE on priority_levels; if it is not applied yet, apply it here too.
        still_open = await conn.execute(
            text("select has_column_privilege('careflow_app', 'sched.priority_levels', 'approved_by', 'update')")
        )
        if still_open.scalar_one():
            await raw(conn, SQL_0005)
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


async def act(conn, uid, fn, *args, **kwargs):
    """Call `fn(conn, ...)` as a request would: app role, claims set, one transaction (a savepoint here).
    On error the savepoint rollback also restores the migrator role."""
    async with conn.begin_nested():
        await conn.exec_driver_sql("set local role careflow_app")
        # Always set the claims, even for "no identity" (empty): they are transaction-local, and this shared
        # outer transaction would otherwise carry the previous caller's identity into the next call.
        if uid is not None:
            await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        else:
            await conn.execute(text("select set_config('request.jwt.claims', '', true)"))
        result = await fn(conn, *args, **kwargs)
        await conn.exec_driver_sql("reset role")
        return result


async def q(conn, sql, **params):
    return (await conn.execute(text(sql), params)).all()


async def one(conn, sql, **params):
    return (await q(conn, sql, **params))[0][0]


class World:
    pass


async def world(conn) -> World:
    """Clinic A (front desk, nurse, doctor, admin; five patients; one visit type; four slots on one
    provider) and clinic B (front desk, one patient). Seeded as the migrator in few round trips."""
    w = World()
    w.a, w.b = _u(), _u()
    w.fd, w.nurse, w.doctor, w.admin, w.fd_b = (_u() for _ in range(5))
    roles = [(w.fd, w.a, "front_desk"), (w.nurse, w.a, "nurse"), (w.doctor, w.a, "doctor"), (w.admin, w.a, "admin"), (w.fd_b, w.b, "front_desk")]
    w.patients = [_u() for _ in range(5)]
    w.patient_b = _u()
    w.prov, w.loc, w.vt, w.vt_b = _u(), _u(), _u(), _u()
    w.slots = [_u() for _ in range(4)]
    w.slot_b = _u()
    pat = lambda pid, c: f"('{pid}', '{c}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'pat-{pid}')"
    slot = lambda sid, c, p, vt, hour: (
        f"('{sid}', '{c}', '{p}', '{w.loc}', '{vt}', '2032-05-05 {hour}:00+00', '2032-05-05 {hour}:30+00', 'test', 'slot-{sid}')"
    )
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'Clinic A', 'test'), ('{w.b}', 'Clinic B', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values "
        + ", ".join(f"('{r[0]}', 'staff', 'active', 'test')" for r in roles)
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{r[0]}', '{r[1]}', '{r[2]}', 'test')" for r in roles)
        + ";\ninsert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values "
        + ", ".join([pat(p, w.a) for p in w.patients] + [pat(w.patient_b, w.b)])
        + f";\ninsert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ('{w.prov}', '{w.a}', 'Dr Test', 'test', 'prov-{w.prov}');\n"
        f"insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values ('{w.loc}', '{w.a}', 'Room', 'test', 'loc-{w.loc}');\n"
        f"insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values "
        f"('{w.vt}', '{w.a}', 'Review', 30, 'test', 'vt-{w.vt}'), ('{w.vt_b}', '{w.b}', 'Review', 30, 'test', 'vtb-{w.vt_b}');\n"
        f"insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ('{w.slot_b}', '{w.b}', 'Dr B', 'test', 'provb-{w.slot_b}');\n"
        "insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, origin, idempotency_key) values "
        + ", ".join(slot(s, w.a, w.prov, w.vt, f"{9 + i:02d}") for i, s in enumerate(w.slots))
        + ";",
    )
    return w


async def add_tiers(conn, w):
    """Three synthetic levels, authored through the real policy (a doctor), approved by that doctor."""
    for code, rank in (("tier_a", 1), ("tier_b", 2), ("tier_c", 3)):
        await act(
            conn, w.doctor, _insert_level, w.a, code, rank, w.doctor,
        )


async def _insert_level(conn, clinic, code, rank, approver):
    await conn.execute(
        text(
            "insert into sched.priority_levels (clinic_id, code, label, rank, approved_by, origin, idempotency_key) "
            "values (:c, :code, :code, :r, :ap, 'test', :k)"
        ),
        {"c": clinic, "code": code, "r": rank, "ap": approver, "k": _key()},
    )


async def age_hold(conn, hold_id):
    """Make a hold expire in the past (as the migrator): created an hour ago, expired a second ago."""
    await conn.execute(
        text("update sched.holds set created_at = created_at - interval '1 hour', expires_at = clock_timestamp() - interval '1 second' where id = :h"),
        {"h": hold_id},
    )


# ---- hold ------------------------------------------------------------------------------------------


async def test_hold_moves_an_open_slot_to_held_with_a_future_expiry(db, mgr):
    w = await world(db)
    r = await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    assert not r.replayed
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "held"
    (h,) = await q(db, "select status, expires_at - clock_timestamp() as left, origin from sched.holds where id = :h", h=r.hold_id)
    assert h.status == "active" and h.origin == "test"
    assert timedelta(minutes=14) < h.left <= timedelta(minutes=15)


async def test_a_second_hold_on_a_held_slot_fails_cleanly(db, mgr):
    w = await world(db)
    await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    with pytest.raises(sch.SlotUnavailable) as exc:
        await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    assert exc.value.status == "held"
    assert await one(db, "select count(*) from sched.holds where slot_id = :s", s=w.slots[0]) == 1


async def test_the_database_itself_refuses_two_active_holds_on_one_slot(db, mgr):
    w = await world(db)
    await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    with pytest.raises(DBAPIError, match="holds_one_active_per_slot"):
        async with db.begin_nested():
            await db.execute(
                text(
                    "insert into sched.holds (clinic_id, slot_id, expires_at, origin, idempotency_key) "
                    "values (:c, :s, clock_timestamp() + interval '5 minutes', 'test', :k)"
                ),
                {"c": w.a, "s": w.slots[0], "k": _key()},
            )


@pytest.mark.parametrize("status", ["booked", "cancelled"])
async def test_a_booked_or_cancelled_slot_cannot_be_held(db, mgr, status):
    w = await world(db)
    await db.execute(text("update sched.slots set status = :st where id = :s"), {"st": status, "s": w.slots[0]})
    with pytest.raises(sch.SlotUnavailable) as exc:
        await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    assert exc.value.status == status


async def test_hold_replay_and_conflict(db, mgr):
    w = await world(db)
    key = _key()
    first = await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=key)
    again = await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=key)
    assert (again.replayed, again.hold_id) == (True, first.hold_id)
    assert await one(db, "select count(*) from sched.holds") == 1
    with pytest.raises(sch.IdempotencyConflict):
        await act(db, w.fd, mgr.hold_slot, w.slots[1], 15, idempotency_key=key)  # same key, different slot
    with pytest.raises(sch.IdempotencyConflict):
        await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=key, patient_id=w.patients[0])


@pytest.mark.parametrize("bad", [0, -5, True, "15", 1.5, None])
async def test_hold_duration_must_be_a_positive_integer(db, mgr, bad):
    w = await world(db)
    with pytest.raises(ValueError):
        await act(db, w.fd, mgr.hold_slot, w.slots[0], bad, idempotency_key=_key())


@pytest.mark.parametrize("bad", ["", "  ", None])
async def test_every_write_requires_an_idempotency_key(db, mgr, bad):
    w = await world(db)
    for call in (
        lambda c: mgr.hold_slot(c, w.slots[0], 15, idempotency_key=bad),
        lambda c: mgr.book_appointment(c, w.slots[0], w.patients[0], bad),
        lambda c: mgr.cancel_appointment(c, _u(), idempotency_key=bad),
        lambda c: mgr.join_waitlist(c, w.patients[0], w.vt, "tier_a", idempotency_key=bad),
    ):
        with pytest.raises(sch.SchedulingError):
            await act(db, w.fd, lambda c, f=call: f(c))


async def test_an_expired_hold_no_longer_blocks_the_slot(db, mgr):
    """Soft filter (A): no sweep has run, yet an expired hold does not block a new hold."""
    w = await world(db)
    old = await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    await age_hold(db, old.hold_id)
    new = await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    assert new.hold_id != old.hold_id
    assert await one(db, "select status from sched.holds where id = :h", h=old.hold_id) == "expired"
    assert await one(db, "select status from sched.holds where id = :h", h=new.hold_id) == "active"
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "held"


async def test_the_sweep_releases_expired_holds_and_is_repeatable(db, mgr):
    w = await world(db)
    stale = await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    live = await act(db, w.fd, mgr.hold_slot, w.slots[1], 15, idempotency_key=_key())
    await age_hold(db, stale.hold_id)
    assert await act(db, w.fd, mgr.release_expired_holds) == 1
    assert await act(db, w.fd, mgr.release_expired_holds) == 0
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "open"
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[1]) == "held"
    assert await one(db, "select status from sched.holds where id = :h", h=live.hold_id) == "active"


# ---- book ------------------------------------------------------------------------------------------


async def test_booking_an_open_slot(db, mgr):
    w = await world(db)
    r = await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], _key())
    assert (r.status, r.replayed, r.patient_id) == ("confirmed", False, w.patients[0])
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "booked"
    (b,) = await q(db, "select clinic_id, origin, hold_id from sched.bookings where id = :b", b=r.booking_id)
    assert (b.clinic_id, b.origin, b.hold_id) == (w.a, "test", None)


async def test_booking_through_a_hold_converts_it(db, mgr):
    w = await world(db)
    hold = await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key(), patient_id=w.patients[0])
    r = await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], _key())
    assert await one(db, "select status from sched.holds where id = :h", h=hold.hold_id) == "converted"
    assert await one(db, "select hold_id from sched.bookings where id = :b", b=r.booking_id) == hold.hold_id
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "booked"


async def test_a_slot_held_for_one_patient_cannot_be_booked_for_another(db, mgr):
    w = await world(db)
    await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key(), patient_id=w.patients[0])
    with pytest.raises(sch.SlotUnavailable):
        await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[1], _key())
    assert await one(db, "select count(*) from sched.bookings") == 0


async def test_an_expired_hold_for_someone_else_no_longer_blocks_booking(db, mgr):
    w = await world(db)
    old = await act(db, w.fd, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key(), patient_id=w.patients[0])
    await age_hold(db, old.hold_id)
    r = await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[1], _key())
    assert r.status == "confirmed"
    assert await one(db, "select status from sched.holds where id = :h", h=old.hold_id) == "expired"


async def test_booking_replay_returns_the_original_and_an_altered_request_conflicts(db, mgr):
    w = await world(db)
    key = _key()
    first = await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], key)
    again = await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], key)
    assert (again.replayed, again.booking_id, again.status) == (True, first.booking_id, "confirmed")
    assert await one(db, "select count(*) from sched.bookings") == 1
    with pytest.raises(sch.IdempotencyConflict):
        await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[1], key)  # altered patient
    with pytest.raises(sch.IdempotencyConflict):
        await act(db, w.fd, mgr.book_appointment, w.slots[1], w.patients[0], key)  # altered slot
    assert await one(db, "select count(*) from sched.bookings") == 1


async def test_a_booked_slot_cannot_be_booked_again_by_the_engine_or_the_database(db, mgr):
    w = await world(db)
    await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], _key())
    with pytest.raises(sch.SlotUnavailable) as exc:
        await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[1], _key())
    assert exc.value.status == "booked"
    with pytest.raises(DBAPIError, match="bookings_one_confirmed_per_slot"):
        async with db.begin_nested():
            await db.execute(
                text("insert into sched.bookings (clinic_id, slot_id, patient_id, origin, idempotency_key) values (:c, :s, :p, 'test', :k)"),
                {"c": w.a, "s": w.slots[0], "p": w.patients[1], "k": _key()},
            )


async def test_a_cancelled_slot_cannot_be_booked(db, mgr):
    w = await world(db)
    await db.execute(text("update sched.slots set status = 'cancelled' where id = :s"), {"s": w.slots[0]})
    with pytest.raises(sch.SlotUnavailable):
        await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], _key())


async def test_a_patient_from_another_clinic_cannot_be_booked(db, mgr):
    w = await world(db)
    with pytest.raises(sch.UnknownReference):
        await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patient_b, _key())
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "open"


# ---- cancel and waitlist reallocation --------------------------------------------------------------


async def test_cancel_frees_the_slot_and_is_idempotent(db, mgr):
    w = await world(db)
    booking = await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], _key())
    key = _key()
    r = await act(db, w.fd, mgr.cancel_appointment, booking.booking_id, idempotency_key=key)
    assert (r.slot_id, r.replayed) == (w.slots[0], False)
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "open"
    assert await one(db, "select status from sched.bookings where id = :b", b=booking.booking_id) == "cancelled"
    assert (await act(db, w.fd, mgr.cancel_appointment, booking.booking_id, idempotency_key=key)).replayed
    with pytest.raises(sch.InvalidTransition):
        await act(db, w.fd, mgr.cancel_appointment, booking.booking_id, idempotency_key=_key())
    again = await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[1], _key())  # the freed slot is bookable
    assert again.status == "confirmed"


async def test_cancelling_an_unknown_booking_fails(db, mgr):
    w = await world(db)
    with pytest.raises(sch.BookingNotFound):
        await act(db, w.fd, mgr.cancel_appointment, _u(), idempotency_key=_key())


async def test_clinical_priority_overrides_waiting_time_when_a_slot_frees_up(db, mgr):
    w = await world(db)
    await add_tiers(db, w)
    p_c_oldest, p_b, p_a_newer, p_a_older = w.patients[1:5]
    entries = {}
    for p, tier in ((p_c_oldest, "tier_c"), (p_b, "tier_b"), (p_a_newer, "tier_a"), (p_a_older, "tier_a")):
        entries[p] = await act(db, w.fd, mgr.join_waitlist, p, w.vt, tier, idempotency_key=_key())
    # Chronology, set explicitly: the lowest-priority patient has waited longest.
    for p, hours in ((p_c_oldest, 48), (p_b, 24), (p_a_older, 5), (p_a_newer, 1)):
        await db.execute(text("update sched.waitlist set joined_at = now() - make_interval(hours => :h) where id = :i"),
                         {"h": hours, "i": entries[p].waitlist_id})
    booking = await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], _key())
    result = await act(db, w.fd, mgr.cancel_appointment, booking.booking_id, idempotency_key=_key())
    assert [c.patient_id for c in result.candidates] == [p_a_older, p_a_newer, p_b, p_c_oldest]
    assert [c.rank for c in result.candidates] == [1, 1, 2, 3]
    assert [c.priority_code for c in result.candidates] == ["tier_a", "tier_a", "tier_b", "tier_c"]
    # Within one priority level the longer wait goes first.
    assert result.candidates[0].joined_at < result.candidates[1].joined_at
    # Ranking only: nobody was held, booked or changed by the cancellation.
    assert await one(db, "select count(*) from sched.holds") == 0
    assert await one(db, "select count(*) from sched.bookings where status = 'confirmed'") == 0
    assert await one(db, "select count(*) from sched.waitlist where status = 'waiting'") == 4
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "open"


async def test_waitlist_ranks_only_the_freed_slots_visit_type_and_clinic(db, mgr):
    w = await world(db)
    await add_tiers(db, w)
    await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "tier_c", idempotency_key=_key())
    assert [c.patient_id for c in await act(db, w.fd, mgr.rank_waitlist, w.a, w.vt)] == [w.patients[1]]
    assert await act(db, w.fd, mgr.rank_waitlist, w.a, w.vt_b) == ()
    assert await act(db, w.fd_b, mgr.rank_waitlist, w.a, w.vt) == ()  # clinic B cannot see clinic A's queue


async def test_booking_a_waiting_patient_fulfils_their_entry_and_drops_them_from_the_queue(db, mgr):
    w = await world(db)
    await add_tiers(db, w)
    entry = await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "tier_a", idempotency_key=_key())
    await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[1], _key())
    assert await one(db, "select status from sched.waitlist where id = :i", i=entry.waitlist_id) == "fulfilled"
    assert await act(db, w.fd, mgr.rank_waitlist, w.a, w.vt) == ()


async def test_a_retired_priority_level_never_drops_a_waiting_patient(db, mgr):
    w = await world(db)
    await add_tiers(db, w)
    await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "tier_b", idempotency_key=_key())
    await db.execute(text("update sched.priority_levels set active = false where clinic_id = :c and code = 'tier_b'"), {"c": w.a})
    assert [c.patient_id for c in await act(db, w.fd, mgr.rank_waitlist, w.a, w.vt)] == [w.patients[1]]


async def test_waitlist_requires_a_defined_priority_and_has_no_default(db, mgr):
    w = await world(db)
    with pytest.raises(sch.UnknownReference):  # no levels have been authored yet
        await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "urgent", idempotency_key=_key())
    await add_tiers(db, w)
    with pytest.raises(sch.UnknownReference):
        await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "made_up", idempotency_key=_key())
    with pytest.raises(sch.SchedulingError):
        await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "", idempotency_key=_key())
    assert await one(db, "select count(*) from sched.waitlist") == 0


async def test_waitlist_idempotency_and_single_waiting_entry(db, mgr):
    w = await world(db)
    await add_tiers(db, w)
    key = _key()
    first = await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "tier_a", idempotency_key=key)
    again = await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "tier_a", idempotency_key=key)
    assert (again.replayed, again.waitlist_id) == (True, first.waitlist_id)
    with pytest.raises(sch.IdempotencyConflict):
        await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "tier_b", idempotency_key=key)
    with pytest.raises(sch.InvalidTransition):
        await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "tier_b", idempotency_key=_key())


# ---- who may do what (RLS) -------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["nurse", "doctor", "admin", "fd_b", None])
async def test_only_front_desk_of_the_clinic_can_hold_book_or_cancel(db, mgr, who):
    w = await world(db)
    uid = getattr(w, who) if who else None
    denied = (sch.NotPermitted, sch.SlotNotFound, sch.BookingNotFound)
    with pytest.raises(denied):
        await act(db, uid, mgr.hold_slot, w.slots[0], 15, idempotency_key=_key())
    with pytest.raises(denied):
        await act(db, uid, mgr.book_appointment, w.slots[0], w.patients[0], _key())
    booking = await act(db, w.fd, mgr.book_appointment, w.slots[1], w.patients[0], _key())
    with pytest.raises(denied):
        await act(db, uid, mgr.cancel_appointment, booking.booking_id, idempotency_key=_key())
    assert await one(db, "select status from sched.slots where id = :s", s=w.slots[0]) == "open"
    assert await one(db, "select status from sched.bookings where id = :b", b=booking.booking_id) == "confirmed"


async def test_priority_levels_are_authored_by_a_doctor_only(db, mgr):
    w = await world(db)
    await act(db, w.doctor, _insert_level, w.a, "tier_a", 1, w.doctor)
    for who in (w.fd, w.nurse, w.admin, w.fd_b):
        with pytest.raises(DBAPIError, match="row-level security"):
            await act(db, who, _insert_level, w.a, "tier_x", 9, w.doctor)
    seen = [r.code for r in await act(db, w.nurse, lambda c: _select_codes(c))]
    assert seen == ["tier_a"]


async def _select_codes(conn):
    return (await conn.execute(text("select code from sched.priority_levels"))).all()


async def test_a_priority_level_needs_an_approver_and_a_unique_rank(db, mgr):
    w = await world(db)
    await act(db, w.doctor, _insert_level, w.a, "tier_a", 1, w.doctor)
    with pytest.raises(DBAPIError, match="priority_levels_clinic_id_rank_key"):
        async with db.begin_nested():
            await _insert_level(db, w.a, "tier_dup_rank", 1, w.doctor)
    with pytest.raises(DBAPIError, match="approved_by"):
        async with db.begin_nested():
            await _insert_level(db, w.a, "tier_nobody", 4, None)
    with pytest.raises(DBAPIError, match="check"):
        async with db.begin_nested():
            await _insert_level(db, w.a, "tier_zero", 0, w.doctor)


async def test_nurse_and_doctor_read_but_admin_does_not_see_bookings_or_waitlist(db, mgr):
    w = await world(db)
    await add_tiers(db, w)
    await act(db, w.fd, mgr.book_appointment, w.slots[0], w.patients[0], _key())
    await act(db, w.fd, mgr.join_waitlist, w.patients[1], w.vt, "tier_a", idempotency_key=_key())
    count = lambda t: (lambda c: _count(c, t))
    for who in (w.nurse, w.doctor):
        assert await act(db, who, count("sched.bookings")) == 1
        assert await act(db, who, count("sched.waitlist")) == 1
    for who in (w.admin, w.fd_b, None):
        assert await act(db, who, count("sched.bookings")) == 0
        assert await act(db, who, count("sched.waitlist")) == 0


async def _count(conn, table):
    return (await conn.execute(text(f"select count(*) from {table}"))).scalar_one()


# ---- table constraints -----------------------------------------------------------------------------


async def test_a_hold_must_expire_in_the_future(db, mgr):
    w = await world(db)
    for expiry in ("clock_timestamp() - interval '1 minute'", "now()"):
        with pytest.raises(DBAPIError, match="check|future"):
            async with db.begin_nested():
                await db.execute(
                    text(f"insert into sched.holds (clinic_id, slot_id, expires_at, origin, idempotency_key) values (:c, :s, {expiry}, 'test', :k)"),
                    {"c": w.a, "s": w.slots[0], "k": _key()},
                )


async def test_origin_has_no_default_and_every_key_is_required_on_the_new_tables(db):
    for table in ("holds", "bookings", "waitlist", "priority_levels"):
        cols = {r.column_name: r for r in await q(
            db, "select column_name, column_default, is_nullable from information_schema.columns where table_schema = 'sched' and table_name = :t", t=table)}
        assert cols["origin"].column_default is None and cols["origin"].is_nullable == "NO", table
        assert cols["idempotency_key"].is_nullable == "NO", table


async def test_forced_rls_everywhere_and_no_delete_for_the_app_role(db):
    rows = await q(
        db,
        "select c.relname, c.relrowsecurity, c.relforcerowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'sched' and c.relkind = 'r'",
    )
    assert {r.relname for r in rows} >= {"holds", "bookings", "waitlist", "priority_levels", "slots"}
    assert all(r.relrowsecurity and r.relforcerowsecurity for r in rows)
    for table in ("holds", "bookings", "waitlist", "priority_levels"):
        assert not await one(db, "select has_table_privilege('careflow_app', :t, 'delete')", t=f"sched.{table}")
        assert await one(db, "select has_table_privilege('careflow_app', :t, 'insert')", t=f"sched.{table}")
        for role in ("anon", "authenticated"):
            assert not await one(db, "select has_table_privilege(:r, :t, 'select')", r=role, t=f"sched.{table}")


# ---- 0005: the approver on a priority level is the signed-in doctor, and cannot be rewritten --------


async def test_a_doctor_can_only_sign_off_as_themselves(db, mgr):
    w = await world(db)
    other_doctor = _u()
    await raw(
        db,
        f"insert into iam.accounts (id, account_type, status, origin) values ('{other_doctor}', 'staff', 'active', 'test'); "
        f"insert into iam.staff_roles (account_id, clinic_id, role, origin) values ('{other_doctor}', '{w.a}', 'doctor', 'test');",
    )
    await act(db, w.doctor, _insert_level, w.a, "tier_a", 1, w.doctor)  # own sign-off: allowed
    for forged in (other_doctor, w.admin, w.fd):
        with pytest.raises(DBAPIError, match="row-level security"):
            await act(db, w.doctor, _insert_level, w.a, "tier_forged", 7, forged)
    assert [r.code for r in await q(db, "select code from sched.priority_levels")] == ["tier_a"]


async def test_the_approver_and_the_ranking_cannot_be_changed_by_the_app_role(db, mgr):
    w = await world(db)
    await act(db, w.doctor, _insert_level, w.a, "tier_a", 1, w.doctor)
    for column, value in (("approved_by", w.admin), ("approved_at", None), ("rank", 5), ("code", "tier_z"), ("clinic_id", w.b)):
        with pytest.raises(DBAPIError, match="permission denied"):
            await act(db, w.doctor, _set_level_column, column, value)
    row = (await q(db, "select approved_by, rank, code from sched.priority_levels"))[0]
    assert (row.approved_by, row.rank, row.code) == (w.doctor, 1, "tier_a")


async def _set_level_column(conn, column, value):
    if column == "approved_at":
        await conn.execute(text("update sched.priority_levels set approved_at = now()"))
    else:
        await conn.execute(text(f"update sched.priority_levels set {column} = :v"), {"v": value})


async def test_a_doctor_can_still_relabel_or_retire_a_level(db, mgr):
    w = await world(db)
    await act(db, w.doctor, _insert_level, w.a, "tier_a", 1, w.doctor)
    await act(db, w.doctor, _relabel)
    row = (await q(db, "select label, active, approved_by from sched.priority_levels"))[0]
    assert (row.label, row.active, row.approved_by) == ("renamed", False, w.doctor)
    for who in (w.fd, w.nurse, w.admin):  # still doctor-only
        assert await act(db, who, _relabel) == 0


async def _relabel(conn):
    result = await conn.execute(text("update sched.priority_levels set label = 'renamed', active = false"))
    return result.rowcount
