"""The red-flag engine's matching mechanism (careflow/rules.py). Pure: no database, no network, no model.

# CLINICAL-PLACEHOLDER: needs clinician sign-off

EVERY phrase and pattern in this file is a SYNTHETIC test token (`zzalpha`, `zz beta`, `zz\\d{2} gamma`, a
neutral Tamil greeting). None is a clinical term, and none may become one: real rules are clinician-authored
data, not test fixtures (CLAUDE.md rule 1, BUILD-SPEC.md §20.2).
"""

import ast
import itertools
import re
import uuid
from pathlib import Path

import pytest

from careflow import rules
from careflow.rules import (
    Evaluation,
    Rule,
    RuleSet,
    StreamEvaluator,
    compile_rule,
    evaluate_with,
    normalize_text,
    resolve_profile,
    scan_normalized,
    select_active_versions,
)

HELLO_TAMIL = "வணக்கம்"  # a neutral greeting: used only to test Tamil word boundaries


def make_rule(code="r1", *, phrases=(), patterns=(), status="approved", version=1, rid=None):
    rule, problem = compile_rule(
        {"id": rid or uuid.uuid4(), "rule_code": code, "version_id": version, "status": status,
         "phrases": list(phrases), "patterns": list(patterns)}
    )
    assert problem is None, problem
    return rule


def ruleset(*rs, profile="clinical", drafts_excluded=0, problems=()):
    return RuleSet(tuple(rs), profile, drafts_excluded, tuple(problems))


def kinds(evaluation: Evaluation):
    return {(m.rule_code, m.kind, m.index) for m in evaluation.matches}


# ---- A. pre-processing -----------------------------------------------------------------------------


def test_case_is_folded_and_whitespace_collapsed():
    assert normalize_text("  ZZAlpha \t\n  ZZ   Beta  ") == "zzalpha zz beta"


def test_compatibility_forms_and_invisible_characters_are_normalised_away():
    assert normalize_text("ＺＺａｌｐｈａ") == "zzalpha"  # full-width letters
    assert normalize_text("zz\u200balpha") == "zzalpha"  # zero-width space cannot hide a phrase
    assert normalize_text("zz­alpha") == "zzalpha"  # soft hyphen
    assert normalize_text("zz\x00al\x07pha") == "zzalpha"  # control characters
    assert normalize_text("zz⁠alpha﻿") == "zzalpha"  # word joiner and byte-order mark


def test_normalisation_is_idempotent_and_type_checked():
    once = normalize_text("  ZZ\u200b  Alpha İ ")
    assert normalize_text(once) == once
    with pytest.raises(TypeError):
        normalize_text(b"bytes")  # type: ignore[arg-type]


def test_a_hidden_phrase_is_still_found():
    rs = ruleset(make_rule(phrases=["zzalpha"]))
    for sneaky in ("ZZ\u200bAL‍PHA", "z z".replace(" ", "­") + "alpha", "ＺＺＡＬＰＨＡ"):
        assert evaluate_with(rs, f"please note {sneaky} thanks").outcome == "red_flag", repr(sneaky)


# ---- phrase boundaries -----------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["zzalpha", "say zzalpha now", "zzalpha.", "(zzalpha)", "x,zzalpha;y", "ZZALPHA!", "\nzzalpha\n", "a zzalpha", "zzalpha_x"])  # an underscore is punctuation, so a boundary: fire, never miss
def test_a_phrase_matches_on_token_boundaries(text):
    assert evaluate_with(ruleset(make_rule(phrases=["zzalpha"])), text).outcome == "red_flag"


@pytest.mark.parametrize("text", ["zzalphas", "azzalpha", "zzalpha2", "2zzalpha", "zzalphaé", "nothing here", ""])
def test_a_phrase_does_not_match_inside_a_longer_word(text):
    assert evaluate_with(ruleset(make_rule(phrases=["zzalpha"])), text).outcome != "red_flag"


def test_multiword_phrases_ignore_spacing_and_case():
    rs = ruleset(make_rule(phrases=["zz  Beta"]))
    for text in ("ZZ beta", "zz     BETA", "zz\n\tbeta", "xx zz beta yy"):
        assert evaluate_with(rs, text).outcome == "red_flag", repr(text)
    assert evaluate_with(rs, "zzbeta").outcome != "red_flag"


