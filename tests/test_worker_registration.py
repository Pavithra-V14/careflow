"""Pre-launch blocker 1: migration 0022 read as TEXT, and the pipeline's function path against a fake connection. No database. The live checks (the attack really fails, as the
restricted role) are in tests/test_rls_mpi_worker_live.py."""

import base64
import os
import re
import uuid
from pathlib import Path

import pytest

from careflow import mpi
from careflow.crypto import DemographicVault

SQL = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0022_worker_registration_function.sql").read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.strip().startswith("--"))
LOW = CODE.lower()


def test_the_function_is_a_pinned_security_definer_that_checks_the_caller_is_a_worker_of_the_clinic():
    assert "create function mpi.register_patient_from_referral(" in LOW
    assert "security definer set search_path = ''" in LOW
    assert "iam.has_clinic_role(p_clinic, array['queue_worker'])" in LOW and "errcode = '42501'" in LOW


def test_no_parameter_names_an_existing_patient_and_the_writes_bind_to_the_one_new_patient():
    names = re.findall(r"^\s+(p_\w+)\s", CODE.split("returns uuid")[0], re.MULTILINE)
    assert names == [
        "p_clinic", "p_origin", "p_name_ct", "p_name_kv", "p_dob_ct", "p_dob_kv", "p_phone_ct", "p_phone_kv", "p_idempotency_key", "p_token_kinds", "p_tokens", "p_token_kv", "p_candidates",
    ], names  # no patient id, no row id
    body = CODE.split("returns uuid", 1)[1]
    assert body.count("insert into mpi.patients") == 1 and "returning id into v_patient" in body
    tokens = body.split("insert into mpi.identity_tokens")[1].split(";")[0]
    assert "select v_patient, p_clinic" in tokens  # tokens belong to the new patient only
    cands = body.split("insert into mpi.match_candidates")[1].split(";")[0]
    assert "values (p_clinic, v_patient, v_other" in cands and "'pending'" in cands  # the patient side is always the new record, status forced
    assert "v_other = v_patient" in body  # never itself


def test_it_writes_nothing_else_and_every_object_is_schema_qualified():
    assert not re.search(r"\b(update|delete from|truncate)\b", LOW.split("returns uuid")[1].split("$$;")[0])
    for table in re.findall(r"\b(?:insert into|from|join)\s+([a-z_.]+)", LOW.split("returns uuid", 1)[1].split("$$;")[0]):
        if table in ("unnest", "jsonb_array_elements", "jsonb_array_elements_text"):
            continue
        assert table.startswith(("mpi.", "iam.")) or table in ("k", "t", "e"), table


def test_bounds_and_closed_sets_are_enforced_in_the_function():
    assert "not in ('live', 'test', 'eval', 'canary')" in LOW
    assert "v_n < 1 or v_n > 2" in LOW and "k not in ('name_dob', 'phone')" in LOW
    assert "jsonb_array_length(p_candidates) > 20" in LOW
    assert "v_reason not in ('near_match', 'ambiguous')" in LOW
    assert "p.erased_at is null" in LOW and "p.origin = p_origin" in LOW and "p.clinic_id = p_clinic" in LOW


def test_the_review_hardening_is_in_the_text():
    assert "matched kinds must be distinct members of the closed set" in LOW
    assert "each candidate must name a different patient" in LOW and "a candidate patient_id must be a uuid" in LOW
    body = CODE.split("returns uuid", 1)[1]
    assert "using errcode = '42501'" in body and body.count("errcode = '42501'") == 1  # 42501 only for the caller check; a bad candidate is 22023


def test_execute_goes_to_the_app_role_only_and_the_three_worker_insert_policies_are_dropped_and_nothing_else():
    assert "revoke all on function mpi.register_patient_from_referral" in LOW and " from public;" in LOW
    assert LOW.count("grant execute on function mpi.register_patient_from_referral") == 1 and "to careflow_app;" in LOW
    assert "'anon', 'authenticated', 'service_role'" in LOW
    assert LOW.count("drop policy") == 3
    for name in ("patients_insert_worker on mpi.patients", "tokens_insert_worker on mpi.identity_tokens", "match_candidates_insert_worker on mpi.match_candidates"):
        assert f"drop policy {name}" in LOW
    assert "create policy" not in LOW and "grant insert" not in LOW and "alter table" not in LOW  # the worker's SELECT policies are untouched, nothing is added to any table


def test_the_merged_migration_0019_is_untouched():
    text = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0019_mpi_queue_worker_ingestion.sql").read_text(encoding="utf-8")
    assert "create policy patients_insert_worker" in text and "create policy tokens_insert_worker" in text  # fixed forward by 0022, never edited


# ---- the pipeline: the function path hands over exactly what the direct path inserts ------------------------------------------------------------


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
    """Records every statement. No token matches, so the pipeline creates a record."""

    def __init__(self, new_id):
        self.calls, self.new_id = [], new_id

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
        if "register_patient_from_referral" in sql or "returning id" in sql:
            return Result(scalar=self.new_id)
        return Result()


def make(registrar):
    k = lambda: base64.b64encode(os.urandom(32)).decode()
    vault = DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": k(), "CAREFLOW_HMAC_SECRET_V1": k()})
    return mpi.PatientIngestionPipeline(vault, key_version=1, registrar=registrar)


ARGS = {"clinic_id": uuid.uuid4(), "origin": "test", "name": "Asha Venkatesh", "dob": "1987-04-23", "phone": "9876543210", "idempotency_key": "referral:doc-1"}


@pytest.mark.asyncio
async def test_the_function_path_makes_one_call_and_no_direct_insert():
    new_id = uuid.uuid4()
    conn = Conn(new_id)
    result = await make("function").ingest(conn, **ARGS)  # type: ignore[arg-type]
    assert (result.outcome, result.patient_id) == ("created", new_id)
    writes = [(sql, p) for sql, p in conn.calls if "insert into" in sql.lower() or "register_patient_from_referral" in sql]
    assert len(writes) == 1 and "register_patient_from_referral" in writes[0][0] and "insert into" not in writes[0][0].lower()
    params = writes[0][1]
    assert params["clinic"] == ARGS["clinic_id"] and params["origin"] == "test" and params["idem"] == "referral:doc-1"
    assert params["kinds"] == ["name_dob", "phone"] and len(params["toks"]) == 2 and all(isinstance(t, bytes) for t in params["toks"])
    assert params["cands"] == "[]" and params["kv"] == 1


@pytest.mark.asyncio
async def test_the_direct_path_is_unchanged_front_desk_still_inserts_its_own_rows():
    conn = Conn(uuid.uuid4())
    await make("direct").ingest(conn, **ARGS)  # type: ignore[arg-type]
    inserts = [sql for sql, _ in conn.calls if "insert into" in sql.lower()]
    assert len(inserts) == 3 and "mpi.patients" in inserts[0] and all("identity_tokens" in s for s in inserts[1:])
    assert not any("register_patient_from_referral" in sql for sql, _ in conn.calls)


def test_the_registrar_must_be_one_of_the_two_and_defaults_to_the_direct_front_desk_path():
    with pytest.raises(ValueError):
        make("anything-else")
    assert make("direct")._registrar == "direct"
    k = lambda: base64.b64encode(os.urandom(32)).decode()
    vault = DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": k(), "CAREFLOW_HMAC_SECRET_V1": k()})
    assert mpi.PatientIngestionPipeline(vault, key_version=1)._registrar == "direct"
