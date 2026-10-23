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
    # msg.record_screening (migration 0013) is the one deliberate extra: stored messages are screened through that function, not by
    # a direct write to the task table. No other table, and no other function, may appear here.
    assert tables == {"kb.red_flag_rules", "agents.review_tasks", "msg.record_screening"}


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


# ---- the rule-set identity and the screening client (migration 0013). Pure: a scripted fake connection, no database ----------

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ID_A = uuid.UUID("0a000000-0000-4000-8000-000000000001")
ID_B = uuid.UUID("ff000000-0000-4000-8000-000000000002")


def _sha(text):
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_empty_rule_set_hashes_to_the_sha256_of_nothing():
    """The state migration 0007 ships in: no approved rule. The database computes the same value (coalesce(string_agg, ''))."""
    assert rules.rule_set_hash([]) == EMPTY_SHA256 == rules.EMPTY_RULE_SET_HASH
    assert ruleset().rule_set_hash == EMPTY_SHA256


def test_a_one_rule_set_hashes_the_single_id_version_line_with_no_trailing_newline():
    assert rules.rule_set_hash([{"id": ID_A, "version_id": 3}]) == _sha(f"{ID_A}:3")


def test_a_two_rule_set_is_sorted_as_plain_strings_joined_by_one_newline_and_order_independent():
    rows = [{"id": ID_B, "version_id": 1}, {"id": ID_A, "version_id": 12}]
    expected = _sha(f"{ID_A}:12\n{ID_B}:1")
    assert rules.rule_set_hash(rows) == expected == rules.rule_set_hash(list(reversed(rows)))


def test_the_hash_covers_rows_that_failed_to_compile():
    """compile_rule drops an uncompilable pattern, but the rule is still IN FORCE, so it is still part of the identity."""
    broken = {"id": ID_A, "rule_code": "broken", "version_id": 1, "status": "approved", "phrases": [], "patterns": ["("]}
    rule, problem = compile_rule(broken)
    assert rule is None and problem
    assert rules.rule_set_hash([broken]) == _sha(f"{ID_A}:1")


def test_a_different_version_is_a_different_rule_set():
    assert rules.rule_set_hash([{"id": ID_A, "version_id": 1}]) != rules.rule_set_hash([{"id": ID_A, "version_id": 2}])


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _Row:
    def __init__(self, screening_id, replayed=False, recorded_outcome=None):
        # recorded_outcome=None means "echo the outcome that was asked for" (see FakeConn.execute)
        self.screening_id, self.replayed, self.recorded_outcome = screening_id, replayed, recorded_outcome