def test_tamil_words_are_not_split_at_combining_marks():
    whole = ruleset(make_rule(phrases=[HELLO_TAMIL]))
    assert evaluate_with(whole, f"அவர் {HELLO_TAMIL} சொன்னார்").outcome == "red_flag"
    prefix = ruleset(make_rule(phrases=["வணக்க"]))  # a prefix of the greeting, ends mid-word
    assert evaluate_with(prefix, HELLO_TAMIL).outcome != "red_flag"


def test_overlapping_and_repeated_occurrences_are_all_found():
    rs = ruleset(make_rule(phrases=["zz zz"]))
    matches = scan_normalized(rs.rules, normalize_text("zz zz zz zz"))
    assert [m.start for m in matches] == [0, 3, 6]


# ---- patterns --------------------------------------------------------------------------------------


def test_patterns_run_on_normalised_text_case_insensitively():
    rs = ruleset(make_rule(patterns=[r"zz\d{2} gamma"]))
    assert evaluate_with(rs, "ZZ42     GAMMA").outcome == "red_flag"
    assert evaluate_with(rs, "zz4 gamma").outcome != "red_flag"


def test_an_empty_match_carries_no_information():
    assert evaluate_with(ruleset(make_rule(patterns=["z*"])), "abc").matches == ()


@pytest.mark.parametrize("bad", ["(unclosed", "[", "*start", "", "   ", "x" * 501])
def test_a_rule_that_cannot_compile_is_reported_not_silently_dropped(bad):
    rule, problem = compile_rule({"id": uuid.uuid4(), "rule_code": "broken", "version_id": 2, "status": "approved", "phrases": [], "patterns": [bad]})
    assert rule is None and "broken v2" in problem
    rs = ruleset(make_rule(code="ok", phrases=["zzalpha"]), problems=[problem])
    assert evaluate_with(rs, "nothing").outcome == "unassessable"  # a broken rule means "cannot vouch", never "clear"
    assert evaluate_with(rs, "zzalpha").outcome == "red_flag"  # but a working rule still fires


def test_the_problem_message_never_contains_rule_text():
    _, problem = compile_rule({"id": uuid.uuid4(), "rule_code": "c", "version_id": 1, "status": "approved", "phrases": [], "patterns": ["(secret-pattern"]})
    assert "secret-pattern" not in problem


# ---- outcomes: red_flag / clear / unassessable -----------------------------------------------------


def test_outcomes():
    live = ruleset(make_rule(phrases=["zzalpha"]))
    assert evaluate_with(live, "zzalpha").outcome == "red_flag"
    assert evaluate_with(live, "all quiet").outcome == "clear"
    assert evaluate_with(live, "all quiet").safe_to_treat_as_clear


def test_no_usable_rules_is_unassessable_never_clear():
    for empty in (ruleset(), ruleset(make_rule(phrases=[], patterns=[])), ruleset(drafts_excluded=3)):
        result = evaluate_with(empty, "anything at all")
        assert result.outcome == "unassessable" and not result.safe_to_treat_as_clear


def test_a_match_always_wins_even_when_other_rules_are_broken():
    rs = ruleset(make_rule(phrases=["zzalpha"]), problems=["rule x v1 could not be compiled"])
    assert evaluate_with(rs, "zzalpha").outcome == "red_flag"


# ---- provenance carries no patient text ------------------------------------------------------------


def test_provenance_records_the_rule_and_position_never_the_patients_words():
    rid = uuid.uuid4()
    rs = ruleset(make_rule("r_prov", phrases=["zzalpha"], patterns=[r"zz\d+ gamma"], version=3, rid=rid))
    secret = "my private sentence about zzalpha and zz42 gamma ok"
    prov = evaluate_with(rs, secret).provenance
    assert {m["kind"] for m in prov["matches"]} == {"phrase", "pattern"}
    first = prov["matches"][0]
    assert first["rule_id"] == str(rid) and first["rule_code"] == "r_prov" and first["version_id"] == 3
    assert first["rule_status"] == "approved" and isinstance(first["start"], int)
    assert "private sentence" not in str(prov) and "zz42" not in str(prov)


