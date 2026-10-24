"""Tiered grounding (careflow/grounding.py; BUILD-SPEC.md §11.2). Offline: a stub decider, no model, no database.

The values are SYNTHETIC and non-clinical in intent: they test parsing, not medicine. No threshold, range or unit conversion is asserted, because
none exists in the code (CLAUDE.md rule 1)."""

import uuid
from decimal import Decimal

import pytest

from careflow import grounding as g
from careflow.gateway import (
    AllRoutesFailed,
    GatewayResult,
    NoApprovedRoute,
    SpendCeilingReached,
)
from careflow.grounding import (
    UNKNOWN,
    FactTier,
    GroundedFact,
    Grounding,
    StructuredKind,
    label_free_text,
    needs_review,
    verify_interpretive,
    verify_structured,
)
from careflow.ports import DataClass, DecisionResult, Usage

CLINIC = uuid.uuid4()
D, P, BP, Q = StructuredKind.DATE, StructuredKind.PHONE, StructuredKind.BLOOD_PRESSURE, StructuredKind.QUANTITY
N = StructuredKind.NAME


def check(kind, value, quote):
    return verify_structured("f", kind, value, quote)


def ok(kind, value, quote):
    fact = check(kind, value, quote)
    assert (fact.status, fact.reason) == (Grounding.VERIFIED, "match"), (value, quote, fact)


def refused(kind, value, quote, reason):
    fact = check(kind, value, quote)
    assert (fact.status, fact.reason) == (Grounding.UNVERIFIED, reason), (value, quote, fact)
    assert not fact.usable_by_rules


# ---- tier 1: dates -----------------------------------------------------------------------------------------------------------------------


def test_a_date_written_differently_still_verifies_because_both_sides_are_made_canonical():
    ok(D, "1980-03-05", "DOB: 5 March 1980")
    ok(D, "1980-03-05", "born March 5th, 1980")
    ok(D, "1980-03-05", "dob 05-Mar-1980")
    ok(D, "5 March 1980", "1980-03-05")
    ok(D, "1980-03-25", "25/03/1980")  # 25 cannot be a month: day first
    ok(D, "1980-03-25", "03/25/1980")  # 25 cannot be a month: month first
    ok(D, "1980-03-03", "03/03/1980")  # both parts equal: no ambiguity


def test_a_numeric_date_whose_order_cannot_be_told_is_ambiguous_never_guessed():
    refused(D, "1980-03-05", "05/03/1980", "ambiguous")
    refused(D, "1980-05-03", "05/03/1980", "ambiguous")


def test_a_different_date_a_second_date_and_an_impossible_date_do_not_verify():
    refused(D, "1980-03-05", "DOB: 6 March 1980", "not_found")
    refused(D, "1980-03-05", "seen 2024-01-02, dob 1980-03-05", "ambiguous")
    refused(D, "1980-03-05", "no date here", "not_found")
    refused(D, "1980-02-30", "30 February 1980", "unparseable_value")
    refused(D, "1980-03-05", "30 February 1980", "ambiguous")
    refused(D, "March 1980", "March 1980", "unparseable_value")
    refused(D, "05/03/1980", "05/03/1980", "unparseable_value")  # the VALUE must itself be unambiguous


# ---- tier 1: phone numbers ---------------------------------------------------------------------------------------------------------------


def test_a_phone_number_is_compared_in_the_mpi_canonical_form():
    ok(P, "9876543210", "Mobile: +91 98765-43210")
    ok(P, "+919876543210", "Ph 09876543210")
    refused(P, "9876543210", "Ph 9876543211", "not_found")
    refused(P, "9876543210", "Ph 9876543210 / 9123456789", "ambiguous")
    refused(P, "12345", "Ph 9876543210", "unparseable_value")
    refused(P, "9876543210", "DOB 1980-03-05", "not_found")  # a date is not a phone number


# ---- tier 1: blood pressure and quantities -----------------------------------------------------------------------------------------------


def test_a_blood_pressure_pair_must_match_exactly():
    ok(BP, "120/80", "BP 120 / 80 mmHg")
    refused(BP, "120/80", "BP 120/85", "not_found")
    refused(BP, "120/80", "BP 120/80 then 118/76", "ambiguous")
    refused(BP, "120", "BP 120/80", "unparseable_value")
    refused(BP, "120/80", "seen on 05/03/1980", "not_found")  # a date is not a blood pressure


def test_a_number_is_never_fuzzy_matched_never_rounded_never_converted():
    ok(Q, "55%", "EF 55 %")
    ok(Q, "5 mg", "Tab X 5 mg once")
    ok(Q, "5.0 mg", "5 mg")  # numerically the same Decimal, not an approximation
    ok(Q, "0.5 mg", "0.5 MG")
    refused(Q, "55%", "EF 54%", "not_found")
    refused(Q, "55%", "EF 55.5%", "not_found")
    refused(Q, "5 mg", "5.0001 mg", "not_found")
    refused(Q, "5 mg", "0.005 g", "not_found")  # no unit conversion
    refused(Q, "5 mg", "5 mcg", "not_found")  # a different unit is a different quantity
    refused(Q, "5", "5 mg", "not_found")  # a bare number does not verify against a number with a unit
    refused(Q, "1000 mg", "1,000 mg", "ambiguous")


