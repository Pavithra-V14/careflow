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
    ("0003", "0003_mpi_match_candidates.sql"), ("0008", "0008_mpi_merge_ledger.sql"), ("0019", "0019_mpi_queue_worker_ingestion.sql"), ("0022", "0022_worker_registration_function.sql"),
    ("0023", "0023_unverified_worker_records.sql"), ("0024", "0024_worker_role_exclusive.sql"))}

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
NAME, DOB, PHONE = "Asha Venkatesh", "1987-04-23", "+91 98765 43210"


def _u() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(scope="module")
def vault():
    b = lambda: base64.b64encode(os.urandom(32)).decode()
    return DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": b(), "CAREFLOW_HMAC_SECRET_V1": b()})


@pytest.fixture(scope="module")
def pipeline(vault):
    """The Front Door's ingestor: the unattended worker registers ONLY through mpi.register_patient_from_referral (migration 0022)."""
    return PatientIngestionPipeline(vault, key_version=1, registrar="function")


@pytest.fixture(scope="module")
def direct_pipeline(vault):
    """The front-desk route's ingestor: direct inserts under front desk's own policies (unchanged)."""
    return PatientIngestionPipeline(vault, key_version=1)


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
        if (await conn.execute(text("select count(*) = 0 from pg_policies where schemaname = 'mpi' and policyname = 'patients_select_worker'"))).scalar_one():
            await raw(conn, SQL["0019"])
        if (await conn.execute(text("select to_regprocedure('mpi.register_patient_from_referral(uuid,text,bytea,smallint,bytea,smallint,bytea,smallint,text,text[],bytea[],smallint,jsonb)') is null"))).scalar_one():
            await raw(conn, SQL["0022"])
        if (await conn.execute(text("select count(*) = 0 from information_schema.columns where table_schema = 'mpi' and table_name = 'patients' and column_name = 'registered_via'"))).scalar_one():
            await raw(conn, SQL["0023"])
        if (await conn.execute(text("select count(*) = 0 from pg_trigger where tgname = 'staff_roles_queue_worker_exclusive'"))).scalar_one():
            await raw(conn, SQL["0024"])
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


async def test_a_confident_match_links_and_writes_nothing_new(db, pipeline, direct_pipeline):
    a, _, p = await seed(db)
    # The record a worker links to must be VERIFIED (migration 0023): here front desk registered it. A record the worker itself created is unverified, so the same person's second
    # referral goes to a person instead (test_a_fully_agreeing_record_created_by_front_desk_still_matches_and_a_confirmed_worker_record_matches_after_confirmation).
    first = await ingest(db, direct_pipeline, p["fd_a"], a)
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
    # the worker cannot write an already-resolved possible duplicate (that would close a human review). Since 0022 it has no INSERT policy at all, so this is refused for that
    # reason (the function forces status 'pending'); only front desk resolves a review.
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


async def test_the_other_roles_are_unchanged_nurse_cannot_register_front_desk_still_can(db, pipeline, direct_pipeline):
    a, _, p = await seed(db)
    with pytest.raises(mpi.NotPermitted):
        await ingest(db, pipeline, p["nurse_a"], a)
    with pytest.raises(mpi.NotPermitted):
        await ingest(db, direct_pipeline, p["nurse_a"], a)
    assert (await ingest(db, direct_pipeline, p["fd_a"], a)).outcome == "created"
    async with db.begin_nested():
        await as_user(db, p["worker_a"])
        assert (await db.execute(text("select count(*) from mpi.patients where clinic_id = :c"), {"c": a})).scalar_one() == 1  # the worker reads its clinic's one patient (needed to match)
        await db.exec_driver_sql("reset role")


# ---- migration 0022: the attack of pre-launch blocker 1 does not work any more ---------------------------------------------------------------------

FUNCTION = "mpi.register_patient_from_referral"
FUNCTION_SIG = "mpi.register_patient_from_referral(uuid,text,bytea,smallint,bytea,smallint,bytea,smallint,text,text[],bytea[],smallint,jsonb)"


