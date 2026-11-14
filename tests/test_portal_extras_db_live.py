"""Migration 0080 against the real database: the patient profile, in-app notifications, patient requests and the public grievance writer. Everything is written by a definer function (the application role has no table grant);
a patient sees only their own rows; front desk of the clinic works the requests with a second factor; the public grievance needs the owner-published clinic and the owner-managed hourly cap. Inside ONE never-committed outer
transaction, as the restricted `careflow_app` role.
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
    ids = {k: uuid.uuid4() for k in ("clinic", "other", "fd", "nurse", "admin", "pa1", "pa2", "pa3", "rec1", "rec2", "ostaff")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'PX-A', 'test'), ($2, 'PX-B', 'test')", ids["clinic"], ids["other"])
    for k in ("fd", "nurse", "admin", "ostaff"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k in ("pa1", "pa2", "pa3"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids[k])
    for k, role in (("fd", "front_desk"), ("nurse", "nurse"), ("admin", "admin")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", ids["ostaff"], ids["other"])
    for rec, acct in (("rec1", "pa1"), ("rec2", "pa2")):
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, account_id, origin, idempotency_key) "
            "values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, $3, 'test', $4)",
            ids[rec], ids["clinic"], ids[acct], f"px-{ids[rec]}",
        )
    return ids


# ---- profile --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


async def test_a_patient_sets_their_own_profile_and_sees_only_their_own_and_bad_values_are_refused(db):
    w = await world(db)
    assert await as_app(db, w["pa1"], "select iam.set_patient_profile('some_word', 'ta')") == [(True,)]
    assert await as_app(db, w["pa1"], "select sex, preferred_language from iam.patient_profiles") == [("some_word", "ta")]
    assert await as_app(db, w["pa2"], "select * from iam.patient_profiles") == []  # not yours
    assert await as_app(db, w["pa1"], "select iam.set_patient_profile(null, 'en')") == [(True,)]  # it sets values: a repeat replaces
    assert await as_app(db, w["pa1"], "select sex, preferred_language from iam.patient_profiles") == [(None, "en")]
    for bad in ("select iam.set_patient_profile('Bad Word', 'en')", "select iam.set_patient_profile('x', 'fr')"):
        assert await as_app(db, w["pa1"], bad) == "42501"
    for who in ("fd", "nurse", "admin"):
        assert await as_app(db, w[who], "select iam.set_patient_profile(null, 'en')") == "42501", who  # only a patient account
    assert await as_app(db, w["pa1"], "insert into iam.patient_profiles (account_id, origin) values (gen_random_uuid(), 'test')") == "42501"  # no table write


# ---- notifications --------------------------------------------------------------------------------------------------------------------------------------------------------------------------


async def test_staff_notify_the_bound_patient_account_once_and_the_patient_reads_and_marks_read_once(db):
    w = await world(db)
    notify = "select iam.notify_patient(:c, :p, 'offer_sent', '00000000-0000-4000-8000-000000000001', :k)"
    assert await as_app(db, w["fd"], notify, c=w["clinic"], p=w["rec1"], k="n1") == [(True,)]
    assert await as_app(db, w["fd"], notify, c=w["clinic"], p=w["rec1"], k="n1") == [(False,)]  # a repeat writes nothing
    assert await as_app(db, w["fd"], "select iam.notify_patient(:c, :p, 'offer_sent', null, 'n2')", c=w["clinic"], p=uuid.uuid4()) == [(False,)]  # no such patient: nothing to tell
    assert await as_app(db, w["fd"], "select iam.notify_patient(:c, :p, 'free_text', null, 'n3')", c=w["clinic"], p=w["rec1"]) == "42501"  # a closed kind
    assert await as_app(db, w["fd"], "select iam.notify_patient(:c, :p, 'offer_sent', 'some words', 'n6')", c=w["clinic"], p=w["rec1"]) == "42501"  # the reference is an id, never text
    assert await as_app(db, w["ostaff"], notify, c=w["clinic"], p=w["rec1"], k="n4") == "42501"  # staff of another clinic
    assert await as_app(db, w["fd"], notify, aal2=False, c=w["clinic"], p=w["rec1"], k="n5") == "42501"  # no second factor
    mine = await as_app(db, w["pa1"], "select id, kind, ref, read_at from iam.notifications")
    assert len(mine) == 1 and mine[0][1:3] == ("offer_sent", "00000000-0000-4000-8000-000000000001") and mine[0][3] is None
    assert [r[0] for r in await owner(db, "select action from audit.log where resource_id = :r and action like 'patient_notification%'", r=str(w["rec1"]))] == ["patient_notification.send"]  # one audit row, no text
    assert await as_app(db, w["pa2"], "select * from iam.notifications") == []  # not yours
    nid = mine[0][0]
    assert await as_app(db, w["pa2"], "select iam.mark_notification_read(:i)", i=nid) == "42501"  # not yours: the same answer as missing
    assert await as_app(db, w["pa1"], "select iam.mark_notification_read(:i)", i=nid) == [(True,)]
    assert await as_app(db, w["pa1"], "select iam.mark_notification_read(:i)", i=nid) == [(False,)]  # read once
    assert await owner(db, "update iam.notifications set read_at = null where id = :i", i=nid) == "23001"  # nothing but "read", once
    assert await owner(db, "delete from iam.notifications where id = :i", i=nid) == "23001"


# ---- patient requests ------------------------------------------------------------------------------------------------------------------------------------------------------------------------


async def test_a_patient_files_a_request_without_free_text_and_front_desk_works_it_and_the_patient_is_told(db):
    w = await world(db)
    file_sql = "select request_id, created from iam.file_patient_request(:c, :k, :f, :i, :key)"
    rid = uuid.uuid4()
    assert await as_app(db, w["pa1"], file_sql, c=w["clinic"], k="correction", f="phone", i=rid, key="r1") == [(rid, True)]
    assert await as_app(db, w["pa1"], file_sql, c=w["clinic"], k="correction", f="phone", i=uuid.uuid4(), key="r1") == [(rid, False)]  # a replay is the same request
    assert await as_app(db, w["pa1"], file_sql, c=w["clinic"], k="correction", f="name", i=uuid.uuid4(), key="r1") == "42501"  # the same key for another request
    assert await as_app(db, w["pa1"], file_sql, c=w["clinic"], k="correction", f=None, i=uuid.uuid4(), key="r2") == "42501"  # a correction names a field
    assert await as_app(db, w["pa1"], file_sql, c=w["clinic"], k="erasure", f="phone", i=uuid.uuid4(), key="r3") == "42501"  # another kind names none
    assert await as_app(db, w["pa1"], file_sql, c=w["other"], k="erasure", f=None, i=uuid.uuid4(), key="r4") == "42501"  # no record of yours at that clinic
    assert await as_app(db, w["pa3"], file_sql, c=w["clinic"], k="erasure", f=None, i=uuid.uuid4(), key="r5") == "42501"  # an account bound to no record
    assert await as_app(db, w["fd"], file_sql, c=w["clinic"], k="erasure", f=None, i=uuid.uuid4(), key="r6") == "42501"  # only a patient account
    assert len(await as_app(db, w["pa1"], "select id from iam.patient_requests")) == 1 and await as_app(db, w["pa2"], "select id from iam.patient_requests") == []
    assert len(await as_app(db, w["fd"], "select id from iam.patient_requests")) == 1
    assert await as_app(db, w["fd"], "select id from iam.patient_requests", aal2=False) == []  # no second factor: nothing
    for who in ("nurse", "admin", "ostaff"):
        assert await as_app(db, w[who], "select id from iam.patient_requests") == [], who
    update = "select iam.update_patient_request(:i, :s, :e)"
    assert await as_app(db, w["nurse"], update, i=rid, s="acknowledged", e=None) == "42501"  # front desk only
    assert await as_app(db, w["ostaff"], update, i=rid, s="acknowledged", e=None) == "42501"
    assert await as_app(db, w["fd"], update, aal2=False, i=rid, s="acknowledged", e=None) == "42501"
    assert await as_app(db, w["fd"], update, i=rid, s="acknowledged", e=None) == [("recorded",)]
    assert await as_app(db, w["fd"], update, i=rid, s="acknowledged", e=None) == [("replay",)]
    assert await as_app(db, w["fd"], update, i=rid, s="resolved", e=None) == [("recorded",)]
    assert await as_app(db, w["fd"], update, i=rid, s="acknowledged", e=None) == [("invalid_transition",)]  # never backwards
    told = await as_app(db, w["pa1"], "select kind, ref from iam.notifications order by created_at")
    assert [t[0] for t in told] == ["request_update", "request_update"] and all(t[1] == str(rid) for t in told)  # an opaque id only
    audits = [r[0] for r in await owner(db, "select action from audit.log where resource_id = :r order by id", r=str(rid))]
    assert audits == ["patient_request.file", "patient_request.update", "patient_request.update"]
    assert await owner(db, "update iam.patient_requests set kind = 'nominee' where id = :i", i=rid) == "23001"  # only the status and the link change
    assert await owner(db, "delete from iam.patient_requests where id = :i", i=rid) == "23001"


async def test_an_erasure_request_may_link_only_the_erasure_request_of_the_same_patient_and_clinic(db):
    w = await world(db)
    h = await pg(db)
    rid = uuid.uuid4()
    await as_app(db, w["pa1"], "select request_id from iam.file_patient_request(:c, 'erasure', null, :i, 'e1')", c=w["clinic"], i=rid)
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    for er, rec in ((mine, "rec1"), (theirs, "rec2")):
        await h.execute(
            "insert into mpi.erasure_requests (id, clinic_id, patient_id, reason, requested_by, origin, idempotency_key) values ($1, $2, $3, 'demo_reason', $4, 'test', $5)",
            er, w["clinic"], w[rec], w["fd"], f"er-{er}",
        )
    update = "select iam.update_patient_request(:i, 'acknowledged', :e)"
    assert await as_app(db, w["fd"], update, i=rid, e=theirs) == "42501"  # another patient's erasure request
    assert await as_app(db, w["fd"], update, i=rid, e=uuid.uuid4()) == "42501"  # no such request
    assert await as_app(db, w["fd"], update, i=rid, e=mine) == [("recorded",)]
    assert (await db.execute(text("select erasure_request_id from iam.patient_requests where id = :i"), {"i": rid})).scalar() == mine
    other = uuid.uuid4()
    await as_app(db, w["pa1"], "select request_id from iam.file_patient_request(:c, 'correction', 'name', :i, 'e2')", c=w["clinic"], i=other)
    assert await as_app(db, w["fd"], update, i=other, e=mine) == "42501"  # only an ERASURE request links


# ---- the public grievance ----------------------------------------------------------------------------------------------------------------------------------------------------------------------

PUBLIC = "select grievance_id, created from iam.file_public_grievance('demo_category', decode(repeat('01', 40), 'hex'), cast(1 as smallint), :g, :k)"


async def test_the_public_grievance_needs_a_published_clinic_and_an_owner_set_cap_and_needs_no_sign_in(db):
    w = await world(db)
    h = await pg(db)
    nobody = uuid.uuid4()
    assert await as_app(db, nobody, PUBLIC, g=uuid.uuid4(), k="g0" + "x" * 32) == "P0002"  # nothing published
    await h.execute("insert into iam.public_clinics (clinic_id) values ($1)", w["clinic"])
    assert await as_app(db, nobody, PUBLIC, g=uuid.uuid4(), k="g0" + "x" * 32) == "42501"  # no cap row: unknown is not a value
    await h.execute("insert into iam.public_grievance_limits (clinic_id, max_per_hour) values ($1, 2)", w["clinic"])
    gid = uuid.uuid4()
    assert await as_app(db, nobody, PUBLIC, g=gid, k="g1" + "x" * 32) == [(gid, True)]
    assert await as_app(db, nobody, PUBLIC, g=uuid.uuid4(), k="g1" + "x" * 32) == [(gid, False)]  # a replay
    row = (await db.execute(text("select source, account_id, clinic_id, origin from iam.grievances where id = :i"), {"i": gid})).one()
    assert (row.source, row.account_id, row.clinic_id, row.origin) == ("public", None, w["clinic"], "test")
    assert await as_app(db, nobody, PUBLIC, g=uuid.uuid4(), k="g2" + "x" * 32) != "54000"
    assert await as_app(db, nobody, PUBLIC, g=uuid.uuid4(), k="g3" + "x" * 32) == "54000"  # the hourly cap (2) is reached
    assert await as_app(db, nobody, PUBLIC, g=gid, k="g1" + "x" * 32) == [(gid, False)]  # a replay still answers while the cap is full
    assert [r[0] for r in await owner(db, "select action from audit.log where resource_id = :r", r=str(gid))] == ["grievance.file_public"]
    # the clinic's admin sees it with the existing list and can acknowledge it; nobody else, and no other clinic
    assert any(r[0] == gid for r in await as_app(db, w["admin"], "select id from iam.grievances where clinic_id = :c", c=w["clinic"]))
    assert await as_app(db, w["admin"], "select iam.update_grievance(:g, 'acknowledged')", g=gid) == [("recorded",)]
    assert await as_app(db, w["ostaff"], "select id from iam.grievances") == []
    assert await as_app(db, w["pa1"], "select id from iam.grievances where id = :i", i=gid) == []  # a patient does not see it
    bad = (
        "select iam.file_public_grievance('Bad Category', decode(repeat('01', 40), 'hex'), cast(1 as smallint), gen_random_uuid(), 'gx' || repeat('x', 32))",
        "select iam.file_public_grievance('x', decode('01', 'hex'), cast(1 as smallint), gen_random_uuid(), 'gy' || repeat('x', 32))",
    )
    for stmt in bad:
        assert await as_app(db, nobody, stmt) == "42501"
    assert await as_app(db, nobody, "select * from iam.public_grievance_limits") == "42501"  # the cap is not readable or writable by the application role
    assert await as_app(db, nobody, "update iam.public_grievance_limits set max_per_hour = 1000") == "42501"
    assert await as_app(db, nobody, PUBLIC, g=uuid.uuid4(), k="short") == "42501"  # an anonymous key must be unguessable (32 characters or more)
    # the erasure link is set once: a different one is not silently ignored (see the erasure-link test above); a twenty-request bound per account stops a flood