def test_a_quote_that_could_mean_more_than_one_number_is_ambiguous():
    refused(Q, "55%", "EF 35% (was 55%)", "ambiguous")
    refused(Q, "10 mg", "5-10 mg", "ambiguous")
    refused(Q, "5 mg", "5 to 10 mg", "ambiguous")
    refused(Q, "2 tab", "1/2 tab", "ambiguous")
    refused(Q, "5 mg", "5 mg and 10 mg", "ambiguous")
    refused(Q, "5 mg", "5,5 mg", "ambiguous")


def test_a_bound_an_approximation_or_a_negative_in_the_quote_is_ambiguous():
    for quote in ("EF <35%", "EF >= 35%", "EF ≤ 35%", "EF about 35%", "EF approx. 35%", "up to 35%", "at least 35%", "-35%", "EF ~35%"):
        refused(Q, "35%", quote, "ambiguous")


def test_a_duplicate_field_name_is_usable_only_if_every_entry_is_and_order_does_not_matter():
    good = g.GroundedFact("ef", FactTier.STRUCTURED, Grounding.VERIFIED, "match", "55%")
    bad = g.GroundedFact("ef", FactTier.STRUCTURED, Grounding.UNVERIFIED, "ambiguous", "35%")
    assert needs_review([good, bad], ["ef"]) == ("ef",) == needs_review([bad, good], ["ef"])
    assert needs_review([good, good], ["ef"]) == ()


def test_only_the_verify_functions_produce_verified_in_this_module():
    import inspect

    # Exactly three places mention VERIFIED: the rule gate, tier 1 and tier 2. A new producer must update this test (and be reviewed).
    assert inspect.getsource(g).count("Grounding.VERIFIED") == 3


def test_a_quantity_in_the_quote_is_not_confused_with_the_digits_of_a_date():
    ok(Q, "55%", "On 05/03/2024 EF 55%")
    ok(Q, "5 mg", "2024-01-05: 5 mg")


def test_unreadable_unicode_and_word_numbers_are_unverified_not_errors():
    refused(Q, "௫ mg", "௫ mg", "unparseable_value")  # a Tamil digit is not read as a digit
    refused(Q, "five mg", "five mg", "unparseable_value")
    refused(Q, "-5 mg", "5 mg", "unparseable_value")
    for value, quote in (("", "x"), ("x", ""), ("  ", "5"), ("5", "  ")):
        refused(Q, value, quote, "empty")


def test_a_non_string_never_raises_it_is_unverified():
    assert verify_structured("f", Q, None, "5 mg").status is Grounding.UNVERIFIED  # type: ignore[arg-type]
    assert verify_structured("f", Q, "5 mg", None).status is Grounding.UNVERIFIED  # type: ignore[arg-type]


def test_there_is_no_false_status_anywhere():
    assert {s.value for s in Grounding} == {"verified", "unverified", "model_generated"}


def test_values_and_quotes_never_reach_a_repr():
    fact = check(Q, "SENTINEL-77 mg", "SENTINEL-77 mg")
    assert "SENTINEL" not in repr(fact)
    assert "SENTINEL" not in repr(check(D, "SENTINEL", "x"))


# ---- tier 3, the rule gate, and review targeting ------------------------------------------------------------------------------------------


def test_free_text_is_labelled_and_can_never_feed_a_rule():
    fact = label_free_text("summary", "a model wrote this")
    assert (fact.tier, fact.status, fact.reason) == (FactTier.FREE_TEXT, Grounding.MODEL_GENERATED, "generated")
    assert not fact.usable_by_rules and fact.rule_value() is UNKNOWN


def test_even_a_verified_label_on_free_text_is_not_usable_by_a_rule():
    forged = GroundedFact("summary", FactTier.FREE_TEXT, Grounding.VERIFIED, "match", "x")
    assert not forged.usable_by_rules and forged.rule_value() is UNKNOWN


def test_an_unverified_fact_gives_the_unknown_sentinel_which_is_neither_true_nor_false():
    fact = check(Q, "55%", "EF 35% (was 55%)")
    assert fact.rule_value() is UNKNOWN
    with pytest.raises(TypeError):
        bool(fact.rule_value())
    good = check(Q, "55%", "EF 55%")
    assert good.rule_value() == "55%"


