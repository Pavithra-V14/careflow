"""IdentityManager (merge and unmerge) against the real hosted project (careflow/identity.py; BUILD-SPEC.md
§12.1, §12.5, §17.1, §17.2; CLAUDE.md rules 5 and 8).

Patients and their possible-duplicate review rows are created by the REAL ingestion pipeline (a near-match
creates exactly the pair a person would review), then merged through the manager as the restricted
`careflow_app` role with verified claims set per transaction. Rolled-back tests share one never-committed
outer transaction, each in its own savepoint; migration 0008 is executed inside it if not applied yet. The
concurrency test needs 0008 applied and commits a small origin='test' fixture it deletes afterwards.
"""

import asyncio
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

from careflow import identity as ident
from careflow.crypto import DemographicVault
from careflow.db import make_app_engine, set_request_claims
from careflow.identity import IdentityManager
from careflow.mpi import PatientIngestionPipeline
from tests.bounds import bounded
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
_ENV = dotenv_values(ROOT / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
APP = os.environ.get("DATABASE_URL_APP") or _ENV.get("DATABASE_URL_APP")
MIGRATIONS = {n: (ROOT / "db" / "migrations" / f).read_text(encoding="utf-8")
              for n, f in (("0003", "0003_mpi_match_candidates.sql"), ("0004", "0004_scheduling_operations.sql"),
                           ("0007", "0007_red_flag_rules_schema.sql"), ("0008", "0008_mpi_merge_ledger.sql"),
                           ("0009", "0009_merge_signature_fix.sql"))}

pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]

CT_COLUMNS = "id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, erased_at, clinic_id, origin"
PHONE = "+91 98765 43210"


def _u() -> uuid.UUID:
    return uuid.uuid4()


def _key() -> str:
    return f"k-{_u()}"


@pytest.fixture(scope="module")
def mgr():
    return IdentityManager()


@pytest.fixture(scope="module")
def pipeline():
    k = lambda: base64.b64encode(os.urandom(32)).decode()
    return PatientIngestionPipeline(DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": k(), "CAREFLOW_HMAC_SECRET_V1": k()}), key_version=1)


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    eng = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await eng.connect()
    outer = await conn.begin()
    try:
        await ensure_app_role_sa(conn)
        for table, name in (("mpi.match_candidates", "0003"), ("sched.holds", "0004"), ("agents.review_tasks", "0007"), ("mpi.merges", "0008")):
            if (await conn.execute(text("select to_regclass(:t) is null"), {"t": table})).scalar_one():
                await raw(conn, MIGRATIONS[name])
        # 0009 replaces 0008's update policy with a trigger-based signature check; apply it here if it is not yet.
        if (await conn.execute(text("select not exists (select 1 from pg_trigger where tgname = 'merges_check_signatures')"))).scalar_one():
            await raw(conn, MIGRATIONS["0009"])
        yield conn
    finally:
        await outer.rollback()
        await conn.close()
        await eng.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


async def act(conn, uid, fn, *args, **kwargs):
    """Call `fn(conn, ...)` as a request would: app role, claims set (empty for "no identity"), one savepoint."""
    async with conn.begin_nested():
        await conn.exec_driver_sql("set local role careflow_app")
        if uid is not None:
            await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})
        else:
            await conn.execute(text("select set_config('request.jwt.claims', '', true)"))
        result = await fn(conn, *args, **kwargs)
        await conn.exec_driver_sql("reset role")
        return result


async def rows(conn, sql, **params):
    return (await conn.execute(text(sql), params)).all()


async def one(conn, sql, **params):
    return (await rows(conn, sql, **params))[0][0]


class World:
    pass


