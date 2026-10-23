"""POST /v1/staff/patients, offline: no database, no network, no real token (BUILD-SPEC.md §17.2; CLAUDE.md rules 5, 6, 7, 8).

The token check, the database transaction and the ingestion pipeline are replaced by fakes, so this proves the ROUTE's own contract:
ids and an outcome only in the response, fixed error bodies, the 404/403 split, the required X-Idempotency-Key, the origin read from the
clinic row (never the client), and the visible 503 when the key vault or the active key version is not configured. Above all it proves the
privacy contract with REAL request validation: a sentinel name, date of birth and phone must never appear in any response or log line.
The role matrix against the real database is in tests/test_api_role_matrix.py.
"""

import base64
import logging
import os
import uuid
from typing import ClassVar

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from careflow import app as app_module
from careflow import mpi
from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.auth import JWKSClient
from careflow.crypto import DemographicVault

PROJECT = "https://example-project.supabase.co"
PATH = "/v1/staff/patients"
CLINIC = uuid.uuid4()
NAME, DOB, PHONE = "Zz Sentinel Person 7731", "1987-04-23", "9876543210"
SENTINELS = (NAME, "7731", DOB, PHONE, "Sentinel")


def _vault(*versions) -> DemographicVault:
    return DemographicVault.from_env(
        {f"CAREFLOW_{k}_SECRET_V{v}": base64.b64encode(os.urandom(32)).decode() for v in (versions or (1,)) for k in ("ENC", "HMAC")}
    )


class _Row:
    def __init__(self, *values):
        self._values = values

    def __getitem__(self, i):
        return self._values[i]


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeConn:
    """Answers the route's two lookups: the clinic's origin (None = invisible) and the front-desk check."""

    def __init__(self, origin="test", front_desk=True):
        self.origin, self.front_desk = origin, front_desk

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "from iam.clinics" in sql:
            return _Result(None if self.origin is None else _Row(self.origin))
        if "from iam.staff_roles" in sql:
            return _Result(_Row(1) if self.front_desk else None)
        raise AssertionError(f"unexpected statement: {sql}")


class FakePipeline:
    calls: ClassVar[list] = []
    result = None
    error: Exception | None = None

    def __init__(self, vault, *, key_version):
        FakePipeline.calls.append(("init", key_version))

    async def ingest(self, conn, **kwargs):
        FakePipeline.calls.append(("ingest", kwargs))
        if FakePipeline.error is not None:
            raise FakePipeline.error
        return FakePipeline.result or mpi.IngestResult("created", uuid.uuid4())


@pytest.fixture(autouse=True)
def _pipeline(monkeypatch):
    monkeypatch.delenv("CAREFLOW_ACTIVE_KEY_VERSION", raising=False)  # Settings() reads the environment (and .env)
    FakePipeline.calls, FakePipeline.result, FakePipeline.error = [], None, None
    monkeypatch.setattr(app_module, "PatientIngestionPipeline", FakePipeline)


def make_client(*, conn=None, vault="default", settings_kwargs=None, authenticated=True):
    fake = conn or FakeConn()

    async def fake_db(claims=Depends(verified_claims)):
        yield fake

    async def fake_claims():
        return {"sub": str(uuid.uuid4()), "role": "authenticated"}

    app = create_app(
        Settings(supabase_url=PROJECT, database_url_app="postgresql://unused", **(settings_kwargs or {})),
        jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(),  # type: ignore[arg-type]
        vault=_vault() if vault == "default" else vault,
    )
    app.dependency_overrides[db_conn] = fake_db
    if authenticated:
        app.dependency_overrides[verified_claims] = fake_claims
    return app


def body(**over):
    base = {"clinic_id": str(CLINIC), "name": NAME, "dob": DOB, "phone": PHONE}
    base.update(over)
    return base


def post(client, payload=None, key="k-1"):
    headers = {"X-Idempotency-Key": key} if key is not None else {}
    return client.post(PATH, json=body() if payload is None else payload, headers=headers)


