"""Pre-launch blocker 10 (planted tokens), option A: a record created by the unattended worker is UNVERIFIED until a front-desk person confirms it, and an unverified record is never a
confident match. Offline: the matcher's rule, the pipeline against a fake connection, the confirm function, and migration 0023 read as TEXT. The live checks (as the restricted
role, including the end-to-end planted-token attack) are in tests/test_rls_mpi_worker_live.py."""

import base64
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from careflow import mpi
from careflow.crypto import DemographicVault
from careflow.mpi import classify

A, B = uuid.uuid4(), uuid.uuid4()
BOTH = {"name_dob", "phone"}

# ---- the rule ------------------------------------------------------------------------------------------------------------------------------


def test_a_fully_agreeing_VERIFIED_record_is_a_match():
    assert classify({A: BOTH}) == ("match", {A: "match"})
    assert classify({A: BOTH}, frozenset()) == ("match", {A: "match"})


def test_a_fully_agreeing_UNVERIFIED_record_is_never_a_match_it_goes_to_a_person():
    decision, reasons = classify({A: BOTH}, {A})
    assert (decision, reasons) == ("review", {A: "near_match"})  # the existing reason: match_candidates' CHECK is untouched


def test_a_verified_record_still_wins_over_an_unverified_one_that_also_agrees():
    # the human-vetted record is the confident one; the unverified (possibly planted) one cannot make it ambiguous or take its place
    decision, reasons = classify({A: BOTH, B: BOTH}, {B})
    assert (decision, list(reasons)) == ("match", [A])


def test_two_unverified_agreeing_records_are_review_for_both():
    decision, reasons = classify({A: BOTH, B: BOTH}, {A, B})
    assert decision == "review" and reasons == {A: "near_match", B: "near_match"}


def test_partial_agreement_is_unchanged_by_verification():
    assert classify({A: {"phone"}}, {A}) == ("review", {A: "near_match"})
    assert classify({A: {"phone"}}) == ("review", {A: "near_match"})
    assert classify({}, {A}) == ("none", {})


# ---- the pipeline: the matcher reads the verification status ----------------------------------------------------------------------------------


class Result:
    def __init__(self, rows=(), scalar=None):
        self._rows, self._scalar = list(rows), scalar

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar

    def __iter__(self):
        return iter(self._rows)


class Conn:
    def __init__(self, hit_rows, new_id):
        self.hit_rows, self.new_id, self.calls = hit_rows, new_id, []

    def begin_nested(self):
        conn = self

        class Savepoint:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return Savepoint()

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "from mpi.identity_tokens t" in sql:
            return Result(self.hit_rows)
        if "register_patient_from_referral" in sql or "returning id" in sql:
            return Result(scalar=self.new_id)
        return Result()


ARGS = {"clinic_id": uuid.uuid4(), "origin": "test", "name": "Asha Venkatesh", "dob": "1987-04-23", "phone": "9876543210", "idempotency_key": "referral:doc-1"}


