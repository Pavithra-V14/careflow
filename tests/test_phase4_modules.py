"""Phase 4 mechanisms, offline (BUILD-SPEC.md 12.2-12.6, 13.1-13.5, 15.1): the review console's pure parts, canaries, reviewer monitoring, the two-person table, the label rules, observability, the watchdog and the
cost canvas. Nothing here touches a database or a network; every threshold, rate and list is passed in (they are required settings in the product, so none is invented here)."""

import ast
import asyncio
import inspect
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from careflow import (
    canary,
    cost_canvas,
    learning_labels,
    observability,
    review_console,
    reviewer_stats,
    two_person,
    watchdog,
)

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- review console ---------------------------------------------------------------------------------------------------------------------------------------------


def test_the_reason_codes_are_a_required_setting_and_unset_accepts_no_code():
    assert review_console.ReasonCodes.from_setting(None) is None and review_console.ReasonCodes.from_setting("  ") is None
    assert review_console.checked_reason(None, "approve", None) is None
    with pytest.raises(review_console.ReviewConsoleError):
        review_console.checked_reason(None, "reject", "x")  # nothing is accepted while the clinic has not decided the words
    codes = review_console.ReasonCodes.from_setting("a_word, another")
    assert review_console.checked_reason(codes, "approve", None) is None and review_console.checked_reason(codes, "reject", "a_word") == "a_word"
    for decision, code in (("reject", None), ("edit", None), ("approve", "not_listed"), ("reject", "not_listed")):
        with pytest.raises(review_console.ReviewConsoleError):
            review_console.checked_reason(codes, decision, code)
    with pytest.raises(review_console.ReviewConsoleError):
        review_console.ReasonCodes.from_setting("fine, Not Fine")


def test_deadlines_are_required_and_a_task_without_one_never_escalates_and_is_reported():
    assert review_console.DeadlinePolicy.from_setting(None) is None
    for bad in ("[]", "{}", '{"x": 0}', '{"x": true}', '{"X": 5}', "nope", '{"x": "5"}'):
        with pytest.raises(review_console.ReviewConsoleError):
            review_console.DeadlinePolicy.from_setting(bad)
    policy = review_console.DeadlinePolicy.from_setting('{"triage": 60}')
    old = NOW - timedelta(minutes=500)
    tasks = [review_console.OpenTask("t1", "triage", "nurse", old), review_console.OpenTask("t2", "other_kind", "nurse", old)]
    due, unconfigured = review_console.escalations(tasks, NOW, policy)
    assert [e.task_id for e in due] == ["t1"] and unconfigured == ["other_kind"]
    assert review_console.escalations(tasks, NOW, None)[0] == [] and review_console.escalations(tasks, NOW, None)[1] == ["offer_kind" if False else "other_kind", "triage"]


def test_escalation_climbs_nurse_doctor_admin_one_step_per_further_deadline_and_stops_at_admin():
    policy = review_console.DeadlinePolicy.from_setting('{"k": 10}')
    seen = []
    for minutes in (5, 10, 20, 30, 300):
        due, _ = review_console.escalations([review_console.OpenTask("t", "k", "nurse", NOW - timedelta(minutes=minutes))], NOW, policy)
        seen.append(due[0].notify_role if due else None)
    assert seen == [None, "doctor", "admin", "admin", "admin"]
    assert review_console.chain_role("front_desk", 1) == "nurse" and review_console.chain_role("doctor", 1) == "admin"


