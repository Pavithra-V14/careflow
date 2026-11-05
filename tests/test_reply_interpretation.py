"""Reply interpretation, FAKES ONLY, offline (remaining-work A8; docs/design/phase3-scheduling-agent.md section 5). No database, no network, no real engine.

What is proven: the deterministic red-flag rules run FIRST and ALONE, on every path (a flagged or unassessable reply never reaches the decision tier, the order is screen then read then decide); only a CLEAR
patient reply on the appointments topic is put to the decision tier, as patient data; a spent ceiling is a VISIBLE stop ("no suggestion: needs a person"), never a silent skip; an uncalibrated confidence
is reported as unknown; no answer is ever the reply text; and the route refuses as documented. The same flow against the real database is in tests/test_api_screen_message_live.py.
"""

import base64
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from careflow import reply_interpretation as ri
from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.auth import JWKSClient
from careflow.crypto import DemographicVault
from careflow.decision import DecisionService, InMemoryDecisionRecorder
from careflow.gateway import Gateway, InMemorySpendLedger, ModelRow, SpendLimits
from careflow.messages import MessageErased, MessageNotFound, ReadMessage
from careflow.ports import (
    AdapterResult,
    DataClass,
    DecisionRequest,
    DecisionResult,
    PermanentError,
    Usage,
)

ZERO = Decimal(0)
CLINIC, MESSAGE, ACTOR, SCREENING = (uuid.uuid4() for _ in range(4))
SECRET = "my-private-sentence-4417"


def adapter_for(choice="accept", *, calibrated=False, confidence=0.9, calls=None, error=None):
    async def adapter(request: DecisionRequest):
        if calls is not None:
            calls.append(request)
        if error:
            raise error
        rest = (1 - confidence) / (len(request.options) - 1)
        probs = {o: (confidence if o == choice else rest) for o in request.options}
        return AdapterResult(DecisionResult(choice, probs, confidence, "fake-engine", calibrated, "fake-route"), Usage(1, 1))

    return adapter


def service_for(adapter, *, policy="phi_approved", price=ZERO, ceiling=Decimal(100)):
    row = ModelRow("decision", "fake", "fake-decision-1", policy, 1, price, price, ZERO, ZERO)
    gateway = Gateway([row], {("fake", "fake-decision-1"): adapter}, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=ceiling), origin="test")
    return DecisionService(gateway, InMemoryDecisionRecorder())


class Flow:
    """Replaces the two message functions with fakes that record the ORDER of the calls."""

    def __init__(self, monkeypatch, *, outcome="clear", sender="patient", topic="appointments", erased=False):
        self.log: list[str] = []
        self.outcome, self.sender, self.topic = outcome, sender, topic

        async def screen(conn, vault, engine, message_id, *, idempotency_key, profile=None):
            self.log.append("screen")
            if erased:
                raise MessageErased("erased")
            return SimpleNamespace(message_id=message_id, screening_id=SCREENING, outcome=self.outcome, replayed=False)

        async def read(conn, vault, message_id, *, actor_id):
            self.log.append("read")
            return ReadMessage(message_id=message_id, thread_id=uuid.uuid4(), clinic_id=CLINIC, topic=self.topic, sender_kind=self.sender, body=SECRET)

        monkeypatch.setattr(ri, "screen_stored_message", screen)
        monkeypatch.setattr(ri, "read_stored_message", read)


async def run(service, **kw):
    return await ri.interpret_reply(object(), object(), object(), service, MESSAGE, actor_id=ACTOR, idempotency_key="k-1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_clear_patient_reply_is_screened_first_then_read_then_put_to_the_decision_tier_as_patient_data(monkeypatch):
    flow, seen = Flow(monkeypatch), []
    out = await run(service_for(adapter_for("accept", calls=seen)))
    assert flow.log == ["screen", "read"] and len(seen) == 1  # the rules before the read, the read before the model
    request = seen[0]
    assert request.data_class is DataClass.PHI and request.options == ri.OPTIONS and request.question_type == "offer_reply" and request.text == SECRET
    assert (out.suggestion, out.outcome, out.reason, out.needs_person, out.engine) == ("accept", "clear", None, True, "fake-engine")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["red_flag", "unassessable"])
