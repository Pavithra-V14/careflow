"""The schema enums of careflow/api_enums.py stay equal to their sources: the database check lists, the catalog and the words the Care Journey graph reports. Offline."""

import re
from pathlib import Path
from typing import get_args

from careflow import api_enums, metrics_catalog

ROOT = Path(__file__).resolve().parent.parent


def _check_list(migration: str, column_pattern: str) -> set[str]:
    text = (ROOT / "db" / "migrations" / migration).read_text(encoding="utf8")
    m = re.search(column_pattern + r"\s+(?:text\s+not null\s+)?check \(\w+ in \(([^)]*)\)\)", text)
    assert m, column_pattern
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_the_alert_classes_are_the_database_check_list():
    assert set(get_args(api_enums.AlertClass)) == _check_list("0039_alerts_and_review_reason_codes.sql", r"class")


def test_the_stored_care_plan_statuses_are_the_database_check_list_plus_erased():
    text = (ROOT / "db" / "migrations" / "0057_erase_messages_and_close_care_plans.sql").read_text(encoding="utf8")
    m = re.search(r"care_plans_status_check check \(status in \(([^)]*)\)\)", text)
    assert m and set(get_args(api_enums.CarePlanStatus)) == set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_every_progress_word_the_graph_returns_is_in_the_enum():
    source = (ROOT / "careflow" / "care_journey.py").read_text(encoding="utf8")
    used = set(re.findall(r'"status": "([a-z_]+)"', source))
    assert used <= set(get_args(api_enums.CarePlanProgress)), used - set(get_args(api_enums.CarePlanProgress))


def test_the_care_gap_sources_are_the_panels_sources():
    from careflow import care_gaps

    assert tuple(get_args(api_enums.CareGapSource)) == tuple(care_gaps.SOURCES) or set(get_args(api_enums.CareGapSource)) == set(care_gaps.SOURCES)


def test_the_message_voice_reviewer_and_cost_words_match_their_sources():
    from careflow import reviewer_stats

    migrations = ROOT / "db" / "migrations"
    assert set(get_args(api_enums.ReviewerVerdict)) == set(get_args(reviewer_stats.Verdict))
    voice = (migrations / "0045_voice_preference_sessions_transcripts.sql").read_text(encoding="utf8")
    assert set(get_args(api_enums.VoiceWorkflow)) == set(re.findall(r"'([a-z_]+)'", re.search(r"workflow\s+text not null check \(workflow in \(([^)]*)\)\)", voice).group(1)))
    assert set(get_args(api_enums.VoiceSessionStatus)) == {"open", "ended"}
    messages = (migrations / "0011_msg_threads_messages.sql").read_text(encoding="utf8")
    assert set(get_args(api_enums.SenderKind)) == set(re.findall(r"'([a-z_]+)'", re.search(r"sender_kind\s+text not null check \(sender_kind in \(([^)]*)\)\)", messages).group(1)))
    cost = (ROOT / "careflow" / "cost_canvas.py").read_text(encoding="utf8")
    used = set(re.findall(r'reasons\.append\("([^"]+)"\)', cost)) | {"fewer than seven days of measured traffic", "no completed workflows"}
    assert used <= set(get_args(api_enums.CostReason)) and "fewer than seven days of measured traffic" in cost, used - set(get_args(api_enums.CostReason))


def test_every_readiness_status_word_the_run_reports_is_in_the_enum():
    source = (ROOT / "careflow" / "visit_readiness.py").read_text(encoding="utf8")
    used = set(re.findall(r'"status": "([a-z_]+)"', source))
    assert used <= set(get_args(api_enums.ReadinessStatus)), used - set(get_args(api_enums.ReadinessStatus))


def test_the_metric_ids_are_the_catalog_ids():
    assert set(get_args(api_enums.MetricId)) == {m["id"] for m in metrics_catalog.load()["metrics"]}
