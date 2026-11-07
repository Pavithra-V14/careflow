"""Migration 0059 against the real database: grievances (filed by a bound patient account, encrypted text, an admin acknowledges and resolves), the export of one's own messages and bookings, erasure of the
grievance text with the patient, and rotation of the text to a new key. Inside ONE never-committed outer transaction, as the restricted `careflow_app` role.
"""

import base64
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
from careflow.crypto import DemographicVault
from careflow.messages import decrypt_grievance_text, encrypt_grievance_text
from careflow.rotation import (
    grievance_version_usage,
    rotate_grievance_texts,
    verify_grievance_texts,
)
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
KEYS = {f"CAREFLOW_{k}_SECRET_V{v}": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC") for v in (1, 2)}
VAULT = DemographicVault.from_env(KEYS)
FILE = "select grievance_id, created from iam.file_grievance(:c, :cat, :ct, cast(1 as smallint), :g, :k)"
UPDATE = "select iam.update_grievance(:g, :s)"


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


def claims_for(person, aal2):
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
    ids = {k: uuid.uuid4() for k in ("clinic", "other_clinic", "admin", "nurse", "p1", "p2", "pat1", "pat2", "unbound", "staff_other")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'PR-A', 'test'), ($2, 'PR-B', 'test')", ids["clinic"], ids["other_clinic"])
    for k in ("admin", "nurse", "staff_other"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k in ("p1", "p2", "unbound"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids[k])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $3, 'admin', 'test'), ($2, $3, 'nurse', 'test')", ids["admin"], ids["nurse"], ids["clinic"])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'admin', 'test')", ids["staff_other"], ids["other_clinic"])
    for rec, acct in (("pat1", "p1"), ("pat2", "p2")):
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, account_id, origin, idempotency_key) values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, $3, 'test', $4)",
            ids[rec], ids["clinic"], ids[acct], f"pr-{ids[rec]}",
        )
    return ids


def cipher(w, grievance, text_="DEMO grievance text", version=1):
    return encrypt_grievance_text(VAULT, text_, clinic_id=w["clinic"], grievance_id=grievance, key_version=version)


async def file(db, w, who="p1", key=None, category="demo_category", clinic=None, text_="DEMO grievance text"):
    g = uuid.uuid4()
    return g, await as_app(db, w[who], FILE, aal2=False, c=clinic or w["clinic"], cat=category, ct=cipher(w, g, text_), g=g, k=key or f"k-{g}")


async def test_a_bound_patient_files_a_grievance_and_an_admin_acknowledges_and_resolves_it(db):
    w = await world(db)
    g, filed = await file(db, w, key="f1")
    assert filed == [(g, True)]
    again = await as_app(db, w["p1"], FILE, aal2=False, c=w["clinic"], cat="demo_category", ct=cipher(w, uuid.uuid4()), g=uuid.uuid4(), k="f1")
    assert again == [(g, False)]  # a replay returns the first grievance
    assert (await file(db, w, who="unbound"))[1] == "42501"  # an account bound to no record has no clinic to write to
    assert (await file(db, w, clinic=w["other_clinic"]))[1] == "42501"  # a clinic the account is not bound to
    assert (await file(db, w, category="Free text here"))[1] == "42501"
    assert (await file(db, w, who="admin"))[1] == "42501"  # staff do not file patient grievances
    row = (await db.execute(text("select category, body_ct is not null, erased_at is null from iam.grievances where id = :g"), {"g": g})).one()
    assert tuple(row) == ("demo_category", True, True)
    stored = bytes((await db.execute(text("select body_ct from iam.grievances where id = :g"), {"g": g})).scalar())
    assert decrypt_grievance_text(VAULT, stored, clinic_id=w["clinic"], grievance_id=g, key_version=1) == "DEMO grievance text"
    assert b"DEMO grievance" not in stored
    mine = lambda who, aal2=True: as_app(db, w[who], "select count(*) from iam.grievances where id = :g", aal2=aal2, g=g)
    assert await mine("p1", aal2=False) == [(1,)] and await mine("admin") == [(1,)]
    assert await mine("p2", aal2=False) == [(0,)] and await mine("nurse") == [(0,)] and await mine("staff_other") == [(0,)]
    assert await mine("admin", aal2=False) == [(0,)]  # an admin reads grievances only with a second factor
    assert await as_app(db, w["nurse"], UPDATE, g=g, s="acknowledged") == "42501"
    assert await as_app(db, w["staff_other"], UPDATE, g=g, s="acknowledged") == "42501"
    assert await as_app(db, w["admin"], UPDATE, aal2=False, g=g, s="acknowledged") == "42501"
    assert await as_app(db, w["admin"], UPDATE, g=g, s="acknowledged") == [("recorded",)]
    assert await as_app(db, w["admin"], UPDATE, g=g, s="acknowledged") == [("replay",)]
    assert await as_app(db, w["admin"], UPDATE, g=g, s="resolved") == [("recorded",)]
    assert await as_app(db, w["admin"], UPDATE, g=g, s="resolved") == [("replay",)]
    trail = await as_app(db, w["p1"], "select status from iam.grievance_updates where grievance_id = :g order by created_at, status", aal2=False, g=g)
    assert sorted(t[0] for t in trail) == ["acknowledged", "resolved"]  # the patient sees the trail of their own grievance
    assert await as_app(db, w["p2"], "select count(*) from iam.grievance_updates where grievance_id = :g", aal2=False, g=g) == [(0,)]
    actions = [a for (a,) in (await db.execute(text("select action from audit.log where resource_id = :g order by id"), {"g": str(g)})).all()]
    assert actions == ["grievance.file", "grievance.acknowledged", "grievance.resolved"]
    details = (await db.execute(text("select details::text from audit.log where resource_id = :g"), {"g": str(g)})).scalars().all()
    assert all("DEMO grievance" not in d for d in details)  # the text is in no audit row


