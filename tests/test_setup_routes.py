"""The clinic set-up routes (locations, providers, visit types, slots) with a scripted fake connection: the gate (404/403), the idempotency rules, the overlap and reference errors, validation and the audit
call. The real database behaviour (row-level security, the exclusion constraint) is in the role matrix, which runs only in CI. Offline."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from careflow.app import Settings, create_app, db_conn, key_scope, verified_claims

CLINIC, ACTOR = uuid.uuid4(), uuid.uuid4()
PROJECT = "https://example-project.supabase.co"
HEADERS = {"X-Idempotency-Key": "k-1"}


class _Result:
    def __init__(self, row=None, rows=None):
        self._row, self._rows = row, rows

    def first(self):
        return self._row

    def scalar(self):
        return self._row


class Fake:
    """Answers only the statements the routes issue. `visible` = the clinic row is visible; `roles` = the roles held; `existing` = the row an `on conflict do nothing` found; `raise_state` = SQLSTATE of an insert error."""

    def __init__(self, *, visible=True, roles=("front_desk",), existing=None, raise_state=None):
        self.visible, self.roles, self.existing, self.raise_state = visible, roles, existing, raise_state
        self.inserted: list[dict] = []

    def begin_nested(self):
        class Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return Ctx()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "from iam.clinics" in sql:
            return _Result(("test",) if self.visible else None)
        if "unnest(iam.effective_roles" in sql:
            held = [r for r in params["r"] if r in self.roles]
            return _Result((held[0],) if held else None)
        if sql.lstrip().startswith("insert into sched."):
            if self.raise_state:
                err = DBAPIError("insert", {}, SimpleNamespace(sqlstate=self.raise_state))
                raise err
            if self.existing is not None:
                return _Result(None)
            self.inserted.append(params)
            return _Result((uuid.uuid4(),))
        if sql.lstrip().startswith("select * from sched."):
            return _Result(self.existing)
        raise AssertionError(sql)


def client(monkeypatch, fake):
    async def fake_db(_claims=Depends(verified_claims)):
        yield fake

    async def fake_claims():
        return {"sub": str(ACTOR), "role": "authenticated"}

    audits: list = []

    async def write_audit(conn, **kw):
        audits.append(kw)
        return True

    monkeypatch.setattr("careflow.app.write_audit", write_audit)
    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), engine=object(), jwks=object())  # type: ignore[arg-type]
    app.dependency_overrides[db_conn] = fake_db
    app.dependency_overrides[verified_claims] = fake_claims
    return TestClient(app, raise_server_exceptions=False), audits


LOCATION = {"clinic_id": str(CLINIC), "name": "  Room 1  "}
SLOT = {
    "clinic_id": str(CLINIC), "provider_id": str(uuid.uuid4()), "location_id": str(uuid.uuid4()), "visit_type_id": str(uuid.uuid4()),
    "starts_at": "2100-01-01T10:00:00+00:00", "ends_at": "2100-01-01T10:30:00+00:00",
}
BASE = "/v1/staff/scheduling"


def test_front_desk_and_admin_create_and_one_audit_row_is_written_with_ids_only(monkeypatch):
    for roles in (("front_desk",), ("admin",)):
        fake = Fake(roles=roles)
        c, audits = client(monkeypatch, fake)
        with c:
            r = c.post(f"{BASE}/locations", json=LOCATION, headers=HEADERS)
        assert r.status_code == 200 and r.json()["replayed"] is False and fake.inserted[0]["name"] == "Room 1"  # trimmed
        assert len(audits) == 1 and audits[0]["action"] == "setup.create_location" and audits[0]["actor_role"] == roles[0] and audits[0]["details"] == {"replayed": False}
        assert audits[0]["idempotency_key"].startswith("audit:setup.create_location:") and audits[0]["resource_type"] == "location"


@pytest.mark.parametrize("roles", [("nurse",), ("doctor",), ("auditor",), ()])
def test_any_other_role_is_403_and_nothing_is_written(monkeypatch, roles):
    fake = Fake(roles=roles)
    c, audits = client(monkeypatch, fake)
    with c:
        r = c.post(f"{BASE}/providers", json={"clinic_id": str(CLINIC), "display_name": "Dr Test"}, headers=HEADERS)
    assert (r.status_code, r.json()) == (403, {"detail": "your role may not do this"}) and fake.inserted == [] and audits == []


def test_a_clinic_the_caller_cannot_see_is_the_fixed_404(monkeypatch):
    c, audits = client(monkeypatch, Fake(visible=False))
    with c:
        r = c.post(f"{BASE}/visit-types", json={"clinic_id": str(CLINIC), "name": "Review", "duration_minutes": 15}, headers=HEADERS)
    assert (r.status_code, r.json()) == (404, {"detail": "not found"}) and audits == []


def test_a_replay_with_the_same_content_answers_the_original_and_different_content_is_a_409(monkeypatch):
    prior = SimpleNamespace(id=uuid.uuid4(), name="Room 1")
    c, audits = client(monkeypatch, Fake(existing=prior))
    with c:
        same = c.post(f"{BASE}/locations", json=LOCATION, headers=HEADERS)
        other = c.post(f"{BASE}/locations", json={**LOCATION, "name": "Room 2"}, headers=HEADERS)
    assert same.status_code == 200 and same.json() == {"id": str(prior.id), "clinic_id": str(CLINIC), "replayed": True}
    assert (other.status_code, other.json()) == (409, {"detail": "this idempotency key was already used by a different request"})
    assert len(audits) == 1 and audits[0]["details"] == {"replayed": True}


def test_a_slot_overlap_and_an_unknown_reference_map_to_the_fixed_answers(monkeypatch):
    for state, expected in (("23P01", (409, {"detail": "that time overlaps another slot of this provider"})), ("23503", (404, {"detail": "not found"})), ("42501", (403, {"detail": "your role may not do this"}))):
        c, audits = client(monkeypatch, Fake(raise_state=state))
        with c:
            r = c.post(f"{BASE}/slots", json=SLOT, headers=HEADERS)
        assert (r.status_code, r.json()) == expected and audits == [], state


@pytest.mark.parametrize(
    "change",
    [
        {"ends_at": "2000-01-01T00:00:00+00:00"},  # ends before it starts
        {"starts_at": "2100-01-01T10:00:00", "ends_at": "2100-01-01T10:30:00"},  # no zone
        {"ends_at": "2100-01-03T00:00:00+00:00"},  # longer than a day
        {"surprise": 1},
    ],
)
def test_bad_slots_are_rejected_before_any_database_work(monkeypatch, change):
    fake = Fake()
    c, _ = client(monkeypatch, fake)
    with c:
        r = c.post(f"{BASE}/slots", json={**SLOT, **change}, headers=HEADERS)
    assert r.status_code == 422 and fake.inserted == []


@pytest.mark.parametrize("duration", [0, -1, 1441])
def test_a_visit_type_needs_a_sensible_length(monkeypatch, duration):
    c, _ = client(monkeypatch, Fake())
    with c:
        r = c.post(f"{BASE}/visit-types", json={"clinic_id": str(CLINIC), "name": "Review", "duration_minutes": duration}, headers=HEADERS)
    assert r.status_code == 422


def test_the_key_is_required_and_the_row_key_names_the_table_the_clinic_and_the_clients_key(monkeypatch):
    fake = Fake()
    c, _ = client(monkeypatch, fake)
    with c:
        assert c.post(f"{BASE}/locations", json=LOCATION).status_code == 422  # no X-Idempotency-Key
        assert c.post(f"{BASE}/locations", json=LOCATION, headers=HEADERS).status_code == 200
    assert fake.inserted[0]["k"] == f"setup:locations:{CLINIC}:{key_scope(str(ACTOR))}:k-1"
