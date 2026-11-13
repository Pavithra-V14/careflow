"""Migration 0073 against the real database: the front-desk supervisor role and the admin alert on a revoke. The role ADDS to front desk (granted only to someone who holds front desk, goes with it, cannot be
given at invitation) and lets its holder see, take and close ONE kind of task assigned to it; the alert is raised by the person who made the revoke, bound to that event. The staff role functions keep their
second-factor check (0065). Inside ONE never-committed outer transaction, as the restricted `careflow_app` role.
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
GRANT = "select iam.grant_staff_role(:a, :c, :r, :k)"
REVOKE = "select iam.revoke_staff_role(:a, :c, :r, :k)"
INVITE = "select iam.invite_staff_account(:a, :c, :r, :k)"


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


async def world(db):
    h = await pg(db)
    ids = {k: uuid.uuid4() for k in ("clinic", "other", "admin", "fd1", "fd2", "sup", "nurse", "nurse_only", "newbie", "patient")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'SUP-A', 'test'), ($2, 'SUP-B', 'test')", ids["clinic"], ids["other"])
    for k in ("admin", "fd1", "fd2", "sup", "nurse", "nurse_only"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", ids[k])
    for k, role in (("admin", "admin"), ("fd1", "front_desk"), ("fd2", "front_desk"), ("sup", "front_desk"), ("nurse", "nurse"), ("nurse_only", "nurse")):
        await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", ids[k], ids["clinic"], role)
    await h.execute(
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) values ($1, $2, '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, 'test', $3)",
        ids["patient"], ids["clinic"], f"sup-{ids['patient']}",
    )
    return ids


async def make_supervisor(db, w):
    assert await as_app(db, w["admin"], GRANT, a=w["sup"], c=w["clinic"], r="front_desk_supervisor", k="g1") == [(True,)]


async def task(db, w, kind="identity_confirmation_revoked", assigned="front_desk_supervisor"):
    tid = uuid.uuid4()
    h = await pg(db)
    await h.execute(
        "insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) values ($1, $2, 'test', $3, 'standard', $4, $5, '{}'::jsonb, $6)",
        tid, w["clinic"], kind, assigned, f"sup-src-{tid}", f"sup-{tid}",
    )
    return tid


async def test_the_role_is_granted_only_to_someone_who_already_holds_front_desk_and_not_at_invitation(db):
    w = await world(db)
    assert await as_app(db, w["admin"], GRANT, a=w["nurse_only"], c=w["clinic"], r="front_desk_supervisor", k="g0") == "42501"  # no front desk
    await make_supervisor(db, w)
    assert await as_app(db, w["admin"], GRANT, a=w["sup"], c=w["clinic"], r="front_desk_supervisor", k="g1") == [(False,)]  # a replay writes nothing
    assert await as_app(db, w["admin"], INVITE, a=uuid.uuid4(), c=w["clinic"], r="front_desk_supervisor", k="i1") == "42501"  # not at invitation
    assert await as_app(db, w["nurse"], GRANT, a=w["fd1"], c=w["clinic"], r="front_desk_supervisor", k="g2") == "42501"  # only an admin grants
    assert await as_app(db, w["admin"], GRANT, aal2=False, a=w["fd1"], c=w["clinic"], r="front_desk_supervisor", k="g3") == "42501"  # the second factor is still required (0065)
    assert await as_app(db, w["admin"], REVOKE, aal2=False, a=w["sup"], c=w["clinic"], r="front_desk_supervisor", k="g4") == "42501"
    assert await as_app(db, w["admin"], GRANT, a=w["fd1"], c=w["other"], r="front_desk_supervisor", k="g5") == "42501"  # another clinic's admin is not yours: this admin is not an admin there


async def test_the_role_goes_when_front_desk_is_revoked(db):
    w = await world(db)
    await make_supervisor(db, w)
    assert await as_app(db, w["admin"], REVOKE, a=w["sup"], c=w["clinic"], r="front_desk", k="r1") == [(True,)]
    live = (await db.execute(text("select role from iam.staff_roles where account_id = :a and revoked_at is null"), {"a": w["sup"]})).all()
    assert live == []


async def test_a_supervisor_sees_takes_and_closes_only_the_one_kind_assigned_to_the_role(db):
    w = await world(db)
    await make_supervisor(db, w)
    mine = await task(db, w)
    to_nurse = await task(db, w, assigned="nurse")
    other_kind = await task(db, w, kind="referral_hold", assigned="front_desk_supervisor")  # a different kind: not theirs even if so assigned
    ids = {"id": [mine, to_nurse, other_kind]}
    seen = await as_app(db, w["sup"], "select id from agents.review_tasks where id = any(cast(:ids as uuid[]))", ids=ids["id"])
    assert {r[0] for r in seen} == {mine}
    assert await as_app(db, w["fd1"], "select id from agents.review_tasks where id = :i", i=mine) == []  # plain front desk does not see it
    assert await as_app(db, w["admin"], "select id from agents.review_tasks where id = :i", i=mine) == []
    assert len(await as_app(db, w["nurse"], "select id from agents.review_tasks where id = :i", i=mine)) == 1  # a nurse sees every kind
    ack = "update agents.review_tasks set status = 'acknowledged', acknowledged_by = :u, acknowledged_at = now() where id = :i"
    assert await as_app(db, w["sup"], ack, u=w["sup"], i=mine) == 1
    assert await as_app(db, w["sup"], ack, u=w["sup"], i=to_nurse) == 0  # not visible, nothing changes
    assert await as_app(db, w["sup"], ack, u=w["sup"], i=other_kind) == 0
    assert await as_app(db, w["fd1"], ack, u=w["fd1"], i=mine) == 0
    assert await as_app(db, w["sup"], "update agents.review_tasks set status = 'resolved', resolved_at = now() where id = :i", i=mine) == 1


async def test_a_supervisor_loses_the_view_when_the_role_is_revoked_and_front_desk_alone_never_had_it(db):
    w = await world(db)
    await make_supervisor(db, w)
    mine = await task(db, w)
    assert len(await as_app(db, w["sup"], "select id from agents.review_tasks where id = :i", i=mine)) == 1
    assert await as_app(db, w["admin"], REVOKE, a=w["sup"], c=w["clinic"], r="front_desk_supervisor", k="r2") == [(True,)]
    assert await as_app(db, w["sup"], "select id from agents.review_tasks where id = :i", i=mine) == []


async def _revoke_event(db, w, who):
    eid = uuid.uuid4()
    n = await as_app(
        db, w[who],
        "insert into mpi.identity_confirmations (id, clinic_id, patient_id, confirmed_by, origin, idempotency_key, kind, reason) values (:i, :c, :p, :u, 'test', :k, 'revoke', 'wrong_person')",
        i=eid, c=w["clinic"], p=w["patient"], u=w[who], k=f"ev-{eid}",
    )
    return eid, n


ALERT = (
    "insert into ops.alerts (clinic_id, origin, class, audience_role, alert_count, ref, idempotency_key) values (:c, 'test', 'identity_revoked_suspect', :aud, :n, :ref, :k)"
)


async def test_the_admin_alert_is_raised_only_by_the_person_who_made_the_revoke_and_only_for_an_admin(db):
    w = await world(db)
    eid, made = await _revoke_event(db, w, "fd1")
    assert made == 1
    ok = await as_app(db, w["fd1"], ALERT, c=w["clinic"], aud="admin", n=1, ref=str(eid), k=f"identity-revoked-alert:{eid}")
    assert ok == 1
    assert await as_app(db, w["fd2"], ALERT, c=w["clinic"], aud="admin", n=1, ref=str(eid), k=f"identity-revoked-alert:{eid}x") == "42501"  # not the person who made the revoke
    assert await as_app(db, w["fd1"], ALERT, c=w["clinic"], aud="nurse", n=1, ref=str(eid), k=f"identity-revoked-alert:{eid}") == "42501"  # only for an admin
    assert await as_app(db, w["fd1"], ALERT, c=w["clinic"], aud="admin", n=5, ref=str(eid), k=f"identity-revoked-alert:{eid}") == "42501"  # a count of one only
    assert await as_app(db, w["fd1"], ALERT, c=w["clinic"], aud="admin", n=1, ref=str(uuid.uuid4()), k="identity-revoked-alert:made-up") == "42501"  # no such event
    assert await as_app(db, w["nurse"], ALERT, c=w["clinic"], aud="admin", n=1, ref=str(eid), k=f"identity-revoked-alert:{eid}") == "42501"
    assert len(await as_app(db, w["admin"], "select id from ops.alerts where ref = :r", r=str(eid))) == 1  # the admin sees it
    assert await as_app(db, w["fd1"], "select id from ops.alerts where ref = :r", r=str(eid)) == []  # the writer does not
