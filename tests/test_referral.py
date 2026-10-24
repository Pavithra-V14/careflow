"""Referral to identity (careflow/referral.py; CLAUDE.md rule 2). Offline: a fake rule engine, extractor and ingestor share ONE event log, so
the tests can prove that no model fake was called before the rules ran, on every path including the failure paths. Synthetic names only."""

import uuid

import pytest

from careflow.gateway import AllRoutesFailed, NoApprovedRoute, SpendCeilingReached
from careflow.mpi import IngestResult, InvalidDateOfBirth
from careflow.ports import DataClass, PermanentError, TransientError
from careflow.referral import (
    Clearance,
    ExtractedReferral,
    ReferralDocumentNotFound,
    ReferralError,
    process_referral,
    screen_referral,
)
from careflow.rules import EscalationResult, Evaluation

pytestmark = pytest.mark.asyncio
CLINIC, DOC, PATIENT, TASK = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
LETTER = "SENTINEL-LETTER-91 Patient: Asha Rao, DOB 5 March 1980, Mobile 98765 43210. Please see."


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class Conn:
    def __init__(self, kind="referral", missing=False, log=None):
        self.kind, self.missing, self.log = kind, missing, log if log is not None else []

    async def execute(self, statement, params=None):
        self.log.append("db:document")
        return Result(None if self.missing else Row(id=DOC, clinic_id=CLINIC, origin="test", kind=self.kind))


def evaluation(outcome):
    return Evaluation(outcome, (), "clinical", 1, 0, (), "h" * 64)


class Engine:
    def __init__(self, log, outcome="clear", fail=None, escalate_fail=None):
        self.log, self.outcome, self.fail, self.escalate_fail = log, outcome, fail, escalate_fail

    async def evaluate_text(self, conn, clinic_id, origin, text, *, profile=None):
        self.log.append("rules")
        if self.fail:
            raise self.fail
        return evaluation(self.outcome)

    async def escalate_red_flag(self, conn, clinic_id, origin, ev, source):
        self.log.append("escalate")
        if self.escalate_fail:
            raise self.escalate_fail
        assert source == f"document:{DOC}"
        return EscalationResult(TASK)


GOOD = ExtractedReferral(
    name="Asha Rao", name_quote="Patient: Asha Rao", dob="1980-03-05", dob_quote="DOB 5 March 1980", phone="9876543210", phone_quote="Mobile 98765 43210"
)


class Extractor:
    def __init__(self, log, answer=GOOD, raises=None):
        self.log, self.answer, self.raises, self.clearances = log, answer, raises, []

    async def extract(self, letter_text, clearance, *, clinic_id):
        self.log.append("MODEL:extract")
        self.clearances.append(clearance)
        if self.raises:
            raise self.raises
        return self.answer


class Ingestor:
    def __init__(self, log, raises=None):
        self.log, self.raises, self.calls = log, raises, []

    async def ingest(self, conn, **kw):
        self.log.append("identity")
        self.calls.append(kw)
        if self.raises:
            raise self.raises
        return IngestResult("created", PATIENT)


def build(outcome="clear", *, answer=GOOD, engine_fail=None, escalate_fail=None, extract_raises=None, ingest_raises=None, **conn_kw):
    log: list[str] = []
    return log, Conn(log=log, **conn_kw), Engine(log, outcome, engine_fail, escalate_fail), Extractor(log, answer, extract_raises), Ingestor(log, ingest_raises)


async def run(parts):
    _, conn, engine, extractor, ingestor = parts
    return await process_referral(conn, engine, extractor, ingestor, DOC, LETTER)


def model_calls(log):
    return [e for e in log if e.startswith("MODEL")]


# ---- the happy path and its order ---------------------------------------------------------------------------------------------------------


async def test_a_clear_letter_runs_the_rules_first_then_the_model_then_identity_with_verified_facts_only():
    parts = build()
    result = await run(parts)
    log, _, _, extractor, ingestor = parts
    assert log == ["db:document", "rules", "db:document", "MODEL:extract", "identity"]
    assert result.status == "identity_resolved" and result.identity.patient_id == PATIENT and result.reasons == ()
    (call,) = ingestor.calls
    assert call["dob"] == "1980-03-05" and call["name"] == "Asha Rao" and call["phone"] == "9876543210"
    assert call["idempotency_key"] == f"referral:{DOC}" and call["clinic_id"] == CLINIC and call["origin"] == "test"
    (clearance,) = extractor.clearances
    assert isinstance(clearance, Clearance) and clearance.document_id == DOC


async def test_a_date_written_as_a_name_in_the_letter_is_stored_as_iso():
    parts = build(answer=ExtractedReferral(name="Asha Rao", name_quote="Asha Rao", dob="5 March 1980", dob_quote="5 March 1980"))
    result = await run(parts)
    assert result.status == "identity_resolved" and parts[4].calls[0]["dob"] == "1980-03-05" and parts[4].calls[0]["phone"] is None


# ---- rule 2: no model before the rules, on every path -------------------------------------------------------------------------------------


async def test_a_red_flag_escalates_and_no_model_is_ever_called():
    parts = build("red_flag")
    result = await run(parts)
    assert (result.status, result.task_id) == ("red_flag_escalated", TASK)
    assert parts[0] == ["db:document", "rules", "escalate"] and model_calls(parts[0]) == [] and parts[4].calls == []


