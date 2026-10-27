"""Migration 0019, against the real hosted project: the queue worker registers patients (BUILD-SPEC.md §17.2, §12.1; CLAUDE.md rule 8).

One never-committed outer transaction; each test in its own savepoint. If 0019 is not applied yet it is executed inside it (after 0003 and 0008 if those are missing), so
the file is verified without being applied. The REAL ingestion pipeline runs as the restricted `careflow_app` role with the worker's verified claims; row-level security is in
the path. The three outcomes of §17.2 are confirmed for the worker: a confident match links, no match creates, a near-match creates AND queues a possible-duplicate, never a merge.
"""

import base64
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import mpi
from careflow.crypto import DemographicVault
from careflow.db import set_request_claims
from careflow.mpi import PatientIngestionPipeline
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
SQL = {n: (ROOT / "db" / "migrations" / f).read_text(encoding="utf-8") for n, f in (
    ("0003", "0003_mpi_match_candidates.sql"), ("0008", "0008_mpi_merge_ledger.sql"), ("0019", "0019_mpi_queue_worker_ingestion.sql"))}

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
NAME, DOB, PHONE = "Asha Venkatesh", "1987-04-23", "+91 98765 43210"


def _u() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(scope="module")
def pipeline():
    b = lambda: base64.b64encode(os.urandom(32)).decode()
    return PatientIngestionPipeline(DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": b(), "CAREFLOW_HMAC_SECRET_V1": b()}), key_version=1)


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
        if (await conn.execute(text("select to_regclass('mpi.match_candidates') is null"))).scalar_one():
            await raw(conn, SQL["0003"])
        if (await conn.execute(text("select to_regclass('mpi.merges') is null"))).scalar_one():
            await raw(conn, SQL["0008"])
        if (await conn.execute(text("select count(*) = 0 from pg_policies where schemaname = 'mpi' and policyname = 'patients_insert_worker'"))).scalar_one():
            await raw(conn, SQL["0019"])
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


async def seed(conn):
    a, b = _u(), _u()
    who = {"worker_a": (a, "queue_worker"), "fd_a": (a, "front_desk"), "nurse_a": (a, "nurse"), "worker_b": (b, "queue_worker"), "fd_b": (b, "front_desk")}
    ids = {k: _u() for k in who}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'Clinic A', 'test'), ('{b}', 'Clinic B', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{ids[k]}', 'staff', 'active', 'test')" for k in who)
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{ids[k]}', '{c}', '{r}', 'test')" for k, (c, r) in who.items()) + ";",
    )
    return a, b, ids


async def as_user(conn, uid):
    await conn.exec_driver_sql("set local role careflow_app")
    await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})


async def ingest(conn, pipeline, uid, clinic, **over):
    args = {"clinic_id": clinic, "name": NAME, "dob": DOB, "phone": PHONE, "origin": "test", "idempotency_key": f"idem-{_u()}", **over}
    async with conn.begin_nested():
        await as_user(conn, uid)
        try:
            return await pipeline.ingest(conn, **args)
        finally:
            await conn.exec_driver_sql("reset role")


async def count(conn, table, clinic):
    return (await conn.execute(text(f"select count(*) from {table} where clinic_id = :c"), {"c": clinic})).scalar_one()


async def attempt(conn, uid, sql, **params):
    """Run one statement as `uid` in a savepoint; return its rowcount, or the SQLSTATE of the failure. No `reset role` after an error: rolling the savepoint back
    undoes `set local role`, and a RESET inside an aborted transaction would replace the real error with 25P02."""
    try:
        async with conn.begin_nested():
            await as_user(conn, uid)
            rowcount = (await conn.execute(text(sql), params)).rowcount
            await conn.exec_driver_sql("reset role")
            return rowcount
    except DBAPIError as exc:
        return getattr(exc.orig, "sqlstate", None)


async def test_a_new_person_is_created_with_tokens_by_the_worker(db, pipeline):
    a, _, p = await seed(db)
    r = await ingest(db, pipeline, p["worker_a"], a)
    assert r.outcome == "created" and r.candidate_ids == ()
    assert await count(db, "mpi.patients", a) == 1 and await count(db, "mpi.identity_tokens", a) == 2 and await count(db, "mpi.match_candidates", a) == 0


async def test_a_confident_match_links_and_writes_nothing_new(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["worker_a"], a)
    again = await ingest(db, pipeline, p["worker_a"], a)
    assert (again.outcome, again.patient_id) == ("matched", first.patient_id)
    assert await count(db, "mpi.patients", a) == 1 and await count(db, "mpi.match_candidates", a) == 0


async def test_a_near_match_creates_a_new_record_and_queues_a_possible_duplicate_never_a_merge(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["worker_a"], a)
    near = await ingest(db, pipeline, p["worker_a"], a, phone="+91 91234 56789")
    assert near.outcome == "review" and near.patient_id != first.patient_id and near.candidate_ids == (first.patient_id,)
    assert await count(db, "mpi.patients", a) == 2 and await count(db, "mpi.match_candidates", a) == 1 and await count(db, "mpi.merges", a) == 0
    no_phone = await ingest(db, pipeline, p["worker_a"], a, phone=None)  # name and date of birth alone are never a confident match
    assert no_phone.outcome == "review" and await count(db, "mpi.merges", a) == 0