async def test_a_resolved_grievance_is_final_and_nothing_writes_a_grievance_directly(db):
    w = await world(db)
    g, _ = await file(db, w)
    assert await as_app(db, w["admin"], UPDATE, g=g, s="resolved") == [("recorded",)]
    assert await as_app(db, w["admin"], UPDATE, g=g, s="acknowledged") == [("invalid_transition",)]
    for sql in (
        "insert into iam.grievances (clinic_id, origin, account_id, category, body_ct, body_key_version, idempotency_key) values (gen_random_uuid(), 'test', gen_random_uuid(), 'x', decode(repeat('01', 40), 'hex'), 1, 'k')",
        "update iam.grievances set category = 'other'",
        "delete from iam.grievances",
        "insert into iam.grievance_updates (grievance_id, clinic_id, status, actor_id) values (gen_random_uuid(), gen_random_uuid(), 'resolved', gen_random_uuid())",
        "select count(*) from iam.grievance_rotation_batches where true",
    ):
        assert await as_app(db, w["admin"], sql) == "42501", sql
    assert await owner(db, "update iam.grievances set category = 'other' where id = :g", g=g) == "23001"
    assert await owner(db, "delete from iam.grievances where id = :g", g=g) == "23001"
    assert await owner(db, "update iam.grievance_updates set status = 'acknowledged' where grievance_id = :g", g=g) == "23001"
    assert await owner(db, "truncate iam.grievances") in ("23001", "0A000")


async def test_the_audited_erasure_erases_the_grievance_text_and_counts_it(db):
    w = await world(db)
    g1, _ = await file(db, w, who="p1")
    g2, _ = await file(db, w, who="p2")
    h = await pg(db)
    fd1, fd2 = uuid.uuid4(), uuid.uuid4()
    for f in (fd1, fd2):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", f)
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'front_desk', 'test')", f, w["clinic"])
    request = (await as_app(db, fd1, "select mpi.request_patient_erasure(:p, :r, :k)", p=w["pat1"], r="entered_in_error", k="rq"))[0][0]
    assert await as_app(db, fd2, "select mpi.approve_patient_erasure(:q, :k)", q=request, k="ap") == [(True,)]
    gone = (await db.execute(text("select body_ct is null, body_key_version is null, erased_at is not null from iam.grievances where id = :g"), {"g": g1})).one()
    assert tuple(gone) == (True, True, True)
    kept = (await db.execute(text("select body_ct is not null, erased_at is null from iam.grievances where id = :g"), {"g": g2})).one()
    assert tuple(kept) == (True, True)  # another patient's grievance is untouched
    audit = (await db.execute(text("select details from audit.log where action = 'care_record.erased' and idempotency_key = :k"), {"k": f"sys:erase-care-record:{request}"})).scalar()
    assert audit["grievances"] == 1
    assert await owner(db, "update iam.grievances set erased_at = null where id = :g", g=g1) == "23001"  # final


