"""The two-person erasure of a patient's identity against the real database (migration 0032) and the reserved `sys:` audit-key prefix (migration 0035). Everything runs as the restricted `careflow_app`
role with a verified account's claims inside ONE never-committed outer transaction."""

import json
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
REFUSED = "patient erasure refused"
REQUEST = "select mpi.request_patient_erasure(:p, :r, :k)"
APPROVE = "select mpi.approve_patient_erasure(:q, :k)"
CANCEL = "select mpi.cancel_patient_erasure(:q)"


def _u() -> uuid.UUID:
    return uuid.uuid4()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


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


class World:
    pass


async def world(conn) -> World:
    w = World()
    w.a, w.b, w.patient = _u(), _u(), _u()
    roster = [("fd1", w.a, "front_desk"), ("fd2", w.a, "front_desk"), ("nurse", w.a, "nurse"), ("fd_b", w.b, "front_desk")]
    w.people = {k: _u() for k, _, _ in roster}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'ER A', 'test'), ('{w.b}', 'ER B', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{i}', 'staff', 'active', 'test')" for i in w.people.values())
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{w.people[k]}', '{c}', '{r}', 'test')" for k, c, r in roster) + ";\n"
        f"insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key) "
        f"values ('{w.patient}', '{w.a}', '\\x0102'::bytea, 1, '\\x0304'::bytea, 1, '\\x0506'::bytea, 1, 'test', 'er-{w.patient}');",
    )
    return w


async def call(db, uid, sql, **params):
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(uid), "role": "authenticated"})})
        value = (await db.execute(text(sql), params)).scalar()
        await db.exec_driver_sql("reset role")
        return value


async def one(db, sql, **params):
    return (await db.execute(text(sql), params)).scalar()


async def test_one_person_requests_a_different_person_approves_and_only_then_is_the_identity_erased(db):
    w = await world(db)
    request = await call(db, w.people["fd1"], REQUEST, p=w.patient, r="entered_in_error", k="k-1")
    assert await call(db, w.people["fd1"], REQUEST, p=w.patient, r="entered_in_error", k="k-1") == request  # the same caller and key: the same request
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people["fd1"], APPROVE, q=request, k="a-self")  # the requester cannot approve their own request
    assert await one(db, "select erased_at is null and name_ct is not null from mpi.patients where id = :p", p=w.patient) is True
    assert await call(db, w.people["fd2"], APPROVE, q=request, k="a-1") is True
    row = (await db.execute(text("select name_ct, dob_ct, phone_ct, name_key_version, dob_key_version, phone_key_version, erased_at is not null from mpi.patients where id = :p"), {"p": w.patient})).one()
    assert row[:6] == (None,) * 6 and row[6] is True
    assert await call(db, w.people["fd2"], APPROVE, q=request, k="a-2") is False  # a replay
    rows = (await db.execute(text("select action, actor_id, details from audit.log where resource_id = :p and action like 'patient.erase%' order by id"), {"p": str(w.patient)})).all()
    assert [(r[0], r[1]) for r in rows] == [("patient.erase_request", w.people["fd1"]), ("patient.erase", w.people["fd2"])]
    assert all(set(r[2]) == {"reason", "role_scope"} and r[2]["reason"] == "entered_in_error" for r in rows)
    status = (await db.execute(text("select status, requested_by, approved_by from mpi.erasure_requests where id = :q"), {"q": request})).one()
    assert tuple(status) == ("erased", w.people["fd1"], w.people["fd2"])


@pytest.mark.parametrize("who", ["nurse", "fd_b"])
async def test_only_front_desk_of_the_clinic_may_request_approve_or_cancel(db, who):
    w = await world(db)
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people[who], REQUEST, p=w.patient, r="entered_in_error", k="x")
    request = await call(db, w.people["fd1"], REQUEST, p=w.patient, r="entered_in_error", k="k-1")
    for sql, params in ((APPROVE, {"q": request, "k": "a"}), (CANCEL, {"q": request})):
        with pytest.raises(Exception, match=REFUSED):
            await call(db, w.people[who], sql, **params)
    assert await one(db, "select status from mpi.erasure_requests where id = :q", q=request) == "requested"