async def test_the_same_key_replays_and_a_key_for_a_different_person_is_a_conflict(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:doc-1")
    again = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:doc-1")
    assert (again.outcome, again.patient_id) == ("replayed", first.patient_id)
    with pytest.raises(mpi.IdempotencyConflict):
        await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:doc-1", name="Somebody Else")


async def test_the_worker_of_another_clinic_cannot_register_into_this_one_and_sees_none_of_its_rows(db, pipeline):
    a, _, p = await seed(db)
    await ingest(db, pipeline, p["worker_a"], a)
    with pytest.raises(mpi.NotPermitted):
        await ingest(db, pipeline, p["worker_b"], a)
    for table in ("mpi.patients", "mpi.identity_tokens", "mpi.match_candidates", "mpi.merges"):
        async with db.begin_nested():
            await as_user(db, p["worker_b"])
            assert (await db.execute(text(f"select count(*) from {table}"))).scalar_one() == 0, table
            await db.exec_driver_sql("reset role")


async def test_the_worker_cannot_update_or_delete_anything_in_mpi_and_cannot_write_merges_and_the_probes_act_on_real_rows(db, pipeline):
    a, b, p = await seed(db)
    first = await ingest(db, pipeline, p["worker_a"], a)
    near = await ingest(db, pipeline, p["worker_a"], a, phone="+91 91234 56789")  # a real possible-duplicate row exists
    assert near.candidate_ids == (first.patient_id,)
    fd, w = p["fd_a"], p["worker_a"]
    merge = (
        "insert into mpi.merges (clinic_id, master_patient_id, subordinate_patient_id, candidate_id, approved_by, origin, idempotency_key, status) "
        "values (:c, :m, :s, :cand, :u, 'test', :k, 'awaiting_second_approval')"
    )
    cand = (await db.execute(text("select id from mpi.match_candidates where patient_id = :p"), {"p": near.patient_id})).scalar_one()
    # merges stay human: the worker cannot propose one between two real, distinct patients. This runs BEFORE front desk's merge below on purpose: the BEFORE INSERT trigger
    # mpi.check_merge_links (flat links) fires before the row-level-security check, so once a live link exists between these two patients the same statement would fail with
    # 23514 (check_violation) instead of 42501. (Found by CI run 40512040646: the test, not the schema, was wrong.)
    assert await attempt(db, w, merge, c=a, m=near.patient_id, s=first.patient_id, cand=cand, u=w, k="m-worker") == "42501"
    assert await attempt(db, fd, merge, c=a, m=first.patient_id, s=near.patient_id, cand=cand, u=fd, k="m-fd") == 1  # a real pending merge, proposed by a person
    # positive controls: the same statements, as front desk, DO change rows, so a 0 for the worker means the policy filtered them
    assert await attempt(db, fd, "update mpi.patients set name_key_version = name_key_version where id = :i", i=first.patient_id) == 1
    assert await attempt(db, fd, "update mpi.match_candidates set status = status where clinic_id = :c", c=a) == 1
    # the worker: nothing is updatable (RLS filters the rows; there is no error)
    assert await attempt(db, w, "update mpi.patients set name_key_version = 2 where id = :i", i=first.patient_id) == 0
    assert await attempt(db, w, "update mpi.patients set erased_at = now() where id = :i", i=first.patient_id) == 0
    assert await attempt(db, w, "update mpi.identity_tokens set key_version = 2 where patient_id = :i", i=first.patient_id) == 0
    assert await attempt(db, w, "update mpi.match_candidates set status = 'resolved', resolved_at = now() where clinic_id = :c", c=a) == 0
    assert await attempt(db, w, "update mpi.merges set status = 'active' where clinic_id = :c", c=a) == 0
    # no DELETE privilege exists on any of them
    for table in ("mpi.patients", "mpi.identity_tokens", "mpi.match_candidates", "mpi.merges"):
        assert await attempt(db, w, f"delete from {table}") == "42501", table
    # the worker cannot write an already-resolved possible duplicate (that would close a human review)
    cand = (
        "insert into mpi.match_candidates (clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, status, resolved_at, origin, idempotency_key) "
        "values (:c, :p, :o, array['phone'], 'near_match', 'resolved', now(), 'test', 'cand-resolved')"
    )
    assert await attempt(db, w, cand, c=a, p=first.patient_id, o=near.patient_id) == "42501"
    # account_id stays out of reach of INSERT and UPDATE (0012)
    assert await attempt(db, w, "update mpi.patients set account_id = :u where id = :i", u=w, i=first.patient_id) == "42501"
    insert_bound = (
        "insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key, account_id) "
        "values (:c, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'bound-1', :u)"
    )
    assert await attempt(db, w, insert_bound, c=a, u=w) == "42501"
    # another clinic: a row with clinic B's id from clinic A's worker
    insert_b = (
        "insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        "values (:c, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'cross-1')"
    )
    assert await attempt(db, w, insert_b, c=b) == "42501"
    # no conflict-driven overwrite either: ON CONFLICT DO UPDATE needs an UPDATE policy the worker lacks
    upsert = (
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        "values (:i, :c, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'upsert-1') on conflict (id) do update set name_key_version = 9"
    )
    assert await attempt(db, w, upsert, i=first.patient_id, c=a) == "42501"


async def test_the_other_roles_are_unchanged_nurse_cannot_register_front_desk_still_can(db, pipeline):
    a, _, p = await seed(db)
    with pytest.raises(mpi.NotPermitted):
        await ingest(db, pipeline, p["nurse_a"], a)
    assert (await ingest(db, pipeline, p["fd_a"], a)).outcome == "created"
    async with db.begin_nested():
        await as_user(db, p["worker_a"])
        assert (await db.execute(text("select count(*) from mpi.patients where clinic_id = :c"), {"c": a})).scalar_one() == 1  # the worker reads its clinic's one patient (needed to match)
        await db.exec_driver_sql("reset role")
