"""MPI ingestion against the real hosted project (careflow/mpi.py; BUILD-SPEC.md §17.2; CLAUDE.md rule 8).

Everything runs through the real pipeline, as the restricted `careflow_app` role with verified claims
set per transaction, over SQLAlchemy Core async. Two kinds of test:

* Rolled-back tests share one connection and one never-committed outer transaction; each test runs in
  its own savepoint. Migration 0003 is executed inside that outer transaction if it is not applied yet,
  so the file is verified without being applied.
* Committed tests (end to end over the transaction-mode pooler, with concurrent writers) need 0003
  actually applied; they skip with a reason before that, commit a small fixture tagged origin='test',
  and delete it in teardown.
"""

import asyncio
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow import mpi
from careflow.crypto import DemographicVault
from careflow.db import make_app_engine, set_request_claims
from careflow.mpi import PatientIngestionPipeline

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
APP = os.environ.get("DATABASE_URL_APP") or _ENV.get("DATABASE_URL_APP")
SQL_0003 = (ROOT / "db" / "migrations" / "0003_mpi_match_candidates.sql").read_text(encoding="utf-8")
SQL_0008 = (ROOT / "db" / "migrations" / "0008_mpi_merge_ledger.sql").read_text(encoding="utf-8")

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

NAME, DOB, PHONE = "Asha Venkatesh", "1987-04-23", "+91 98765 43210"


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _key() -> str:
    return f"idem-{_u()}"


@pytest.fixture(scope="module")
def vault():
    import base64

    b = lambda: base64.b64encode(os.urandom(32)).decode()
    return DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": b(), "CAREFLOW_HMAC_SECRET_V1": b()})


@pytest.fixture(scope="module")
def pipeline(vault):
    return PatientIngestionPipeline(vault, key_version=1)


async def raw(conn, sql):
    """Multi-statement script on the same connection and transaction (SQLAlchemy would prepare it)."""
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    url = make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url, connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await conn.exec_driver_sql("grant careflow_app to current_user with set true")
        if (await conn.execute(text("select to_regclass('mpi.match_candidates') is null"))).scalar_one():
            await raw(conn, SQL_0003)
        # Matching now routes a merged subordinate to its master, so it reads mpi.merges (migration 0008).
        if (await conn.execute(text("select to_regclass('mpi.merges') is null"))).scalar_one():
            await raw(conn, SQL_0008)
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
    """Clinic A (front desk, nurse, auditor) and clinic B (front desk)."""
    a, b = _u(), _u()
    who = {"fd_a": (a, "front_desk"), "nurse_a": (a, "nurse"), "auditor_a": (a, "auditor"), "fd_b": (b, "front_desk")}
    ids = {k: _u() for k in who}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{a}', 'Clinic A', 'test'), ('{b}', 'Clinic B', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values "
        + ", ".join(f"('{ids[k]}', 'staff', 'active', 'test')" for k in who)
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{ids[k]}', '{c}', '{r}', 'test')" for k, (c, r) in who.items())
        + ";",
    )
    return a, b, ids


async def ingest(conn, pipeline, uid, **kwargs):
    """Run the pipeline exactly as a request would: app role, claims set, one transaction (a savepoint here)."""
    kwargs.setdefault("origin", "test")
    kwargs.setdefault("idempotency_key", _key())
    async with conn.begin_nested():
        await conn.exec_driver_sql("set local role careflow_app")
        if uid is not None:
            await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        else:  # transaction-local claims must not leak the previous caller's identity into this call
            await conn.execute(text("select set_config('request.jwt.claims', '', true)"))
        try:
            return await pipeline.ingest(conn, **kwargs)
        finally:
            await conn.exec_driver_sql("reset role")


def person(clinic, **over):
    return {"clinic_id": clinic, "name": NAME, "dob": DOB, "phone": PHONE, **over}


async def q(conn, sql, **params):
    return (await conn.execute(text(sql), params)).all()


async def count(conn, table, clinic=None):
    sql = f"select count(*) from {table}" + (" where clinic_id = :c" if clinic else "")
    return (await q(conn, sql, **({"c": clinic} if clinic else {})))[0][0]


# ---- no match -> create ----------------------------------------------------------------------------