# ---- versions and profiles -------------------------------------------------------------------------


def row(code, version, status, clinic=None):
    return {"id": uuid.uuid4(), "clinic_id": clinic, "rule_code": code, "version_id": version, "status": status}


def test_a_newer_draft_never_replaces_an_approved_version_in_the_clinical_profile():
    rows = [row("r", 1, "approved"), row("r", 2, "draft_unreviewed")]
    chosen, passed = select_active_versions(rows, "clinical")
    assert [(c["version_id"], c["status"]) for c in chosen] == [(1, "approved")] and passed == 1
    chosen, passed = select_active_versions(rows, "demo")
    assert [(c["version_id"], c["status"]) for c in chosen] == [(2, "draft_unreviewed")] and passed == 0


def test_a_draft_only_rule_is_unusable_in_clinical_but_usable_in_demo():
    rows = [row("r", 1, "draft_unreviewed")]
    assert select_active_versions(rows, "clinical") == ([], 1)
    assert [c["rule_code"] for c in select_active_versions(rows, "demo")[0]] == ["r"]


def test_a_retired_version_withdraws_the_rule_entirely():
    rows = [row("r", 1, "approved"), row("r", 2, "retired")]
    assert select_active_versions(rows, "clinical")[0] == [] and select_active_versions(rows, "demo")[0] == []


def test_clinic_and_platform_rules_with_the_same_code_are_separate():
    clinic = uuid.uuid4()
    rows = [row("r", 1, "approved", None), row("r", 1, "approved", clinic)]
    assert len(select_active_versions(rows, "clinical")[0]) == 2


@pytest.mark.parametrize("text", ["zzalpha here", "nothing", "zz42 gamma", "ZZ   BETA", ""])
def test_demo_and_clinical_give_identical_results_for_the_same_approved_rules(text):
    approved = [make_rule("a", phrases=["zzalpha", "zz beta"], patterns=[r"zz\d{2} gamma"])]
    demo = evaluate_with(ruleset(*approved, profile="demo"), text)
    clinical = evaluate_with(ruleset(*approved, profile="clinical"), text)
    assert (demo.outcome, demo.matches) == (clinical.outcome, clinical.matches)


def test_the_profile_defaults_to_the_stricter_one(monkeypatch):
    monkeypatch.delenv("CAREFLOW_PROFILE", raising=False)
    assert resolve_profile() == "clinical"
    for value, expected in (("demo", "demo"), (" DEMO ", "demo"), ("clinical", "clinical"), ("", "clinical"), ("production", "clinical")):
        monkeypatch.setenv("CAREFLOW_PROFILE", value)
        assert resolve_profile() == expected, value
    assert resolve_profile("demo") == "demo" and resolve_profile("garbage") == "clinical"


# ---- voice: the same engine over a stream (§11.6, rule 11) -----------------------------------------

RULES = (make_rule("p", phrases=["zzalpha", "zz beta gamma"], patterns=[r"zz\d{2} delta"]),)
STREAM_TEXTS = [
    "hello there zzalpha and more",
    "zz beta gamma",
    "start zz   beta \n gamma end",
    "ZZ42 DELTA is here",
    "nothing to see",
    f"{HELLO_TAMIL} zzalpha",
    "zzalpha zz beta gamma zz11 delta",
]


def whole(text):
    return kinds(evaluate_with(ruleset(*RULES), text))


def streamed(chunks):
    s = StreamEvaluator(ruleset(*RULES))
    found, reports = set(), []
    for chunk in chunks:
        result = s.feed(chunk)
        found |= kinds(result)
        reports.extend((m.rule_code, m.kind, m.index, m.start) for m in result.matches)
    return found, reports, s


@pytest.mark.parametrize("text", STREAM_TEXTS)
def test_every_two_way_split_finds_what_the_whole_text_scan_finds(text):
    expected = whole(text)
    for cut in range(len(text) + 1):
        found, _, _ = streamed([text[:cut], text[cut:]])
        assert found == expected, (text, cut)


