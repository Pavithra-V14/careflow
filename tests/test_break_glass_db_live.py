"""Migration 0058 against the real database: break-glass access. A staff member of a clinic opens a time-limited grant for ONE patient with a reason code from the clinic's owner-kept list; while it is open the
holder (only) reads that patient's messages as ciphertext and every read is audited; an admin who is not the opener reviews it; nothing can create, extend or alter a grant except the three functions. Inside ONE
never-committed outer transaction, as the restricted `careflow_app` role.
"""

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
OPEN = "select grant_id, expires_at, created from iam.open_break_glass(:c, :p, :r, cast(:m as integer), :k)"
READ = "select status, message_id, body_key_version from msg.break_glass_messages(:g)"
REVIEW = "select iam.review_break_glass(:g, :o)"


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


def claims_for(person, aal2=True):
    c = {"sub": str(person), "role": "authenticated"}
    if aal2:
        c.update({"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 60}]})
    return json.dumps(c)


async def as_app(db, person, sql, aal2=True, **params):
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims_for(person, aal2)})
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


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other_clinic", "fd", "nurse", "doctor", "admin", "admin2", "outsider", "patient", "other_patient")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'BG-A', 'test'), ($2, 'BG-B', 'test')", ids["clinic"], ids["other_clinic"])
    for k in ("fd", "nurse", "doctor", "admin", "admin2", "outsider"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k, role in (("fd", "front_desk"), ("nurse", "nurse"), ("doctor", "doctor"), ("admin", "admin"), ("admin2", "admin")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", ids["outsider"], ids["other_clinic"])
    for k in ("patient", "other_patient"):
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, 'test', $3)",
            ids[k], ids["clinic"], f"bg-{ids[k]}",
        )
    await h.execute("insert into iam.break_glass_reasons (clinic_id, code) values ($1, 'demo_reason')", ids["clinic"])
    await h.execute("insert into iam.break_glass_settings (clinic_id, max_minutes, max_reopens) values ($1, 120, 50)", ids["clinic"])  # owner-managed limits (migration 0066)
    # threads and messages are written by the doctor under row-level security (the owner connection is subject to those policies too)
    thread = (await as_app(db, ids["doctor"], "insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) values (:c, :p, 'care', :u, 'test', :k) returning id", c=ids["clinic"], p=ids["patient"], u=ids["doctor"], k=f"th-{ids['patient']}"))[0][0]
    for k in ("m1", "m2"):
        assert await as_app(
            db, ids["doctor"],
            "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) values (gen_random_uuid(), :c, 'test', 'care', :t, 'staff', :u, decode(repeat('01', 32), 'hex'), 1, :k)",
            c=ids["clinic"], t=thread, u=ids["doctor"], k=f"{k}-{thread}",
        ) == 1
    return ids


async def open_grant(db, w, who="nurse", key=None, minutes=30, patient=None, reason="demo_reason"):
    return await as_app(db, w[who], OPEN, c=w["clinic"], p=patient or w["patient"], r=reason, m=minutes, k=key or f"k-{uuid.uuid4()}")


async def test_a_staff_member_opens_a_grant_reads_the_messages_and_every_step_is_audited(db):
    w = await world(db)
    opened = await open_grant(db, w, key="open-1")
    assert opened[0][2] is True
    grant = opened[0][0]
    assert await open_grant(db, w, key="open-1") == [(grant, opened[0][1], False)]  # a replay is the same grant
    assert await open_grant(db, w, key="open-1", patient=w["other_patient"]) == "42501"  # the same key for another patient is refused
    rows = await as_app(db, w["nurse"], READ, g=grant)
    assert [r[0] for r in rows] == ["ok", "ok"]
    await as_app(db, w["nurse"], READ, g=grant)  # a second read is its own audit row
    audits = (await db.execute(text("select action, reason, details from audit.log where details ->> 'grant_id' = :g order by id"), {"g": str(grant)})).all()
    assert [a.action for a in audits] == ["break_glass.open", "break_glass.read", "break_glass.read"]
    assert all(a.reason == "demo_reason" for a in audits) and audits[1].details["messages"] == 2
    assert await as_app(db, w["doctor"], READ, g=grant) == "42501"  # only the holder reads
    assert await as_app(db, w["nurse"], READ, aal2=False, g=grant) == "42501"  # no second factor


