"""The merge routes, offline (BUILD-SPEC.md §17.2, §18.1; CLAUDE.md rules 3, 5, 8): no database, no network, no real token.

The identity module and the connection are fakes, so this proves the ROUTE's contract: the step-up gate comes first (one fixed 401, a visible
503 while the window is unset, nothing reaches the database), the 404/403 split, one fixed body per refusal class, ids and statuses only,
the required X-Idempotency-Key, and the origin read from the clinic row. The role matrix against the real database is in
tests/test_api_role_matrix.py; the step-up claim shape itself is unverified against a real token (docs/design/step-up-spike.md).
"""

import logging
import uuid
from typing import ClassVar

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from careflow import identity
from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.auth import JWKSClient

PROJECT = "https://example-project.supabase.co"
CLINIC, MASTER, SUB, MERGE, USER = (uuid.uuid4() for _ in range(5))
NOW = 1_800_000_000.0
SECRET = "Zz Sentinel Person 7731"


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeConn:
    def __init__(self, *, clinic_origin="test", front_desk=True, merge_clinic=CLINIC):
        self.clinic_origin, self.front_desk, self.merge_clinic = clinic_origin, front_desk, merge_clinic
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "from iam.clinics" in sql:
            return _Result((self.clinic_origin,) if self.clinic_origin else None)
        if "from iam.staff_roles" in sql:
            return _Result((1,) if self.front_desk else None)
        if "from mpi.merges" in sql:
            return _Result((self.merge_clinic,) if self.merge_clinic else None)
        raise AssertionError(sql)


class FakeManager:
    calls: ClassVar[list] = []
    error: Exception | None = None
    result = None

    async def _do(self, name, *args):
        FakeManager.calls.append((name, args))
        if FakeManager.error:
            raise FakeManager.error
        return FakeManager.result

    async def execute_merge(self, *a):
        return await self._do("propose", *a)

    async def approve_merge(self, *a):
        return await self._do("approve", *a)

    async def execute_unmerge(self, *a):
        return await self._do("unmerge", *a)


def stepped_claims(**over):
    base = {"sub": str(USER), "role": "authenticated", "aal": "aal2", "amr": [{"method": "totp", "timestamp": int(NOW) - 5}]}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    monkeypatch.delenv("CAREFLOW_STEP_UP_MAX_AGE_SECONDS", raising=False)  # Settings() reads the environment (and .env)
    monkeypatch.setattr("careflow.app.identity.IdentityManager", FakeManager)
    FakeManager.calls, FakeManager.error = [], None
    FakeManager.result = identity.MergeResult(MERGE, "awaiting_second_approval", MASTER, SUB)


def make_client(*, conn=None, claims=None, window=300):
    fake = conn or FakeConn()

    async def fake_db(_claims=Depends(verified_claims)):
        yield fake

    async def fake_claims():
        return stepped_claims() if claims is None else claims

    settings = Settings(supabase_url=PROJECT, database_url_app="postgresql://unused", step_up_max_age_seconds=window)
    app = create_app(
        settings, jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(), clock=lambda: NOW  # type: ignore[arg-type]
    )
    app.dependency_overrides[db_conn] = fake_db
    app.dependency_overrides[verified_claims] = fake_claims
    return app, fake


def propose(c, key="k-1", **over):
    payload = {"clinic_id": str(CLINIC), "master_patient_id": str(MASTER), "subordinate_patient_id": str(SUB), **over}
    return c.post("/v1/staff/merges", json=payload, headers={"X-Idempotency-Key": key} if key else {})


def act(c, verb, key="k-1"):
    return c.post(f"/v1/staff/merges/{MERGE}/{verb}", headers={"X-Idempotency-Key": key} if key else {})


def _propose(c, key="k-1"):
    return propose(c, key=key)


def _approve(c, key="k-1"):
    return act(c, "approve", key=key)


def _unmerge(c, key="k-1"):
    return act(c, "unmerge", key=key)


ALL = (_propose, _approve, _unmerge)


# ---- step-up ---------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("call", ALL)
@pytest.mark.parametrize(
    "claims",
    [
        stepped_claims(aal="aal1"),
        stepped_claims(amr=None),
        stepped_claims(amr=[{"method": "totp", "timestamp": int(NOW) - 301}]),
        stepped_claims(amr=[{"method": "totp", "timestamp": int(NOW) + 60}]),
        stepped_claims(amr=[{"method": "password", "timestamp": int(NOW)}]),
        stepped_claims(amr=["totp"]),
    ],
)
def test_not_stepped_up_is_one_fixed_401_and_nothing_reaches_the_database(call, claims, caplog):
    caplog.set_level(logging.DEBUG)
    app, conn = make_client(claims=claims)
    with TestClient(app) as c:
        r = call(c)
    assert (r.status_code, r.json(), r.headers["www-authenticate"]) == (401, {"detail": "step-up required"}, "step-up")
    assert conn.statements == [] and FakeManager.calls == []
    assert "step-up refused:" in caplog.text and str(USER) not in caplog.text


