"""Migration 0082 against the real database: a patient may open the thread of a topic and send a message ONLY through definer functions; care needs stage `active`; `safety` is never patient-sent; a patient reads only their own threads; a patient
message cannot commit unscreened, and a patient can screen only their own message. Inside ONE never-committed outer transaction, as the restricted `careflow_app` role. Synthetic data only; the ciphertext here is filler (the encryption round trip is in
tests/test_portal_messaging_route.py)."""

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

CT = "decode(repeat('ab', 40), 'hex')"  # filler ciphertext: 40 bytes, inside the table's size bounds


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


async def as_app(db, person, *steps):
    """Run each (sql, params) as the application role with the person's claims; the last result, or the SQLSTATE of the first refusal."""
    try:
        async with db.begin_nested():
            await db.exec_driver_sql("set local role careflow_app")
            await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims_for(person)})
            value = None
            for sql, params in steps:
                result = await db.execute(text(sql), params)
                value = [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount
            await db.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return sqlstate(exc)


async def owner(db, sql, **params):
    result = await db.execute(text(sql), params)
    return [tuple(r) for r in result.all()] if result.returns_rows else result.rowcount


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other", "nurse", "pa1", "pa2", "pa3", "rec1", "rec2", "rec3", "fd")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'PM-A', 'test'), ($2, 'PM-B', 'test')", ids["clinic"], ids["other"])
    for k in ("nurse", "fd"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k, role in (("nurse", "nurse"), ("fd", "front_desk")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    for k in ("pa1", "pa2", "pa3"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", ids[k])
    for rec, acct in (("rec1", "pa1"), ("rec2", "pa2"), ("rec3", "pa3")):
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, account_id, origin, idempotency_key) "
            "values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, $3, 'test', $4)",
            ids[rec], ids["clinic"], ids[acct], f"pm-{ids[rec]}",
        )
    # pa1 is `active` (a resolved claim at the clinic); pa2 and pa3 are only `new_patient`
    await h.execute("insert into mpi.claim_requests (clinic_id, origin, account_id) values ($1, 'test', $2)", ids["clinic"], ids["pa1"])
    await h.execute(
        "update mpi.claim_requests set status = 'resolved', resolved_by = $1, resolved_at = clock_timestamp(), resolved_patient_id = $2 where account_id = $3",
        ids["fd"], ids["rec1"], ids["pa1"],
    )
    return ids


OPEN = "select thread_id from msg.patient_open_thread(:c, :t, :n, :k)"
POST = f"select message_id, created from msg.patient_post_message(:t, :m, {CT}, 1::smallint, :k)"


async def open_thread(db, w, who, topic="appointments", clinic=None):
    return await as_app(db, w[who], (OPEN, {"c": clinic or w["clinic"], "t": topic, "n": uuid.uuid4(), "k": "k"}))


async def thread_of(db, w, who, topic="appointments"):
    rows = await open_thread(db, w, who, topic)
    assert isinstance(rows, list), rows
    return rows[0][0]


async def test_a_bound_patient_opens_an_appointments_thread_once_and_replays_to_the_same_thread(db):
    w = await world(db)
    first = await open_thread(db, w, "pa2")
    assert isinstance(first, list) and len(first) == 1
    assert await open_thread(db, w, "pa2") == first  # the same thread, not a second
    assert (await owner(db, "select count(*) from msg.threads where patient_id = :p", p=w["rec2"])) == [(1,)]


async def test_care_opens_only_at_stage_active_and_safety_is_never_patient_sent(db):
    w = await world(db)
    assert await open_thread(db, w, "pa2", "care") == "42501"  # new_patient
    assert isinstance(await open_thread(db, w, "pa1", "care"), list)  # active
    assert await open_thread(db, w, "pa1", "safety") == "42501"
    assert await open_thread(db, w, "pa1", "readiness") == "42501"


async def test_a_patient_cannot_open_a_thread_at_another_clinic_or_without_a_record_or_as_staff(db):
    w = await world(db)
    assert await open_thread(db, w, "pa1", clinic=w["other"]) == "42501"
    assert await open_thread(db, w, "nurse") == "42501"
    assert await open_thread(db, w, "fd") == "42501"
    await owner(db, "update mpi.patients set erased_at = clock_timestamp(), name_ct = null, dob_ct = null, phone_ct = null, name_key_version = null, dob_key_version = null, phone_key_version = null where id = :p", p=w["rec3"])
    assert await open_thread(db, w, "pa3") == "42501"  # an erased record


async def test_a_posted_message_cannot_commit_unscreened_and_the_patient_can_screen_only_their_own(db):
    w = await world(db)
    thread = await thread_of(db, w, "pa2")
    mid = uuid.uuid4()
    posted = await as_app(db, w["pa2"], (POST, {"t": thread, "m": mid, "k": "m1"}))
    assert posted == [(mid, True)]
    # not screened: the deferred check refuses to commit it
    assert await as_app(db, w["pa2"], (POST, {"t": thread, "m": uuid.uuid4(), "k": "m2"}), ("set constraints all immediate", {})) == "23514"
    # another patient cannot screen it; the owner of the message can (an unassessable result is always allowed: it raises a nurse task)
    screen = "select recorded_outcome from msg.record_screening(:m, 'unassessable', 'demo', 0, :h, '{}'::jsonb, :k)"
    args = {"m": mid, "h": "0" * 64, "k": "s1"}
    assert await as_app(db, w["pa1"], (screen, args)) == "42501"
    assert await as_app(db, w["nurse"], (screen, {**args, "k": "s2"})) == [("unassessable",)]
    # a fresh message with no earlier screening: the patient who wrote it screens it themselves in the same transaction, and then the deferred check passes
    own = uuid.uuid4()
    done = await as_app(db, w["pa2"], (POST, {"t": thread, "m": own, "k": "m3"}), (screen, {**args, "m": own, "k": "s3"}), ("set constraints all immediate", {}))
    assert not isinstance(done, str), done
    assert (await owner(db, "select count(*) from agents.review_tasks where source_message_id = :m and assigned_role = 'nurse'", m=str(mid)))[0][0] >= 1


async def test_a_patient_cannot_screen_a_staff_message_or_post_into_a_thread_that_is_not_theirs(db):
    w = await world(db)
    thread = await thread_of(db, w, "pa2")
    assert await as_app(db, w["pa3"], (POST, {"t": thread, "m": uuid.uuid4(), "k": "x"})) == "42501"
    assert await as_app(db, w["pa1"], (POST, {"t": uuid.uuid4(), "m": uuid.uuid4(), "k": "x"})) == "42501"
    staff_msg = uuid.uuid4()
    await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": claims_for(w["nurse"])})  # the staff insert is made as the nurse, explicitly
    await owner(
        db,
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values (:m, :c, 'test', 'appointments', :t, 'staff', :n, {CT}, 1, 'staff-1')",
        m=staff_msg, c=w["clinic"], t=thread, n=w["nurse"],
    )
    screen = "select recorded_outcome from msg.record_screening(:m, 'unassessable', 'demo', 0, :h, '{}'::jsonb, 'k')"
    assert await as_app(db, w["pa2"], (screen, {"m": staff_msg, "h": "0" * 64})) == "42501"  # not a patient message


async def test_a_post_replays_and_reads_are_private_to_the_owner_of_the_thread(db):
    w = await world(db)
    thread = await thread_of(db, w, "pa2")
    mid = uuid.uuid4()
    assert await as_app(db, w["pa2"], (POST, {"t": thread, "m": mid, "k": "same"})) == [(mid, True)]
    assert await as_app(db, w["pa2"], (POST, {"t": thread, "m": uuid.uuid4(), "k": "same"})) == [(mid, False)]  # a replay answers the first message
    own = await as_app(db, w["pa2"], ("select message_id, sender_kind from msg.patient_thread_messages(:t)", {"t": thread}))
    assert own == [(mid, "patient")]
    assert await as_app(db, w["pa1"], ("select message_id from msg.patient_thread_messages(:t)", {"t": thread})) == []  # not theirs: the same as no thread
    assert await as_app(db, w["pa1"], ("select count(*) from msg.patient_threads()", {})) == [(0,)]
    assert await as_app(db, w["pa2"], ("select count(*) from msg.patient_threads()", {})) == [(1,)]
    assert await as_app(db, w["pa2"], ("select count(*) from msg.messages where clinic_id = :c", {"c": w["clinic"]})) == [(0,)]  # no table access at all
    assert await as_app(db, w["pa2"], ("select count(*) from msg.threads where clinic_id = :c", {"c": w["clinic"]})) == [(0,)]
    assert await as_app(db, w["nurse"], ("select count(*) from msg.messages where thread_id = :t", {"t": thread})) == [(1,)]  # the clinic's nurse reads it as before
    assert await as_app(db, w["nurse"], ("select count(*) from msg.patient_threads()", {})) == "42501"  # staff have no patient stage


async def test_a_care_thread_is_hidden_from_a_patient_who_is_not_active_even_if_it_exists(db):
    w = await world(db)
    thread = await thread_of(db, w, "pa1", "care")
    await owner(db, "update iam.accounts set status = 'suspended' where id = :a", a=w["pa1"])  # no longer an active account
    assert await as_app(db, w["pa1"], ("select count(*) from msg.patient_threads()", {})) == [(0,)]  # a suspended account reads nothing
    assert await as_app(db, w["pa1"], ("select message_id from msg.patient_thread_messages(:t)", {"t": thread})) == []
    assert await as_app(db, w["pa1"], (POST, {"t": thread, "m": uuid.uuid4(), "k": "c"})) == "42501"


async def test_the_screening_rules_are_readable_only_by_a_bound_patient_of_that_clinic(db):
    w = await world(db)
    sql = "select count(*) >= 0 from kb.patient_screening_rules(:c, 'test')"
    assert await as_app(db, w["pa1"], (sql, {"c": w["clinic"]})) == [(True,)]
    assert await as_app(db, w["pa1"], (sql, {"c": w["other"]})) == "42501"
    assert await as_app(db, w["nurse"], (sql, {"c": w["clinic"]})) == "42501"
    assert await as_app(db, w["pa1"], ("select count(*) from kb.red_flag_rules", {})) == [(0,)]  # still no table read


async def test_the_application_role_still_cannot_insert_a_patient_message_directly(db):
    w = await world(db)
    thread = await thread_of(db, w, "pa2")
    direct = (
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        f"values (:m, :c, 'test', 'appointments', :t, 'patient', :a, {CT}, 1, 'direct')"
    )
    assert await as_app(db, w["pa2"], (direct, {"m": uuid.uuid4(), "c": w["clinic"], "t": thread, "a": w["pa2"]})) == "42501"
