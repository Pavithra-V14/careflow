"""POST /v1/staff/referrals/{document_id}/run and /review, offline: no database, no network, no real token (BUILD-SPEC.md 7.2, 12.1; CLAUDE.md rules 2, 5, 8).

The token check, the transaction, the graph and the queue are replaced by fakes, so this proves the ROUTES' own contract: the required X-Idempotency-Key, the exact response
shapes, the status mapping, the reviewer coming from the verified token and never the body, and the visible 503 when nothing is configured. The role matrix against the real
database (and the whole run end to end) is tests/test_api_referrals_live.py."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from careflow import app as app_module
from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.auth import JWKSClient
from careflow.checkpointer import CheckpointerError
from careflow.front_door import FrontDoorError
from careflow.front_door_runner import FrontDoorComponents
from careflow.queue import EnqueueResult
from careflow.triage_store import DecisionConflict, NotPermitted, TriageStoreError

PROJECT = "https://example-project.supabase.co"
RUN, REVIEW = "/v1/staff/referrals/{}/run", "/v1/staff/referrals/{}/review"
SUB = uuid.uuid4()
CLINIC = uuid.uuid4()
DOC = uuid.uuid4()


def components():
    return FrontDoorComponents(None, None, None, None, None, None, None, None, None)  # type: ignore[arg-type]


async def fake_db(claims=Depends(verified_claims)):
    yield object()


async def fake_claims():
    return {"sub": str(SUB), "role": "authenticated"}


class FakeGraph:
    def __init__(self, values=None, next_=()):
        self.state = SimpleNamespace(values=values or {}, next=next_)
        self.invoked = []
        self.raises = None

    async def aget_state(self, config):
        return self.state

    async def ainvoke(self, payload, config):
        self.invoked.append((payload, config))
        if self.raises:
            raise self.raises


@pytest.fixture
def world(monkeypatch):
    w = SimpleNamespace(enqueued=[], role_ok=True, document=SimpleNamespace(clinic_id=CLINIC, origin="test", kind="referral"), graph=FakeGraph())

    async def referral_document(conn, document_id):
        if w.document is None:
            raise app_module.HTTPException(status_code=404, detail="not found")
        return w.document

    async def holds_role(conn, claims, clinic_id, roles):
        w.roles_asked = roles
        return w.role_ok

    async def enqueue(self, conn, clinic_id, origin, task_type, payload, key):
        w.enqueued.append((clinic_id, origin, task_type, payload, key))
        return EnqueueResult(uuid.UUID(int=7), replayed=len(w.enqueued) > 1)

    monkeypatch.setattr(app_module, "_referral_document", referral_document)
    monkeypatch.setattr(app_module, "_holds_role", holds_role)
    monkeypatch.setattr(app_module.PostgresQueue, "enqueue", enqueue)
    monkeypatch.setattr(app_module, "graph_for", lambda components, session: w.graph)
    return w


def make_client(*, configured=True, authenticated=True):
    app = create_app(
        Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"),
        engine=object(), front_door=components() if configured else None,  # type: ignore[arg-type]
    )
    app.dependency_overrides[db_conn] = fake_db
    if authenticated:
        app.dependency_overrides[verified_claims] = fake_claims
    return TestClient(app)


def key(value="k-1"):
    return {"X-Idempotency-Key": value} if value is not None else {}


# ---- /run ----------------------------------------------------------------------------------------------------------------------------------


def test_run_enqueues_one_front_door_job_scoped_to_the_document_with_ids_only(world):
    with make_client() as c:
        r = c.post(RUN.format(DOC), headers=key("abc"))
    assert r.status_code == 200 and set(r.json()) == {"job_id", "replayed"} and r.json()["replayed"] is False
    ((clinic, origin, task, payload, k),) = world.enqueued
    assert (clinic, origin, task, payload, k) == (CLINIC, "test", "front_door_run", {"document_id": str(DOC)}, f"front-door-run:{DOC}:abc")
    assert world.roles_asked == ("front_desk", "nurse", "doctor")


def test_a_replay_reports_replayed_and_the_key_is_scoped_to_the_document(world):
    with make_client() as c:
        c.post(RUN.format(DOC), headers=key("same"))
        again = c.post(RUN.format(DOC), headers=key("same"))
        other_doc = c.post(RUN.format(uuid.uuid4()), headers=key("same"))
    assert again.json()["replayed"] is True and other_doc.status_code == 200
    assert world.enqueued[0][4] != world.enqueued[2][4]


def test_run_refuses_a_role_that_may_not_start_one_and_a_document_it_cannot_see(world):
    world.role_ok = False
    with make_client() as c:
        assert c.post(RUN.format(DOC), headers=key()).status_code == 403
        world.document = None
        assert c.post(RUN.format(DOC), headers=key()).status_code == 404
    assert world.enqueued == []


def test_run_needs_a_key_a_uuid_a_token_and_a_configured_front_door(world):
    with make_client() as c:
        for bad in (None, "", "   "):
            assert c.post(RUN.format(DOC), headers=key(bad)).status_code == 422
        assert c.post(RUN.format("nope"), headers=key()).status_code == 422
    with make_client(authenticated=False) as c:
        assert c.post(RUN.format(DOC), headers=key()).status_code == 401  # authentication comes first, even before the missing-key 422
        assert c.post(RUN.format(DOC)).status_code == 401
    with make_client(configured=False) as c:
        assert c.post(RUN.format(DOC), headers=key()).status_code == 503
    assert world.enqueued == []


# ---- /review -------------------------------------------------------------------------------------------------------------------------------


def paused():
    return FakeGraph({"status": "triage_proposed", "proposal_id": str(uuid.uuid4())}, ("review",))


def test_review_resumes_the_paused_run_with_the_reviewer_from_the_token_and_the_decision_from_the_body(world):
    world.graph = paused()
    with make_client() as c:
        r = c.post(REVIEW.format(DOC), headers=key(), json={"decision": "approve"})
    assert r.status_code == 200 and r.json() == {"document_id": str(DOC), "decision": "approve", "replayed": False}
    ((payload, config),) = world.graph.invoked
    assert payload.resume == {"decision": "approve", "reviewer_id": str(SUB)}
    assert config["configurable"]["thread_id"] == f"front_door:{DOC}" and config["configurable"]["clinic_id"] == str(CLINIC)
    assert world.roles_asked == ("nurse", "doctor")


def test_a_body_that_names_a_reviewer_or_an_unknown_decision_is_refused_and_nothing_is_resumed(world):
    world.graph = paused()
    with make_client() as c:
        assert c.post(REVIEW.format(DOC), headers=key(), json={"decision": "approve", "reviewer_id": str(uuid.uuid4())}).status_code == 422
        for bad in ({}, {"decision": "approve-all"}, {"decision": None}):
            assert c.post(REVIEW.format(DOC), headers=key(), json=bad).status_code == 422
        assert c.post(REVIEW.format(DOC), headers=key(None), json={"decision": "approve"}).status_code == 422
    assert world.graph.invoked == []


def test_review_maps_the_edges_404_403_409_and_503(world):
    with make_client() as c:
        world.graph = FakeGraph()  # no run for this document
        assert c.post(REVIEW.format(DOC), headers=key(), json={"decision": "approve"}).status_code == 404
        world.graph = FakeGraph({"status": "triage_proposed"}, ("match_protocol",))  # running, not waiting for a person
        assert c.post(REVIEW.format(DOC), headers=key(), json={"decision": "approve"}).status_code == 409
        world.role_ok = False
        assert c.post(REVIEW.format(DOC), headers=key(), json={"decision": "approve"}).status_code == 403
    with make_client(configured=False) as c:
        assert c.post(REVIEW.format(DOC), headers=key(), json={"decision": "approve"}).status_code == 503


def test_a_repeated_decision_is_a_replay_and_a_different_one_or_by_somebody_else_is_409(world):
    done = {"status": "reviewed", "decision": {"decision": "approve", "reviewer_id": str(SUB)}}
    world.graph = FakeGraph(done, ())
    with make_client() as c:
        same = c.post(REVIEW.format(DOC), headers=key(), json={"decision": "approve"})
        different = c.post(REVIEW.format(DOC), headers=key(), json={"decision": "reject"})
        world.graph = FakeGraph({"status": "reviewed", "decision": {"decision": "approve", "reviewer_id": str(uuid.uuid4())}}, ())
        other_person = c.post(REVIEW.format(DOC), headers=key(), json={"decision": "approve"})
    assert same.status_code == 200 and same.json()["replayed"] is True
    assert different.status_code == 409 and other_person.status_code == 409
    assert world.graph.invoked == []


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [(DecisionConflict("SECRET"), 409, "this referral is not waiting for a decision"), (NotPermitted("SECRET"), 403, "you cannot record this decision"),
     (TriageStoreError("SECRET"), 404, "not found"), (FrontDoorError("SECRET"), 503, "referral runs are unavailable"),
     (CheckpointerError("SECRET"), 503, "referral runs are unavailable")],
)
def test_review_maps_writer_errors_to_fixed_bodies_without_leaking_their_text(world, error, status, detail):
    world.graph = paused()
    world.graph.raises = error
    with make_client() as c:
        r = c.post(REVIEW.format(DOC), headers=key(), json={"decision": "reject"})
    assert (r.status_code, r.json()) == (status, {"detail": detail}) and "SECRET" not in r.text



def test_the_poke_is_given_only_after_a_successful_enqueue_and_a_refused_run_never_pokes(world):
    with make_client() as c:
        assert not c.app.state.poker._event.is_set()
        world.role_ok = False
        c.post(RUN.format(DOC), headers=key())
        assert not c.app.state.poker._event.is_set()  # nothing was enqueued, nobody is woken
        world.role_ok = True
        assert c.post(RUN.format(DOC), headers=key("poke")).status_code == 200
        assert c.app.state.poker._event.is_set()  # woken after the response (a background task), i.e. after the commit
