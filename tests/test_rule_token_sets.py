"""Token sets in the red-flag engine (migration 0076): every word of a set must appear as a WHOLE word, in any order; no regular expression is involved. Offline. The words are MEANINGLESS markers."""

import uuid

from careflow import rules


def ruleset(token_sets, status="approved", profile="clinical", phrases=()):
    row = {"id": uuid.uuid4(), "clinic_id": None, "rule_code": "zz_sets", "version_id": 1, "status": status, "phrases": list(phrases), "patterns": [], "token_sets": token_sets}
    chosen, passed = rules.select_active_versions([row], profile)
    compiled = [rules.compile_rule(r)[0] for r in chosen]
    return rules.RuleSet(tuple(r for r in compiled if r), profile, passed, (), rules.rule_set_hash(chosen))


def test_every_token_present_as_a_whole_word_fires_in_any_order():
    rs = ruleset([["zzalpha", "zzbeta"]])
    for text in ("zzalpha then zzbeta", "zzbeta ... somewhere ... zzalpha", "ZZAlpha   ZZBeta"):
        ev = rules.evaluate_with(rs, text)
        assert ev.outcome == "red_flag" and ev.matches[0].kind == "token_set", text


def test_a_missing_token_or_a_token_inside_a_longer_word_does_not_fire():
    rs = ruleset([["zzalpha", "zzbeta"]])
    assert rules.evaluate_with(rs, "only zzalpha here").outcome == "clear"
    assert rules.evaluate_with(rs, "zzalphas and zzbetas").outcome == "clear"  # not whole words
    assert rules.evaluate_with(rs, "zzalpha-zzbeta").outcome == "red_flag"  # a hyphen is a boundary


def test_the_match_carries_ids_and_offsets_only_never_the_text():
    rs = ruleset([["zzalpha", "zzbeta"]])
    ev = rules.evaluate_with(rs, "xx zzalpha yy zzbeta")
    p = ev.matches[0].as_provenance()
    assert set(p) == {"rule_id", "rule_code", "version_id", "rule_status", "kind", "index", "start", "end"} and (p["start"], p["end"]) == (3, 20)
    assert "zzalpha" not in str(ev.provenance)


def test_a_rule_with_only_token_sets_counts_as_a_rule_with_criteria_so_a_clear_is_possible_only_when_it_ran():
    rs = ruleset([["zzalpha", "zzbeta"]])
    assert rs.usable_with_criteria == 1 and rules.evaluate_with(rs, "nothing").outcome == "clear"
    assert rules.evaluate_with(ruleset([]), "nothing").outcome == "unassessable"  # no criteria at all: not clear


def test_a_draft_token_set_is_not_in_force_in_the_clinical_profile():
    assert rules.evaluate_with(ruleset([["zzalpha", "zzbeta"]], status="draft_unreviewed"), "zzalpha zzbeta").outcome == "unassessable"
    assert rules.evaluate_with(ruleset([["zzalpha", "zzbeta"]], status="draft_unreviewed", profile="demo"), "zzalpha zzbeta").outcome == "red_flag"


def test_a_token_with_a_space_cannot_compile_and_the_failure_is_reported_not_ignored():
    row = {"id": uuid.uuid4(), "rule_code": "zz_bad", "version_id": 1, "status": "approved", "phrases": [], "patterns": [], "token_sets": [["zz alpha", "zzbeta"]]}
    rule, problem = rules.compile_rule(row)
    assert rule is None and problem and "zz alpha" not in problem


def test_the_stream_evaluator_finds_a_set_completed_across_chunks():
    stream = rules.StreamEvaluator(ruleset([["zzalpha", "zzbeta"]]))
    assert stream.feed("zzal").outcome != "red_flag" and stream.feed("pha ").outcome != "red_flag"
    assert stream.feed("and zzbeta").outcome == "red_flag" and stream.red_flag_seen
