"""The audited erasure route (POST /v1/staff/patients/{id}/erase) with a scripted connection: the required reason list, the gate, the step-up requirement, and the mapping of the database function's refusals.
The function itself, the column grants and the audit row are checked live (the role matrix and tests/test_patient_binding_live.py). Offline."""

import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from careflow.app import Settings, create_app, db_conn, verified_claims

CLINIC, ACTOR, PATIENT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
PROJECT = "https://example-project.supabase.co"
HEADERS = {"X-Idempotency-Key": "k-1"}
BODY = {"reason": "entered_in_error"}
URL = f"/v1/staff/patients/{PATIENT}/erase"


class _R:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row

    def scalar(self):
        return self._row


class Fake:
    def __init__(self, *, visible=True, roles=("front_desk",), result=True, raise_state=None):
        self.visible, self.roles, self.result, self.raise_state = visible, roles, result, raise_state
        self.calls: list[dict] = []

    def begin_nested(self):
        class Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return Ctx()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "from mpi.patients where id" in sql:
            return _R((CLINIC,) if self.visible else None)
        if "iam.has_clinic_role" in sql:
            return _R(any(r in self.roles for r in params["r"]))
        if "mpi.erase_patient" in sql:
            if self.raise_state:
                raise DBAPIError("select", {}, SimpleNamespace(sqlstate=self.raise_state))  # type: ignore[arg-type]
            self.calls.append(params)
            return _R(self.result)
        raise AssertionError(sql)


def client(monkeypatch, fake, *, reasons="entered_in_error,requested_by_patient", stepped=True):
    async def fake_db(_claims=Depends(verified_claims)):
        yield fake

    async def fake_claims():
        claims = {"sub": str(ACTOR), "role": "authenticated"}
        if stepped:
            claims |= {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 5}]}
        return claims

    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused", erase_reasons=reasons, step_up_max_age_seconds=300), engine=object(), jwks=object())  # type: ignore[arg-type]
    app.dependency_overrides[db_conn] = fake_db
    app.dependency_overrides[verified_claims] = fake_claims
    return TestClient(app, raise_server_exceptions=False)


def test_front_desk_with_a_second_factor_erases_through_the_database_function(monkeypatch):
    fake = Fake()
    with client(monkeypatch, fake) as c:
        r = c.post(URL, json=BODY, headers=HEADERS)
    assert (r.status_code, r.json()) == (200, {"patient_id": str(PATIENT), "erased": True})
    assert fake.calls == [{"p": PATIENT, "r": "entered_in_error", "k": "k-1"}]


def test_an_already_erased_patient_is_a_replay(monkeypatch):
    with client(monkeypatch, Fake(result=False)) as c:
        assert c.post(URL, json=BODY, headers=HEADERS).json()["erased"] is False


def test_without_a_configured_list_of_reasons_the_route_is_unavailable(monkeypatch):
    for reasons in (None, "", " , "):
        fake = Fake()
        with client(monkeypatch, fake, reasons=reasons) as c:
            r = c.post(URL, json=BODY, headers=HEADERS)
        assert (r.status_code, r.json()) == (503, {"detail": "erasure is unavailable: not configured"}) and fake.calls == []


def test_a_reason_not_on_the_list_is_rejected_and_nothing_is_called(monkeypatch):
    fake = Fake()
    with client(monkeypatch, fake) as c:
        assert c.post(URL, json={"reason": "other_reason"}, headers=HEADERS).status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize(
    ("fake", "expected"),
    [
        (Fake(visible=False), (404, {"detail": "not found"})),
        (Fake(roles=("nurse",)), (403, {"detail": "your role may not do this"})),
        (Fake(raise_state="23514"), (409, {"detail": "this patient is in a live merge link"})),
        (Fake(raise_state="42501"), (403, {"detail": "your role may not do this"})),
    ],
)
def test_the_gate_and_the_mapped_refusals(monkeypatch, fake, expected):
    with client(monkeypatch, fake) as c:
        r = c.post(URL, json=BODY, headers=HEADERS)
    assert (r.status_code, r.json()) == expected


def test_a_session_without_a_recent_second_factor_is_told_to_step_up_before_anything_runs(monkeypatch):
    fake = Fake()
    with client(monkeypatch, fake, stepped=False) as c:
        r = c.post(URL, json=BODY, headers=HEADERS)
    assert (r.status_code, r.json()) == (401, {"detail": "step-up required"}) and r.headers["www-authenticate"] == "step-up" and fake.calls == []


def test_the_key_and_the_body_are_required(monkeypatch):
    with client(monkeypatch, Fake()) as c:
        assert c.post(URL, json=BODY).status_code == 422
        assert c.post(URL, json={}, headers=HEADERS).status_code == 422
        assert c.post(URL, json={"reason": "Entered In Error"}, headers=HEADERS).status_code == 422