async def test_the_export_returns_only_the_callers_own_appointment_and_safety_messages_and_is_audited(db):
    w = await world(db)
    h = await pg(db)
    t_app = await h.fetchval("insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) values ($1, $2, 'appointments', $3, 'test', 'ta') returning id", w["clinic"], w["pat1"], w["nurse"])
    t_care = await h.fetchval("insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) values ($1, $2, 'care', $3, 'test', 'tc') returning id", w["clinic"], w["pat1"], w["nurse"])
    t_other = await h.fetchval("insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) values ($1, $2, 'appointments', $3, 'test', 'to') returning id", w["clinic"], w["pat2"], w["nurse"])
    for t, topic, k in ((t_app, "appointments", "m1"), (t_care, "care", "m2"), (t_other, "appointments", "m3")):
        await h.execute(
            "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) values (gen_random_uuid(), $1, 'test', $2, $3, 'staff', $4, decode(repeat('01', 32), 'hex'), 1, $5)",
            w["clinic"], topic, t, w["nurse"], f"{k}-{t}",
        )
    rows = await as_app(db, w["p1"], "select topic from iam.export_my_messages()", aal2=False)
    assert rows == [("appointments",)]  # the care topic is not exported, nor another patient's message
    assert await as_app(db, w["unbound"], "select count(*) from iam.export_my_messages()", aal2=False) == [(0,)]
    assert await as_app(db, w["admin"], "select count(*) from iam.export_my_messages()") == "42501"  # staff have no export
    assert await as_app(db, w["p1"], "select count(*) from iam.export_my_bookings()", aal2=False) == [(0,)]
    n = (await db.execute(text("select count(*) from audit.log where action = 'portal.export' and actor_id = :a"), {"a": w["p1"]})).scalar()
    assert n == 1


async def test_grievance_texts_rotate_to_a_new_key_with_a_ledger_row_and_erased_ones_are_skipped(db):
    w = await world(db)
    h = await pg(db)
    made = []
    for n in range(2):
        g = uuid.uuid4()
        await h.execute(
            "insert into iam.grievances (id, clinic_id, origin, account_id, category, body_ct, body_key_version, idempotency_key) values ($1, $2, 'test', $3, 'demo_category', $4, 1, $5)",
            g, w["clinic"], w["p1"], cipher(w, g, f"DEMO text {n}"), f"rot-{n}",
        )
        made.append(g)
    gone = uuid.uuid4()
    await h.execute("insert into iam.grievances (id, clinic_id, origin, account_id, category, body_ct, body_key_version, idempotency_key) values ($1, $2, 'test', $3, 'demo_category', $4, 1, 'rot-gone')", gone, w["clinic"], w["p1"], cipher(w, gone))
    assert await owner(db, "update iam.grievances set body_ct = null, body_key_version = null, erased_at = now() where id = :g", g=gone) == 1
    assert await grievance_version_usage(db, clinic_id=w["clinic"]) == {1: 2}
    report = await rotate_grievance_texts(db, VAULT, from_version=1, to_version=2, clinic_id=w["clinic"])
    assert report.texts_rotated == 2 and report.batches == 1
    for g, expected in zip(made, ("DEMO text 0", "DEMO text 1"), strict=True):
        row = (await db.execute(text("select body_ct, body_key_version from iam.grievances where id = :g"), {"g": g})).one()
        assert row.body_key_version == 2 and decrypt_grievance_text(VAULT, bytes(row.body_ct), clinic_id=w["clinic"], grievance_id=g, key_version=2) == expected
    assert (await verify_grievance_texts(db, VAULT, to_version=2, clinic_id=w["clinic"])).ok
    assert (await rotate_grievance_texts(db, VAULT, from_version=1, to_version=2, clinic_id=w["clinic"])).texts_rotated == 0
    assert (await db.execute(text("select texts_rotated from iam.grievance_rotation_batches where clinic_id = :c"), {"c": w["clinic"]})).scalars().all() == [2]
    assert await owner(db, "update iam.grievances set body_key_version = 1, body_ct = body_ct where id = :g", g=made[0]) == "23001"  # a version change without new ciphertext is not a rotation