def assert_clean(response, caplog):
    for sentinel in SENTINELS:
        assert sentinel not in response.text, sentinel
        assert sentinel not in caplog.text, sentinel


# ---- the contract ------------------------------------------------------------------------------------------------------------------


def test_a_registration_returns_ids_and_an_outcome_only(caplog):
    caplog.set_level(logging.DEBUG)
    pid, other = uuid.uuid4(), uuid.uuid4()
    FakePipeline.result = mpi.IngestResult("review", pid, (other,))
    with TestClient(make_client()) as c:
        r = post(c)
    assert r.status_code == 200
    assert r.json() == {"patient_id": str(pid), "outcome": "review", "candidate_ids": [str(other)]}
    assert_clean(r, caplog)


@pytest.mark.parametrize("outcome", ["matched", "created", "review", "replayed"])
def test_every_outcome_is_returned_as_is(outcome):
    FakePipeline.result = mpi.IngestResult(outcome, uuid.uuid4())
    with TestClient(make_client()) as c:
        assert post(c).json()["outcome"] == outcome


def test_the_idempotency_key_reaches_the_pipeline_and_is_required():
    with TestClient(make_client()) as c:
        for key in (None, "", "   "):
            assert post(c, key=key).status_code == 422
        assert FakePipeline.calls == []
        assert post(c, key="abc-123").status_code == 200
    (_, kwargs) = FakePipeline.calls[-1]
    assert kwargs["idempotency_key"] == "abc-123" and kwargs["clinic_id"] == CLINIC
    assert (kwargs["name"], kwargs["dob"], kwargs["phone"]) == (NAME, DOB, PHONE)


def test_the_origin_comes_from_the_clinic_row_never_from_the_client():
    with TestClient(make_client(conn=FakeConn(origin="eval"))) as c:
        assert post(c).status_code == 200
        assert FakePipeline.calls[-1][1]["origin"] == "eval"
        FakePipeline.calls.clear()
        assert post(c, payload=body(origin="live")).status_code == 422  # a client-supplied origin is an unknown field
    assert FakePipeline.calls == []
    with TestClient(make_client(conn=FakeConn(origin="canary"))) as c:
        post(c)
    assert FakePipeline.calls[-1][1]["origin"] == "canary"


def test_an_absent_phone_is_passed_as_none():
    with TestClient(make_client()) as c:
        assert post(c, payload={"clinic_id": str(CLINIC), "name": NAME, "dob": DOB}).status_code == 200
    assert FakePipeline.calls[-1][1]["phone"] is None


def test_unauthenticated_is_401_before_validation():
    with TestClient(make_client(authenticated=False)) as c:
        assert post(c).status_code == 401
        assert post(c, key=None).status_code == 401


def test_a_clinic_you_cannot_see_is_404_and_a_clinic_you_can_see_without_being_front_desk_is_403():
    with TestClient(make_client(conn=FakeConn(origin=None))) as c:
        gone = post(c)
    with TestClient(make_client(conn=FakeConn(front_desk=False))) as c:
        nope = post(c)
    assert (gone.status_code, gone.json()) == (404, {"detail": "not found"})
    assert (nope.status_code, nope.json()) == (403, {"detail": "your role may not register patients"})
    assert FakePipeline.calls == []  # neither reached the pipeline: a matched person is never looked up for a non-front-desk caller


