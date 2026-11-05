"""The staff account routes with a scripted connection and the fake Auth admin: the 503 without an adapter, no address in any log or response, the mapping of the database functions' refusals, and the
second-factor and step-up requirements. The functions themselves are tested live (tests/test_staff_accounts_live.py) and the routes in the role matrix. Offline."""

import asyncio
import logging
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.auth_admin import (
    AuthAdminError,
    FakeAuthAdmin,
    SupabaseAuthAdmin,
    from_environment,
)

CLINIC, ACTOR = uuid.uuid4(), uuid.uuid4()
PROJECT = "https://example-project.supabase.co"
HEADERS = {"X-Idempotency-Key": "k-1"}
EMAIL = "someone.invented@example.test"
BASE = "/v1/staff/accounts"


class _R:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row

    def scalar(self):
        return self._row


class Fake:
    def __init__(self, *, clinic=True, roles=("admin",), result=True, raise_state=None):
        self.clinic, self.roles, self.result, self.raise_state = clinic, roles, result, raise_state
        self.calls: list[tuple[str, dict]] = []

    def begin_nested(self):
        class Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return Ctx()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "select 1 from iam.clinics" in sql:
            return _R((1,) if self.clinic else None)
        if "unnest(iam.effective_roles" in sql:
            held = [r for r in params["r"] if r in self.roles]
            return _R((held[0],) if held else None)
        if sql.lstrip().startswith("select iam."):
            if self.raise_state:
                raise DBAPIError("select", {}, SimpleNamespace(sqlstate=self.raise_state))  # type: ignore[arg-type]
            self.calls.append((sql, params))
            return _R(self.result)
        raise AssertionError(sql)


def client(fake, *, port=None, stepped=True):
    async def fake_db(_claims=Depends(verified_claims)):
        yield fake

    async def fake_claims():
        claims = {"sub": str(ACTOR), "role": "authenticated"}
        if stepped:
            claims |= {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 5}]}
        return claims

    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused", step_up_max_age_seconds=300), engine=object(), jwks=object(), auth_admin=port)  # type: ignore[arg-type]
    app.dependency_overrides[db_conn] = fake_db
    app.dependency_overrides[verified_claims] = fake_claims
    return TestClient(app, raise_server_exceptions=False)


INVITE = {"clinic_id": str(CLINIC), "email": EMAIL, "role": "nurse"}


def test_an_invitation_creates_the_sign_in_through_the_port_then_records_the_account_and_no_address_is_kept(caplog):
    caplog.set_level(logging.DEBUG)
    port, fake = FakeAuthAdmin(), Fake()
    with client(fake, port=port) as c:
        r = c.post(f"{BASE}/invite", json=INVITE, headers=HEADERS)
    body = r.json()
    assert r.status_code == 200 and body["replayed"] is False and body["role"] == "nurse" and EMAIL not in r.text and EMAIL not in caplog.text
    assert port.invited[EMAIL] == uuid.UUID(body["account_id"])
    ((sql, params),) = fake.calls
    assert "iam.invite_staff_account" in sql and EMAIL not in str(params) and params["r"] == "nurse"


def test_without_an_auth_admin_adapter_the_invite_route_is_unavailable_and_nothing_is_recorded():
    fake = Fake()
    with client(fake, port=None) as c:
        r = c.post(f"{BASE}/invite", json=INVITE, headers=HEADERS)
    assert (r.status_code, r.json()) == (503, {"detail": "invitations are unavailable: not configured"}) and fake.calls == []