def make(registrar="function"):
    k = lambda: base64.b64encode(os.urandom(32)).decode()
    return mpi.PatientIngestionPipeline(DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": k(), "CAREFLOW_HMAC_SECRET_V1": k()}), key_version=1, registrar=registrar)


@pytest.mark.asyncio
async def test_an_exact_hit_on_an_unverified_record_creates_a_new_record_and_queues_a_review_instead_of_linking():
    planted = uuid.uuid4()
    conn = Conn([(planted, ["name_dob", "phone"], False)], uuid.uuid4())
    result = await make().ingest(conn, **ARGS)  # type: ignore[arg-type]
    assert result.outcome == "review" and result.candidate_ids == (planted,)  # NOT "matched"


@pytest.mark.asyncio
async def test_an_exact_hit_on_a_verified_record_still_links_and_writes_nothing():
    real = uuid.uuid4()
    conn = Conn([(real, ["name_dob", "phone"], True)], uuid.uuid4())
    result = await make().ingest(conn, **ARGS)  # type: ignore[arg-type]
    assert (result.outcome, result.patient_id) == ("matched", real)
    assert not any("register_patient_from_referral" in sql or "insert into" in sql.lower() for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_the_lookup_reads_the_registration_source_and_the_confirmation_through_the_master_of_a_merge():
    conn = Conn([], uuid.uuid4())
    await make().ingest(conn, **ARGS)  # type: ignore[arg-type]
    (sql,) = [s for s, _ in conn.calls if "from mpi.identity_tokens t" in s]
    assert "coalesce(le.kind = 'confirm', pm.registered_via = 'front_desk')" in sql
    assert "from mpi.identity_confirmations e where e.patient_id = pm.id" in sql
    assert "join mpi.patients pm on pm.id = coalesce(mg.master_patient_id, t.patient_id)" in sql  # a subordinate takes its master's status


# ---- confirm_identity -----------------------------------------------------------------------------------------------------------------------


class ConfirmConn:
    """A scripted connection: `latest` is the patient's latest event (kind, id, created_at) or None; `by_key` is (patient, clinic, kind) or None; `registered_via` is the record's source."""

    def __init__(self, *, by_key=None, live=True, latest=None, registered_via="referral_worker", task_error=None):
        self.by_key, self.live, self.latest, self.registered_via, self.task_error, self.calls = by_key, live, latest, registered_via, task_error, []

    def begin_nested(self):
        conn = self

        class Savepoint:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return Savepoint()

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "pg_advisory_xact_lock" in sql:
            return Result([])
        if "from mpi.merges" in sql:
            return Result([])  # not the subordinate of an active merge
        if "where idempotency_key = :k" in sql:
            return Result([self.by_key] if self.by_key else [])
        if "select registered_via from mpi.patients" in sql:
            return Result([(self.registered_via,)] if self.live else [])
        if "from mpi.patients" in sql:
            return Result([(1,)] if self.live else [])
        if "order by seq desc limit 1" in sql:
            return Result([self.latest] if self.latest else [])
        if "insert into agents.review_tasks" in sql and self.task_error is not None:
            raise self.task_error
        if sql.lstrip().startswith("insert into"):
            return Result([])
        raise AssertionError(sql)

    def inserts(self, table):
        return [(s, p) for s, p in self.calls if f"insert into {table}" in s]


KW = {"clinic_id": uuid.uuid4(), "origin": "test", "patient_id": uuid.uuid4(), "confirmed_by": uuid.uuid4(), "idempotency_key": "k-1"}
NOW_TS = datetime(2030, 1, 1, tzinfo=UTC)
CONFIRMED = ("confirm", uuid.uuid4(), NOW_TS)
REVOKED = ("revoke", uuid.uuid4(), NOW_TS)
RKW = {"clinic_id": KW["clinic_id"], "origin": "test", "patient_id": KW["patient_id"], "revoked_by": KW["confirmed_by"], "reason": "wrong_person", "idempotency_key": "r-1"}
# test inputs only: stand-ins for the clinician-owned task settings, not recommendations
TASK = mpi.RevokeTaskSettings(_env_file=None, priority="standard", assigned_role="nurse")  # type: ignore[call-arg]
NO_TASK = mpi.RevokeTaskSettings(_env_file=None)  # type: ignore[call-arg]


def db_error(sqlstate):
    return DBAPIError("insert", {}, SimpleNamespace(sqlstate=sqlstate))


@pytest.mark.asyncio
async def test_a_confirmation_is_one_plain_insert_by_the_caller_serialised_per_patient_and_not_a_replay():
    conn = ConfirmConn()
    result = await mpi.confirm_identity(conn, **KW)  # type: ignore[arg-type]
    assert (result.patient_id, result.replayed) == (KW["patient_id"], False)
    ((sql, p),) = conn.inserts("mpi.identity_confirmations")
    assert p["u"] == KW["confirmed_by"] and p["k"] == "k-1" and "'confirm'" in sql and "on conflict" not in sql.lower() and "returning" not in sql.lower()
    assert any("pg_advisory_xact_lock" in s for s, _ in conn.calls)
    assert not any(re.search(r"\b(update|delete)\b", s.lower()) for s, _ in conn.calls)  # the patient row is never touched


@pytest.mark.asyncio
async def test_the_same_key_for_the_same_patient_replays_and_for_another_patient_or_a_revoke_conflicts():
    same = (KW["patient_id"], KW["clinic_id"], "confirm")
    assert (await mpi.confirm_identity(ConfirmConn(by_key=same), **KW)).replayed is True  # type: ignore[arg-type]
    for other in ((uuid.uuid4(), KW["clinic_id"], "confirm"), (KW["patient_id"], KW["clinic_id"], "revoke")):
        with pytest.raises(mpi.IdempotencyConflict):
            await mpi.confirm_identity(ConfirmConn(by_key=other), **KW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_patient_whose_latest_event_is_a_confirmation_answers_as_a_replay_after_a_revoke_a_new_confirmation_is_written_and_an_unknown_patient_is_not_found():
    done = ConfirmConn(latest=CONFIRMED)
    assert (await mpi.confirm_identity(done, **KW)).replayed is True  # type: ignore[arg-type]
    assert done.inserts("mpi.identity_confirmations") == []
    again = ConfirmConn(latest=REVOKED)
    assert (await mpi.confirm_identity(again, **KW)).replayed is False  # type: ignore[arg-type]
    assert len(again.inserts("mpi.identity_confirmations")) == 1
    with pytest.raises(mpi.PatientNotFound):
        await mpi.confirm_identity(ConfirmConn(live=False), **KW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_blank_key_and_an_unknown_origin_are_refused_before_any_query():
    for bad in ({"idempotency_key": "  "}, {"origin": "production"}):
        conn = ConfirmConn()
        with pytest.raises((mpi.MissingRequiredField, ValueError)):
            await mpi.confirm_identity(conn, **{**KW, **bad})  # type: ignore[arg-type]
        assert conn.calls == []


# ---- revoke_identity (migration 0025) ------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_revoke_of_a_confirmed_record_writes_one_event_and_one_task_with_ids_and_closed_words_only():
    conn = ConfirmConn(latest=CONFIRMED)
    result = await mpi.revoke_identity(conn, task=TASK, **RKW)  # type: ignore[arg-type]
    assert (result.replayed, result.review_task) == (False, "raised")
    ((sql, p),) = conn.inserts("mpi.identity_confirmations")
    assert "'revoke'" in sql and p["r"] == "wrong_person" and p["u"] == RKW["revoked_by"] and "on conflict" not in sql.lower()
    ((tsql, tp),) = conn.inserts("agents.review_tasks")
    assert "returning" not in tsql.lower() and "on conflict" not in tsql.lower()  # front desk cannot read the task, so neither is allowed
    assert (tp["kind"], tp["pr"], tp["role"]) == ("identity_confirmation_revoked", "standard", "nurse")
    provenance = json.loads(tp["p"])
    assert set(provenance) == {"patient_id", "revoke_event_id", "reason", "confirmed_at", "record_created_by"} and provenance["reason"] == "wrong_person"
    assert tp["k"] == f"identity-revoke-review:{provenance['revoke_event_id']}"


@pytest.mark.asyncio
async def test_a_front_desk_created_record_with_no_event_can_be_demoted_and_a_worker_created_one_has_nothing_to_revoke():
    created_here = ConfirmConn(registered_via="front_desk")
    assert (await mpi.revoke_identity(created_here, task=TASK, **RKW)).replayed is False  # type: ignore[arg-type]
    assert json.loads(created_here.inserts("agents.review_tasks")[0][1]["p"])["confirmed_at"] is None
    for conn in (ConfirmConn(registered_via="referral_worker"), ConfirmConn(latest=REVOKED, registered_via="front_desk")):
        result = await mpi.revoke_identity(conn, task=TASK, **RKW)  # type: ignore[arg-type]
        assert (result.replayed, result.review_task) == (True, "on_record")
        assert conn.inserts("mpi.identity_confirmations") == [] and conn.inserts("agents.review_tasks") == []  # a no-op writes nothing


@pytest.mark.asyncio
async def test_unset_task_settings_still_record_the_revoke_and_say_no_task_was_raised():
    conn = ConfirmConn(latest=CONFIRMED)
    result = await mpi.revoke_identity(conn, task=NO_TASK, **RKW)  # type: ignore[arg-type]
    assert (result.replayed, result.review_task) == (False, "not_configured")
    assert len(conn.inserts("mpi.identity_confirmations")) == 1 and conn.inserts("agents.review_tasks") == []
    assert NO_TASK.priority is None and NO_TASK.assigned_role is None and not NO_TASK.configured  # no default for either


@pytest.mark.asyncio
async def test_a_revoke_replays_on_its_key_and_conflicts_for_another_patient_or_a_confirmation():
    assert (await mpi.revoke_identity(ConfirmConn(by_key=(RKW["patient_id"], RKW["clinic_id"], "revoke")), task=TASK, **RKW)).replayed is True  # type: ignore[arg-type]
    for other in ((uuid.uuid4(), RKW["clinic_id"], "revoke"), (RKW["patient_id"], RKW["clinic_id"], "confirm")):
        with pytest.raises(mpi.IdempotencyConflict):
            await mpi.revoke_identity(ConfirmConn(by_key=other), task=TASK, **RKW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_reason_is_a_closed_word_and_nothing_is_queried_for_a_bad_one():
    for bad in ("because I said so", "", "WRONG_PERSON", None):
        conn = ConfirmConn(latest=CONFIRMED)
        with pytest.raises(mpi.InvalidRevokeReason):
            await mpi.revoke_identity(conn, task=TASK, **{**RKW, "reason": bad})  # type: ignore[arg-type]
        assert conn.calls == []
    assert set(mpi.REVOKE_REASONS) == {"wrong_person", "planted_or_suspect", "mistaken_click"}
    with pytest.raises(mpi.PatientNotFound):
        await mpi.revoke_identity(ConfirmConn(live=False), task=TASK, **RKW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_duplicate_task_is_a_replay_but_any_other_task_failure_propagates_so_the_revoke_rolls_back():
    assert (await mpi.revoke_identity(ConfirmConn(latest=CONFIRMED, task_error=db_error("23505")), task=TASK, **RKW)).review_task == "raised"  # type: ignore[arg-type]
    with pytest.raises(DBAPIError):
        await mpi.revoke_identity(ConfirmConn(latest=CONFIRMED, task_error=db_error("42501")), task=TASK, **RKW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_matcher_treats_the_latest_event_as_the_verification_not_the_existence_of_a_confirmation():
    conn = Conn([], uuid.uuid4())
    await make().ingest(conn, **ARGS)  # type: ignore[arg-type]
    (sql,) = [s for s, _ in conn.calls if "from mpi.identity_tokens t" in s]
    assert "order by e.seq desc limit 1" in sql and "coalesce(le.kind = 'confirm', pm.registered_via = 'front_desk')" in sql


# ---- migration 0023 as text ------------------------------------------------------------------------------------------------------------------

SQL = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0023_unverified_worker_records.sql").read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.strip().startswith("--"))
LOW = CODE.lower()


def test_the_column_defaults_to_front_desk_is_closed_to_two_values_and_the_app_role_gets_no_grant_on_it():
    assert "add column registered_via text not null default 'front_desk' check (registered_via in ('front_desk', 'referral_worker'))" in LOW
    assert not re.search(r"grant\s+(insert|update)[^;]*registered_via", LOW)  # 0012: column-level grants; nothing here adds one


def test_the_registration_function_marks_the_record_unverified_and_keeps_its_pins():
    body = CODE.split("create or replace function mpi.register_patient_from_referral", 1)[1]
    assert "'referral_worker'" in body and "security definer set search_path = ''" in body.lower()
    assert "iam.has_clinic_role(p_clinic, array['queue_worker'])" in body
    assert LOW.count("grant execute on function mpi.register_patient_from_referral") == 1 and " from public;" in LOW
    params = re.findall(r"^\s+(p_\w+)\s", body.split("returns uuid")[0], re.MULTILINE)
    assert "p_registered_via" not in params and len(params) == 13  # nothing lets the caller choose the flag


def test_confirmations_are_append_only_row_level_secured_and_front_desk_writes_only_as_itself():
    assert "alter table mpi.identity_confirmations enable row level security" in LOW and "force row level security" in LOW
    assert "unique (patient_id)" in LOW and "idempotency_key text not null unique" in LOW
    assert "restrict_violation" in LOW and "before update or delete" in LOW and "before truncate" in LOW
    insert = CODE.split("create policy identity_confirmations_insert_front_desk")[1].split(";")[0]
    assert "array['front_desk']" in insert and "confirmed_by = auth.uid()" in insert
    select = CODE.split("create policy identity_confirmations_select")[1].split(";")[0]
    assert "array['front_desk', 'nurse', 'doctor', 'queue_worker']" in select
    assert "grant insert, select on mpi.identity_confirmations to careflow_app" in LOW and "revoke all on mpi.identity_confirmations from careflow_app" in LOW


def test_migration_0022_is_not_edited_by_0023():
    assert "0022" in SQL and "never edits a merged migration" in SQL


def test_a_confirmation_carries_the_origin_of_its_patient_and_the_app_role_gets_nothing_beyond_insert_and_select():
    assert "create trigger identity_confirmations_origin before insert on mpi.identity_confirmations" in LOW
    assert "p.origin = new.origin" in LOW and "p.clinic_id = new.clinic_id" in LOW
    assert "revoke all on mpi.identity_confirmations from careflow_app" in LOW and "grant insert, select on mpi.identity_confirmations to careflow_app" in LOW
    assert "rows that existed before 0023" in SQL.lower()  # the pre-0023 residual is stated in the header


# ---- migration 0025 as text ----------------------------------------------------------------------------------------------------------------------

SQL25 = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0025_identity_confirmation_revoke.sql").read_text(encoding="utf-8")
CODE25 = "\n".join(line for line in SQL25.splitlines() if not line.strip().startswith("--"))
LOW25 = CODE25.lower()


def test_0025_makes_the_confirmations_an_event_log_with_a_closed_reason_list_and_adds_no_grant_role_or_other_policy():
    assert "drop constraint identity_confirmations_patient_id_key" in LOW25  # one row per patient is gone; the idempotency key stays unique (0023)
    assert "kind text not null default 'confirm' check (kind in ('confirm', 'revoke'))" in LOW25
    assert "reason in ('wrong_person', 'planted_or_suspect', 'mistaken_click')" in LOW25 and "(kind = 'revoke') = (reason is not null)" in LOW25
    assert "seq bigint generated always as identity" in LOW25
    assert not re.search(r"\bgrant\b|\bcreate role\b|\balter role\b|\bsecurity definer\b", LOW25)  # no new grant, role or definer function
    assert LOW25.count("create policy") == 1 and LOW25.count("drop policy") == 1  # exactly the one review_tasks insert policy is replaced


def test_0025_extends_the_review_task_insert_policy_by_exactly_one_front_desk_clause_for_one_kind_assigned_to_nurse_or_doctor():
    policy = CODE25.split("create policy review_tasks_insert")[1].split(";")[0].lower()
    assert "iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker'])" in policy  # the 0013 clauses are kept
    assert "iam.has_clinic_role(clinic_id, array['front_desk', 'admin']) and kind = 'unmerge_review'" in policy
    assert "iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'identity_confirmation_revoked' and assigned_role in ('nurse', 'doctor')" in policy
    assert policy.count(" or ") == 2  # three clauses in all
    assert "review_tasks_select" not in LOW25 and "create policy" in LOW25 and LOW25.count("create policy") == 1  # the SELECT policy is untouched: front desk cannot read the task
    # bound to a revoke event the same person wrote: open, unacknowledged, key and source fixed by the event id (no squatting on another writer's predictable key)
    assert "status = 'open' and acknowledged_by is null" in policy and "e.confirmed_by = auth.uid()" in policy
    assert "review_tasks.idempotency_key = 'identity-revoke-review:' || e.id::text" in policy and "review_tasks.source_message_id = 'identity-revoke:' || e.id::text" in policy


def test_the_application_never_reads_back_or_arbitrates_a_task_front_desk_cannot_see():
    source = (Path(__file__).resolve().parent.parent / "careflow" / "mpi.py").read_text(encoding="utf-8")
    task_insert = source.split("insert into agents.review_tasks")[1].split('"""')[0]
    assert "returning" not in task_insert.lower() and "on conflict" not in task_insert.lower()


@pytest.mark.asyncio
async def test_a_record_merged_into_another_is_refused_for_confirm_and_revoke_because_the_matcher_reads_its_master():
    class Merged(ConfirmConn):
        async def execute(self, statement, params=None):
            if "from mpi.merges" in str(statement):
                self.calls.append((str(statement), params or {}))
                return Result([(1,)])
            return await super().execute(statement, params)

    for conn, call in ((Merged(latest=CONFIRMED), lambda c: mpi.revoke_identity(c, task=TASK, **RKW)), (Merged(), lambda c: mpi.confirm_identity(c, **KW))):
        with pytest.raises(mpi.RecordIsMerged):
            await call(conn)
        assert conn.inserts("mpi.identity_confirmations") == [] and conn.inserts("agents.review_tasks") == []


@pytest.mark.asyncio
async def test_a_key_held_by_a_row_this_role_cannot_see_is_an_idempotency_conflict_not_a_server_error():
    class Collides(ConfirmConn):
        async def execute(self, statement, params=None):
            if "insert into mpi.identity_confirmations" in str(statement):
                raise db_error("23505")
            return await super().execute(statement, params)

    with pytest.raises(mpi.IdempotencyConflict):
        await mpi.confirm_identity(Collides(), **KW)  # type: ignore[arg-type]
    with pytest.raises(mpi.IdempotencyConflict):
        await mpi.revoke_identity(Collides(latest=CONFIRMED), task=TASK, **RKW)  # type: ignore[arg-type]