async def test_new_person_is_created_encrypted_with_tokens(db, pipeline, vault):
    a, _, p = await seed(db)
    r = await ingest(db, pipeline, p["fd_a"], **person(a))
    assert (r.outcome, r.candidate_ids) == ("created", ())
    (row,) = await q(db, "select * from mpi.patients where id = :i", i=r.patient_id)
    m = row._mapping
    assert (m["clinic_id"], m["origin"], m["erased_at"]) == (a, "test", None)
    assert (m["name_key_version"], m["dob_key_version"], m["phone_key_version"]) == (1, 1, 1)
    assert vault.decrypt(bytes(m["name_ct"]), field="name", key_version=1) == NAME
    assert vault.decrypt(bytes(m["dob_ct"]), field="dob", key_version=1) == DOB
    assert vault.decrypt(bytes(m["phone_ct"]), field="phone", key_version=1) == "+919876543210"  # canonicalised first
    kinds = await q(db, "select kind, key_version from mpi.identity_tokens where patient_id = :i order by kind", i=r.patient_id)
    assert [tuple(k) for k in kinds] == [("name_dob", 1), ("phone", 1)]


async def test_no_plaintext_demographic_reaches_the_database(db, pipeline):
    a, _, p = await seed(db)
    r = await ingest(db, pipeline, p["fd_a"], **person(a))
    (hit,) = await q(
        db,
        """select position(convert_to('Asha', 'utf8') in name_ct) > 0 as n, position(convert_to('1987', 'utf8') in dob_ct) > 0 as d,
                  position(convert_to('98765', 'utf8') in phone_ct) > 0 as p
           from mpi.patients where id = :i""",
        i=r.patient_id,
    )
    assert tuple(hit) == (False, False, False)


async def test_absent_phone_is_a_database_null_not_an_empty_value(db, pipeline):
    a, _, p = await seed(db)
    r = await ingest(db, pipeline, p["fd_a"], **person(a, phone=None))
    (row,) = await q(db, "select phone_ct, phone_key_version from mpi.patients where id = :i", i=r.patient_id)
    assert tuple(row) == (None, None)
    kinds = await q(db, "select kind from mpi.identity_tokens where patient_id = :i", i=r.patient_id)
    assert [k[0] for k in kinds] == ["name_dob"]  # no phone token is invented


# ---- confident match -> link -----------------------------------------------------------------------


async def test_duplicate_submission_returns_the_existing_record_and_writes_nothing(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["fd_a"], **person(a))
    before = (await count(db, "mpi.patients"), await count(db, "mpi.identity_tokens"), await count(db, "mpi.match_candidates"))
    again = await ingest(db, pipeline, p["fd_a"], **person(a, name="  asha   VENKATESH ", phone="098765-43210"))
    assert (again.outcome, again.patient_id, again.candidate_ids) == ("matched", first.patient_id, ())
    assert (await count(db, "mpi.patients"), await count(db, "mpi.identity_tokens"), await count(db, "mpi.match_candidates")) == before


# ---- near match -> new record + human review, never a merge ----------------------------------------


async def test_same_phone_different_person_creates_a_new_record_and_queues_review(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["fd_a"], **person(a))
    snapshot = await q(db, "select name_ct, dob_ct, phone_ct from mpi.patients where id = :i", i=first.patient_id)
    r = await ingest(db, pipeline, p["fd_a"], **person(a, name="Ravi Kumar", dob="1990-01-15"))
    assert r.outcome == "review" and r.patient_id != first.patient_id
    assert r.candidate_ids == (first.patient_id,)
    (cand,) = await q(db, "select * from mpi.match_candidates where patient_id = :i", i=r.patient_id)
    m = cand._mapping
    assert (m["candidate_patient_id"], m["matched_kinds"], m["reason"], m["status"]) == (
        first.patient_id, ["phone"], "near_match", "pending",
    )
    assert await count(db, "mpi.patients") == 2  # nothing merged
    assert await q(db, "select name_ct, dob_ct, phone_ct from mpi.patients where id = :i", i=first.patient_id) == snapshot


async def test_same_name_and_dob_with_a_different_phone_is_a_near_match(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["fd_a"], **person(a))
    r = await ingest(db, pipeline, p["fd_a"], **person(a, phone="+91 91234 56789"))
    assert (r.outcome, r.candidate_ids) == ("review", (first.patient_id,))
    (kinds,) = await q(db, "select matched_kinds from mpi.match_candidates where patient_id = :i", i=r.patient_id)
    assert kinds[0] == ["name_dob"]