def test_a_failing_adapter_is_a_fixed_422_with_nothing_recorded():
    class Broken:
        async def invite(self, email):
            raise AuthAdminError("the invitation could not be created")

    fake = Fake()
    with client(fake, port=Broken()) as c:
        r = c.post(f"{BASE}/invite", json=INVITE, headers=HEADERS)
    assert (r.status_code, r.json()) == (422, {"detail": "request rejected"}) and fake.calls == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/invite", INVITE),
        (f"/{ACTOR}/roles", {"clinic_id": str(CLINIC), "role": "nurse"}),
        (f"/{ACTOR}/roles/revoke", {"clinic_id": str(CLINIC), "role": "nurse"}),
        (f"/{ACTOR}/suspend", None),
        (f"/{ACTOR}/reinstate", None),
    ],
)
def test_every_admin_write_needs_a_recent_second_factor_before_anything_runs(path, body):
    fake = Fake()
    with client(fake, port=FakeAuthAdmin(), stepped=False) as c:
        r = c.post(BASE + path, json=body, headers=HEADERS)
    assert (r.status_code, r.json()) == (401, {"detail": "step-up required"}) and fake.calls == []


def test_a_non_admin_is_403_and_an_invisible_clinic_is_404():
    with client(Fake(roles=("nurse",)), port=FakeAuthAdmin()) as c:
        assert c.post(f"{BASE}/{ACTOR}/roles", json={"clinic_id": str(CLINIC), "role": "nurse"}, headers=HEADERS).status_code == 403
    with client(Fake(clinic=False), port=FakeAuthAdmin()) as c:
        assert c.post(f"{BASE}/{ACTOR}/roles", json={"clinic_id": str(CLINIC), "role": "nurse"}, headers=HEADERS).status_code == 404


@pytest.mark.parametrize(("state", "expected"), [("42501", (403, "your role may not do this")), ("23514", (409, "a clinic must keep an active administrator"))])
def test_the_database_refusals_map_to_fixed_answers(state, expected):
    with client(Fake(raise_state=state)) as c:
        r = c.post(f"{BASE}/{ACTOR}/roles/revoke", json={"clinic_id": str(CLINIC), "role": "admin"}, headers=HEADERS)
    assert (r.status_code, r.json()) == (expected[0], {"detail": expected[1]})


def test_queue_worker_cannot_be_named_in_any_request():
    with client(Fake(), port=FakeAuthAdmin()) as c:
        assert c.post(f"{BASE}/invite", json={**INVITE, "role": "queue_worker"}, headers=HEADERS).status_code == 422
        assert c.post(f"{BASE}/{ACTOR}/roles", json={"clinic_id": str(CLINIC), "role": "queue_worker"}, headers=HEADERS).status_code == 422


def test_activate_me_runs_the_database_function_for_the_caller():
    fake = Fake()
    with client(fake) as c:
        assert c.post(f"{BASE}/activate-me").json() == {"activated": True}
    assert "iam.activate_own_account" in fake.calls[0][0]


# ---- the fake and the unverified real adapter -----------------------------------------------------------------------------------------------------------------------------------


def test_the_fake_is_idempotent_per_address_and_rejects_nonsense():
    fake = FakeAuthAdmin()
    first, again = asyncio.run(fake.invite(EMAIL)), asyncio.run(fake.invite(EMAIL.upper()))
    assert first == again
    with pytest.raises(AuthAdminError):
        asyncio.run(fake.invite("nope"))


def test_the_real_adapter_refuses_to_exist_unverified_or_without_a_key():
    for verified in (None, "not-a-date", "2999-01-01"):
        with pytest.raises(AuthAdminError):
            SupabaseAuthAdmin(PROJECT, "a-key", verified_on=verified)
    with pytest.raises(AuthAdminError):
        SupabaseAuthAdmin(PROJECT, "", verified_on="2026-01-01")
    with pytest.raises(AuthAdminError):
        SupabaseAuthAdmin("http://insecure.example", "a-key", verified_on="2026-01-01")
    assert SupabaseAuthAdmin(PROJECT, "a-key", verified_on="2026-01-01")
    assert from_environment({}, PROJECT) is None
    with pytest.raises(AuthAdminError):
        from_environment({"SUPABASE_SECRET_KEY": "k"}, PROJECT)  # a key without a verified date
