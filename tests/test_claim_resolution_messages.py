"""The portal claim path says what is wrong, in plain closed words (2026-11-20, found reading the path for the demo e-mail sign-in), OFFLINE: migration 0098 and the claim route's `activation_required`."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
M51 = (ROOT / "db" / "migrations" / "0051_review_fixes_portal_resolution_worker_wake.sql").read_text(encoding="utf-8")
M98 = (ROOT / "db" / "migrations" / "0098_claim_resolution_identity_not_confirmed.sql").read_text(encoding="utf-8")


def function(sql: str) -> str:
    return re.search(r"create (?:or replace )?function mpi\.resolve_claim_request.*?\n\$\$;\n", sql.replace("\r\n", "\n"), re.DOTALL).group(0)  # type: ignore[union-attr]


def test_0098_reissues_the_same_function_changing_only_the_confirmation_branch():
    old, new = function(M51), function(M98)
    assert new.startswith("create or replace function mpi.resolve_claim_request(p_request_id uuid, p_decision text, p_patient_id uuid, p_idempotency_key text) returns text")
    cut = lambda text: (text[: text.index("    -- a CONFIRMED (newest identity event" if "-- a CONFIRMED" in text else "    -- the record must be live")], text[text.index("    begin\n        update mpi.patients set account_id = v_req.account_id") :])
    old_head, old_tail = cut(old)
    new_head, new_tail = cut(new)
    assert old_head.split("returns text", 1)[1] == new_head.split("returns text", 1)[1] and old_tail == new_tail  # every other check, lock, audit row and result is unchanged
    assert "return 'identity_not_confirmed'" in new and new.count("return 'record_not_claimable'") >= 3
    code = "\n".join(line for line in M98.splitlines() if not line.lstrip().startswith("--"))
    assert "grant " not in code.lower()  # the signature is unchanged, so the grants of 0051 stand


def test_a_record_that_is_not_live_unbound_and_of_this_clinic_is_still_not_claimable_before_confirmation_is_looked_at():
    new = function(M98)
    assert new.index("return 'record_not_claimable';\n    end if;\n    -- and it must be CONFIRMED") < new.index("return 'identity_not_confirmed'")
    live_check = new[new.index("    -- the record must be live") : new.index("    -- and it must be CONFIRMED")]
    for condition in ("p.clinic_id = v_req.clinic_id", "p.origin = v_req.origin", "p.account_id is null", "p.erased_at is null", "for share of p"):
        assert condition in live_check


def test_the_route_words_are_in_the_response_types_and_the_claim_route_names_an_unactivated_account_plainly():
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    assert '"identity_not_confirmed"' in source and '"activation_required"' in source
    route = source[source.index("async def portal_claim") : source.index("# ---- Care Journey: plans")]
    assert route.index("activation_required") < route.index("await _patient_account(conn, claims)") < route.index("demo_email_signin.contact_phone")
