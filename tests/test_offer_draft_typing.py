"""The typed offer draft (frontend request 6): a closed reason word, the missing setting names in their own field, typed data. Offline; synthetic ids only."""

import uuid
from datetime import UTC, datetime

from careflow.app import _offer_draft_out
from careflow.mcp_kit import Envelope


def test_unavailable_names_the_settings_in_their_own_field_and_the_reason_is_a_closed_word():
    out = _offer_draft_out(Envelope.unavailable("not_configured:offer_expiry_seconds,offer_message_template"))
    assert (out.status, out.reason, out.missing_settings, out.data) == ("unavailable", "not_configured", ["offer_expiry_seconds", "offer_message_template"], None)


def test_empty_has_a_closed_reason_and_no_data():
    out = _offer_draft_out(Envelope.empty("no_ranked_candidates"))
    assert (out.status, out.reason, out.missing_settings, out.data) == ("empty", "no_ranked_candidates", None, None)


def test_ok_carries_typed_candidates_and_the_draft():
    pid, wid = uuid.uuid4(), uuid.uuid4()
    env = Envelope.ok({
        "candidates": [{"waitlist_id": str(wid), "patient_id": str(pid), "priority_code": "level_one", "rank": 1, "joined_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat()}],
        "draft": {"candidate_patient_id": str(pid), "waitlist_id": str(wid), "template": "TEST", "offer_expiry_seconds": 60, "hold_lifetime_seconds": 30, "rule": "rank_then_joined_at"},
    })
    out = _offer_draft_out(env)
    assert out.status == "ok" and out.reason is None and out.data is not None and out.data.candidates[0].rank == 1 and out.data.draft.template == "TEST"


def test_an_unknown_reason_word_is_never_passed_through():
    assert _offer_draft_out(Envelope.empty("some free text from elsewhere")).reason is None