async def test_who_may_open_and_what_the_database_refuses(db):
    w = await world(db)
    for who in ("fd", "nurse", "doctor"):
        assert (await open_grant(db, w, who=who))[0][2] is True, who
    assert await open_grant(db, w, who="admin") == "42501"  # an admin reviews, never opens
    assert await open_grant(db, w, who="outsider") == "42501"  # staff of another clinic
    assert await as_app(db, w["nurse"], OPEN, aal2=False, c=w["clinic"], p=w["patient"], r="demo_reason", m=30, k="k-x") == "42501"
    assert await open_grant(db, w, reason="not_on_the_list") == "22023"  # unknown is not a value
    assert await open_grant(db, w, minutes=0) == "42501" and await open_grant(db, w, minutes=241) == "42501"
    assert await open_grant(db, w, patient=uuid.uuid4()) == "42501"  # a patient that is not in the clinic
    assert await as_app(db, w["nurse"], OPEN, c=w["clinic"], p=w["patient"], r="Free text here", m=30, k="k-y") == "42501"  # a reason is a code, never text


async def test_the_window_is_enforced_by_the_database_and_an_expired_or_reviewed_grant_reads_nothing_and_is_audited(db):
    w = await world(db)
    grant = (await open_grant(db, w))[0][0]
    assert await owner(db, "update iam.break_glass_grants set expires_at = expires_at + interval '1 hour' where id = :g", g=grant) == "23001"  # never extended, even by the owner
    # the window closes (the owner moves the clock by creating an already-expired grant in the past is not possible; shrink via a fresh row is refused by the guard, so test expiry on a past-dated insert)
    h = await pg(db)
    past = await h.fetchval(
        "insert into iam.break_glass_grants (clinic_id, origin, patient_id, opened_by, reason_code, opened_at, expires_at, idempotency_key) values ($1, 'test', $2, $3, 'demo_reason', now() - interval '2 hours', now() - interval '1 hour', 'past') returning id",
        w["clinic"], w["patient"], w["nurse"],
    )
    assert await as_app(db, w["nurse"], READ, g=past) == [("refused", None, None)]
    assert (await db.execute(text("select count(*) from audit.log where action = 'break_glass.refused' and details ->> 'grant_id' = :g"), {"g": str(past)})).scalar() == 1
    assert await as_app(db, w["admin"], REVIEW, g=grant, o="appropriate") == [("reviewed",)]
    assert await as_app(db, w["nurse"], READ, g=grant) == [("refused", None, None)]  # a reviewed grant reads nothing more


async def test_review_is_by_an_admin_who_did_not_open_it_once(db):
    w = await world(db)
    grant = (await open_grant(db, w))[0][0]
    for who in ("fd", "nurse", "doctor", "outsider"):
        assert await as_app(db, w[who], REVIEW, g=grant, o="appropriate") == "42501", who
    assert await as_app(db, w["admin"], REVIEW, aal2=False, g=grant, o="appropriate") == "42501"
    assert await as_app(db, w["admin"], REVIEW, g=grant, o="whatever") == "42501"
    assert await as_app(db, w["admin"], REVIEW, g=grant, o="inappropriate") == [("reviewed",)]
    assert await as_app(db, w["admin2"], REVIEW, g=grant, o="inappropriate") == [("reviewed",)]  # the same outcome again is a replay
    assert await as_app(db, w["admin2"], REVIEW, g=grant, o="appropriate") == [("already_reviewed",)]
    assert (await db.execute(text("select count(*) from audit.log where action = 'break_glass.review' and details ->> 'grant_id' = :g"), {"g": str(grant)})).scalar() == 1
    # an admin who opened a grant (granted a second role in the test) cannot review their own
    h = await pg(db)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", w["admin"], w["clinic"])
    own = await as_app(db, w["admin"], OPEN, c=w["clinic"], p=w["patient"], r="demo_reason", m=30, k="admin-own")
    if isinstance(own, list):  # (the active-role narrowing may hide the second role; when it does not, the self review is refused as same_person)
        assert await as_app(db, w["admin"], REVIEW, g=own[0][0], o="appropriate") == [("same_person",)]


async def test_no_application_write_reaches_a_grant_or_the_reason_list_and_the_table_guards_hold_for_the_owner(db):
    w = await world(db)
    grant = (await open_grant(db, w))[0][0]
    for sql in (
        "insert into iam.break_glass_grants (clinic_id, origin, patient_id, opened_by, reason_code, expires_at, idempotency_key) values (gen_random_uuid(), 'test', gen_random_uuid(), gen_random_uuid(), 'x', now(), 'k')",
        "update iam.break_glass_grants set reviewed_at = now()",
        "delete from iam.break_glass_grants",
        "select count(*) from iam.break_glass_reasons where true",
        "insert into iam.break_glass_reasons (clinic_id, code) values (gen_random_uuid(), 'x')",
    ):
        assert await as_app(db, w["nurse"], sql) == "42501", sql
    assert await owner(db, "delete from iam.break_glass_grants where id = :g", g=grant) == "23001"
    assert await owner(db, "update iam.break_glass_grants set reason_code = 'other' where id = :g", g=grant) == "23001"
    assert await owner(db, "update iam.break_glass_grants set reviewed_by = opened_by, reviewed_at = now(), review_outcome = 'appropriate' where id = :g", g=grant) == "23514"  # the reviewer must be another person
    assert await owner(db, "truncate iam.break_glass_grants") in ("23001", "0A000")
    # who sees grants: the holder and the clinic's admins (with a second factor), nobody else
    visible = lambda who, aal2=True: as_app(db, w[who], "select count(*) from iam.break_glass_grants where id = :g", aal2=aal2, g=grant)
    assert await visible("nurse") == [(1,)] and await visible("admin") == [(1,)]
    assert await visible("doctor") == [(0,)] and await visible("fd") == [(0,)] and await visible("outsider") == [(0,)]
    assert await visible("nurse", aal2=False) == [(0,)] and await visible("admin", aal2=False) == [(0,)]