async def world(conn, pipeline) -> World:
    """Clinic A (two front-desk people, nurse, doctor, admin, auditor, business owner), clinic B (front desk), and
    a possible-duplicate pair created by real ingestion: `master` and `sub` share a phone but not a name."""
    w = World()
    w.a, w.b = _u(), _u()
    spec = {"fd1": (w.a, "front_desk"), "fd2": (w.a, "front_desk"), "nurse": (w.a, "nurse"), "doctor": (w.a, "doctor"),
            "admin": (w.a, "admin"), "auditor": (w.a, "auditor"), "owner": (w.a, "business_owner"), "fd_b": (w.b, "front_desk")}
    for k in spec:
        setattr(w, k, _u())
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'Clinic A', 'test'), ('{w.b}', 'Clinic B', 'test'); "
        "insert into iam.accounts (id, account_type, status, origin) values "
        + ", ".join(f"('{getattr(w, k)}', 'staff', 'active', 'test')" for k in spec)
        + "; insert into iam.staff_roles (account_id, clinic_id, role, origin) values "
        + ", ".join(f"('{getattr(w, k)}', '{c}', '{r}', 'test')" for k, (c, r) in spec.items())
        + ";",
    )
    first = await act(conn, w.fd1, pipeline.ingest, clinic_id=w.a, origin="test", name="Asha Venkatesh", dob="1987-04-23", phone=PHONE, idempotency_key=_key())
    second = await act(conn, w.fd1, pipeline.ingest, clinic_id=w.a, origin="test", name="Ravi Kumar", dob="1990-01-15", phone=PHONE, idempotency_key=_key())
    assert (first.outcome, second.outcome) == ("created", "review")
    w.master, w.sub = first.patient_id, second.patient_id
    return w


async def propose(db, mgr, w, who=None, key=None, **over):
    args = {"master": w.master, "sub": w.sub, "approver": who or w.fd1}
    args.update(over)
    return await act(db, args["approver"], mgr.execute_merge, w.a, "test", args["master"], args["sub"], args["approver"], key if key is not None else _key())


async def confirm(db, mgr, w, merge_id, who=None, key=None):
    who = who or w.fd2
    return await act(db, who, mgr.approve_merge, w.a, "test", merge_id, who, key if key is not None else _key())


async def snapshot(conn, *ids):
    return [tuple(r) for r in await rows(conn, f"select {CT_COLUMNS} from mpi.patients where id = any(:ids) order by id", ids=list(ids))]


async def token_count(conn, *ids):
    return await one(conn, "select count(*) from mpi.identity_tokens where patient_id = any(:ids)", ids=list(ids))


# ---- propose, confirm: the logical link ------------------------------------------------------------


async def test_a_proposal_links_nothing_until_a_second_person_confirms(db, mgr, pipeline):
    w = await world(db, pipeline)
    result = await propose(db, mgr, w)
    assert (result.status, result.replayed) == ("awaiting_second_approval", False)
    (row,) = await rows(db, "select status, approved_by, second_approved_by, origin, clinic_id from mpi.merges where id = :i", i=result.merge_id)
    assert (row.status, row.approved_by, row.second_approved_by, row.origin, row.clinic_id) == ("awaiting_second_approval", w.fd1, None, "test", w.a)
    assert await act(db, w.fd1, mgr.resolve, w.sub) == w.sub  # not routed yet
    assert await one(db, "select status from mpi.match_candidates where candidate_patient_id = :m", m=w.master) == "pending"