@pytest.mark.parametrize("text", STREAM_TEXTS)
def test_character_by_character_feeding_finds_the_same_matches(text):
    found, reports, _ = streamed(list(text))
    assert found == whole(text)
    assert len(reports) == len(set(reports)), "a match was reported twice"


def test_every_three_way_split_of_a_phrase_with_spaces_still_matches():
    text = "x zz   beta  gamma y"
    for a, b in itertools.combinations(range(len(text) + 1), 2):
        found, _, _ = streamed([text[:a], text[a:b], text[b:]])
        assert ("p", "phrase", 1) in found, (a, b)


def test_a_phrase_is_reported_by_the_feed_that_completes_it_and_only_once():
    s = StreamEvaluator(ruleset(*RULES))
    assert s.feed("so zz").outcome != "red_flag" and not s.red_flag_seen
    assert s.feed(" beta ").outcome != "red_flag"
    done = s.feed("gamma")
    assert done.outcome == "red_flag" and s.red_flag_seen
    assert s.feed(" and then some more words").matches == ()  # not reported again


def test_in_a_stream_the_end_of_the_text_counts_as_a_boundary_so_it_fires_early_never_late():
    s = StreamEvaluator(ruleset(*RULES))
    assert s.feed("say zzalpha").outcome == "red_flag"  # fires before we know whether more letters follow
    assert whole("say zzalphas") == set()  # the full text, with its suffix, would not have matched


def test_streaming_has_no_end_of_stream_step_and_rejects_non_text():
    s = StreamEvaluator(ruleset(*RULES))
    assert s.feed("").outcome in {"clear", "unassessable"}
    with pytest.raises(TypeError):
        s.feed(b"bytes")  # type: ignore[arg-type]


def test_an_unassessable_stream_stays_unassessable_not_clear():
    s = StreamEvaluator(ruleset())
    assert s.feed("anything").outcome == "unassessable"


# ---- structure: no model, no network ---------------------------------------------------------------

SOURCE = (Path(__file__).resolve().parent.parent / "careflow" / "rules.py").read_text(encoding="utf-8")


def test_the_engine_imports_no_model_network_or_messaging_library():
    tree = ast.parse(SOURCE)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    forbidden = {"httpx", "requests", "aiohttp", "urllib", "socket", "smtplib", "subprocess", "anthropic", "openai",
                 "google", "langchain", "langgraph", "transformers", "torch", "onnxruntime", "sklearn", "numpy"}
    assert roots & forbidden == set()


def test_only_the_rule_and_task_tables_are_touched():
    tree = ast.parse(SOURCE)
    sql = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and re.match(r"\s*(select|insert|update|delete)\b", n.value, flags=re.IGNORECASE)]
    assert len(sql) >= 3
    tables = set()
    for statement in sql:
        tables |= set(re.findall(r"\b(?:from|into|update|join)\s+([a-z_]+\.[a-z_]+)", statement, flags=re.IGNORECASE))
    assert tables == {"kb.red_flag_rules", "agents.review_tasks"}


def test_the_module_carries_the_placeholder_and_no_embedded_rule_vocabulary():
    assert SOURCE.count(rules.PLACEHOLDER) >= 2
    assert not any(isinstance(n, ast.List) and n.elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts)
                   and len(n.elts) > 5 for n in ast.walk(ast.parse(SOURCE))), "a long word list was embedded in the engine"


def test_rule_inputs_are_checked_before_anything_touches_the_database():
    import asyncio

    engine = rules.RedFlagEngine()

    async def attempt(**kw):
        base = {"conn": None, "clinic_id": uuid.uuid4(), "origin": "test", "match_provenance": {"matches": [{"x": 1}]}, "source_message_id": "m1"}
        base.update(kw)
        return await engine.escalate_red_flag(**base)

    for kw, error in (
        ({"origin": "production"}, ValueError),
        ({"source_message_id": ""}, rules.RulesError),
        ({"source_message_id": "  "}, rules.RulesError),
        ({"source_message_id": "x" * 201}, rules.RulesError),
        ({"match_provenance": {"matches": []}}, rules.RulesError),
        ({"match_provenance": {}}, rules.RulesError),
    ):
        with pytest.raises(error):
            asyncio.run(attempt(**kw))
    assert isinstance(Rule, type)
