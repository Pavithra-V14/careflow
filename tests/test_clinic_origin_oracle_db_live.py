"""Migration 0069 against the real database: ops.clinic_origin answers only a caller who belongs to the clinic, so clinic B's session cannot tell whether clinic A (or any clinic id) exists. Inside ONE
never-committed outer transaction, as the restricted `careflow_app` role.
"""

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
ASK = "select ops.clinic_origin(:c)"


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


async def ask(db, person, clinic):
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        claims = {} if person is None else {"sub": str(person), "role": "authenticated"}
        await db.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)})
        value = (await db.execute(text(ASK), {"c": clinic})).scalar()
        await db.exec_driver_sql("reset role")
        return value


async def test_only_members_of_a_clinic_learn_its_origin_and_everyone_else_gets_the_answer_for_a_clinic_that_does_not_exist(db):
    h = (await db.get_raw_connection()).driver_connection
    a, b = uuid.uuid4(), uuid.uuid4()
    people = {k: uuid.uuid4() for k in ("nurse_a", "worker_a", "nurse_b", "patient_a", "patient_b", "nobody")}
    await h.execute("insert into iam.clinics (id, name, origin) values ($1, 'OR-A', 'test'), ($2, 'OR-B', 'test')", a, b)
    for k in ("nurse_a", "worker_a", "nurse_b"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", people[k])
    for k in ("patient_a", "patient_b"):
        await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'patient', 'active', 'test')", people[k])
    await h.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", people["nobody"])
    await h.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $4, 'nurse', 'test'), ($2, $4, 'queue_worker', 'test'), ($3, $5, 'nurse', 'test')", people["nurse_a"], people["worker_a"], people["nurse_b"], a, b)
    for k, clinic in (("patient_a", a), ("patient_b", b)):
        await h.execute(
            "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, account_id, origin, idempotency_key) values (gen_random_uuid(), $1, '\x0102'::bytea, 1, '\x0304'::bytea, 1, $2, 'test', $3)",
            clinic, people[k], f"or-{people[k]}",
        )
    missing = uuid.uuid4()
    assert await ask(db, people["nurse_a"], a) == "test" and await ask(db, people["worker_a"], a) == "test" and await ask(db, people["patient_a"], a) == "test"  # members
    assert await ask(db, people["nurse_b"], a) is None and await ask(db, people["patient_b"], a) is None  # clinic B cannot see clinic A
    assert await ask(db, people["nobody"], a) is None  # staff with no role there
    assert await ask(db, people["nurse_b"], a) == await ask(db, people["nurse_b"], missing)  # the answer for A is the answer for a clinic that does not exist
    assert await ask(db, None, a) is None  # a session with no claims
    assert await ask(db, people["nurse_b"], b) == "test"  # and a member still learns their own