async def test_name_and_dob_with_no_phone_supplied_is_never_a_confident_match(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["fd_a"], **person(a))
    r = await ingest(db, pipeline, p["fd_a"], **person(a, phone=None))
    assert (r.outcome, r.candidate_ids) == ("review", (first.patient_id,))


async def _clone(conn, patient_id):
    """A second existing record identical to the first (as the migrator), to create an ambiguous situation."""
    key = _key()
    (new,) = await q(
        conn,
        """insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version,
                                     origin, idempotency_key)
           select clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, :k
           from mpi.patients where id = :p returning id""",
        k=key, p=patient_id,
    )
    await conn.execute(
        text(
            """insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin)
               select :n, clinic_id, kind, token, key_version, origin from mpi.identity_tokens where patient_id = :p"""
        ),
        {"n": new[0], "p": patient_id},
    )
    return new[0]


async def test_two_existing_confident_records_are_ambiguous_and_nothing_is_linked(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["fd_a"], **person(a))
    second_id = await _clone(db, first.patient_id)
    r = await ingest(db, pipeline, p["fd_a"], **person(a))
    assert r.outcome == "review" and r.patient_id not in (first.patient_id, second_id)
    assert set(r.candidate_ids) == {first.patient_id, second_id}
    reasons = await q(db, "select reason from mpi.match_candidates where patient_id = :i", i=r.patient_id)
    assert {x[0] for x in reasons} == {"ambiguous"}


# ---- idempotency -----------------------------------------------------------------------------------


async def test_retry_with_the_same_key_replays_the_original_result(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["fd_a"], **person(a))
    key = _key()
    created = await ingest(db, pipeline, p["fd_a"], idempotency_key=key, **person(a, name="Ravi Kumar", dob="1990-01-15"))
    assert created.outcome == "review"
    replay = await ingest(db, pipeline, p["fd_a"], idempotency_key=key, **person(a, name="Ravi Kumar", dob="1990-01-15"))
    assert (replay.outcome, replay.patient_id, replay.candidate_ids) == ("replayed", created.patient_id, (first.patient_id,))
    assert await count(db, "mpi.patients") == 2 and await count(db, "mpi.match_candidates") == 1


async def test_reusing_a_key_for_a_different_person_is_a_conflict_not_a_replay(db, pipeline):
    a, _, p = await seed(db)
    key = _key()
    await ingest(db, pipeline, p["fd_a"], idempotency_key=key, **person(a))
    with pytest.raises(mpi.IdempotencyConflict):
        await ingest(db, pipeline, p["fd_a"], idempotency_key=key, **person(a, name="Someone Else", phone="+91 91234 56789"))


async def test_a_key_already_used_in_another_clinic_is_a_conflict(db, pipeline):
    a, b, p = await seed(db)
    key = _key()
    await ingest(db, pipeline, p["fd_a"], idempotency_key=key, **person(a))
    with pytest.raises(mpi.IdempotencyConflict):
        await ingest(db, pipeline, p["fd_b"], idempotency_key=key, **person(b))


# ---- isolation -------------------------------------------------------------------------------------


async def test_the_same_person_in_another_clinic_is_a_separate_record_with_no_cross_clinic_review(db, pipeline):
    a, b, p = await seed(db)
    in_a = await ingest(db, pipeline, p["fd_a"], **person(a))
    in_b = await ingest(db, pipeline, p["fd_b"], **person(b))
    assert (in_b.outcome, in_b.candidate_ids) == ("created", ())
    assert in_a.patient_id != in_b.patient_id
    assert await count(db, "mpi.match_candidates") == 0
    # tokens are keyed per clinic row, and a clinic-B front desk cannot see clinic A's records at all
    async with db.begin_nested():
        await db.exec_driver_sql("set local role careflow_app")
        await set_request_claims(db, {"sub": str(p["fd_b"]), "role": "authenticated"})
        seen = [r[0] for r in await q(db, "select id from mpi.patients")]
        await db.exec_driver_sql("reset role")
    assert seen == [in_b.patient_id]


async def test_front_desk_cannot_register_into_another_clinic(db, pipeline):
    _, b, p = await seed(db)
    with pytest.raises(mpi.NotPermitted):
        await ingest(db, pipeline, p["fd_a"], **person(b))
    assert await count(db, "mpi.patients") == 0


