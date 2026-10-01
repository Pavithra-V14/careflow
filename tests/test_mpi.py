"""MPI canonicalisation and the match rule (careflow/mpi.py). Pure: no database."""

import uuid
from datetime import date

import pytest

from careflow import mpi
from careflow.mpi import (
    InvalidDateOfBirth,
    InvalidPhone,
    MissingRequiredField,
    canonicalize_phone,
    classify,
    parse_date_of_birth,
)


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "09876543210",
        "919876543210",
        "+919876543210",
        "+91 98765 43210",
        "+91-98765-43210",
        "0091 98765 43210",
        "(98765) 43210",
        "98765.43210",
        "  +91 9876543210  ",
    ],
)
def test_every_accepted_spelling_canonicalises_to_the_same_value(raw):
    assert canonicalize_phone(raw) == "+919876543210"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "12345",  # too short
        "98765432101",  # too long
        "5876543210",  # not a mobile prefix
        "+449876543210",  # not India
        "+91 98765 4321",  # +91 with nine digits
        "9876-ABC-210",  # letters
        "9999999999",  # placeholder: ten identical digits
        "+91 0000000000",
        "044 2345 6789",  # landline
        "+91 +91 9876543210",
    ],
)
def test_anything_that_is_not_an_indian_mobile_is_refused_not_guessed(raw):
    with pytest.raises(InvalidPhone):
        canonicalize_phone(raw)


def test_phone_must_be_text():
    with pytest.raises(TypeError):
        canonicalize_phone(9876543210)  # type: ignore[arg-type]


def test_date_of_birth_accepts_a_real_past_iso_date():
    assert parse_date_of_birth("1987-04-23") == date(1987, 4, 23)
    assert parse_date_of_birth(" 1987-04-23 ") == date(1987, 4, 23)


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_date_of_birth_is_a_missing_field(raw):
    with pytest.raises(MissingRequiredField):
        parse_date_of_birth(raw)


@pytest.mark.parametrize("raw", ["58", "58 yr", "23/04/1987", "1987-4-23", "1987-02-30", "1987-13-01", "1899-12-31", "2999-01-01"])
def test_ages_odd_formats_impossible_and_out_of_range_dates_are_refused(raw):
    with pytest.raises(InvalidDateOfBirth):
        parse_date_of_birth(raw)


def test_today_is_allowed_tomorrow_is_not():
    today = date(2030, 6, 1)
    assert parse_date_of_birth("2030-06-01", today=today) == today
    with pytest.raises(InvalidDateOfBirth):
        parse_date_of_birth("2030-06-02", today=today)


# ---- the match rule --------------------------------------------------------------------------------

A, B = uuid.uuid4(), uuid.uuid4()


def test_strong_identifiers_are_name_dob_and_phone_together():
    assert mpi.STRONG_IDENTIFIERS == {"name_dob", "phone"}


def test_nothing_agrees_means_no_match():
    assert classify({}) == ("none", {})


def test_one_record_agreeing_on_every_strong_identifier_is_a_match():
    assert classify({A: {"name_dob", "phone"}}) == ("match", {A: "match"})


@pytest.mark.parametrize("kinds", [{"phone"}, {"name_dob"}])
def test_partial_agreement_is_a_near_match_for_review_never_a_link(kinds):
    assert classify({A: kinds}) == ("review", {A: "near_match"})


def test_two_fully_agreeing_records_are_ambiguous_and_never_linked():
    decision, reasons = classify({A: {"name_dob", "phone"}, B: {"name_dob", "phone"}})
    assert decision == "review"
    assert reasons == {A: "ambiguous", B: "ambiguous"}


def test_a_confident_record_plus_a_weaker_one_is_still_a_match_only_if_unique():
    decision, reasons = classify({A: {"name_dob", "phone"}, B: {"phone"}})
    assert (decision, list(reasons)) == ("match", [A])


def test_phone_and_name_dob_matching_different_records_is_two_near_matches():
    decision, reasons = classify({A: {"phone"}, B: {"name_dob"}})
    assert decision == "review"
    assert reasons == {A: "near_match", B: "near_match"}


# ---- pipeline input validation: refused before any database or vault work --------------------------

import base64
import os

from careflow.crypto import DemographicVault


@pytest.fixture
def pipeline():
    k = lambda: base64.b64encode(os.urandom(32)).decode()
    vault = DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": k(), "CAREFLOW_HMAC_SECRET_V1": k()})
    return mpi.PatientIngestionPipeline(vault, key_version=1)


GOOD = {"clinic_id": uuid.uuid4(), "origin": "test", "name": "Asha", "dob": "1987-04-23", "phone": "9876543210", "idempotency_key": "k"}


@pytest.mark.parametrize(
    "override, error",
    [
        ({"name": ""}, MissingRequiredField),
        ({"name": "   "}, MissingRequiredField),
        ({"dob": ""}, MissingRequiredField),
        ({"dob": "58 yr"}, InvalidDateOfBirth),
        ({"phone": ""}, InvalidPhone),
        ({"phone": "   "}, InvalidPhone),
        ({"phone": "12345"}, InvalidPhone),
        ({"idempotency_key": ""}, MissingRequiredField),
        ({"idempotency_key": "  "}, MissingRequiredField),
        ({"origin": "production"}, ValueError),
    ],
)
@pytest.mark.asyncio
async def test_bad_input_is_refused_before_touching_the_database(pipeline, override, error):
    with pytest.raises(error):
        await pipeline.ingest(None, **{**GOOD, **override})  # type: ignore[arg-type]  # conn is never reached


def test_pipeline_requires_a_loaded_key_version():
    k = lambda: base64.b64encode(os.urandom(32)).decode()
    vault = DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": k(), "CAREFLOW_HMAC_SECRET_V1": k()})
    with pytest.raises(ValueError):
        mpi.PatientIngestionPipeline(vault, key_version=2)
