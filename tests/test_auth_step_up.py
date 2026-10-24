"""careflow.auth.step_up_failure: the pure step-up decision (BUILD-SPEC.md §18.1). No token, network or database.

The claim shape under test, `amr` = a list of exactly {"method": str, "timestamp": int}, is DOCUMENTED but UNVERIFIED against a real token
(Appendix B; docs/design/step-up-spike.md). These tests pin what the code accepts today; when the real shape is confirmed, they and
`_totp_verified_at` change together.
"""

import pytest

from careflow.auth import step_up_failure

NOW = 1_800_000_000.0
WINDOW = 300


def claims(**over):
    base = {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(NOW) - 10}]}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def check(c, window=WINDOW):
    return step_up_failure(c, max_age_seconds=window, now=NOW)


def test_a_recent_totp_verification_at_aal2_is_stepped_up():
    assert check(claims()) is None


def test_the_edge_of_the_window_is_inside_and_one_second_past_it_is_outside():
    assert check(claims(amr=[{"method": "totp", "timestamp": int(NOW) - WINDOW}])) is None
    assert check(claims(amr=[{"method": "totp", "timestamp": int(NOW) - WINDOW - 1}])) == "timestamp_expired"


def test_the_newest_totp_entry_counts_when_there_are_several():
    amr = [
        {"method": "password", "timestamp": int(NOW) - 5000},
        {"method": "totp", "timestamp": int(NOW) - 9000},
        {"method": "totp", "timestamp": int(NOW) - 20},
    ]
    assert check(claims(amr=amr)) is None


@pytest.mark.parametrize(
    ("label", "c", "reason"),
    [
        ("no amr at all", claims(amr=None), "amr_missing"),
        ("an empty amr", claims(amr=[]), "amr_missing"),
        ("amr that is not a list", claims(amr="totp"), "amr_missing"),
        ("no aal", claims(aal=None), "aal_not_aal2"),
        ("aal1", claims(aal="aal1"), "aal_not_aal2"),
        ("aal2 with different case", claims(aal="AAL2"), "aal_not_aal2"),
        ("a future timestamp", claims(amr=[{"method": "totp", "timestamp": int(NOW) + 1}]), "timestamp_future"),
        ("a timestamp long ago", claims(amr=[{"method": "totp", "timestamp": int(NOW) - 86_400}]), "timestamp_expired"),
        ("only a non-totp method", claims(amr=[{"method": "password", "timestamp": int(NOW)}]), "no_totp_entry"),
        ("an otp (email/sms) method is not a second factor", claims(amr=[{"method": "otp", "timestamp": int(NOW)}]), "no_totp_entry"),
        ("legacy plain-string entries", claims(amr=["totp"]), "amr_malformed"),
        ("an entry with no timestamp", claims(amr=[{"method": "totp"}]), "amr_malformed"),
        ("an entry with no method", claims(amr=[{"timestamp": int(NOW)}]), "amr_malformed"),
        ("an entry with an extra key", claims(amr=[{"method": "totp", "timestamp": int(NOW), "x": 1}]), "amr_malformed"),
        ("a string timestamp", claims(amr=[{"method": "totp", "timestamp": str(int(NOW))}]), "amr_malformed"),
        ("a float timestamp", claims(amr=[{"method": "totp", "timestamp": NOW - 5.5}]), "amr_malformed"),
        ("a boolean timestamp", claims(amr=[{"method": "totp", "timestamp": True}]), "amr_malformed"),
        ("a non-string method", claims(amr=[{"method": 7, "timestamp": int(NOW)}]), "amr_malformed"),
        ("a good totp entry beside a malformed one", claims(amr=[{"method": "totp", "timestamp": int(NOW)}, "password"]), "amr_malformed"),
    ],
)
def test_anything_missing_or_malformed_is_not_stepped_up(label, c, reason):
    assert check(c) == reason, label


def test_the_reason_codes_never_contain_a_claim_value():
    sentinel_claim = "sentinel-claim-value-4471"
    for c in (claims(aal=sentinel_claim), claims(amr=[{"method": sentinel_claim, "timestamp": int(NOW)}]), claims(amr=[sentinel_claim]), claims(amr=sentinel_claim)):
        assert sentinel_claim not in str(check(c))
