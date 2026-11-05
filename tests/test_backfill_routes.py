"""The backfill routes and the runner (careflow/scheduling_runner.py, app.py) with a scripted connection and fake ports: the role gates, the 503 without components, the pause-by-pause flow through the HTTP
surface, a step the run is not waiting for (409), audit rows, and that nothing is delivered by the system. The graph itself is in tests/test_scheduling_graph.py. Offline."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.autonomy import Policy
from careflow.scheduling_runner import SchedulingComponents

CLINIC, ACTOR, SLOT, VT, P1, P2, W1, W2, REPLY = (uuid.uuid4() for _ in range(9))
PROJECT = "https://example-project.supabase.co"
H = {"X-Idempotency-Key": "k-1"}
BASE = "/v1/staff/scheduling/backfills"


class _R:
    def __init__(self, row=None, rows=None):
        self._row, self._rows = row, rows

    def first(self):
        return self._row

    def scalar(self):
        return self._row

    def __iter__(self):
        return iter(self._rows or [])


class Conn:
    def __init__(self, roles=("front_desk",), visible=True):
        self.roles, self.visible = roles, visible

    def begin_nested(self):
        class Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return Ctx()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "from iam.clinics where id" in sql:
            return _R(("test",) if self.visible else None)
        if "from sched.slots where id" in sql:
            return _R(SimpleNamespace(clinic_id=CLINIC, origin="test") if self.visible else None)
        if "iam.has_clinic_role(:c" in sql:
            return _R(any(r in self.roles for r in params["r"]))
        if "unnest(iam.effective_roles" in sql:
            held = [r for r in params["r"] if r in self.roles]
            return _R((held[0],) if held else None)
        raise AssertionError(sql)


class Ports:
    def __init__(self):
        self.log = []

    async def rank(self, conn, clinic_id, visit_type_id):
        return [{"waitlist_id": W1, "patient_id": P1, "rank": 1}, {"waitlist_id": W2, "patient_id": P2, "rank": 2}]

    async def send(self, conn, **kw):
        self.log.append("offer")
        return {"hold_id": str(uuid.uuid4()), "message_id": str(uuid.uuid4())}

    async def screen(self, conn, *, message_id):
        self.log.append("screen")
        return "clear"

    async def interpret(self, conn, *, message_id, clinic_id):
        self.log.append("model")
        return {"answer": "accept", "reason": None}

    async def book(self, conn, **kw):
        self.log.append("book")

    async def release(self, conn, **kw):
        self.log.append("release")

    async def raise_task(self, conn, **kw):
        self.log.append("task")
        return uuid.uuid4()


def client(monkeypatch, conn, *, configured=True, mode="assisted"):
    ports = Ports()
    components = SchedulingComponents(ports, ports, ports, ports, ports, ports, ports, Policy(mode), 2, "P-TEST", "front_desk", InMemorySaver()) if configured else None  # type: ignore[arg-type]

    async def fake_db(_claims=Depends(verified_claims)):
        yield conn

    async def fake_claims():
        return {"sub": str(ACTOR), "role": "authenticated"}

    audits = []

    async def write_audit(c, **kw):
        audits.append(kw)
        return True

    monkeypatch.setattr("careflow.app.write_audit", write_audit)
    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), engine=object(), jwks=object(), scheduling_components=components)  # type: ignore[arg-type]
    app.dependency_overrides[db_conn] = fake_db
    app.dependency_overrides[verified_claims] = fake_claims
    return TestClient(app, raise_server_exceptions=bool(__import__("os").environ.get("DEBUG_RAISE"))), ports, audits


START = {"clinic_id": str(CLINIC), "slot_id": str(SLOT), "visit_type_id": str(VT)}


def test_the_whole_flow_through_the_routes_a_person_presses_every_button_and_the_system_delivers_nothing(monkeypatch):
    c, ports, audits = client(monkeypatch, Conn())
    with c:
        started = c.post(BASE, json=START, headers=H).json()
        assert (started["waiting_for"], started["candidate_patient_id"], started["candidate_rank"], ports.log) == ("send_decision", str(P1), 1, [])  # nothing held yet
        sent = c.post(f"{BASE}/{SLOT}/decide", json={"action": "send"}, headers={"X-Idempotency-Key": "k-2"}).json()
        assert (sent["waiting_for"], sent["status"]) == ("reply_entry", "offer_sent") and sent["hold_id"] and ports.log == ["offer"]
        done = c.post(f"{BASE}/{SLOT}/reply", json={"outcome": "replied", "message_id": str(REPLY)}, headers={"X-Idempotency-Key": "k-3"}).json()
        assert (done["status"], done["finished"], done["waiting_for"]) == ("booked", True, "none") and ports.log == ["offer", "screen", "model", "book"]
        assert c.get(f"{BASE}/{SLOT}").json()["status"] == "booked"
    assert [a["action"] for a in audits] == ["backfill.start", "backfill.decide", "backfill.reply"] and all(a["actor_role"] == "front_desk" for a in audits)
    assert audits[1]["details"] == {"action": "send", "status": "offer_sent"} and all(str(P1) not in str(a["details"]) for a in audits)


def test_a_step_the_run_is_not_waiting_for_is_a_409_and_a_missing_run_is_a_404_and_a_second_start_is_the_same_run(monkeypatch):
    c, ports, _ = client(monkeypatch, Conn())
    with c:
        assert c.post(f"{BASE}/{SLOT}/decide", json={"action": "send"}, headers=H).status_code == 404  # no run yet
        c.post(BASE, json=START, headers=H)
        again = c.post(BASE, json=START, headers={"X-Idempotency-Key": "k-9"})
        assert again.status_code == 200 and again.json()["waiting_for"] == "send_decision"
        wrong = c.post(f"{BASE}/{SLOT}/reply", json={"outcome": "timed_out"}, headers=H)  # the run is waiting for a send decision, not a reply
        assert (wrong.status_code, wrong.json()) == (409, {"detail": "this run is not waiting for that"}) and ports.log == []


def test_a_reply_needs_a_message_exactly_when_the_outcome_is_replied(monkeypatch):
    c, _, _ = client(monkeypatch, Conn())
    with c:
        assert c.post(f"{BASE}/{SLOT}/reply", json={"outcome": "replied"}, headers=H).status_code == 422
        assert c.post(f"{BASE}/{SLOT}/reply", json={"outcome": "timed_out", "message_id": str(REPLY)}, headers=H).status_code == 422
        assert c.post(f"{BASE}/{SLOT}/decide", json={"action": "book it"}, headers=H).status_code == 422


@pytest.mark.parametrize(("roles", "expected"), [(("nurse",), 403), (("doctor",), 403), (("auditor",), 403), ((), 403)])
def test_only_front_desk_acts_and_a_clinic_the_caller_cannot_see_is_a_404(monkeypatch, roles, expected):
    c, ports, audits = client(monkeypatch, Conn(roles=roles))
    with c:
        assert c.post(BASE, json=START, headers=H).status_code == expected
        assert c.post(f"{BASE}/{SLOT}/decide", json={"action": "send"}, headers=H).status_code == expected
    assert ports.log == [] and audits == []
    c2, _, _ = client(monkeypatch, Conn(visible=False))
    with c2:
        assert c2.post(BASE, json=START, headers=H).status_code == 404 and c2.get(f"{BASE}/{SLOT}").status_code == 404


def test_nurse_and_doctor_may_read_the_run_but_not_act_on_it(monkeypatch):
    for roles in (("nurse",), ("doctor",), ("front_desk",)):
        c, _, _ = client(monkeypatch, Conn(roles=roles))
        with c:
            assert c.get(f"{BASE}/{SLOT}").status_code == 404  # no run yet: the gate passed, the run is missing
    c, _, _ = client(monkeypatch, Conn(roles=("auditor",)))
    with c:
        assert c.get(f"{BASE}/{SLOT}").status_code == 403


def test_without_configured_components_every_route_is_a_visible_503_after_the_role_check(monkeypatch):
    c, _, audits = client(monkeypatch, Conn(), configured=False)
    with c:
        for r in (c.post(BASE, json=START, headers=H), c.get(f"{BASE}/{SLOT}"), c.post(f"{BASE}/{SLOT}/decide", json={"action": "send"}, headers=H), c.post(f"{BASE}/{SLOT}/reply", json={"outcome": "timed_out"}, headers=H)):
            assert (r.status_code, r.json()) == (503, {"detail": "backfill runs are unavailable: not configured"})
    assert audits == []
    c2, _, _ = client(monkeypatch, Conn(roles=("nurse",)), configured=False)
    with c2:
        assert c2.post(BASE, json=START, headers=H).status_code == 403  # the role check comes first


def test_shadow_mode_never_books_even_after_an_accepted_reply(monkeypatch):
    c, ports, _ = client(monkeypatch, Conn(), mode="shadow")
    with c:
        c.post(BASE, json=START, headers=H)
        c.post(f"{BASE}/{SLOT}/decide", json={"action": "send"}, headers={"X-Idempotency-Key": "k-2"})
        done = c.post(f"{BASE}/{SLOT}/reply", json={"outcome": "replied", "message_id": str(REPLY)}, headers={"X-Idempotency-Key": "k-3"}).json()
    assert done["status"] == "booking_needs_human" and done["task_id"] and "book" not in ports.log