# ---- errors name the problem and never echo the person --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (mpi.NotPermitted(f"{NAME} {PHONE}"), 403, "your role may not register patients"),
        (mpi.IdempotencyConflict(f"{NAME} {DOB}"), 409, "this idempotency key was already used by a different request"),
        (mpi.InvalidDateOfBirth(f"{DOB} {NAME}"), 422, "date of birth must be a real past date in YYYY-MM-DD form"),
        (mpi.InvalidPhone(f"{PHONE} {NAME}"), 422, "phone must be a valid Indian mobile number"),
        (mpi.MissingRequiredField(f"{NAME}"), 422, "a required field is missing or blank"),
        (mpi.IngestionError(f"{NAME} {DOB} {PHONE}"), 422, "a required field is missing or blank"),
    ],
)
def test_ingestion_errors_map_to_fixed_bodies_even_if_the_exception_text_held_the_person(error, status, detail, caplog):
    FakePipeline.error = error
    caplog.set_level(logging.DEBUG)
    with TestClient(make_client()) as c:
        r = post(c)
    assert (r.status_code, r.json()) == (status, {"detail": detail})
    assert_clean(r, caplog)


@pytest.mark.parametrize(
    "payload",
    [
        {"clinic_id": str(CLINIC), "name": NAME * 20, "dob": DOB},            # name too long: the sentinel is the offending input
        {"clinic_id": str(CLINIC), "name": NAME, "dob": DOB + DOB},          # dob too long
        {"clinic_id": str(CLINIC), "name": NAME, "dob": 19870423},           # wrong type
        {"clinic_id": str(CLINIC), "name": NAME, "dob": DOB, "phone": PHONE * 5},
        {"clinic_id": "not-a-uuid", "name": NAME, "dob": DOB, "phone": PHONE},
        {"clinic_id": str(CLINIC), "name": NAME, "dob": DOB, "phone": PHONE, "note": f"{NAME} {PHONE}"},  # an unknown field with the person in it
        {"name": NAME, "dob": DOB, "phone": PHONE},                          # no clinic
        {"clinic_id": str(CLINIC), "name": "", "dob": DOB, "phone": PHONE},
    ],
)
def test_a_validation_error_never_echoes_the_input(payload, caplog):
    caplog.set_level(logging.DEBUG)
    with TestClient(make_client()) as c:
        r = post(c, payload=payload)
    assert r.status_code == 422
    for error in r.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}  # no `input`, no `ctx`, no `url`
    assert_clean(r, caplog)
    assert FakePipeline.calls == []


def test_malformed_json_containing_the_person_is_not_echoed_either(caplog):
    caplog.set_level(logging.DEBUG)
    with TestClient(make_client()) as c:
        r = c.post(PATH, content=f'{{"name": "{NAME}", "dob": "{DOB}", "phone": "{PHONE}" '.encode(), headers={"X-Idempotency-Key": "k", "Content-Type": "application/json"})
    assert r.status_code == 422
    assert_clean(r, caplog)


def test_the_validation_handler_applies_app_wide_and_keeps_type_loc_and_msg():
    with TestClient(make_client()) as c:
        r = c.post("/v1/staff/messages/not-a-uuid/screen", headers={"X-Idempotency-Key": "k"})
    assert r.status_code == 422 and set(r.json()["detail"][0]) == {"type", "loc", "msg"}


def test_a_name_that_cannot_be_encoded_is_a_fixed_422_that_does_not_echo_the_character(caplog):
    """A lone surrogate reaches the encryption/HMAC step as a UnicodeEncodeError holding the offending character."""
    FakePipeline.error = UnicodeEncodeError("utf-8", "Zz Sentinel \ud800 7731", 12, 13, "surrogates not allowed")
    caplog.set_level(logging.DEBUG)
    with TestClient(make_client()) as c:
        r = post(c)
    assert (r.status_code, r.json()) == (422, {"detail": "the name contains characters that cannot be stored"})
    assert_clean(r, caplog)


def test_a_lone_surrogate_in_the_request_never_reaches_a_500_with_the_character(caplog):
    """Whatever pydantic does with the raw JSON escape, the response is a 422 (or a handled fixed error) and never echoes the name."""
    caplog.set_level(logging.DEBUG)
    with TestClient(make_client(), raise_server_exceptions=False) as c:
        r = c.post(PATH, content=(f'{{"clinic_id": "{CLINIC}", "name": "{NAME} \\ud800", "dob": "{DOB}"}}').encode(),
                   headers={"X-Idempotency-Key": "k", "Content-Type": "application/json"})
    assert r.status_code in {200, 422}
    for sentinel in SENTINELS:
        assert sentinel not in r.text