def call_sql(kinds="array['name_dob']", tokens="array['\\x0102'::bytea]"):
    return (
        f"select {FUNCTION}(cast(:c as uuid), :o, '\\x01'::bytea, cast(1 as smallint), '\\x02'::bytea, cast(1 as smallint), null, null, :k, "
        f"{kinds}, {tokens}, cast(1 as smallint), cast(:cands as jsonb))"
    )


async def fn(conn, uid, clinic, *, cands="[]", origin="test", key=None, **sql_over):
    """Call the function as `uid`; return the new patient id or the SQLSTATE of the refusal."""
    sql = call_sql(**sql_over)
    try:
        async with conn.begin_nested():
            await as_user(conn, uid)
            value = (await conn.execute(text(sql), {"c": str(clinic), "cands": cands, "k": key or f"fn-{_u()}", "o": origin})).scalar_one()
            await conn.exec_driver_sql("reset role")
            return value
    except DBAPIError as exc:
        return getattr(exc.orig, "sqlstate", None)


def candidate(patient, kinds='["name_dob"]', reason="near_match"):
    return f'{{"patient_id": "{patient}", "kinds": {kinds}, "reason": "{reason}"}}'


async def test_the_worker_cannot_insert_into_the_three_tables_directly_but_front_desk_still_can(db, direct_pipeline):
    a, _, p = await seed(db)
    victim = (await ingest(db, direct_pipeline, p["fd_a"], a)).patient_id
    other = (await ingest(db, direct_pipeline, p["fd_a"], a, name="Meera Nair", phone="+91 90000 00001")).patient_id
    w, fd = p["worker_a"], p["fd_a"]
    attach = "insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin) values (:v, :c, :kind, '\\x0a0b'::bytea, 1, 'test')"
    forge = (
        "insert into mpi.match_candidates (clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, origin, idempotency_key) "
        "values (:c, :v, :o, array['phone'], 'near_match', 'test', :k)"
    )
    new_patient = (
        "insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        "values (:c, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', :k)"
    )
    # positive controls: the SAME statements work for front desk (a person's route), so a 42501 for the worker is the missing policy and not a malformed statement
    assert await attempt(db, fd, attach, v=victim, c=a, kind="email") == 1
    assert await attempt(db, fd, forge, c=a, v=victim, o=other, k="forge-fd") == 1
    assert await attempt(db, fd, new_patient, c=a, k="np-fd") == 1
    # the attack of blocker 1, as the worker: attach a token to an existing patient, forge a possible duplicate between two existing patients, insert a patient of its own
    before = (await count(db, "mpi.identity_tokens", a), await count(db, "mpi.match_candidates", a), await count(db, "mpi.patients", a))
    assert await attempt(db, w, attach, v=victim, c=a, kind="sms") == "42501"
    assert await attempt(db, w, forge, c=a, v=other, o=victim, k="forge-w") == "42501"
    assert await attempt(db, w, new_patient, c=a, k="np-w") == "42501"
    assert (await count(db, "mpi.identity_tokens", a), await count(db, "mpi.match_candidates", a), await count(db, "mpi.patients", a)) == before


async def test_the_function_has_no_parameter_that_names_an_existing_patient_and_is_a_pinned_security_definer(db):
    args = (await db.execute(text("select pg_get_function_arguments(to_regprocedure(:s))"), {"s": FUNCTION_SIG})).scalar_one()
    names = [part.split()[0] for part in args.split(",") if part.strip()]
    assert names == [
        "p_clinic", "p_origin", "p_name_ct", "p_name_kv", "p_dob_ct", "p_dob_kv", "p_phone_ct", "p_phone_kv", "p_idempotency_key", "p_token_kinds", "p_tokens", "p_token_kv", "p_candidates",
    ], names  # no patient id and no row id: there is nothing a token could be attached to
    row = (await db.execute(text("select prosecdef, proconfig from pg_proc where oid = to_regprocedure(:s)"), {"s": FUNCTION_SIG})).one()
    assert row.prosecdef is True and list(row.proconfig) == ["search_path=\"\""], row
    acl = (await db.execute(text("select proacl::text from pg_proc where oid = to_regprocedure(:s)"), {"s": FUNCTION_SIG})).scalar_one()
    grantees = {entry.split("=")[0] for entry in acl.strip("{}").split(",")}
    assert "careflow_app" in grantees and "" not in grantees and not grantees & {"anon", "authenticated", "service_role"}, acl  # EXECUTE for careflow_app; none for PUBLIC or the API roles


async def test_a_new_record_with_a_victims_token_leaves_the_victims_tokens_untouched_and_candidates_always_name_the_new_patient(db, direct_pipeline):
    a, _, p = await seed(db)
    victim = await ingest(db, direct_pipeline, p["fd_a"], a)
    stolen = (await db.execute(text("select kind, token from mpi.identity_tokens where patient_id = :v and kind = 'name_dob'"), {"v": victim.patient_id})).one()
    victim_tokens = (await db.execute(text("select kind, token from mpi.identity_tokens where patient_id = :v order by kind"), {"v": victim.patient_id})).all()
    # what the function still permits: a NEW record that carries the victim's token. It gets its own row; nothing of the victim's is moved or duplicated onto it
    fresh = await fn(db, p["worker_a"], a, kinds="array['name_dob']", tokens=f"array['\\x{bytes(stolen.token).hex()}'::bytea]", cands=f"[{candidate(victim.patient_id)}]")
    assert isinstance(fresh, uuid.UUID) and fresh != victim.patient_id
    assert (await db.execute(text("select kind, token from mpi.identity_tokens where patient_id = :v order by kind"), {"v": victim.patient_id})).all() == victim_tokens
    assert (await db.execute(text("select patient_id from mpi.identity_tokens where patient_id <> :v and clinic_id = :c"), {"v": victim.patient_id, "c": a})).all() == [(fresh,)]
    pairs = (await db.execute(text("select patient_id, candidate_patient_id, status from mpi.match_candidates where clinic_id = :c"), {"c": a})).all()
    assert pairs == [(fresh, victim.patient_id, "pending")]  # the patient side is the NEW record, pending: never two existing patients, never already resolved


async def test_a_replay_with_the_same_key_creates_nothing_more(db, pipeline):
    a, _, p = await seed(db)
    first = await fn(db, p["worker_a"], a, key="referral:doc-77")
    assert isinstance(first, uuid.UUID)
    snapshot = (await count(db, "mpi.patients", a), await count(db, "mpi.identity_tokens", a))
    assert await fn(db, p["worker_a"], a, key="referral:doc-77") == "23505"  # the database refuses a second record under the key
    assert (await count(db, "mpi.patients", a), await count(db, "mpi.identity_tokens", a)) == snapshot
    created = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:doc-88")
    replay = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:doc-88")  # the pipeline answers a replay before it ever calls the function
    assert (replay.outcome, replay.patient_id) == ("replayed", created.patient_id) and await count(db, "mpi.patients", a) == snapshot[0] + 1


async def test_only_a_worker_of_the_clinic_can_call_the_function(db):
    a, b, p = await seed(db)
    assert isinstance(await fn(db, p["worker_a"], a), uuid.UUID)  # positive control: the right caller works with these exact arguments
    assert isinstance(await fn(db, p["worker_b"], b), uuid.UUID)
    for who in ("fd_a", "nurse_a", "fd_b"):
        assert await fn(db, p[who], a) == "42501", who
    assert await fn(db, p["worker_b"], a) == "42501"  # a worker of ANOTHER clinic
    assert await fn(db, _u(), a) == "42501"  # an account with no role at all
    try:
        async with db.begin_nested():  # no verified claims at all
            await db.exec_driver_sql("set local role careflow_app")
            await set_request_claims(db, {"role": "anon"})
            await db.execute(text(call_sql()), {"c": str(a), "cands": "[]", "k": "anon-1", "o": "test"})
        raise AssertionError("an unauthenticated call was accepted")
    except DBAPIError as exc:
        assert exc.orig.sqlstate == "42501"


async def test_the_function_refuses_bad_candidates_and_bad_shapes(db, direct_pipeline):
    a, b, p = await seed(db)
    own = (await ingest(db, direct_pipeline, p["fd_a"], a)).patient_id
    foreign = (await ingest(db, direct_pipeline, p["fd_b"], b)).patient_id
    w = p["worker_a"]
    assert isinstance(await fn(db, w, a, cands=f"[{candidate(own)}]"), uuid.UUID)  # positive control for every refusal below
    assert await fn(db, w, a, cands=f"[{candidate(foreign)}]") == "22023"  # a patient of another clinic: a bad argument (22023), never reported as an authorisation failure (42501)
    assert await fn(db, w, a, cands=f"[{candidate(_u())}]") == "22023"  # a patient that does not exist
    assert await fn(db, w, a, cands=f"[{candidate(own)}]", origin="eval") == "22023"  # the candidate is an origin='test' record, the new one would be 'eval'
    EMAIL_KIND, REPEATED_KIND = '["email"]', '["phone", "phone"]'
    bad_shapes = {
        "an unknown reason": f"[{candidate(own, reason='resolved')}]",
        "no kinds": f"[{candidate(own, kinds='[]')}]",
        "not an array": candidate(own),
        "an element that is not an object": '["x"]',
        "more than twenty": "[" + ",".join([candidate(own)] * 21) + "]",
        "the same patient twice": f"[{candidate(own)},{candidate(own)}]",
        "a kind outside the closed set": f"[{candidate(own, kinds=EMAIL_KIND)}]",
        "a null kind": f"[{candidate(own, kinds='[null]')}]",
        "a repeated kind": f"[{candidate(own, kinds=REPEATED_KIND)}]",
        "a patient_id that is not a uuid": '[{"patient_id": "not-a-uuid", "kinds": ["phone"], "reason": "near_match"}]',
    }
    for label, cands in bad_shapes.items():
        assert await fn(db, w, a, cands=cands) == "22023", label
    assert await fn(db, w, a, kinds="array['name_dob','phone','email']", tokens="array['\\x01'::bytea,'\\x02'::bytea,'\\x03'::bytea]") == "22023"  # three tokens
    assert await fn(db, w, a, kinds="array['name_dob','name_dob']", tokens="array['\\x01'::bytea,'\\x02'::bytea]") == "22023"  # duplicate kind
    assert await fn(db, w, a, kinds="array['email']") == "22023"  # a kind outside the closed set
    assert await fn(db, w, a, origin="prod") == "22023"
    assert await fn(db, w, a, key="   ") == "22023"


async def test_the_migrating_role_bypasses_rls_which_the_definer_function_relies_on(db):
    # The function writes tables with FORCE ROW LEVEL SECURITY as its owner (the migrating role). If that role did not bypass RLS the function would fail closed with 42501 and look
    # like an authorisation failure, so assert the assumption directly (the same check as tests/test_rls_msg_live.py for msg.record_screening).
    owner = (await db.execute(text("select pg_get_userbyid(proowner) from pg_proc where oid = to_regprocedure(:s)"), {"s": FUNCTION_SIG})).scalar_one()
    assert (await db.execute(text("select rolbypassrls or rolsuper from pg_roles where rolname = :r"), {"r": owner})).scalar_one() is True, owner


# ---- migration 0023: worker-created records are unverified; the planted-token attack lands in review ------------------------------------------------


async def owner_sqlstate(conn, sql, **params):
    """Run one statement as the (owner) test connection in a savepoint: the SQLSTATE of the refusal, or 'ok'."""
    try:
        async with conn.begin_nested():
            await conn.execute(text(sql), params)
        return "ok"
    except DBAPIError as exc:
        return getattr(exc.orig, "sqlstate", None)


async def registered_via(conn, patient):
    return (await conn.execute(text("select registered_via from mpi.patients where id = :p"), {"p": patient})).scalar_one()


async def confirm_as(conn, uid, clinic, patient, *, confirmed_by=None, key=None):
    return await attempt(
        conn, uid,
        "insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key) values (:c, :p, :u, 'test', :k)",
        c=clinic, p=patient, u=confirmed_by or uid, k=key or f"conf-{_u()}",
    )


async def test_the_function_marks_the_record_unverified_and_front_desk_records_are_front_desk_and_nobody_can_change_the_flag(db, pipeline, direct_pipeline):
    a, _, p = await seed(db)
    worker_made = (await ingest(db, pipeline, p["worker_a"], a)).patient_id
    desk_made = (await ingest(db, direct_pipeline, p["fd_a"], a, name="Meera Nair", phone="+91 90000 00001")).patient_id
    assert await registered_via(db, worker_made) == "referral_worker" and await registered_via(db, desk_made) == "front_desk"
    # the application role has no column privilege on it, and the worker cannot set it in a direct insert either
    for priv in ("INSERT", "UPDATE"):
        assert (await db.execute(text("select has_column_privilege('careflow_app', 'mpi.patients', 'registered_via', :p)"), {"p": priv})).scalar_one() is False, priv
    for who in ("worker_a", "fd_a"):
        assert await attempt(db, p[who], "update mpi.patients set registered_via = 'front_desk' where id = :i", i=worker_made) in ("42501", 0), who  # denied, or no row visible to change
    assert await registered_via(db, worker_made) == "referral_worker"


async def test_the_planted_token_attack_lands_in_review_and_never_links(db, pipeline, vault):
    a, _, p = await seed(db)
    victim = {"name": "Kavya Menon", "dob": "1991-02-14", "phone": "+91 98111 22233"}
    # the attacker (a worker) computes the real person's tokens and plants a junk record carrying them BEFORE the person is registered
    tokens = mpi.derive_tokens(vault, name=victim["name"], dob_iso=victim["dob"], phone_canonical=mpi.canonicalize_phone(victim["phone"]), key_version=1)
    kinds = list(tokens)
    token_sql = "array[" + ", ".join(f"'\\x{tokens[k].hex()}'::bytea" for k in kinds) + "]"
    planted = await fn(db, p["worker_a"], a, kinds="array[" + ", ".join(f"'{k}'" for k in kinds) + "]", tokens=token_sql)
    assert isinstance(planted, uuid.UUID) and await registered_via(db, planted) == "referral_worker"
    # the person's real referral arrives (through the worker's pipeline): it must NOT be linked to the planted record
    real = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:real-1", **victim)
    assert real.outcome == "review" and real.outcome != "matched"
    assert real.patient_id != planted and real.candidate_ids == (planted,)  # a NEW record, and the planted one queued for a person to look at
    pending = (await db.execute(text("select patient_id, candidate_patient_id, reason, status from mpi.match_candidates where clinic_id = :c"), {"c": a})).all()
    assert pending == [(real.patient_id, planted, "near_match", "pending")]


async def test_a_fully_agreeing_record_created_by_front_desk_still_matches_and_a_confirmed_worker_record_matches_after_confirmation(db, pipeline, direct_pipeline):
    a, _, p = await seed(db)
    desk = await ingest(db, direct_pipeline, p["fd_a"], a)
    again = await ingest(db, pipeline, p["worker_a"], a)  # the same person, referred: the front-desk record is verified
    assert (again.outcome, again.patient_id) == ("matched", desk.patient_id)
    other = {"name": "Ravi Shankar", "dob": "1979-06-30", "phone": "+91 97000 11122"}
    first = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:r-1", **other)  # created by the worker: unverified
    second = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:r-2", **other)
    assert first.outcome == "created" and second.outcome == "review" and second.candidate_ids == (first.patient_id,)  # repeat referral for a worker-created record goes to a person
    assert await confirm_as(db, p["fd_a"], a, first.patient_id) == 1  # front desk confirms the first record
    third = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:r-3", **other)
    assert third.outcome == "matched" and third.patient_id == first.patient_id  # now it matches normally


async def test_only_front_desk_of_the_clinic_can_confirm_and_only_as_themselves_and_the_record_is_append_only(db, pipeline, direct_pipeline):
    a, b, p = await seed(db)
    target = (await ingest(db, pipeline, p["worker_a"], a)).patient_id
    assert await confirm_as(db, p["fd_a"], a, target, confirmed_by=p["nurse_a"]) == "42501"  # naming somebody else
    for who in ("worker_a", "nurse_a"):  # they can SEE the patient, so the trigger passes and row-level security refuses
        assert await confirm_as(db, p[who], a, target) == "42501", who
    # Another clinic's people cannot see the patient, and the BEFORE INSERT trigger (a confirmation carries its patient's origin) runs BEFORE the row-level-security check and cannot find
    # the patient either: still refused, with the trigger's SQLSTATE 23514 instead of 42501. The wrong clinic id for this patient is the same case.
    for who in ("fd_b", "worker_b"):
        assert await confirm_as(db, p[who], a, target) == "23514", who
    assert await confirm_as(db, p["fd_a"], b, target) == "23514"  # the wrong clinic id for this patient
    key = f"conf-{_u()}"
    assert await confirm_as(db, p["fd_a"], a, target, key=key) == 1  # positive control: the right caller, the right clinic, as themselves
    assert await confirm_as(db, p["fd_a"], a, target, key=key) == "23505"  # the same idempotency key cannot be used twice (since 0025 a LATER confirmation under a new key is a new event)
    row = (await db.execute(text("select id, confirmed_by from mpi.identity_confirmations where patient_id = :p"), {"p": target})).one()
    assert row.confirmed_by == p["fd_a"]
    # append-only for everyone, the owner included
    assert await owner_sqlstate(db, "update mpi.identity_confirmations set origin = 'live' where id = :i", i=row.id) == "23001"
    assert await owner_sqlstate(db, "delete from mpi.identity_confirmations where id = :i", i=row.id) == "23001"
    assert await owner_sqlstate(db, "truncate mpi.identity_confirmations") == "23001"
    # who can read them: front desk, nurse, doctor and the worker of the clinic (the matcher needs it); nobody of another clinic
    for who, expected in (("fd_a", 1), ("nurse_a", 1), ("worker_a", 1), ("fd_b", 0), ("worker_b", 0)):
        async with db.begin_nested():
            await as_user(db, p[who])
            n = (await db.execute(text("select count(*) from mpi.identity_confirmations where patient_id = :p"), {"p": target})).scalar_one()
            await db.exec_driver_sql("reset role")
        assert n == expected, who


# ---- migration 0024: queue_worker and any other role never coexist ---------------------------------------------------------------------------------


async def test_a_queue_worker_cannot_hold_another_role_and_nobody_else_can_become_one(db):
    a, b, p = await seed(db)
    worker_a, fd_a, nurse_a = p["worker_a"], p["fd_a"], p["nurse_a"]
    grant = "insert into iam.staff_roles (account_id, clinic_id, role, origin) values (:u, :c, :r, 'test')"
    assert await owner_sqlstate(db, grant, u=worker_a, c=a, r="front_desk") == "23514"  # a worker with front desk
    assert await owner_sqlstate(db, grant, u=worker_a, c=b, r="nurse") == "23514"  # in another clinic too
    assert await owner_sqlstate(db, grant, u=fd_a, c=a, r="queue_worker") == "23514"  # front desk becoming a worker
    assert await owner_sqlstate(db, grant, u=nurse_a, c=b, r="queue_worker") == "23514"
    # allowed: a worker in a second clinic, and an ordinary account with a second ordinary role (positive controls)
    assert await owner_sqlstate(db, grant, u=worker_a, c=b, r="queue_worker") == "ok"
    assert await owner_sqlstate(db, grant, u=nurse_a, c=b, r="doctor") == "ok"
    # a REVOKED grant conflicts with nothing: revoke front desk's grant, then it may become a worker
    await db.execute(text("update iam.staff_roles set revoked_at = now() where account_id = :u and role = 'front_desk'"), {"u": fd_a})
    assert await owner_sqlstate(db, grant, u=fd_a, c=a, r="queue_worker") == "ok"
    # changing an existing grant's role into the forbidden combination is refused too. (Order-dependent on purpose: worker_a holds a SECOND queue_worker grant in clinic b, added above.)
    assert await owner_sqlstate(db, "update iam.staff_roles set role = 'nurse' where account_id = :u and role = 'queue_worker' and clinic_id = :c", u=worker_a, c=b) == "23514"
    survivors = (await db.execute(text("select count(distinct role) from iam.staff_roles where account_id = :u and revoked_at is null"), {"u": worker_a})).scalar_one()
    assert survivors == 1  # the worker still holds exactly one distinct role


# ---- migration 0025: revoking a confirmation (an append-only event log; the latest event wins) -----------------------------------------------------

# test inputs only: stand-ins for the clinician-owned task settings, not recommendations
REVOKE_TASK = mpi.RevokeTaskSettings(_env_file=None, priority="standard", assigned_role="nurse")  # type: ignore[call-arg]


async def revoke_as(conn, uid, clinic, patient, *, reason="wrong_person", key=None, task=REVOKE_TASK):
    """Run mpi.revoke_identity as `uid`: its result, or the SQLSTATE-ish word of a refusal."""
    try:
        async with conn.begin_nested():
            await as_user(conn, uid)
            try:
                return await mpi.revoke_identity(
                    conn, clinic_id=clinic, origin="test", patient_id=patient, revoked_by=uid, reason=reason, idempotency_key=key or f"rev-{_u()}", task=task
                )
            finally:
                await conn.exec_driver_sql("reset role")
    except mpi.NotPermitted:
        return "42501"
    except DBAPIError as exc:
        return getattr(exc.orig, "sqlstate", None)


async def test_a_revoke_makes_the_next_full_match_a_review_and_a_later_confirmation_restores_matching(db, pipeline, direct_pipeline):
    a, _, p = await seed(db)
    other = {"name": "Ravi Shankar", "dob": "1979-06-30", "phone": "+91 97000 11122"}
    first = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:rv-1", **other)
    assert await confirm_as(db, p["fd_a"], a, first.patient_id) == 1
    assert (await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:rv-2", **other)).outcome == "matched"  # confirmed: matches normally
    revoked = await revoke_as(db, p["fd_a"], a, first.patient_id)
    assert (revoked.replayed, revoked.review_task) == (False, "raised")
    after = await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:rv-3", **other)
    assert after.outcome == "review" and after.candidate_ids == (first.patient_id,)  # unverified again: a person decides
    assert await confirm_as(db, p["fd_a"], a, first.patient_id) == 1  # a second confirmation after the revoke is a NEW event (no unique per patient since 0025)
    assert (await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:rv-4", **other)).outcome == "matched"
    # a record created by front desk can be demoted too
    desk = await ingest(db, direct_pipeline, p["fd_a"], a, name="Meera Nair", phone="+91 90000 00001")
    assert (await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:rv-5", name="Meera Nair", phone="+91 90000 00001", dob=DOB)).outcome == "matched"
    assert (await revoke_as(db, p["fd_a"], a, desk.patient_id)).replayed is False
    assert (await ingest(db, pipeline, p["worker_a"], a, idempotency_key="referral:rv-6", name="Meera Nair", phone="+91 90000 00001", dob=DOB)).outcome == "review"
    # nothing was undone: the earlier links are untouched, and a revoke of an already unverified record writes nothing
    before = await count(db, "mpi.identity_confirmations", a)
    again = await revoke_as(db, p["fd_a"], a, desk.patient_id)
    assert (again.replayed, again.review_task) == (True, "on_record") and await count(db, "mpi.identity_confirmations", a) == before


async def test_only_front_desk_of_the_clinic_can_revoke_as_themselves_with_a_listed_reason_and_the_task_is_for_nurses_only(db, pipeline):
    a, b, p = await seed(db)
    target = (await ingest(db, pipeline, p["worker_a"], a)).patient_id
    assert await confirm_as(db, p["fd_a"], a, target) == 1
    insert = "insert into mpi.identity_confirmations (clinic_id, patient_id, confirmed_by, origin, idempotency_key, kind, reason) values (:c, :p, :u, 'test', :k, :kind, :r)"

    async def raw_event(uid, *, clinic=a, by=None, kind="revoke", reason="wrong_person"):
        return await attempt(db, uid, insert, c=clinic, p=target, u=by or uid, k=f"ev-{_u()}", kind=kind, r=reason)

    assert await raw_event(p["nurse_a"]) == "42501" and await raw_event(p["worker_a"]) == "42501"  # they can see the patient; row-level security refuses
    assert await raw_event(p["fd_a"], by=p["nurse_a"]) == "42501"  # naming somebody else
    assert await raw_event(p["fd_b"]) == "23514" and await raw_event(p["fd_a"], clinic=b) == "23514"  # another clinic: the origin trigger runs first
    for bad in ("a free sentence", "WRONG_PERSON", ""):
        assert await raw_event(p["fd_a"], reason=bad) == "23514", bad  # the reason list is closed
    assert await raw_event(p["fd_a"], kind="revoke", reason=None) == "23514"  # a revoke needs its reason
    assert await raw_event(p["fd_a"], kind="confirm", reason="wrong_person") == "23514"  # a confirmation has none
    assert await raw_event(p["fd_a"], kind="whatever", reason=None) == "23514"
    assert await revoke_as(db, p["nurse_a"], a, target) == "42501" and await revoke_as(db, p["fd_b"], a, target) == "23514"  # through the function too
    done = await revoke_as(db, p["fd_a"], a, target, key=f"rev-{_u()}")
    assert (done.replayed, done.review_task) == (False, "raised")  # positive control
    # append-only for everyone, the owner included
    row = (await db.execute(text("select id from mpi.identity_confirmations where patient_id = :p and kind = 'revoke'"), {"p": target})).one()
    assert await owner_sqlstate(db, "update mpi.identity_confirmations set reason = 'mistaken_click' where id = :i", i=row.id) == "23001"
    assert await owner_sqlstate(db, "delete from mpi.identity_confirmations where id = :i", i=row.id) == "23001"
    # the task: a nurse of the clinic reads it, with ids and closed words only; front desk wrote it but cannot read it; nobody of another clinic sees it
    task = "select kind, priority, assigned_role, provenance from agents.review_tasks where clinic_id = :c and kind = 'identity_confirmation_revoked' and provenance ->> 'patient_id' = :p"
    for who, expected in (("nurse_a", 1), ("fd_a", 0), ("fd_b", 0), ("worker_b", 0)):
        async with db.begin_nested():
            await as_user(db, p[who])
            rows = (await db.execute(text(task), {"c": a, "p": str(target)})).all()
            await db.exec_driver_sql("reset role")
        assert len(rows) == expected, who
        if expected:
            kind, priority, role, provenance = rows[0]
            assert (kind, priority, role) == ("identity_confirmation_revoked", "standard", "nurse") and set(provenance) == {"patient_id", "revoke_event_id", "reason", "confirmed_at", "record_created_by"}
    # front desk may raise ONLY this kind of task, for a nurse or doctor, in its own clinic, BOUND to a revoke event it wrote (the key and source id are fixed by the event's id)
    new_task = (
        "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key, status) "
        "values (:c, 'test', :k, 'standard', :r, :src, '{}'::jsonb, :ik, :st)"
    )

    async def fresh_event(uid=p["fd_a"], clinic=a):
        event = _u()
        n = await attempt(
            db, uid,
            "insert into mpi.identity_confirmations (id, clinic_id, patient_id, confirmed_by, origin, idempotency_key, kind, reason) values (:i, :c, :p, :u, 'test', :k, 'revoke', 'wrong_person')",
            i=event, c=clinic, p=target, u=uid, k=f"ev-{_u()}",
        )
        assert n == 1
        return event

    async def task_as(uid, event, *, clinic=a, kind="identity_confirmation_revoked", role="nurse", status="open", ik=None, src=None):
        return await attempt(
            db, uid, new_task, c=clinic, k=kind, r=role, st=status, ik=ik or f"identity-revoke-review:{event}", src=src or f"identity-revoke:{event}",
        )

    event = await fresh_event()
    assert await task_as(p["fd_a"], event) == 1  # positive control: bound to its own revoke event
    assert await task_as(p["fd_a"], await fresh_event(), role="doctor") == 1
    assert await task_as(p["fd_a"], await fresh_event(), role="front_desk") == "42501"  # not for itself
    for other_kind in ("referral_hold", "some_other_kind"):
        assert await task_as(p["fd_a"], await fresh_event(), kind=other_kind) == "42501", other_kind  # no other kind
    assert await task_as(p["fd_a"], await fresh_event(), ik="redflag:squatted-key") == "42501"  # an arbitrary or predictable key (key squatting)
    assert await task_as(p["fd_a"], await fresh_event(), src="whatever") == "42501"
    assert await task_as(p["fd_a"], await fresh_event(), status="resolved") == "42501"  # a new task is open
    assert await task_as(p["fd_a"], uuid.uuid4()) == "42501"  # no such revoke event
    assert await task_as(p["fd_a"], event, clinic=b) == "42501" and await task_as(p["fd_b"], event) == "42501"  # not across clinics