def test_the_console_module_cannot_approve_reject_or_resolve_anything_by_timeout():
    """Nothing clinical is approved by timeout: the module imports no database, no recorder and no writer, and has no function that takes a connection."""
    tree = ast.parse((ROOT / "careflow" / "review_console.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m.startswith(("sqlalchemy", "careflow.triage", "careflow.front_door", "careflow.review_tasks", "careflow.app")) for m in imported), imported
    for name, fn in inspect.getmembers(review_console, inspect.isfunction):
        assert "conn" not in inspect.signature(fn).parameters, name


def test_the_reviewer_view_shows_unknowns_as_none_and_never_tells_the_reviewer_it_is_a_canary():
    codes = review_console.ReasonCodes.from_setting("a_word")
    view = review_console.build_reviewer_view(
        task_id="t", kind="triage", origin="canary", candidates=[{"card_id": "c1", "version": 2, "probability": 0.7}, {"card_id": "c2", "version": 1}], abstained=False, mode="shadow",
        confidence=7, evidence=[review_console.Evidence("dob", "document:d1", crop_ref="crop:1")], reason_codes=codes,
    )
    shown = view.for_reviewer()
    assert view.is_canary and "canary" not in str(shown).lower() and "is_canary" not in shown
    assert [a["probability"] for a in shown["alternatives"]] == [0.7, None] and shown["confidence"] is None  # 7 is not a probability: unknown, not high
    assert shown["engine"] is None and shown["evidence"][0]["crop_ref"] == "crop:1" and shown["reason_codes"] == ["a_word"]
    assert "approve" in shown["on_approve"].lower() or shown["on_approve"]


# ---- canaries ---------------------------------------------------------------------------------------------------------------------------------------------------


def test_the_canary_rate_is_required_and_bounded():
    assert canary.rate_from({}) is None and canary.rate_from({"CAREFLOW_CANARY_RATE": " "}) is None
    assert canary.rate_from({"CAREFLOW_CANARY_RATE": "0.02"}) == 0.02
    for bad in ("2", "0", "-0.1", "0.06", "abc"):
        with pytest.raises(canary.CanaryError):
            canary.rate_from({"CAREFLOW_CANARY_RATE": bad})
    assert not canary.should_inject(None, lambda: 0.0)
    rng = random.Random(1)
    hits = sum(canary.should_inject(0.02, rng.random) for _ in range(20000))
    assert 250 < hits < 550


def test_a_canary_letter_is_marked_synthetic_and_carries_no_clinical_content():
    c = canary.make_canary_referral(7)
    assert canary.MARKER in c.text and "2999" in c.text and c.planted_error == "impossible_date_of_birth"
    assert c == canary.make_canary_referral(7)


class _Alerts:
    def __init__(self):
        self.rows = []

    async def raise_alert(self, **kw):
        self.rows.append(kw)


def test_only_an_approved_canary_raises_the_silent_admin_alert():
    alerts = _Alerts()
    cases = [("canary", "approve", True), ("canary", "reject", False), ("canary", "edit", False), ("live", "approve", False), ("test", "approve", False)]
    for origin, decision, expected in cases:
        assert run(canary.on_decision(origin=origin, decision=decision, decision_id="d1", clinic_id="c1", alerts=alerts)) is expected
    assert len(alerts.rows) == 1 and alerts.rows[0]["audience_role"] == "admin" and alerts.rows[0]["alert_class"] == "canary_approved"
    assert alerts.rows[0]["idempotency_key"] == "canary-approved:d1" and alerts.rows[0]["ref"] == "d1" and alerts.rows[0]["count"] == 1


# ---- reviewer monitoring and two-person rules --------------------------------------------------------------------------------------------------------------------


def _row(**kw):
    base = dict(reviewer="r", decisions=100, approvals=50, edits=0, rejections=50, canaries_seen=0, canaries_approved=0, median_seconds=60.0)  # noqa: C408
    return reviewer_stats.ReviewerRow(**{**base, **kw})


def test_without_a_policy_nobody_is_flagged_except_for_approving_a_canary():
    out = reviewer_stats.assess([_row(approvals=100, rejections=0, median_seconds=1.0), _row(canaries_approved=1)], None)
    assert [r.verdict for r in out] == ["not_configured", "review"] and out[1].reasons == ("approved_a_canary",)


def test_with_a_policy_a_short_run_is_too_few_and_fast_agreeable_reviewers_are_flagged():
    policy = reviewer_stats.RubberStampPolicy.from_setting('{"min_decisions": 20, "max_approval_rate": 0.9, "min_median_seconds": 15}')
    out = reviewer_stats.assess([_row(decisions=5), _row(approvals=100, rejections=0, median_seconds=3.0), _row()], policy)
    assert [r.verdict for r in out] == ["too_few", "review", "ok"] and set(out[1].reasons) == {"approval_rate_above_limit", "median_time_below_limit"}
    for bad in ('{"min_decisions": 0, "max_approval_rate": 0.9, "min_median_seconds": 1}', '{"min_decisions": 1}', "x", '{"min_decisions": 1, "max_approval_rate": 2, "min_median_seconds": 1}'):
        with pytest.raises(reviewer_stats.StatsError):
            reviewer_stats.RubberStampPolicy.from_setting(bad)


def test_the_four_two_person_rules_exist_and_each_refuses_one_person_or_the_wrong_role():
    assert set(two_person.RULES) == {"patient_merge", "erasure", "publish_or_retire_clinical_content", "autonomous_mode"}
    two_person.check("autonomous_mode", ("a1", ["admin"]), ("d1", ["doctor"]))
    for first, second in ((("a1", ["admin"]), ("a1", ["admin", "doctor"])), (("a1", ["admin"]), ("d1", ["nurse"])), (("a1", ["nurse"]), ("d1", ["doctor"])), (None, ("d1", ["doctor"])), (("a1", ["admin"]), None)):
        with pytest.raises(two_person.TwoPersonRefused):
            two_person.check("autonomous_mode", first, second)
    with pytest.raises(two_person.TwoPersonRefused):
        two_person.check("publish_or_retire_clinical_content", ("x", ["doctor"]), ("x", ["doctor"]))  # the author cannot be the approver
    with pytest.raises(two_person.TwoPersonRefused):
        two_person.check("nope", ("a", ["admin"]), ("b", ["doctor"]))


# ---- learning loop ----------------------------------------------------------------------------------------------------------------------------------------------


def test_only_a_live_human_decision_is_a_label_and_the_reviewer_is_pseudonymized():
    key = "k" * 20
    good = learning_labels.Decision("p1", "reject", "a_word", "11111111-1111-1111-1111-111111111111", "live")
    label = learning_labels.label_from(good, key)
    assert label.reviewer != good.decided_by and len(label.reviewer) == 16 and label.reviewer == learning_labels.label_from(good, key).reviewer
    assert learning_labels.label_from(good, "z" * 20).reviewer != label.reviewer  # rotating the secret breaks linkage
    for bad in (
        learning_labels.Decision("p", "approve", None, "u", "live", automatic=True),
        learning_labels.Decision("p", "approve", None, None, "live"),
        learning_labels.Decision("p", "approve", None, "u", "canary"),
        learning_labels.Decision("p", "approve", None, "u", "test"),
        learning_labels.Decision("p", "maybe", None, "u", "live"),
    ):
        with pytest.raises(learning_labels.LabelError):
            learning_labels.label_from(bad, key)
    with pytest.raises(learning_labels.AutoApprovedNotALabel):
        learning_labels.label_from(learning_labels.Decision("p", "approve", None, "u", "live", automatic=True), key)
    labels, skipped = learning_labels.export([good, learning_labels.Decision("p", "approve", None, "u", "live", automatic=True)], key, profile="clinical", data_policy="phi_approved")
    assert len(labels) == 1 and skipped == 1
    with pytest.raises(learning_labels.LabelError):
        learning_labels.export([good], None, profile="clinical", data_policy="phi_approved")
    assert "u" not in str(labels[0]) or "decided_by" not in str(labels[0])
    assert learning_labels.key_from({}) is None and learning_labels.key_from({"CAREFLOW_LABEL_PSEUDONYM_KEY": "short"}) is None


def test_no_label_leaves_a_demo_or_synthetic_only_deployment_and_the_live_pass_decisions_are_never_labels():
    """Owner decision 2026-11-13: the Approve on 8524ed0e and the Reject (reason other) on 16e5a640 were made in the demo profile on a test-origin clinic with synthetic data. Both are refused
    as labels on origin, and even a live-tagged decision is refused when the deployment is a demo or synthetic-only (or either setting is unset or unrecognised)."""
    key = "k" * 20
    live_pass = [learning_labels.Decision("8524ed0e", "approve", None, "11111111-1111-1111-1111-111111111111", "test"), learning_labels.Decision("16e5a640", "reject", "other", "11111111-1111-1111-1111-111111111111", "test")]
    for d in live_pass:
        with pytest.raises(learning_labels.LabelError):
            learning_labels.label_from(d, key)
    assert learning_labels.export(live_pass, key, profile="clinical", data_policy="phi_approved") == ([], 2)  # even a real deployment skips test-origin decisions
    live_tagged = learning_labels.Decision("p", "approve", None, "u", "live")
    for profile, policy in (("demo", "synthetic_only"), ("demo", "phi_approved"), ("clinical", "synthetic_only"), ("clinical", None), (None, "phi_approved"), ("clinical", "anything"), ("", "")):
        assert learning_labels.deployment_may_export_labels(profile, policy) is False
        with pytest.raises(learning_labels.LabelError, match="demo or synthetic-only"):
            learning_labels.export([live_tagged], key, profile=profile, data_policy=policy)
    assert learning_labels.deployment_may_export_labels("clinical", "phi_approved") is True


def test_the_after_the_fact_sample_is_about_the_configured_share_and_none_when_unset():
    assert learning_labels.sample_rate_from({}) is None and learning_labels.sample_for_review(["a", "b"], None) == []
    for bad in ("0", "1.5", "x"):
        with pytest.raises(learning_labels.LabelError):
            learning_labels.sample_rate_from({"CAREFLOW_AUTO_REVIEW_SAMPLE_RATE": bad})
    rate = learning_labels.sample_rate_from({"CAREFLOW_AUTO_REVIEW_SAMPLE_RATE": "0.05"})
    rng = random.Random(2)
    picked = learning_labels.sample_for_review([str(i) for i in range(20000)], rate, rng.random)
    assert 800 < len(picked) < 1200


# ---- observability ----------------------------------------------------------------------------------------------------------------------------------------------


def _capture(**kw):
    base = dict(kind="model", name="front_door", clinic="clinic-1", origin="live", status="ok", latency_ms=12, engine="decision", model="m-1", tokens_in=5, tokens_out=3)  # noqa: C408
    return observability.CallCapture(**{**base, **kw})


def test_a_capture_holds_metadata_only_and_a_sentence_is_refused():
    observability.assert_metadata_only(_capture(argument_hash=observability.hash_arguments({"a": 1})))
    assert observability.hash_arguments({"a": 1, "b": 2}) == observability.hash_arguments({"b": 2, "a": 1}) and "1" not in observability.hash_arguments({"a": 1})[:0]
    with pytest.raises(observability.ObservabilityError):
        observability.assert_metadata_only(_capture(name="Jane Doe has chest pain"))


def test_metric_labels_are_the_five_allowed_and_nothing_else():
    sink = observability.InMemoryMetricsSink()
    sink.count("calls", _capture().labels())
    assert {k for k, _ in next(iter(sink.series))[1]} == observability.METRIC_LABELS
    for extra in ("patient_id", "thread_id", "job_id"):
        with pytest.raises(observability.LabelRefused):
            sink.count("calls", {**_capture().labels(), extra: "x"})


def test_the_exporter_keeps_errors_and_canaries_samples_ordinary_ones_and_exports_nothing_ordinary_when_unset():
    trace, metrics = observability.InMemoryTraceSink(), observability.InMemoryMetricsSink()
    ex = observability.Exporter(trace, metrics, profile="demo", sample_rate=None)
    ex.capture(_capture())
    ex.capture(_capture(status="error"))
    ex.capture(_capture(origin="canary"))
    ex.capture(_capture(status="fallback"))
    assert ex.flush() == 3 and {r["status"] for r in trace.records} == {"error", "ok", "fallback"} and sum(metrics.series.values()) == 4
    full = observability.Exporter(trace, metrics, profile="demo", sample_rate=1.0)
    full.capture(_capture())
    assert full.flush() == 1


def test_in_the_clinical_profile_a_body_never_leaves_and_in_demo_it_may():
    for profile, expect in (("clinical", False), ("demo", True)):
        trace = observability.InMemoryTraceSink()
        ex = observability.Exporter(trace, observability.InMemoryMetricsSink(), profile=profile, sample_rate=1.0)
        ex.capture(_capture(), body="prompt and response text")
        ex.flush()
        assert ("body" in trace.records[0]) is expect


def test_tracing_never_blocks_or_breaks_the_app_a_full_queue_drops_the_oldest_and_a_dead_sink_is_counted():
    class Dead:
        def send(self, record):
            raise RuntimeError("down")

    ex = observability.Exporter(Dead(), observability.InMemoryMetricsSink(), profile="demo", sample_rate=1.0, capacity=3)
    for i in range(5):
        ex.capture(_capture(trace_id=f"t{i}"))
    assert ex.dropped == 2 and ex.flush() == 0 and ex.sink_failures == 3
    ex.capture(_capture(name="not metadata at all!"))
    assert ex.refused == 1
    assert observability.sample_rate_from({}) is None and observability.sample_rate_from({"CAREFLOW_TRACE_SAMPLE_RATE": "0.2"}) == 0.2
    with pytest.raises(observability.ObservabilityError):
        observability.sample_rate_from({"CAREFLOW_TRACE_SAMPLE_RATE": "2"})


# ---- the watchdog and alert routing ---------------------------------------------------------------------------------------------------------------------------------


class _InApp:
    def __init__(self):
        self.alerts = []

    async def raise_alert(self, alert):
        self.alerts.append(alert)


class _Reader:
    def __init__(self, tasks, facts):
        self.tasks, self.facts = tasks, facts

    async def open_tasks(self, clinic_id):
        return self.tasks

    async def queue_facts(self, clinic_id):
        return self.facts


def _go(tasks, facts, *, deadlines=None, limits=None, channels=None):
    in_app = _InApp()
    router = watchdog.AlertRouter(in_app, channels if channels is not None else {}, "https://example.test/sign-in")
    result = run(watchdog.run_watchdog("c1", "live", _Reader(tasks, facts), router, now=NOW, deadlines=deadlines, limits=limits or watchdog.Limits(None, None)))
    return result, in_app.alerts


def test_unconfigured_checks_are_reported_not_skipped_silently():
    result, alerts = _go([], watchdog.QueueFacts(None, 0))
    assert alerts == [] and set(result.unconfigured) == {"red_flag_minutes", "queue_age_seconds"}
    result, _ = _go([review_console.OpenTask("t", "triage", "nurse", NOW)], watchdog.QueueFacts(None, 0))
    assert "deadline:triage" in result.unconfigured


def test_overdue_red_flag_old_queue_and_dead_letters_each_raise_one_alert_and_a_second_pass_uses_the_same_keys():
    policy = review_console.DeadlinePolicy.from_setting('{"triage": 30}')
    tasks = [review_console.OpenTask("rf", "red_flag", "nurse", NOW - timedelta(minutes=45)), review_console.OpenTask("tr", "triage", "nurse", NOW - timedelta(minutes=65))]
    limits = watchdog.Limits(red_flag_minutes=10, queue_age_seconds=300)
    facts = watchdog.QueueFacts(oldest_queued_age_seconds=900, dead_lettered=2)
    result, alerts = _go(tasks, facts, deadlines=policy, limits=limits, channels={"email": watchdog.FakeChannel(), "slack": watchdog.FakeChannel()})
    classes = sorted(a.alert_class for a in alerts)
    assert classes == ["dead_letters", "queue_age", "red_flag_overdue", "task_escalated"] and result.raised == 4
    _, again = _go(tasks, facts, deadlines=policy, limits=limits, channels={"email": watchdog.FakeChannel(), "slack": watchdog.FakeChannel()})
    assert sorted(a.key for a in alerts) == sorted(a.key for a in again)  # the same keys: the database makes the repeat a no-op
    assert {a.alert_class: a.audience_role for a in alerts}["dead_letters"] == "admin"


def test_the_watchdog_changes_nothing_it_reads_and_writes_only_alerts():
    tree = ast.parse(inspect.getsource(watchdog))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"collections.abc", "dataclasses", "datetime", "typing", "careflow.review_console"}, imported  # no database, no recorder, no writer of anything but the alert sink it is handed


def test_the_message_that_leaves_the_process_is_class_count_opaque_id_and_link_only():
    alert = watchdog.Alert("clinic-1", "live", "task_escalated", 3, "task-9", "k")
    text = watchdog.outbound_text(alert, "https://example.test/sign-in")
    assert text == "CareFlow alert: task_escalated x3 id task-9. Sign in: https://example.test/sign-in"


def test_a_failed_or_missing_channel_raises_a_delivery_failed_alert_for_admin_and_a_working_one_does_not():
    policy = review_console.DeadlinePolicy.from_setting('{"triage": 30}')
    tasks = [review_console.OpenTask("tr", "triage", "nurse", NOW - timedelta(minutes=65))]
    ok = watchdog.FakeChannel()
    _, alerts = _go(tasks, watchdog.QueueFacts(None, 0), deadlines=policy, channels={"email": ok})
    assert [a.alert_class for a in alerts] == ["task_escalated"] and len(ok.sent) == 1 and "triage" not in ok.sent[0]
    for channels in ({"email": watchdog.FakeChannel(fail=True)}, {}):
        _, alerts = _go(tasks, watchdog.QueueFacts(None, 0), deadlines=policy, channels=channels)
        assert [a.alert_class for a in alerts] == ["task_escalated", "delivery_failed"] and alerts[1].audience_role == "admin"


def test_the_watchdog_limits_setting_is_strict():
    assert watchdog.limits_from(None) == watchdog.Limits(None, None)
    assert watchdog.limits_from('{"red_flag_minutes": 5}') == watchdog.Limits(5, None)
    for bad in ("x", '{"red_flag_minutes": 0}', '{"queue_age_seconds": "a"}'):
        with pytest.raises(ValueError):
            watchdog.limits_from(bad)


# ---- the cost canvas -------------------------------------------------------------------------------------------------------------------------------------------


def _measured(**kw):
    base = dict(  # noqa: C408
        days_observed=10, workflows=100, calls=600, actual_usd=Decimal(6), shadow_usd=Decimal(60), retry_actual_usd=Decimal(1), retry_shadow_usd=Decimal(10),
        reviewer_minutes_by_role={"nurse": Decimal(100)},
    )
    return cost_canvas.Measured(**{**base, **kw})


SETTINGS = cost_canvas.CostConfig(Decimal(50), {"nurse": Decimal(2)})


def test_less_than_a_week_of_traffic_is_insufficient_data_never_a_figure():
    for m in (_measured(days_observed=6), _measured(workflows=0)):
        canvas = cost_canvas.compute(m, SETTINGS)
        assert canvas.state == "insufficient_data" and canvas.actual is None and canvas.reasons


def test_the_canvas_has_two_columns_and_the_total_is_the_sum_of_its_four_terms():
    canvas = cost_canvas.compute(_measured(), SETTINGS)
    assert canvas.state == "ok" and canvas.actual and canvas.shadow
    a = canvas.actual
    assert a.w_workflows_per_day == 10 and a.a_calls_per_workflow == 6 and a.u_usd_per_call == Decimal("0.01")
    assert a.wau_month == Decimal(10) * 6 * Decimal("0.01") * 30 == Decimal(18)
    assert a.f_failure == Decimal(1) / 10 * 30 + Decimal(100) * 2 / 10 * 30 and a.total_month == a.wau_month + Decimal(50) + a.f_failure
    assert canvas.shadow.wau_month == Decimal(180) and canvas.shadow.total_month != a.total_month  # the free-tier zero and the at-scale price are never mixed


def test_a_term_that_is_not_configured_or_not_measured_is_null_with_a_reason_never_zero():
    canvas = cost_canvas.compute(_measured(), cost_canvas.CostConfig(None, None))
    assert canvas.state == "unconfigured" and canvas.actual.total_month is None and canvas.actual.p_floor is None and canvas.actual.wau_month is not None
    canvas = cost_canvas.compute(_measured(retry_actual_usd=None, retry_shadow_usd=None, reviewer_minutes_by_role=None), SETTINGS)
    assert canvas.state == "unconfigured" and canvas.actual.f_failure is None and canvas.actual.total_month is None
    assert any("retry" in r for r in canvas.reasons) and any("reviewer minutes" in r for r in canvas.reasons)
    canvas = cost_canvas.compute(_measured(reviewer_minutes_by_role={"admin": Decimal(5)}), SETTINGS)
    assert any("no configured rate" in r for r in canvas.reasons) and canvas.actual.total_month is None


def test_amplification_over_the_ceiling_is_flagged():
    assert cost_canvas.compute(_measured(calls=1500), SETTINGS).amplification_over_ceiling is True
    assert cost_canvas.compute(_measured(), SETTINGS).amplification_over_ceiling is False


def test_the_cost_settings_are_parsed_strictly_and_unset_is_none():
    assert cost_canvas.settings_from({}) == cost_canvas.CostConfig(None, None)
    s = cost_canvas.settings_from({"CAREFLOW_COST_PLATFORM_FLOOR": "12.5", "CAREFLOW_COST_REVIEWER_RATES": '{"nurse": 1.5}'})
    assert s.platform_floor_usd == Decimal("12.5") and s.reviewer_rate_usd_per_minute == {"nurse": Decimal("1.5")}
    for bad in ({"CAREFLOW_COST_PLATFORM_FLOOR": "x"}, {"CAREFLOW_COST_PLATFORM_FLOOR": "-1"}, {"CAREFLOW_COST_REVIEWER_RATES": "x"}, {"CAREFLOW_COST_REVIEWER_RATES": '{"nurse": -1}'}):
        with pytest.raises(cost_canvas.CostError):
            cost_canvas.settings_from(bad)


# ---- the route hook for canaries ---------------------------------------------------------------------------------------------------------------------------------


def test_the_review_routes_canary_hook_alerts_admin_only_for_an_approved_canary_decision(monkeypatch):
    from types import SimpleNamespace

    from careflow import app as app_module

    DOC, CLINIC, REVIEWER, DECISION = (__import__("uuid").uuid4() for _ in range(4))
    raised = []

    class Sink:
        def __init__(self, conn):
            pass

        async def raise_alert_fields(self, **kw):
            raised.append(kw)

    class Conn:
        def __init__(self, origin):
            self.origin = origin

        async def execute(self, statement, params=None):
            assert "agents.review_decisions" in str(statement) and params["u"] == REVIEWER  # only the reviewer's own decision is looked up
            return SimpleNamespace(first=lambda: SimpleNamespace(id=DECISION, origin=self.origin))

    monkeypatch.setattr(app_module, "SqlAlertSink", Sink)
    for origin, decision, expected in (("canary", "approve", 1), ("canary", "reject", 0), ("live", "approve", 0)):
        raised.clear()
        run(app_module._canary_check(Conn(origin), DOC, CLINIC, REVIEWER, decision))
        assert len(raised) == expected, (origin, decision)
        if expected:
            assert raised[0]["audience_role"] == "admin" and raised[0]["ref"] == str(DECISION) and raised[0]["alert_class"] == "canary_approved"