@pytest.mark.parametrize("who", ["nurse_a", "auditor_a", None])
async def test_only_front_desk_can_register_patients(db, pipeline, who):
    a, _, p = await seed(db)
    with pytest.raises(mpi.NotPermitted):
        await ingest(db, pipeline, p[who] if who else None, **person(a))
    assert await count(db, "mpi.patients") == 0


async def test_a_record_from_another_origin_never_matches(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["fd_a"], origin="test", **person(a))
    r = await ingest(db, pipeline, p["fd_a"], origin="eval", **person(a))
    assert (r.outcome, r.candidate_ids) == ("created", ())
    assert r.patient_id != first.patient_id


async def test_erased_records_do_not_match(db, pipeline):
    a, _, p = await seed(db)
    first = await ingest(db, pipeline, p["fd_a"], **person(a))
    await db.execute(
        text("update mpi.patients set erased_at = now(), name_ct = null, name_key_version = null, dob_ct = null, "
             "dob_key_version = null, phone_ct = null, phone_key_version = null where id = :i"),
        {"i": first.patient_id},
    )
    r = await ingest(db, pipeline, p["fd_a"], **person(a))
    assert (r.outcome, r.candidate_ids) == ("created", ())


# ---- committed: the real transaction-mode pooler path and concurrent duplicates --------------------


@pytest_asyncio.fixture(loop_scope="module")
async def committed():
    if not APP:
        pytest.skip("DATABASE_URL_APP not configured")
    url = make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg")
    admin = create_async_engine(url, connect_args={"statement_cache_size": 0})
    async with admin.connect() as c:
        absent = (await c.execute(text("select to_regclass('mpi.match_candidates') is null"))).scalar_one()
    if absent:
        await admin.dispose()
        pytest.skip("migration 0003 is not applied yet; the pooler/concurrency tests need committed tables")
    clinic, acct = _u(), _u()
    async with admin.begin() as c:
        await c.execute(text("insert into iam.clinics (id, name, origin) values (:c, 'MPI Concurrency', 'test')"), {"c": clinic})
        await c.execute(text("insert into iam.accounts (id, account_type, status, origin) values (:a, 'staff', 'active', 'test')"), {"a": acct})
        await c.execute(
            text("insert into iam.staff_roles (account_id, clinic_id, role, origin) values (:a, :c, 'front_desk', 'test')"),
            {"a": acct, "c": clinic},
        )
    try:
        yield clinic, acct
    finally:
        async with admin.begin() as c:
            for t in ("mpi.match_candidates", "mpi.identity_tokens", "mpi.patients"):
                await c.execute(text(f"delete from {t} where clinic_id = :c"), {"c": clinic})
            await c.execute(text("delete from iam.staff_roles where clinic_id = :c"), {"c": clinic})
            await c.execute(text("delete from iam.accounts where id = :a"), {"a": acct})
            await c.execute(text("delete from iam.clinics where id = :c"), {"c": clinic})
        await admin.dispose()


async def _pooled_ingest(engine, pipeline, acct, **kwargs):
    async with engine.connect() as conn, conn.begin():
        await set_request_claims(conn, {"sub": str(acct), "role": "authenticated"})
        return await pipeline.ingest(conn, **kwargs)


async def test_pooler_end_to_end_and_concurrent_identical_requests_make_exactly_one_record(committed, pipeline):
    clinic, acct = committed
    engine = make_app_engine(APP or "", pool_size=6, max_overflow=0)
    try:
        results = await asyncio.gather(
            *(
                _pooled_ingest(engine, pipeline, acct, origin="test", idempotency_key=_key(), **person(clinic))
                for _ in range(6)
            )
        )
    finally:
        await engine.dispose()
    outcomes = sorted(r.outcome for r in results)
    assert outcomes == ["created"] + ["matched"] * 5, outcomes
    assert len({r.patient_id for r in results}) == 1
    admin = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    try:
        async with admin.connect() as c:
            n = (await c.execute(text("select count(*) from mpi.patients where clinic_id = :c"), {"c": clinic})).scalar_one()
            cands = (await c.execute(text("select count(*) from mpi.match_candidates where clinic_id = :c"), {"c": clinic})).scalar_one()
    finally:
        await admin.dispose()
    assert (n, cands) == (1, 0)
