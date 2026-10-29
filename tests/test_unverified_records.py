"""Pre-launch blocker 10 (planted tokens), option A: a record created by the unattended worker is UNVERIFIED until a front-desk person confirms it, and an unverified record is never a
confident match. Offline: the matcher's rule, the pipeline against a fake connection, the confirm function, and migration 0023 read as TEXT. The live checks (as the restricted
role, including the end-to-end planted-token attack) are in tests/test_rls_mpi_worker_live.py."""

import base64
import os
import re
import uuid
from pathlib import Path

import pytest

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
    assert "pm.registered_via = 'front_desk' or ic.id is not null" in sql
    assert "left join mpi.identity_confirmations ic on ic.patient_id = pm.id" in sql
    assert "join mpi.patients pm on pm.id = coalesce(mg.master_patient_id, t.patient_id)" in sql  # a subordinate takes its master's status


# ---- confirm_identity -----------------------------------------------------------------------------------------------------------------------


class ConfirmConn:
    def __init__(self, *, by_key=None, live=True, inserted=True):
        self.by_key, self.live, self.inserted, self.calls = by_key, live, inserted, []

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
        if "from mpi.identity_confirmations where idempotency_key" in sql:
            return Result([self.by_key] if self.by_key else [])
        if "from mpi.patients" in sql:
            return Result([(1,)] if self.live else [])
        if "insert into mpi.identity_confirmations" in sql:
            return Result([(uuid.uuid4(),)] if self.inserted else [])
        raise AssertionError(sql)


KW = {"clinic_id": uuid.uuid4(), "origin": "test", "patient_id": uuid.uuid4(), "confirmed_by": uuid.uuid4(), "idempotency_key": "k-1"}


@pytest.mark.asyncio
async def test_a_confirmation_is_one_insert_by_the_caller_and_not_a_replay():
    conn = ConfirmConn()
    result = await mpi.confirm_identity(conn, **KW)  # type: ignore[arg-type]
    assert (result.patient_id, result.replayed) == (KW["patient_id"], False)
    (insert,) = [(s, p) for s, p in conn.calls if "insert into" in s]
    assert insert[1]["u"] == KW["confirmed_by"] and insert[1]["k"] == "k-1" and "on conflict (patient_id) do nothing" in insert[0]
    assert not any(re.search(r"\b(update|delete)\b", s.lower()) for s, _ in conn.calls)  # the patient row is never touched


@pytest.mark.asyncio
async def test_the_same_key_for_the_same_patient_replays_and_for_another_patient_conflicts():
    assert (await mpi.confirm_identity(ConfirmConn(by_key=(KW["patient_id"], KW["clinic_id"])), **KW)).replayed is True  # type: ignore[arg-type]
    with pytest.raises(mpi.IdempotencyConflict):
        await mpi.confirm_identity(ConfirmConn(by_key=(uuid.uuid4(), KW["clinic_id"])), **KW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_an_already_confirmed_patient_answers_as_a_replay_and_an_unknown_one_is_not_found():
    assert (await mpi.confirm_identity(ConfirmConn(inserted=False), **KW)).replayed is True  # type: ignore[arg-type]
    with pytest.raises(mpi.PatientNotFound):
        await mpi.confirm_identity(ConfirmConn(live=False), **KW)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_blank_key_and_an_unknown_origin_are_refused_before_any_query():
    for bad in ({"idempotency_key": "  "}, {"origin": "production"}):
        conn = ConfirmConn()
        with pytest.raises((mpi.MissingRequiredField, ValueError)):
            await mpi.confirm_identity(conn, **{**KW, **bad})  # type: ignore[arg-type]
        assert conn.calls == []


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