def test_an_unexpected_failure_is_a_bare_500_that_does_not_echo_the_person(caplog):
    FakePipeline.error = RuntimeError(f"{NAME} {DOB} {PHONE}")
    caplog.set_level(logging.DEBUG)
    with TestClient(make_client(), raise_server_exceptions=False) as c:
        r = post(c)
    assert r.status_code == 500
    for sentinel in SENTINELS:
        assert sentinel not in r.text


# ---- deployment faults are visible, never silent ------------------------------------------------------------------------------------------


def test_without_a_key_vault_registration_is_a_503_with_an_error_log_and_nothing_is_written(monkeypatch, caplog):
    monkeypatch.setattr(app_module, "_load_vault", lambda: None)
    caplog.set_level(logging.ERROR, logger="careflow.app")
    with TestClient(make_client(vault=None)) as c:
        r = post(c)
    assert r.status_code == 503 and "encryption keys are not configured" in r.json()["detail"]
    assert FakePipeline.calls == []
    assert len([x for x in caplog.records if x.levelno == logging.ERROR]) == 1
    assert_clean(r, caplog)


def test_several_key_versions_without_an_explicit_active_one_is_a_visible_503(caplog):
    caplog.set_level(logging.ERROR, logger="careflow.app")
    with TestClient(make_client(vault=_vault(1, 2))) as c:
        r = post(c)
    assert r.status_code == 503 and "active encryption key version" in r.json()["detail"]
    assert FakePipeline.calls == [] and "CAREFLOW_ACTIVE_KEY_VERSION" in caplog.text


def test_an_active_version_that_is_not_loaded_is_a_503():
    with TestClient(make_client(vault=_vault(1, 2), settings_kwargs={"active_key_version": 5})) as c:
        assert post(c).status_code == 503
    assert FakePipeline.calls == []


def test_the_configured_active_version_is_the_one_new_rows_are_written_under():
    with TestClient(make_client(vault=_vault(1, 2), settings_kwargs={"active_key_version": 2})) as c:
        assert post(c).status_code == 200
    assert FakePipeline.calls[0] == ("init", 2)


def test_one_loaded_version_needs_no_configuration():
    with TestClient(make_client(vault=_vault(3))) as c:
        assert post(c).status_code == 200
    assert FakePipeline.calls[0] == ("init", 3)


def test_the_active_key_version_can_be_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("CAREFLOW_ACTIVE_KEY_VERSION", "2")
    assert Settings(supabase_url=PROJECT, database_url_app="postgresql://x").active_key_version == 2


# ---- structure -----------------------------------------------------------------------------------------------------------------------------


def test_the_response_model_forbids_extra_fields_and_has_no_personal_field():
    from careflow.app import IngestOut

    assert IngestOut.model_config.get("extra") == "forbid"
    assert set(IngestOut.model_fields) == {"patient_id", "outcome", "candidate_ids"}


def test_the_request_model_forbids_unknown_fields_so_origin_cannot_be_supplied():
    from careflow.app import IngestIn

    assert IngestIn.model_config.get("extra") == "forbid"
    assert set(IngestIn.model_fields) == {"clinic_id", "name", "dob", "phone"}


def test_the_route_is_a_post_with_the_required_idempotency_header_in_the_schema():
    spec = create_app(Settings(supabase_url=PROJECT, database_url_app="postgresql://unused")).openapi()
    op = spec["paths"]["/v1/staff/patients"]["post"]
    header = [p for p in op["parameters"] if p["in"] == "header"]
    assert [p["name"] for p in header] == ["X-Idempotency-Key"] and header[0]["required"] is True