async def test_a_flagged_or_unassessable_reply_never_reaches_the_decision_tier_or_even_a_second_read(monkeypatch, outcome):
    flow, seen = Flow(monkeypatch, outcome=outcome), []
    out = await run(service_for(adapter_for("accept", calls=seen)))
    assert flow.log == ["screen"] and seen == []  # rule 2: the model is never asked; not even the text is read again
    assert (out.suggestion, out.reason, out.outcome, out.needs_person) == (None, outcome, outcome, True)
    assert out.confidence is None and out.engine is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("sender", "topic"), [("staff", "appointments"), ("patient", "care"), ("patient", "safety"), ("system", "appointments")])
async def test_only_a_patients_reply_on_the_appointments_topic_is_ever_put_to_the_model(monkeypatch, sender, topic):
    flow, seen = Flow(monkeypatch, sender=sender, topic=topic), []
    out = await run(service_for(adapter_for(calls=seen)))
    assert flow.log == ["screen", "read"] and seen == [] and (out.suggestion, out.reason) == (None, "not_a_patient_reply")


@pytest.mark.asyncio
async def test_a_spent_ceiling_is_a_visible_stop_never_a_silent_skip_or_a_made_up_answer(monkeypatch):
    Flow(monkeypatch)
    calls: list = []
    paid = service_for(adapter_for(calls=calls), price=Decimal("0.01"), ceiling=ZERO)  # a paid route with a daily ceiling of zero: spent already equals it
    out = await run(paid)
    assert (out.suggestion, out.reason, out.needs_person) == (None, "spend_ceiling", True) and calls == []


@pytest.mark.asyncio
async def test_no_approved_route_for_patient_text_and_an_engine_failure_are_both_no_suggestion(monkeypatch):
    Flow(monkeypatch)
    seen: list = []
    third_party = await run(service_for(adapter_for(calls=seen), policy="synthetic_only"))  # a synthetic_only engine is never offered patient text
    assert (third_party.suggestion, third_party.reason) == (None, "no_route") and seen == []
    broken = await run(service_for(adapter_for(error=PermanentError("boom"))))
    assert (broken.suggestion, broken.reason) == (None, "engine_failed")


@pytest.mark.asyncio
async def test_an_uncalibrated_confidence_is_unknown_and_a_calibrated_one_is_reported(monkeypatch):
    Flow(monkeypatch)
    raw = await run(service_for(adapter_for("decline", calibrated=False, confidence=0.97)))
    assert (raw.suggestion, raw.confidence, raw.calibrated) == ("decline", None, False)  # unknown is not a value: 0.97 is a raw score, not a probability
    calibrated = await run(service_for(adapter_for("question", calibrated=True, confidence=0.8)))
    assert (calibrated.suggestion, calibrated.confidence, calibrated.calibrated) == ("question", 0.8, True)


@pytest.mark.asyncio
async def test_nothing_the_model_says_can_change_the_screening_outcome_and_the_answer_carries_no_text(monkeypatch):
    Flow(monkeypatch)
    out = await run(service_for(adapter_for("unclear")))
    assert out.outcome == "clear" and out.suggestion == "unclear"  # "unclear" is a suggestion for a person; it is never consent
    assert SECRET not in repr(out) and set(out.__dataclass_fields__) == {
        "message_id", "screening_id", "outcome", "replayed", "suggestion", "confidence", "calibrated", "engine", "reason", "needs_person"}


@pytest.mark.asyncio
async def test_an_erased_or_missing_message_raises_before_any_model_is_asked(monkeypatch):
    Flow(monkeypatch, erased=True)
    seen: list = []
    with pytest.raises(MessageErased):
        await run(service_for(adapter_for(calls=seen)))
    assert seen == []


# ---- the route ----------------------------------------------------------------------------------------------------------------------------------------------

PROJECT = "https://example-project.supabase.co"
URL = "/v1/staff/scheduling/offers/interpret-reply"


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeConn:
    def __init__(self, *, message=True, front_desk=True):
        self.message, self.front_desk = message, front_desk

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "from msg.messages" in sql:
            return _Result(SimpleNamespace(clinic_id=CLINIC, origin="test") if self.message else None)
        if "from iam.staff_roles" in sql:
            return _Result((1,) if self.front_desk else None)
        raise AssertionError(sql)


