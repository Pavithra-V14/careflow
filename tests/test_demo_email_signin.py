"""Patient e-mail sign-in is a DEMO convenience (owner decision 2026-11-20): the synthetic phone (derived from the ACCOUNT ID, not the e-mail address: adversarial review 2026-11-20), who may use it, and the refusal to
start outside the synthetic demo. OFFLINE."""

import base64
import os
import uuid

import pytest

from careflow import demo_email_signin as d
from careflow import portal_sql
from careflow.crypto import DemographicVault
from careflow.mpi import canonicalize_phone

ON = {d.SETTING: "on"}
ACCOUNT = "6f1c7c2e-8f0a-4a52-9d6e-2b1d0c3a9e11"
EMAIL = "Demo.Patient@Example.test"
CLAIMS = {"sub": ACCOUNT, "email": EMAIL}


def test_the_synthetic_phone_is_deterministic_canonical_case_insensitive_and_distinct_per_account():
    phone = d.phone_for_account(ACCOUNT)
    assert phone == d.phone_for_account(ACCOUNT.upper()) == d.phone_for_account(uuid.UUID(ACCOUNT)) == d.phone_for_account("  " + ACCOUNT + " ")
    assert canonicalize_phone(phone) == phone and phone.startswith("+91") and phone[3] in "6789"
    assert len({d.phone_for_account(uuid.uuid4()) for _ in range(300)}) == 300
    for bad in ("not-a-uuid", "", "demo@example.test"):
        with pytest.raises(ValueError):
            d.phone_for_account(bad)


def test_the_claim_step_accepts_the_synthetic_phone_through_the_same_token_function_as_a_real_phone():
    vault = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
    phone_token, name_dob = portal_sql.claim_tokens(vault, phone=d.phone_for_account(ACCOUNT), name="DEMO Portal Patient", dob="1990-01-01", key_version=1)
    assert phone_token and name_dob and portal_sql.claim_tokens(vault, phone=d.phone_for_account(ACCOUNT), name="DEMO Portal Patient", dob="1990-01-01", key_version=1)[0] == phone_token


def test_a_real_verified_phone_always_wins_and_is_never_touched_by_the_switch():
    claims = {**CLAIMS, "phone": "+919876543210"}
    assert d.contact_phone(claims, {}, demo_allowed=False) == "+919876543210"
    assert d.contact_phone(claims, ON, demo_allowed=True) == "+919876543210"


def test_the_email_path_needs_the_switch_the_synthetic_demo_and_a_plain_address_and_derives_the_phone_from_the_account_id():
    assert d.contact_phone(CLAIMS, ON, demo_allowed=True) == d.phone_for_account(ACCOUNT)
    # the e-mail address does NOT decide the phone: the same address under another account gets another phone, and another address under the same account gets the same one
    assert d.contact_phone({**CLAIMS, "sub": str(uuid.uuid4())}, ON, demo_allowed=True) != d.contact_phone(CLAIMS, ON, demo_allowed=True)
    assert d.contact_phone({**CLAIMS, "email": "someone.else@example.test"}, ON, demo_allowed=True) == d.contact_phone(CLAIMS, ON, demo_allowed=True)
    assert d.contact_phone(CLAIMS, {}, demo_allowed=True) is None  # switch off (the default)
    for value in ("", "off", "1", "true", "yes"):
        assert d.contact_phone(CLAIMS, {d.SETTING: value}, demo_allowed=True) is None
    assert d.contact_phone(CLAIMS, ON, demo_allowed=False) is None  # not the synthetic demo: the clinical profile keeps phone codes only
    for bad in (None, "", "no-at-sign", "two@@signs", "has space@x.test", "a@" + "b" * 200, 5, ["x@y.z"]):
        assert d.contact_phone({**CLAIMS, "email": bad}, ON, demo_allowed=True) is None
    assert d.contact_phone({"sub": ACCOUNT}, ON, demo_allowed=True) is None  # no e-mail: not an e-mail sign-in
    for bad_sub in (None, "", "not-a-uuid", 7):
        assert d.contact_phone({"email": EMAIL, "sub": bad_sub}, ON, demo_allowed=True) is None
    assert d.contact_phone({**CLAIMS, "phone": "  "}, ON, demo_allowed=True) == d.phone_for_account(ACCOUNT)  # a blank phone is no phone


def test_an_address_the_project_marks_unverified_is_not_a_contact():
    assert d.contact_phone({**CLAIMS, "user_metadata": {"email_verified": False}}, ON, demo_allowed=True) is None
    assert d.contact_phone({**CLAIMS, "user_metadata": {"email_verified": True}}, ON, demo_allowed=True) == d.phone_for_account(ACCOUNT)


def test_signing_up_first_with_someone_elses_address_gains_nothing_because_the_record_is_prepared_for_the_account_id():
    victim_record_phone = d.phone_for_account(ACCOUNT)  # what scripts/seed_dev_portal_patient.py stores for the victim's account id
    attacker_claims = {"sub": str(uuid.uuid4()), "email": EMAIL}  # same address, another account
    assert d.contact_phone(attacker_claims, ON, demo_allowed=True) != victim_record_phone


def test_the_switch_outside_the_synthetic_demo_refuses_to_start_and_off_it_never_does():
    d.refuse_outside_demo(ON, demo_allowed=True)
    d.refuse_outside_demo({}, demo_allowed=False)
    d.refuse_outside_demo({d.SETTING: "off"}, demo_allowed=False)
    with pytest.raises(d.EmailSigninRefused, match="refusing to start"):
        d.refuse_outside_demo(ON, demo_allowed=False)


@pytest.mark.parametrize(("profile", "policy"), [("clinical", "synthetic_only"), ("clinical", None), ("demo", None), ("demo", "phi_approved")])
def test_the_application_refuses_to_build_with_the_switch_on_outside_demo_and_synthetic(monkeypatch, profile, policy):
    from careflow.app import Settings, create_app
    from tests.test_api_role_matrix import PROJECT, fake_jwks_client

    monkeypatch.setenv(d.SETTING, "on")
    monkeypatch.setenv("CAREFLOW_PROFILE", profile)
    if policy is None:
        monkeypatch.delenv("CAREFLOW_DATA_POLICY", raising=False)
    else:
        monkeypatch.setenv("CAREFLOW_DATA_POLICY", policy)
    with pytest.raises(d.EmailSigninRefused):
        create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=fake_jwks_client(), engine=object())  # type: ignore[arg-type]


def test_the_claim_route_uses_contact_phone_with_the_deployments_profile_and_nothing_else_reads_the_phone_claim():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "careflow" / "app.py").read_text(encoding="utf-8")
    assert "demo_email_signin.contact_phone(claims, os.environ, demo_allowed=demo_allowed(resolve_profile(), _data_policy(os.environ)))" in source
    assert source.count('claims.get("phone")') == 0