@pytest.mark.parametrize("call", ALL)
@pytest.mark.parametrize("window", [None, 0, -5])
def test_an_unset_window_is_a_visible_503_with_an_error_log(call, window, caplog):
    app, conn = make_client(window=window)
    with TestClient(app) as c:
        r = call(c)
    assert (r.status_code, r.json()) == (503, {"detail": "step-up re-authentication is not configured"})
    assert any(rec.levelno == logging.ERROR for rec in caplog.records)
    assert conn.statements == [] and FakeManager.calls == []


# ---- the routes ------------------------------------------------------------------------------------------------------------------------


def test_propose_returns_ids_and_a_status_only_and_reads_origin_from_the_clinic():
    app, _ = make_client(conn=FakeConn(clinic_origin="canary"))
    with TestClient(app) as c:
        r = propose(c)
    assert r.status_code == 200
    assert r.json() == {
        "merge_id": str(MERGE), "status": "awaiting_second_approval", "master_patient_id": str(MASTER),
        "subordinate_patient_id": str(SUB), "replayed": False,
    }
    ((name, args),) = FakeManager.calls
    assert name == "propose" and args[2] == "canary" and args[5] == USER and args[6] == "k-1"


def test_a_client_supplied_origin_is_an_unknown_field():
    app, _ = make_client()
    with TestClient(app) as c:
        assert propose(c, origin="live").status_code == 422
    assert FakeManager.calls == []


def test_approve_and_unmerge_return_ids_and_statuses():
    app, _ = make_client()
    FakeManager.result = identity.MergeResult(MERGE, "active", MASTER, SUB)
    with TestClient(app) as c:
        approved = act(c, "approve").json()
    assert approved["status"] == "active"
    assert set(approved) == {"merge_id", "status", "master_patient_id", "subordinate_patient_id", "replayed"}
    FakeManager.result = identity.UnmergeResult(MERGE, uuid.uuid4(), 3)
    with TestClient(app) as c:
        out = act(c, "unmerge").json()
    assert set(out) == {"merge_id", "status", "review_task_id", "decisions_flagged", "replayed"}
    assert out["status"] == "unmerged" and out["decisions_flagged"] == 3
    FakeManager.result = identity.UnmergeResult(MERGE, None, 0)
    with TestClient(app) as c:
        assert act(c, "unmerge").json()["review_task_id"] is None  # a never-confirmed proposal raises no task


@pytest.mark.parametrize("call", ALL)
def test_the_idempotency_key_is_required_and_not_blank(call):
    app, _ = make_client()
    with TestClient(app) as c:
        assert call(c, key=None).status_code == 422
        assert call(c, key="   ").status_code == 422
    assert FakeManager.calls == []


@pytest.mark.parametrize("call", ALL)
def test_an_invisible_clinic_is_404_and_a_non_front_desk_visitor_is_403(call):
    for conn, status in ((FakeConn(clinic_origin=None), 404), (FakeConn(front_desk=False), 403)):
        app, _ = make_client(conn=conn)
        with TestClient(app) as c:
            assert call(c).status_code == status
    assert FakeManager.calls == []


@pytest.mark.parametrize("call", [_approve, _unmerge])
def test_an_invisible_or_unknown_merge_is_404(call):
    app, _ = make_client(conn=FakeConn(merge_clinic=None))
    with TestClient(app) as c:
        r = call(c)
    assert (r.status_code, r.json()) == (404, {"detail": "not found"})
    assert FakeManager.calls == []


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (identity.PatientNotFound("both patients must exist in this clinic"), 404, "not found"),
        (identity.MergeNotFound("x"), 404, "not found"),
        (identity.NotPermitted("x"), 403, "your role may not do this"),
        (identity.IdempotencyConflict("x"), 409, "this idempotency key was already used by a different request"),
        (identity.InvalidTransition(f"this merge is active {SECRET}"), 409, "this merge is not in a state that allows that"),
        (identity.MergeNotAllowed(f"a merge needs two different people to approve it {SECRET}"), 409, "this merge cannot be carried out"),
    ],
)
@pytest.mark.parametrize("call", ALL)
def test_every_identity_refusal_is_one_fixed_body_that_carries_no_detail(call, error, status, detail, caplog):
    caplog.set_level(logging.DEBUG)
    FakeManager.error = error
    app, _ = make_client()
    with TestClient(app) as c:
        r = call(c)
    assert (r.status_code, r.json()) == (status, {"detail": detail})
    assert SECRET not in r.text and SECRET not in caplog.text


def test_the_same_person_approving_gets_the_same_body_as_any_other_not_allowed_refusal():
    app, _ = make_client()
    with TestClient(app) as c:
        FakeManager.error = identity.MergeNotAllowed("a merge needs two different people to approve it")
        same = act(c, "approve")
        FakeManager.error = identity.MergeNotAllowed("that record is already part of a live merge link")
        other = propose(c)
    assert (same.status_code, same.json()) == (other.status_code, other.json()) == (409, {"detail": "this merge cannot be carried out"})