async def test_an_unassessable_screening_is_not_clear_no_model_is_called_and_the_hold_is_visible():
    parts = build("unassessable")
    result = await run(parts)
    assert (result.status, result.reasons) == ("held_unassessable", ("screening_unassessable",))
    assert model_calls(parts[0]) == [] and parts[4].calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"engine_fail": RuntimeError("the rule table could not be read")},
        {"outcome": "red_flag", "escalate_fail": RuntimeError("escalation refused")},
        {"missing": True},
        {"kind": "report"},
    ],
)
async def test_when_the_rules_cannot_run_or_escalation_fails_the_error_propagates_and_no_model_is_called(kwargs):
    parts = build(**kwargs)
    with pytest.raises((RuntimeError, ReferralDocumentNotFound)):
        await run(parts)
    assert model_calls(parts[0]) == [] and parts[4].calls == []


async def test_an_empty_letter_raises_before_anything_runs():
    log, conn, engine, *_ = build()
    with pytest.raises(ReferralError):
        await screen_referral(conn, engine, DOC, "  ")
    assert log == []


async def test_every_path_that_reaches_the_model_has_the_rules_before_it():
    for kwargs in ({}, {"answer": ExtractedReferral()}, {"extract_raises": TransientError("x")}, {"ingest_raises": InvalidDateOfBirth("x")}):
        parts = build(**kwargs)
        await run(parts)
        log = parts[0]
        assert "MODEL:extract" in log and log.index("rules") < log.index("MODEL:extract")


async def test_a_clearance_cannot_be_forged():
    with pytest.raises(ReferralError):
        Clearance(DOC, "h", "clinical")
    with pytest.raises(ReferralError):
        Clearance(DOC, "h", "clinical", object())


async def test_the_extractor_only_ever_receives_a_clearance_that_screen_referral_minted_for_this_document():
    _, conn, engine, *_ = build()
    screened = await screen_referral(conn, engine, DOC, LETTER)
    assert screened.result is None and screened.clearance.document_id == DOC and screened.clearance.rule_set_hash == "h" * 64


# ---- grounding and failure paths after the rules -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (ExtractedReferral(name="Asha Rau", name_quote="Patient: Asha Rao", dob="1980-03-05", dob_quote="DOB 5 March 1980"), "name_not_found"),
        (ExtractedReferral(name="Asha Rao", name_quote="Asha Rao", dob="1980-03-06", dob_quote="DOB 5 March 1980"), "dob_not_found"),
        (ExtractedReferral(name="Asha Rao", name_quote="Asha Rao", dob="1980-03-05", dob_quote="DOB 05/03/1980"), "dob_ambiguous"),
        (ExtractedReferral(name=None, dob="1980-03-05", dob_quote="DOB 5 March 1980"), "name_missing"),
        (ExtractedReferral(name="Asha Rao", name_quote="Asha Rao"), "dob_missing"),
        (ExtractedReferral(), "name_missing"),
    ],
)
async def test_an_unverified_or_missing_name_or_date_never_reaches_identity_and_goes_to_the_front_desk(answer, reason):
    parts = build(answer=answer)
    result = await run(parts)
    assert result.status == "needs_front_desk" and reason in result.reasons and parts[4].calls == []


async def test_an_unverified_phone_is_dropped_not_passed_on():
    answer = ExtractedReferral(
        name="Asha Rao", name_quote="Asha Rao", dob="1980-03-05", dob_quote="5 March 1980", phone="9876543211", phone_quote="Mobile 98765 43210"
    )
    parts = build(answer=answer)
    result = await run(parts)
    assert result.status == "identity_resolved" and parts[4].calls[0]["phone"] is None and result.reasons == ("phone_dropped_not_found",)


@pytest.mark.parametrize(
    "error",
    [NoApprovedRoute("decision", DataClass.PHI), SpendCeilingReached("cap"), AllRoutesFailed("decision", ()), TransientError("x"), PermanentError("x")],
)
async def test_a_model_outage_or_missing_route_degrades_visibly_with_no_identity_write(error):
    parts = build(extract_raises=error)
    result = await run(parts)
    assert result.status == "extraction_unavailable" and parts[4].calls == []


async def test_an_unexpected_extractor_error_is_not_swallowed():
    with pytest.raises(RuntimeError):
        await run(build(extract_raises=RuntimeError("bug")))


async def test_an_identity_refusal_goes_to_the_front_desk_with_the_reason():
    result = await run(build(ingest_raises=InvalidDateOfBirth("in the future")))
    assert result.status == "needs_front_desk" and result.reasons[-1] == "identity_refused"


async def test_no_letter_text_or_extracted_value_reaches_a_repr_or_a_result():
    parts = build()
    result = await run(parts)
    for obj in (result, GOOD, parts[3].clearances[0]):
        text = repr(obj)
        assert "SENTINEL" not in text and "Asha" not in text and "1980" not in text and "98765" not in text


async def test_structurally_the_rules_step_precedes_the_only_model_call_in_the_source():
    import inspect

    import careflow.referral as module

    body = inspect.getsource(module.process_referral)
    assert body.count("extractor.extract(") == 1 and body.index("screen_referral(") < body.index("extractor.extract(")
    assert "Clearance(" not in body  # process_referral cannot mint one itself