async def test_the_second_approval_activates_the_link_resolves_the_review_row_and_routes(db, mgr, pipeline):
    w = await world(db, pipeline)
    before = await snapshot(db, w.master, w.sub)
    tokens_before = await token_count(db, w.master, w.sub)
    merge = await propose(db, mgr, w)
    done = await confirm(db, mgr, w, merge.merge_id)
    assert done.status == "active"
    (row,) = await rows(db, "select status, second_approved_by, activated_at is not null as stamped from mpi.merges where id = :i", i=merge.merge_id)
    assert (row.status, row.second_approved_by, row.stamped) == ("active", w.fd2, True)
    (cand,) = await rows(db, "select status, resolved_by, resolved_at is not null as stamped from mpi.match_candidates where candidate_patient_id = :m", m=w.master)
    assert (cand.status, cand.resolved_by, cand.stamped) == ("resolved", w.fd2, True)
    assert await act(db, w.nurse, mgr.resolve, w.sub) == w.master  # the subordinate now routes to the survivor
    assert await act(db, w.nurse, mgr.resolve, w.master) == w.master
    # Never destructive: both rows exist and every ciphertext, key version and token is byte-for-byte unchanged.
    assert await snapshot(db, w.master, w.sub) == before
    assert await token_count(db, w.master, w.sub) == tokens_before
    assert await one(db, "select count(*) from mpi.patients where clinic_id = :c", c=w.a) == 2