class FakeConn:
    """Scripted answers for `select * from msg.record_screening(...)`: a row, or an exception to raise. Records every call."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[dict] = []

    def begin_nested(self):
        return _Nested()

    async def execute(self, statement, params=None):
        self.calls.append(params)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if step.recorded_outcome is None:
            step.recorded_outcome = params["o"]
        return _Result(step)


def _db_error(message, sqlstate=None):
    from sqlalchemy.exc import DBAPIError

    class Orig(Exception):
        pass

    orig = Orig(message)
    orig.sqlstate = sqlstate  # type: ignore[attr-defined]
    return DBAPIError("select ...", {}, orig)


def _evaluation(outcome="clear", profile="clinical", matches=(), n=1):
    return Evaluation(outcome, tuple(matches), profile, n, 0, (), "ab" * 32)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_a_clear_is_sent_to_the_database_with_the_rule_set_identity():
    sid = uuid.uuid4()
    conn = FakeConn(_Row(sid))
    out = _run(rules.RedFlagEngine().record_screening(conn, uuid.uuid4(), "test", uuid.uuid4(), _evaluation(), idempotency_key="k1"))
    assert (out.screening_id, out.replayed, out.outcome) == (sid, False, "clear")
    (call,) = conn.calls
    assert (call["o"], call["p"], call["n"], call["h"], call["k"]) == ("clear", "clinical", 1, "ab" * 32, "k1")


def test_a_clear_refused_by_the_database_is_recorded_as_unassessable_and_warned_once_without_patient_text(caplog):
    import logging

    clinic, message, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = FakeConn(_db_error("CLEAR_REFUSED"), _Row(sid))
    caplog.set_level(logging.WARNING, logger="careflow.rules")
    out = _run(rules.RedFlagEngine().record_screening(conn, clinic, "test", message, _evaluation(), idempotency_key="k1"))
    assert out.outcome == "unassessable" and out.screening_id == sid
    assert [c["o"] for c in conn.calls] == ["clear", "unassessable"]  # never retried as a clear
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert str(clinic) in caplog.text and str(message) in caplog.text


def test_a_demo_clear_on_live_origin_is_never_sent_as_a_clear_and_logs_the_explainable_warning(caplog):
    import logging

    clinic, message = uuid.uuid4(), uuid.uuid4()
    conn = FakeConn(_Row(uuid.uuid4()))
    caplog.set_level(logging.WARNING, logger="careflow.rules")
    out = _run(rules.RedFlagEngine().record_screening(conn, clinic, "live", message, _evaluation(profile="demo"), idempotency_key="k1"))
    assert out.outcome == "unassessable" and [c["o"] for c in conn.calls] == ["unassessable"]
    assert f"screening: demo-profile clear on live origin treated as unassessable (clinic={clinic} message={message})" in caplog.text


def test_a_demo_clear_on_a_test_origin_is_still_sent_as_a_clear():
    conn = FakeConn(_Row(uuid.uuid4()))
    out = _run(rules.RedFlagEngine().record_screening(conn, uuid.uuid4(), "test", uuid.uuid4(), _evaluation(profile="demo"), idempotency_key="k1"))
    assert out.outcome == "clear" and [c["o"] for c in conn.calls] == ["clear"]


def test_a_red_flag_is_sent_with_at_most_fifty_matches_and_says_when_it_truncated():
    matches = [rules.Match(uuid.uuid4(), "r", 1, "approved", "phrase", 0, i, i + 1) for i in range(60)]
    conn = FakeConn(_Row(uuid.uuid4()))
    _run(rules.RedFlagEngine().record_screening(conn, uuid.uuid4(), "test", uuid.uuid4(), _evaluation("red_flag", matches=matches), idempotency_key="k"))
    import json

    sent = json.loads(conn.calls[0]["prov"])
    assert len(sent["matches"]) == 50 and sent["matches_truncated"] is True and sent["outcome"] == "red_flag"
    assert set(sent["matches"][0]) == {"rule_id", "rule_code", "version_id", "rule_status", "kind", "index", "start", "end"}


def test_the_result_reports_the_outcome_of_the_row_the_database_returned_not_the_one_requested():
    """An unassessable satisfied by an existing red flag comes back as the red flag."""
    sid = uuid.uuid4()
    conn = FakeConn(_Row(sid, replayed=True, recorded_outcome="red_flag"))
    out = _run(rules.RedFlagEngine().record_screening(conn, uuid.uuid4(), "test", uuid.uuid4(), _evaluation("unassessable"), idempotency_key="k"))
    assert (out.screening_id, out.replayed, out.outcome) == (sid, True, "red_flag")


class _FixedEvaluationEngine(rules.RedFlagEngine):
    """An engine whose rules are not under test: evaluate_text returns a prepared Evaluation."""

    def __init__(self, evaluation):
        self._evaluation = evaluation

    async def evaluate_text(self, *args, **kwargs):
        return self._evaluation


def test_the_default_screening_key_changes_with_the_rule_set_and_profile():
    message = uuid.UUID(int=7)
    keys = []
    for evaluation in (_evaluation(), Evaluation("clear", (), "clinical", 1, 0, (), "cd" * 32), Evaluation("clear", (), "demo", 1, 0, (), "ab" * 32)):
        conn = FakeConn(_Row(uuid.uuid4()))
        _run(_FixedEvaluationEngine(evaluation).evaluate_and_escalate(conn, uuid.uuid4(), "test", "text", message))
        keys.append(conn.calls[0]["k"])
    assert len(set(keys)) == 3 and all(str(message) in k for k in keys)


def test_a_permission_refusal_is_not_permitted_and_is_never_swallowed():
    conn = FakeConn(_db_error("new row violates row-level security policy", "42501"))
    with pytest.raises(rules.NotPermitted):
        _run(rules.RedFlagEngine().record_screening(conn, uuid.uuid4(), "test", uuid.uuid4(), _evaluation("red_flag"), idempotency_key="k"))


def test_an_unexpected_database_error_on_a_red_flag_propagates_loudly():
    conn = FakeConn(_db_error("boom", "XX000"))
    with pytest.raises(Exception, match="boom|select"):
        _run(rules.RedFlagEngine().record_screening(conn, uuid.uuid4(), "test", uuid.uuid4(), _evaluation("red_flag"), idempotency_key="k"))


def test_screening_inputs_are_checked_before_the_database_is_touched():
    engine = rules.RedFlagEngine()
    conn = FakeConn()
    for kwargs, error in (
        ({"origin": "production"}, ValueError),
        ({"message_id": "not-a-uuid"}, TypeError),
        ({"idempotency_key": "  "}, rules.RulesError),
    ):
        args = {"clinic_id": uuid.uuid4(), "origin": "test", "message_id": uuid.uuid4(), "idempotency_key": "k", **kwargs}
        with pytest.raises(error):
            _run(engine.record_screening(conn, args["clinic_id"], args["origin"], args["message_id"], _evaluation(), idempotency_key=args["idempotency_key"]))
    assert conn.calls == []


def test_the_screening_client_never_sends_the_patient_text():
    """record_screening takes an Evaluation (rule ids and offsets), not the message text, so there is nothing to leak."""
    import inspect

    params = inspect.signature(rules.RedFlagEngine.record_screening).parameters
    assert "text_content" not in params and "text" not in params