async def test_a_second_open_request_for_the_same_patient_is_refused_and_a_cancel_reopens_the_door(db):
    w = await world(db)
    first = await call(db, w.people["fd1"], REQUEST, p=w.patient, r="entered_in_error", k="k-1")
    with pytest.raises(Exception, match="erasure_one_open_request_per_patient|duplicate key"):
        await call(db, w.people["fd2"], REQUEST, p=w.patient, r="entered_in_error", k="k-2")
    assert await call(db, w.people["fd2"], CANCEL, q=first) is True  # any front desk person of the clinic may cancel
    assert await call(db, w.people["fd2"], CANCEL, q=first) is False
    with pytest.raises(Exception, match=REFUSED):
        await call(db, w.people["fd2"], APPROVE, q=first, k="a")  # a cancelled request cannot be approved
    assert await call(db, w.people["fd2"], REQUEST, p=w.patient, r="entered_in_error", k="k-3") != first


async def test_a_patient_in_a_live_merge_link_cannot_be_erased(db):
    w = await world(db)
    other = _u()
    cand = _u()
    await raw(
        db,
        f"insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ('{other}', '{w.a}', '\\x01'::bytea, 1, '\\x02'::bytea, 1, 'test', 'er-{other}');\n"
        f"insert into mpi.match_candidates (id, clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, origin, idempotency_key) values ('{cand}', '{w.a}', '{other}', '{w.patient}', array['phone'], 'near_match', 'test', 'erc-{cand}');\n"
        f"insert into mpi.merges (clinic_id, master_patient_id, subordinate_patient_id, candidate_id, approved_by, origin, idempotency_key) values ('{w.a}', '{w.patient}', '{other}', '{cand}', '{w.people['fd1']}', 'test', 'erm-{cand}');",
    )
    request = await call(db, w.people["fd1"], REQUEST, p=w.patient, r="entered_in_error", k="k-1")
    with pytest.raises(Exception, match="live merge link"):
        await call(db, w.people["fd2"], APPROVE, q=request, k="a-1")


async def test_the_application_role_can_neither_write_the_ledger_nor_update_the_identity_columns_directly(db):
    w = await world(db)
    for statement in (
        f"insert into mpi.erasure_requests (clinic_id, patient_id, reason, requested_by, origin, idempotency_key) values ('{w.a}', '{w.patient}', 'x', '{w.people['fd1']}', 'test', 'direct')",
        f"update mpi.patients set erased_at = now(), name_ct = null where id = '{w.patient}'",
        f"update mpi.patients set phone_ct = null where id = '{w.patient}'",
    ):
        with pytest.raises(Exception, match="permission denied"):
            async with db.begin_nested():
                await db.exec_driver_sql("set local role careflow_app")
                await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(w.people["fd1"]), "role": "authenticated"})})
                await db.exec_driver_sql(statement)
    async with db.begin_nested():  # and a row lock still works (lock_version is the one updatable column)
        await db.exec_driver_sql("set local role careflow_app")
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(w.people["fd1"]), "role": "authenticated"})})
        await db.exec_driver_sql(f"select 1 from mpi.patients where id = '{w.patient}' for update")
        await db.exec_driver_sql("reset role")


async def test_nobody_but_the_database_can_write_an_audit_key_with_the_reserved_prefix(db):
    w = await world(db)
    for key in ("sys:erase:planted", "sys:"):
        with pytest.raises(Exception, match="row-level security"):
            async with db.begin_nested():
                await db.exec_driver_sql("set local role careflow_app")
                await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps({"sub": str(w.people["nurse"]), "role": "authenticated"})})
                await db.execute(
                    text("insert into audit.log (origin, clinic_id, actor_id, actor_role, action, idempotency_key) values ('test', :c, :u, 'nurse', 'test.action', :k)"),
                    {"c": w.a, "u": w.people["nurse"], "k": key},
                )
