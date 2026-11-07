"""Offline: the Visit Readiness content setting is validated and never supplied, and a stored autonomy mode can only make behaviour more human (owner decision of 2026-11-07, items 1 and 9)."""

import json

import pytest

from careflow.autonomy import Policy, tightened
from careflow.visit_readiness_content import VisitContentError, parse

GOOD = {"facts": {"f_one": "DEMO question?"}, "visit_types": {"V": {"intake": ["f_one"], "checklist": [{"id": "i_one", "text": "DEMO item"}]}}}


def test_unset_is_none_and_a_good_setting_gives_the_exact_wording():
    for raw in (None, "", "  "):
        assert parse(raw) is None
    content = parse(json.dumps(GOOD))
    entry = content.for_visit_type("V")
    assert entry.card.intake_questions == {"f_one": "DEMO question?"} and entry.item_text == {"i_one": "DEMO item"}
    assert content.for_visit_type("unknown type") is None  # unknown is not an empty checklist


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("facts"),
        lambda d: d["visit_types"]["V"].update(checklist=[]),  # an empty checklist is a problem
        lambda d: d["visit_types"]["V"].update(intake=["not_declared"]),
        lambda d: d["visit_types"]["V"]["checklist"].append({"id": "i_one", "text": "dup"}),
        lambda d: d["visit_types"]["V"]["checklist"][0].update(text="  "),
        lambda d: d["visit_types"]["V"]["checklist"][0].update(extra=1),
        lambda d: d["facts"].update(Bad_Name="x"),
        lambda d: d.update(visit_types={}),
    ],
)
def test_a_malformed_setting_is_refused(mutate):
    data = json.loads(json.dumps(GOOD))
    mutate(data)
    with pytest.raises(VisitContentError):
        parse(json.dumps(data))


def test_the_stored_mode_can_only_tighten_the_configured_policy():
    assisted = Policy(mode="assisted", opt_in_records_release=True)
    assert tightened(assisted, None) is assisted  # no stored row changes nothing
    assert tightened(assisted, "shadow").mode == "shadow"  # more human wins
    assert tightened(assisted, "assisted") is assisted
    assert tightened(assisted, "autonomous") is assisted  # a looser stored mode never loosens
    assert tightened(Policy(mode="shadow"), "assisted").mode == "shadow"
    assert tightened(assisted, "nonsense").mode == "shadow"  # an unrecognised stored value reads as the most human mode
    assert tightened(assisted, "shadow").opt_in_records_release is True  # opt-ins are carried; they only act in autonomous