def test_review_is_needed_only_for_unverified_facts_a_matched_rule_reads_and_for_facts_missing_altogether():
    facts = [check(Q, "55%", "EF 55%"), verify_structured("dob", D, "1980-03-05", "5/3/1980"), verify_structured("phone", P, "1", "x"),
             label_free_text("summary", "text")]
    facts[0] = g.GroundedFact("ef", FactTier.STRUCTURED, Grounding.VERIFIED, "match", "55%")
    assert needs_review(facts, ["ef"]) == ()
    assert needs_review(facts, ["dob"]) == ("dob",)
    assert needs_review(facts, ["ef", "dob", "bp"]) == ("bp", "dob")  # bp has no entry: unknown, so a rule needing it cannot be satisfied
    assert needs_review(facts, []) == ()  # nothing reads the unverified facts: no review, no flood
    assert needs_review(facts, ["summary"]) == ("summary",)  # free text is never usable by a rule


# ---- tier 2 ------------------------------------------------------------------------------------------------------------------------------


class StubDecider:
    def __init__(self, *, choice="supported", calibrated=True, raises=None):
        self.choice, self.calibrated, self.raises, self.requests = choice, calibrated, raises, []

    async def decide(self, request, *, clinic_id, call_class=None, budget=None):
        self.requests.append((request, clinic_id, budget))
        if self.raises is not None:
            raise self.raises
        result = DecisionResult(self.choice, {self.choice: 0.9}, 0.9, "stub", calibrated=self.calibrated, route="r1")
        return GatewayResult(result, "p", "m", Usage(), Decimal(0), Decimal(0), ())


@pytest.mark.asyncio
async def test_a_calibrated_supported_answer_verifies_and_the_request_says_what_it_carries():
    decider = StubDecider()
    fact = await verify_interpretive(decider, "ecg", "the ECG is abnormal", "passage text", clinic_id=CLINIC, data_class=DataClass.PHI)
    assert (fact.tier, fact.status, fact.reason) == (FactTier.INTERPRETIVE, Grounding.VERIFIED, "match") and fact.usable_by_rules
    (request, clinic, _budget), = decider.requests
    assert request.data_class is DataClass.PHI and clinic == CLINIC
    assert request.question_type == "grounding_entailment" and request.options == ("supported", "not_supported")
    assert "passage text" in request.text and "the ECG is abnormal" in request.text and "passage text" not in repr(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decider", "reason"),
    [
        (StubDecider(choice="not_supported"), "not_supported"),
        (StubDecider(calibrated=False), "uncalibrated"),
        (StubDecider(choice="maybe"), "invalid_answer"),
        (StubDecider(raises=NoApprovedRoute("decision", DataClass.PHI)), "no_engine"),
        (StubDecider(raises=SpendCeilingReached("cap")), "no_engine"),
        (StubDecider(raises=AllRoutesFailed("decision", ())), "no_engine"),
    ],
)
async def test_every_other_tier_2_outcome_is_unverified_and_usable_by_no_rule(decider, reason):
    fact = await verify_interpretive(decider, "ecg", "claim", "passage", clinic_id=CLINIC, data_class=DataClass.PHI)
    assert (fact.status, fact.reason) == (Grounding.UNVERIFIED, reason)
    assert not fact.usable_by_rules and fact.rule_value() is UNKNOWN


@pytest.mark.asyncio
async def test_an_empty_claim_or_passage_asks_no_engine():
    decider = StubDecider()
    for claim, passage in (("", "p"), ("c", ""), ("  ", "p"), ("c", " ")):
        fact = await verify_interpretive(decider, "f", claim, passage, clinic_id=CLINIC, data_class=DataClass.PHI)
        assert (fact.status, fact.reason) == (Grounding.UNVERIFIED, "empty")
    assert decider.requests == []


@pytest.mark.asyncio
async def test_an_unexpected_error_is_not_swallowed_into_unverified():
    with pytest.raises(RuntimeError):
        await verify_interpretive(StubDecider(raises=RuntimeError("bug")), "f", "c", "p", clinic_id=CLINIC, data_class=DataClass.PHI)


def test_a_name_must_appear_as_the_same_words_in_order_and_a_near_miss_does_not_verify():
    ok(N, "Asha Rao", "Patient: ASHA  RAO, 42 years")
    ok(N, "Asha Rao", "Rao, Asha Rao referred")  # appears (contiguously) once or more
    refused(N, "Asha Rao", "Patient: Asha Rau", "not_found")
    refused(N, "Asha Rao", "Patient: A. Rao", "not_found")
    refused(N, "Asha Rao", "Asha K Rao", "not_found")
    refused(N, "Asha Rao", "Rao Asha", "not_found")
    refused(N, "Asha", "Ashana Rao", "not_found")  # a word, not a prefix of one
    refused(N, "...", "Asha Rao", "unparseable_value")
    ok(N, "அஸ்வின்", "அஸ்வின் குமார்")


def test_canonical_date_is_none_unless_exactly_one_unambiguous_date():
    from datetime import date

    assert g.canonical_date("5 March 1980") == date(1980, 3, 5) and g.canonical_date("1980-03-05") == date(1980, 3, 5)
    assert g.canonical_date("05/03/1980") is None and g.canonical_date("1980-02-30") is None and g.canonical_date("x") is None