def make_client(monkeypatch, *, conn=None, service="unset", vault=True):
    fake = conn or FakeConn()

    async def fake_db(_claims=Depends(verified_claims)):
        yield fake

    async def fake_claims():
        return {"sub": str(ACTOR), "role": "authenticated"}

    audits: list = []

    async def write_audit(conn, **kw):
        audits.append(kw)
        return True

    monkeypatch.setattr("careflow.app.write_audit", write_audit)
    the_vault = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")}) if vault else None
    app = create_app(
        Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(),  # type: ignore[arg-type]
        vault=the_vault, reply_interpreter=service_for(adapter_for("accept")) if service == "unset" else service,
    )
    app.dependency_overrides[db_conn] = fake_db
    app.dependency_overrides[verified_claims] = fake_claims
    return TestClient(app, raise_server_exceptions=False), audits


def post(client, key="k-1", **body):
    return client.post(URL, json={"message_id": str(MESSAGE), **body}, headers={"X-Idempotency-Key": key} if key else {})


def test_the_route_answers_503_without_a_decision_service_or_keys(monkeypatch):
    for service, vault in ((None, True), ("unset", False)):
        client, _ = make_client(monkeypatch, service=service, vault=vault)
        with client:
            r = post(client)
        assert (r.status_code, r.json()) == (503, {"detail": "reply interpretation is not available"})


def test_the_route_404s_an_invisible_message_and_403s_a_caller_who_is_not_front_desk_there(monkeypatch):
    client, audits = make_client(monkeypatch, conn=FakeConn(message=False))
    with client:
        r = post(client)
    assert (r.status_code, r.json()) == (404, {"detail": "not found"}) and audits == []
    client, audits = make_client(monkeypatch, conn=FakeConn(front_desk=False))
    with client:
        r = post(client)
    assert (r.status_code, r.json()) == (403, {"detail": "your role may not do this"}) and audits == []


def test_the_route_needs_an_idempotency_key_and_a_uuid_and_rejects_extra_fields(monkeypatch):
    client, _ = make_client(monkeypatch)
    with client:
        assert post(client, key=None).status_code == 422
        assert post(client, key="  ").status_code == 422
        assert client.post(URL, json={"message_id": "nope"}, headers={"X-Idempotency-Key": "k"}).status_code == 422
        assert post(client, reviewer_id=str(ACTOR)).status_code == 422  # the actor is the signed-in account, never the body


def test_a_successful_answer_has_ids_and_closed_words_only_and_writes_one_audit_row(monkeypatch):
    flow = Flow(monkeypatch)
    client, audits = make_client(monkeypatch)
    with client:
        r = post(client)
    assert r.status_code == 200 and flow.log == ["screen", "read"]
    body = r.json()
    assert body == {"message_id": str(MESSAGE), "screening_id": str(SCREENING), "outcome": "clear", "replayed": False, "suggestion": "accept", "confidence": None,
                    "calibrated": False, "engine": "fake-engine", "reason": None, "needs_person": True}
    assert SECRET not in r.text
    (audit,) = audits
    assert (audit["action"], audit["actor_id"], audit["resource_id"], audit["details"]) == ("offer.interpret_reply", ACTOR, MESSAGE, {"outcome": "clear", "suggestion": "accept", "reason": "none"})
    assert audit["idempotency_key"] == f"audit:interpret-reply:{MESSAGE}:k-1"


def test_a_flagged_reply_through_the_route_is_no_suggestion_with_the_reason_and_the_model_is_never_asked(monkeypatch):
    Flow(monkeypatch, outcome="red_flag")
    seen: list = []
    client, audits = make_client(monkeypatch, service=service_for(adapter_for(calls=seen)))
    with client:
        r = post(client)
    assert r.status_code == 200 and r.json()["suggestion"] is None and r.json()["reason"] == "red_flag" and r.json()["needs_person"] is True and seen == []
    assert audits[0]["details"] == {"outcome": "red_flag", "suggestion": "none", "reason": "red_flag"}


def test_the_route_maps_a_missing_or_erased_message_from_the_screening_to_the_fixed_answers(monkeypatch):
    async def missing(*a, **k):
        raise MessageNotFound("x")

    async def erased(*a, **k):
        raise MessageErased("x")

    for fn, expected in ((missing, (404, {"detail": "not found"})), (erased, (409, {"detail": "the message body was erased"}))):
        monkeypatch.setattr(ri, "screen_stored_message", fn)
        client, audits = make_client(monkeypatch)
        with client:
            r = post(client)
        assert (r.status_code, r.json()) == expected and audits == []