async def test_a_holder_who_loses_the_role_or_whose_patient_is_erased_is_cut_off(db):
    w = await world(db)
    h = await pg(db)
    grant = (await open_grant(db, w))[0][0]
    await h.execute("update iam.staff_roles set revoked_at = now() where account_id = $1 and clinic_id = $2", w["nurse"], w["clinic"])
    assert await as_app(db, w["nurse"], READ, g=grant) == "42501"
    grant2 = (await open_grant(db, w, who="doctor"))[0][0]
    req = (await as_app(db, w["fd"], "select mpi.request_patient_erasure(:p, :r, :k)", p=w["patient"], r="entered_in_error", k="rq"))[0][0]
    fd2 = uuid.uuid4()
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", fd2)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", fd2, w["clinic"])
    assert await as_app(db, fd2, "select mpi.approve_patient_erasure(:q, :k)", q=req, k="ap") == [(True,)]
    assert await as_app(db, w["doctor"], READ, g=grant2) == [("refused", None, None)]  # the patient is erased: nothing to read, and the attempt is audited


async def test_the_limits_are_the_clinics_own_and_the_database_enforces_them_whatever_the_application_passes(db):
    w = await world(db)
    h = await pg(db)
    await h.execute("update iam.break_glass_settings set max_reopens = 3, max_minutes = 60 where clinic_id = $1", w["clinic"])
    for n in range(3):
        assert isinstance(await open_grant(db, w, who="nurse", key=f"chain-{n}"), list)
    assert await open_grant(db, w, who="nurse", key="chain-3") == "54000"  # past the clinic's cap: the database says so
    assert isinstance(await open_grant(db, w, who="nurse", key="chain-0"), list)  # a replay is still the same grant
    assert isinstance(await open_grant(db, w, who="doctor", key="d0"), list)  # the cap is per person
    assert isinstance(await open_grant(db, w, who="nurse", key="other-patient", patient=w["other_patient"]), list)  # and per patient
    assert await open_grant(db, w, who="fd", minutes=61) == "22003"  # a window above the clinic's maximum is refused (the trigger is the backstop)
    shorter = await open_grant(db, w, who="fd", minutes=5)
    assert isinstance(shorter, list)
    assert await open_grant(db, w, minutes=0) == "42501" and await open_grant(db, w, minutes=241) == "42501"
    assert isinstance(await as_app(db, w["fd"], "select grant_id from iam.open_break_glass(:c, :p, :r, null, 'null-minutes')", c=w["clinic"], p=w["patient"], r="demo_reason"), list)  # null means the clinic's maximum


async def test_with_no_settings_row_no_grant_can_be_opened_and_the_application_cannot_touch_the_limits(db):
    w = await world(db)
    h = await pg(db)
    await h.execute("delete from iam.break_glass_settings where clinic_id = $1", w["clinic"])
    assert await open_grant(db, w) == "55000"  # unknown is not a value
    assert await owner(db, "insert into iam.break_glass_grants (clinic_id, origin, patient_id, opened_by, reason_code, expires_at, idempotency_key) values (:c, 'test', :p, :u, 'demo_reason', now() + interval '1 hour', 'direct')", c=w["clinic"], p=w["patient"], u=w["nurse"]) == "55000"  # not even the owner's direct insert
    await h.execute("insert into iam.break_glass_settings (clinic_id, max_minutes, max_reopens) values ($1, 60, 2)", w["clinic"])
    assert await owner(db, "insert into iam.break_glass_grants (clinic_id, origin, patient_id, opened_by, reason_code, expires_at, idempotency_key) values (:c, 'test', :p, :u, 'demo_reason', now() + interval '3 hours', 'long')", c=w["clinic"], p=w["patient"], u=w["nurse"]) == "54000"
    for sql in ("select count(*) from iam.break_glass_settings where true", "insert into iam.break_glass_settings (clinic_id, max_minutes, max_reopens) values (gen_random_uuid(), 240, 50)", "update iam.break_glass_settings set max_reopens = 50", "delete from iam.break_glass_settings"):
        assert await as_app(db, w["nurse"], sql) == "42501", sql
