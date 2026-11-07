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


def test_the_metric_ids_are_the_catalog_ids():
    assert set(get_args(api_enums.MetricId)) == {m["id"] for m in metrics_catalog.load()["metrics"]}