async def test_one_person_cannot_complete_a_merge_alone(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge = await propose(db, mgr, w)
    with pytest.raises(ident.MergeNotAllowed, match="two different people"):
        await confirm(db, mgr, w, merge.merge_id, who=w.fd1)  # the proposer confirming their own proposal
    assert await one(db, "select status from mpi.merges where id = :i", i=merge.merge_id) == "awaiting_second_approval"


async def test_the_database_refuses_an_active_merge_with_fewer_than_two_distinct_people(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge = await propose(db, mgr, w)
    for bad in ("second_approved_by = approved_by, activated_at = now()", "activated_at = now()"):
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text(f"update mpi.merges set status = 'active', {bad} where id = :i"), {"i": merge.merge_id})


async def test_nobody_can_sign_as_somebody_else(db, mgr, pipeline):
    w = await world(db, pipeline)
    with pytest.raises(ident.NotPermitted):  # fd2 is signed in but names fd1 as the approver
        await act(db, w.fd2, mgr.execute_merge, w.a, "test", w.master, w.sub, w.fd1, _key())
    merge = await propose(db, mgr, w)
    with pytest.raises(ident.NotPermitted):  # fd2 confirms but names the doctor as the second approver
        await act(db, w.fd2, mgr.approve_merge, w.a, "test", merge.merge_id, w.doctor, _key())
    assert await one(db, "select count(*) from mpi.merges where status = 'active' and clinic_id = :c", c=w.a) == 0


@pytest.mark.parametrize("who", ["admin", "auditor", "owner", "nurse", "doctor", "fd_b", None])
async def test_only_front_desk_of_the_clinic_can_propose_or_confirm(db, mgr, pipeline, who):
    w = await world(db, pipeline)
    uid = getattr(w, who) if who else None
    denied = (ident.NotPermitted, ident.PatientNotFound, ident.MergeNotFound)
    with pytest.raises(denied):
        await act(db, uid, mgr.execute_merge, w.a, "test", w.master, w.sub, uid or _u(), _key())
    merge = await propose(db, mgr, w)
    with pytest.raises(denied):
        await act(db, uid, mgr.approve_merge, w.a, "test", merge.merge_id, uid or _u(), _key())
    with pytest.raises(denied):
        await act(db, uid, mgr.execute_unmerge, w.a, "test", merge.merge_id, uid or _u(), _key())
    assert await one(db, "select status from mpi.merges where id = :i", i=merge.merge_id) == "awaiting_second_approval"


# ---- what may never be merged ----------------------------------------------------------------------


async def test_self_merge_and_pairs_with_no_review_row_are_refused(db, mgr, pipeline):
    w = await world(db, pipeline)
    with pytest.raises(ident.MergeNotAllowed, match="itself"):
        await propose(db, mgr, w, master=w.master, sub=w.master)
    stranger = await act(db, w.fd1, pipeline.ingest, clinic_id=w.a, origin="test", name="Someone Else", dob="1971-02-03", phone="+91 90000 12345", idempotency_key=_key())
    with pytest.raises(ident.MergeNotAllowed, match="review row"):
        await propose(db, mgr, w, master=w.master, sub=stranger.patient_id)


async def test_patients_must_exist_in_this_clinic_with_the_same_origin_and_not_be_erased(db, mgr, pipeline):
    w = await world(db, pipeline)
    with pytest.raises(ident.PatientNotFound):
        await propose(db, mgr, w, master=_u())
    with pytest.raises(ident.PatientNotFound):
        await act(db, w.fd_b, mgr.execute_merge, w.b, "test", w.master, w.sub, w.fd_b, _key())  # other clinic's front desk
    with pytest.raises(ident.MergeNotAllowed, match="origin"):
        await act(db, w.fd1, mgr.execute_merge, w.a, "eval", w.master, w.sub, w.fd1, _key())
    await db.execute(text("update mpi.patients set erased_at = now(), name_ct = null, name_key_version = null, dob_ct = null, "
                          "dob_key_version = null, phone_ct = null, phone_key_version = null where id = :i"), {"i": w.sub})
    with pytest.raises(ident.MergeNotAllowed, match="erased"):
        await propose(db, mgr, w)


async def test_a_subordinate_is_in_at_most_one_live_link_and_links_stay_flat(db, mgr, pipeline):
    w = await world(db, pipeline)
    third = await act(db, w.fd1, pipeline.ingest, clinic_id=w.a, origin="test", name="Third Person", dob="1999-09-09", phone=PHONE, idempotency_key=_key())
    await propose(db, mgr, w)  # master <- sub is now live (proposed)
    # the same subordinate again, toward the third record, or in the opposite direction: all refused
    with pytest.raises(ident.MergeNotAllowed):
        await propose(db, mgr, w, master=third.patient_id, sub=w.sub)
    with pytest.raises(ident.MergeNotAllowed):
        await propose(db, mgr, w, master=w.sub, sub=w.master)
    with pytest.raises(ident.MergeNotAllowed, match="flat"):  # sub is already a subordinate, so it cannot become a master
        await propose(db, mgr, w, master=w.sub, sub=third.patient_id)
    with pytest.raises(ident.MergeNotAllowed, match="flat"):  # master is already a master, so it cannot become a subordinate
        await propose(db, mgr, w, master=third.patient_id, sub=w.master)


async def test_the_database_enforces_the_link_rules_even_without_the_engine(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge = await propose(db, mgr, w)
    (cand,) = await rows(db, "select candidate_id from mpi.merges where id = :i", i=merge.merge_id)
    third = (await act(db, w.fd1, pipeline.ingest, clinic_id=w.a, origin="test", name="Third Person", dob="1999-09-09", phone=PHONE, idempotency_key=_key())).patient_id
    insert = ("insert into mpi.merges (clinic_id, master_patient_id, subordinate_patient_id, candidate_id, approved_by, origin, idempotency_key) "
              "values (:c, :m, :s, :cand, :ap, 'test', :k)")
    cases = {
        "merges_not_self|flat": (w.master, w.master),
        "merges_one_live_link_per_subordinate": (third, w.sub),
        "merges_one_live_link_per_pair": (w.sub, w.master),
    }
    for expected, (m, s) in cases.items():
        with pytest.raises(DBAPIError, match=expected if expected != "merges_one_live_link_per_pair" else "merges_one_live_link|flat"):
            async with db.begin_nested():
                await db.execute(text(insert), {"c": w.a, "m": m, "s": s, "cand": cand[0], "ap": w.fd1, "k": _key()})


# ---- idempotency -----------------------------------------------------------------------------------


async def test_replaying_a_step_returns_the_original_and_an_altered_one_conflicts(db, mgr, pipeline):
    w = await world(db, pipeline)
    key, key2 = _key(), _key()
    first = await propose(db, mgr, w, key=key)
    again = await propose(db, mgr, w, key=key)
    assert (again.replayed, again.merge_id) == (True, first.merge_id)
    with pytest.raises(ident.IdempotencyConflict):
        await act(db, w.fd1, mgr.execute_merge, w.a, "test", w.sub, w.master, w.fd1, key)  # same key, swapped roles
    done = await confirm(db, mgr, w, first.merge_id, key=key2)
    replay = await act(db, w.fd2, mgr.approve_merge, w.a, "test", first.merge_id, w.fd2, key2)
    assert (replay.replayed, replay.merge_id, done.status) == (True, first.merge_id, "active")
    with pytest.raises(ident.InvalidTransition):
        await confirm(db, mgr, w, first.merge_id, key=_key())  # a different key for an already-active merge
    assert await one(db, "select count(*) from mpi.merges where clinic_id = :c", c=w.a) == 1


@pytest.mark.parametrize("bad", ["", "  ", None])
async def test_every_step_requires_a_key(db, mgr, pipeline, bad):
    w = await world(db, pipeline)
    with pytest.raises(ident.IdentityError):
        await propose(db, mgr, w, key=bad if bad is not None else "")
    merge = await propose(db, mgr, w)
    for call in (
        lambda c: mgr.approve_merge(c, w.a, "test", merge.merge_id, w.fd2, bad),
        lambda c: mgr.execute_unmerge(c, w.a, "test", merge.merge_id, w.fd1, bad),
    ):
        with pytest.raises(ident.IdentityError):
            await act(db, w.fd2, lambda c, f=call: f(c))


# ---- unmerge ---------------------------------------------------------------------------------------


async def make_active(db, mgr, w):
    merge = await propose(db, mgr, w)
    await confirm(db, mgr, w, merge.merge_id)
    return merge.merge_id


async def seed_booking(conn, w, patient_id, *, when):
    """A booking recorded for `patient_id` at `when` (an SQL timestamp expression), as the migrator."""
    prov, loc, vt, slot = _u(), _u(), _u(), _u()
    await raw(
        conn,
        f"insert into sched.providers (id, clinic_id, display_name, origin, idempotency_key) values ('{prov}', '{w.a}', 'Dr T', 'test', 'p-{prov}'); "
        f"insert into sched.locations (id, clinic_id, name, origin, idempotency_key) values ('{loc}', '{w.a}', 'Room', 'test', 'l-{loc}'); "
        f"insert into sched.visit_types (id, clinic_id, name, duration_minutes, origin, idempotency_key) values ('{vt}', '{w.a}', 'Review', 30, 'test', 'v-{vt}'); "
        f"insert into sched.slots (id, clinic_id, provider_id, location_id, visit_type_id, starts_at, ends_at, status, origin, idempotency_key) "
        f"values ('{slot}', '{w.a}', '{prov}', '{loc}', '{vt}', '2033-01-01 10:00+00', '2033-01-01 10:30+00', 'booked', 'test', 's-{slot}');",
    )
    bid = _u()
    await conn.execute(text(f"insert into sched.bookings (id, clinic_id, slot_id, patient_id, origin, idempotency_key, created_at) "
                            f"values (:b, :c, :s, :p, 'test', :k, {when})"), {"b": bid, "c": w.a, "s": slot, "p": patient_id, "k": f"b-{bid}"})
    return bid


async def test_unmerge_breaks_the_link_restores_independence_and_raises_a_review_task(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge_id = await make_active(db, mgr, w)
    before_link = await seed_booking(db, w, w.sub, when="clock_timestamp() - interval '1 hour'")  # recorded before the link
    during = await seed_booking(db, w, w.sub, when="clock_timestamp()")  # recorded inside the link window
    snapshot_before = await snapshot(db, w.master, w.sub)
    result = await act(db, w.fd1, mgr.execute_unmerge, w.a, "test", merge_id, w.fd1, _key())
    assert result.review_task_id is not None and not result.replayed
    assert await one(db, "select status from mpi.merges where id = :i", i=merge_id) == "unmerged"
    assert await act(db, w.nurse, mgr.resolve, w.sub) == w.sub and await act(db, w.nurse, mgr.resolve, w.master) == w.master
    (task,) = await rows(db, "select * from agents.review_tasks where id = :i", i=result.review_task_id)
    t = task._mapping
    assert (t["kind"], t["priority"], t["assigned_role"], t["status"], t["origin"], t["clinic_id"]) == ("unmerge_review", "immediate", "nurse", "open", "test", w.a)
    flagged = t["provenance"]["decisions"]
    assert flagged["bookings"] == [str(during)] and str(before_link) not in str(t["provenance"])  # only decisions inside the window
    assert t["provenance"]["merge_id"] == str(merge_id)
    assert await snapshot(db, w.master, w.sub) == snapshot_before  # nothing about either patient changed
    assert await one(db, "select count(*) from mpi.patients where clinic_id = :c", c=w.a) == 2


async def test_unmerge_flags_review_rows_created_inside_the_window_too(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge_id = await make_active(db, mgr, w)
    # A new person sharing the phone arrives while the link is active: a fresh possible-duplicate row appears.
    newcomer = await act(db, w.fd1, pipeline.ingest, clinic_id=w.a, origin="test", name="Fourth Person", dob="1960-06-06", phone=PHONE, idempotency_key=_key())
    assert newcomer.outcome == "review" and newcomer.candidate_ids == (w.master,)  # the pooled identifiers point at the survivor only
    row_id = await one(db, "select id from mpi.match_candidates where patient_id = :p", p=newcomer.patient_id)
    await db.execute(text("update mpi.match_candidates set created_at = clock_timestamp() where id = :i"), {"i": row_id})
    result = await act(db, w.fd1, mgr.execute_unmerge, w.a, "test", merge_id, w.fd1, _key())
    provenance = (await rows(db, "select provenance from agents.review_tasks where id = :i", i=result.review_task_id))[0][0]
    assert provenance["decisions"]["review_rows"] == [str(row_id)]
    assert result.decisions_flagged == 1
    for patient_text in ("Asha", "Venkatesh", "Ravi", "Kumar", "Fourth", "1987", "98765"):
        assert patient_text not in str(provenance), f"patient text leaked into the review task: {patient_text}"


async def test_unmerge_is_idempotent_one_person_and_cannot_be_repeated_under_a_new_key(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge_id = await make_active(db, mgr, w)
    key = _key()
    with pytest.raises(ident.NotPermitted):  # a person signs an unmerge as themselves: fd1 cannot name fd2
        await act(db, w.fd1, mgr.execute_unmerge, w.a, "test", merge_id, w.fd2, _key())
    first = await act(db, w.fd2, mgr.execute_unmerge, w.a, "test", merge_id, w.fd2, key)  # a single person is enough
    again = await act(db, w.fd2, mgr.execute_unmerge, w.a, "test", merge_id, w.fd2, key)
    assert (again.replayed, again.review_task_id) == (True, first.review_task_id)
    with pytest.raises(ident.InvalidTransition):
        await act(db, w.fd2, mgr.execute_unmerge, w.a, "test", merge_id, w.fd2, _key())
    assert await one(db, "select count(*) from agents.review_tasks where kind = 'unmerge_review' and clinic_id = :c", c=w.a) == 1


async def test_cancelling_an_unconfirmed_proposal_raises_no_review_task(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge = await propose(db, mgr, w)
    result = await act(db, w.fd1, mgr.execute_unmerge, w.a, "test", merge.merge_id, w.fd1, _key())
    assert (result.review_task_id, result.decisions_flagged) == (None, 0)
    assert await one(db, "select status from mpi.merges where id = :i", i=merge.merge_id) == "unmerged"
    assert await one(db, "select count(*) from agents.review_tasks where clinic_id = :c", c=w.a) == 0
    assert await one(db, "select status from mpi.match_candidates where candidate_patient_id = :m", m=w.master) == "pending"  # still awaiting a decision


async def test_an_unmerged_pair_needs_a_fresh_review_row_to_be_merged_again(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge_id = await make_active(db, mgr, w)
    await act(db, w.fd1, mgr.execute_unmerge, w.a, "test", merge_id, w.fd1, _key())
    with pytest.raises(ident.MergeNotAllowed, match="review row"):
        await propose(db, mgr, w)  # the earlier decision resolved the only row; a person must re-flag the pair


async def test_unknown_merges_are_not_found(db, mgr, pipeline):
    w = await world(db, pipeline)
    with pytest.raises(ident.MergeNotFound):
        await act(db, w.fd1, mgr.execute_unmerge, w.a, "test", _u(), w.fd1, _key())
    with pytest.raises(ident.MergeNotFound):
        await act(db, w.fd2, mgr.approve_merge, w.a, "test", _u(), w.fd2, _key())


# ---- matching follows the link ---------------------------------------------------------------------


async def test_ingesting_a_merged_persons_details_returns_the_survivor_not_a_third_record(db, mgr, pipeline):
    w = await world(db, pipeline)
    await make_active(db, mgr, w)
    again = await act(db, w.fd1, pipeline.ingest, clinic_id=w.a, origin="test", name="Ravi Kumar", dob="1990-01-15", phone=PHONE, idempotency_key=_key())
    assert (again.outcome, again.patient_id) == ("matched", w.master)  # the subordinate's identifiers route to the master
    assert await one(db, "select count(*) from mpi.patients where clinic_id = :c", c=w.a) == 2


async def test_after_an_unmerge_the_two_records_are_independent_matches_again(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge_id = await make_active(db, mgr, w)
    await act(db, w.fd1, mgr.execute_unmerge, w.a, "test", merge_id, w.fd1, _key())
    again = await act(db, w.fd1, pipeline.ingest, clinic_id=w.a, origin="test", name="Ravi Kumar", dob="1990-01-15", phone=PHONE, idempotency_key=_key())
    assert (again.outcome, again.patient_id) == ("matched", w.sub)  # its own record again, not the master


# ---- who can read, and table-level guarantees ------------------------------------------------------


async def test_clinical_staff_read_the_links_and_everyone_else_sees_none(db, mgr, pipeline):
    w = await world(db, pipeline)
    await make_active(db, mgr, w)
    count = lambda c: _count(c)
    for who in (w.fd1, w.nurse, w.doctor):
        assert await act(db, who, count) == 1, who
    for who in (w.admin, w.auditor, w.owner, w.fd_b, None):
        assert await act(db, who, count) == 0, who


async def _count(conn):
    return (await conn.execute(text("select count(*) from mpi.merges"))).scalar_one()


async def test_no_delete_forced_rls_origin_has_no_default_and_the_link_is_immutable(db, mgr, pipeline):
    w = await world(db, pipeline)
    merge = await propose(db, mgr, w)
    assert not await one(db, "select has_table_privilege('careflow_app', 'mpi.merges', 'delete')")
    for role in ("anon", "authenticated"):
        assert not await one(db, "select has_table_privilege(:r, 'mpi.merges', 'select')", r=role)
    flags = (await rows(db, "select relrowsecurity, relforcerowsecurity from pg_class where oid = 'mpi.merges'::regclass"))[0]
    assert tuple(flags) == (True, True)
    col = (await rows(db, "select column_default, is_nullable from information_schema.columns where table_schema = 'mpi' and table_name = 'merges' and column_name = 'origin'"))[0]
    assert (col.column_default, col.is_nullable) == (None, "NO")
    for column, value in (("master_patient_id", str(w.sub)), ("subordinate_patient_id", str(w.master)), ("approved_by", str(w.fd2)), ("clinic_id", str(w.b)), ("origin", "live")):
        with pytest.raises(DBAPIError, match="permission denied"):
            await act(db, w.fd1, _set_merge_column, merge.merge_id, column, value)
    with pytest.raises(DBAPIError, match="permission denied"):
        await act(db, w.fd1, _delete_merges)


async def _set_merge_column(conn, merge_id, column, value):
    cast = "::uuid" if column != "origin" else ""
    await conn.execute(text(f"update mpi.merges set {column} = cast(:v as {'uuid' if cast else 'text'}) where id = :i"), {"v": value, "i": merge_id})


async def _delete_merges(conn):
    await conn.execute(text("delete from mpi.merges"))


# ---- concurrency: real sessions through the real pooler --------------------------------------------


@pytest_asyncio.fixture(loop_scope="module")
async def committed():
    if not APP:
        pytest.skip("DATABASE_URL_APP not configured")
    admin = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    async with admin.connect() as c:
        absent = (await c.execute(text("select to_regclass('mpi.merges') is null"))).scalar_one()
    if absent:
        await admin.dispose()
        pytest.skip("migration 0008 is not applied yet; the concurrency test needs committed tables")
    clinic, fd1, fd2 = _u(), _u(), _u()
    master1, master2, sub = _u(), _u(), _u()
    async with admin.begin() as c:
        await c.execute(text("insert into iam.clinics (id, name, origin) values (:c, 'Merge Concurrency', 'test')"), {"c": clinic})
        for acct in (fd1, fd2):
            await c.execute(text("insert into iam.accounts (id, account_type, status, origin) values (:a, 'staff', 'active', 'test')"), {"a": acct})
            await c.execute(text("insert into iam.staff_roles (account_id, clinic_id, role, origin) values (:a, :c, 'front_desk', 'test')"), {"a": acct, "c": clinic})
        for pid in (master1, master2, sub):
            await c.execute(text("insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
                                 "values (:p, :c, '\\x01', 1, '\\x01', 1, 'test', :k)"), {"p": pid, "c": clinic, "k": f"p-{pid}"})
        for other in (master1, master2):
            await c.execute(text("insert into mpi.match_candidates (clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, origin, idempotency_key) "
                                 "values (:c, :s, :m, array['phone'], 'near_match', 'test', :k)"), {"c": clinic, "s": sub, "m": other, "k": f"c-{other}"})
    engine = make_app_engine(APP or "", pool_size=6, max_overflow=0)
    try:
        yield clinic, fd1, fd2, master1, master2, sub, engine, admin
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            for t in ("mpi.merges", "mpi.match_candidates", "mpi.patients"):
                await c.execute(text(f"delete from {t} where clinic_id = :c"), {"c": clinic})
            await c.execute(text("delete from iam.staff_roles where clinic_id = :c"), {"c": clinic})
            await c.execute(text("delete from iam.accounts where id = any(:a)"), {"a": [fd1, fd2]})
            await c.execute(text("delete from iam.clinics where id = :c"), {"c": clinic})
        await admin.dispose()


async def test_competing_proposals_for_one_subordinate_produce_exactly_one_live_link(committed, mgr):
    clinic, fd1, fd2, master1, master2, sub, engine, admin = committed

    async def attempt(who, master):
        async with engine.connect() as conn, conn.begin():
            await set_request_claims(conn, {"sub": str(who), "role": "authenticated"})
            try:
                await mgr.execute_merge(conn, clinic, "test", master, sub, who, _key())
                return "won"
            except ident.MergeNotAllowed:
                return "refused"

    results = await bounded(
        asyncio.gather(*(attempt(who, master) for who, master in ((fd1, master1), (fd2, master2), (fd1, master2), (fd2, master1)))),
        "the concurrent merge attempts",
    )
    assert results.count("won") == 1 and results.count("refused") == 3, results
    async with admin.connect() as c:
        live = (await c.execute(text("select count(*) from mpi.merges where clinic_id = :c and status in ('awaiting_second_approval', 'active')"), {"c": clinic})).scalar_one()
    assert live == 1
