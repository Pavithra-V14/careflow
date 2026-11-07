"""Offer delivery by a person (GET /offers, POST /offers/{hold_id}/mark-delivered) with a scripted fake connection: the gate, the replay, the not-an-offer and not-live conflicts, and the single closed channel.
The database behaviour (row-level security, the append-only trigger) is in the role matrix and migration 0031's verification block. Offline."""

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from careflow.app import Settings, create_app, db_conn, verified_claims

CLINIC, ACTOR, HOLD = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
PROJECT = "https://example-project.supabase.co"
HEADERS = {"X-Idempotency-Key": "k-1"}
NOW = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)


class _R:
    def __init__(self, row=None, rows=None):
        self._row, self._rows = row, rows

    def __iter__(self):
        return iter(self._rows or [])

    def first(self):
        return self._row

    def all(self):
        return self._rows or []

    def scalar(self):
        return self._row


class Fake:
    def __init__(self, *, hold="live", roles=("front_desk",), delivered=None, race=False):
        self.hold, self.roles, self.delivered, self.race = hold, roles, delivered, race
        self.inserted: list[dict] = []
        self.reads = 0

    def begin_nested(self):
        class Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return Ctx()

    def _hold_row(self):
        if self.hold is None:
            return None
        status = {"live": "active", "released": "released", "converted": "converted", "plain": "active"}[self.hold]
        return SimpleNamespace(clinic_id=CLINIC, origin="test", status=status, live=self.hold in ("live", "plain"), is_offer=self.hold != "plain")

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "from iam.clinics c where iam.has_clinic_role" in sql:
            return _R(rows=[(CLINIC,)])
        if "from sched.holds where id" in sql:
            return _R(self._hold_row())
        if "iam.has_clinic_role" in sql:
            return _R(any(r in self.roles for r in params["r"]))
        if "unnest(iam.effective_roles" in sql:
            held = [r for r in params["r"] if r in self.roles]
            return _R((held[0],) if held else None)
        if "select delivered_at, channel from sched.offer_deliveries" in sql:
            self.reads += 1
            if self.reads == 1:
                return _R(self.delivered)  # the first look: has a person already marked it?
            return _R(SimpleNamespace(delivered_at=NOW, channel="manual"))  # the look after losing a race: the winner's row
        if sql.lstrip().startswith("insert into sched.offer_deliveries"):
            if self.race:
                return _R(None)  # on conflict do nothing: somebody else's press won
            self.inserted.append(params)
            return _R(SimpleNamespace(delivered_at=NOW, channel="manual"))
        if "with o as" in sql:
            return _R(rows=[SimpleNamespace(id=HOLD, clinic_id=CLINIC, slot_id=uuid.uuid4(), patient_id=None, created_at=NOW, expires_at=NOW, delivered_at=None, channel=None, message_id=None, state="awaiting_delivery")])
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


URL = f"/v1/staff/scheduling/offers/{HOLD}/mark-delivered"


def test_front_desk_marks_an_offer_delivered_by_hand_with_one_audit_row(monkeypatch):
    fake = Fake()
    c, audits = client(monkeypatch, fake)
    with c:
        r = c.post(URL, headers=HEADERS)
    assert r.status_code == 200 and r.json()["channel"] == "manual" and r.json()["replayed"] is False
    assert fake.inserted[0]["u"] == ACTOR and fake.inserted[0]["k"] == f"offer-delivery:{HOLD}:k-1"
    assert len(audits) == 1 and audits[0]["action"] == "offer.mark_delivered" and audits[0]["details"] == {"channel": "manual", "replayed": False}


def test_a_second_press_is_a_replay_and_writes_nothing_more(monkeypatch):
    fake = Fake(delivered=SimpleNamespace(delivered_at=NOW, channel="manual"))
    c, audits = client(monkeypatch, fake)
    with c:
        r = c.post(URL, headers=HEADERS)
    assert r.status_code == 200 and r.json()["replayed"] is True and fake.inserted == []
    assert audits[0]["details"]["replayed"] is True


def test_a_concurrent_press_that_won_makes_this_one_a_replay(monkeypatch):
    c, _ = client(monkeypatch, Fake(race=True))
    with c:
        r = c.post(URL, headers=HEADERS)
    assert r.status_code == 200 and r.json()["replayed"] is True


@pytest.mark.parametrize(
    ("fake", "expected"),
    [
        (Fake(hold=None), (404, {"detail": "not found"})),
        (Fake(roles=("nurse",)), (403, {"detail": "your role may not do this"})),
        (Fake(roles=("admin",)), (403, {"detail": "your role may not do this"})),
        (Fake(hold="plain"), (409, {"detail": "this hold is not an offer"})),
        (Fake(hold="released"), (409, {"detail": "this offer is no longer live"})),
        (Fake(hold="converted"), (409, {"detail": "this offer is no longer live"})),
    ],
)
def test_the_gate_and_the_conflicts(monkeypatch, fake, expected):
    c, audits = client(monkeypatch, fake)
    with c:
        r = c.post(URL, headers=HEADERS)
    assert (r.status_code, r.json()) == expected and fake.inserted == [] and audits == []


def test_the_key_is_required_and_the_list_carries_ids_times_and_closed_words_only(monkeypatch):
    c, _ = client(monkeypatch, Fake())
    with c:
        assert c.post(URL).status_code == 422
        listed = c.get("/v1/staff/scheduling/offers")
        bad = c.get("/v1/staff/scheduling/offers?state=sent")
    assert listed.status_code == 200 and bad.status_code == 422
    assert set(listed.json()["items"][0]) == {"hold_id", "clinic_id", "slot_id", "patient_id", "state", "created_at", "expires_at", "delivered_at", "channel", "message_id"}
    assert listed.json()["items"][0]["state"] == "awaiting_delivery"
