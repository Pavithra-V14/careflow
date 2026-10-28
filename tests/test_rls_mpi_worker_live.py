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
    ("0003", "0003_mpi_match_candidates.sql"), ("0008", "0008_mpi_merge_ledger.sql"), ("0019", "0019_mpi_queue_worker_ingestion.sql"), ("0022", "0022_worker_registration_function.sql"))}

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
    assert await fn(db, w, a, cands=f"[{candidate(foreign)}]") == "42501"  # a patient of another clinic
    assert await fn(db, w, a, cands=f"[{candidate(_u())}]") == "42501"  # a patient that does not exist
    assert await fn(db, w, a, cands=f"[{candidate(own)}]", origin="eval") == "42501"  # the candidate is an origin='test' record, the new one would be 'eval'
    bad_shapes = {
        "an unknown reason": f"[{candidate(own, reason='resolved')}]",
        "no kinds": f"[{candidate(own, kinds='[]')}]",
        "not an array": candidate(own),
        "an element that is not an object": '["x"]',
        "more than twenty": "[" + ",".join([candidate(own)] * 21) + "]",
    }
    for label, cands in bad_shapes.items():
        assert await fn(db, w, a, cands=cands) == "22023", label
    assert await fn(db, w, a, kinds="array['name_dob','phone','email']", tokens="array['\\x01'::bytea,'\\x02'::bytea,'\\x03'::bytea]") == "22023"  # three tokens
    assert await fn(db, w, a, kinds="array['name_dob','name_dob']", tokens="array['\\x01'::bytea,'\\x02'::bytea]") == "22023"  # duplicate kind
    assert await fn(db, w, a, kinds="array['email']") == "22023"  # a kind outside the closed set
    assert await fn(db, w, a, origin="prod") == "22023"
    assert await fn(db, w, a, key="   ") == "22023"
