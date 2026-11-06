"""The Care Gaps panel offline (careflow/care_gaps.py): recurrence arithmetic, the three sources, ages, the unknown-is-not-zero rule, the overdue alert, the catalog link and the reachability gate
(every row resolves to a real open item, and no real open item is missing). Seed data is synthetic (DEMO-*); no rule, limit or clinical content is in the module."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from careflow import care_gaps
from careflow.care_gaps import (
    SOURCES,
    GapItem,
    RecurrenceRule,
    alerts_for,
    build_panel,
    is_overdue,
    metric_values,
    next_due,
    rule_problems,
    verify_reachable,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def ago(days):
    return NOW - timedelta(days=days)


class Src:
    def __init__(self, source, items, *, fails=False):
        self.source, self.items, self.fails = source, list(items), fails

    async def open_items(self):
        if self.fails:
            raise RuntimeError("query failed")
        return self.items

    async def resolve(self, item_id):
        return next((i for i in self.items if i.item_id == item_id), None)


def seed():
    return [
        Src("overdue_recurrence", [GapItem("overdue_recurrence", "rec-1", ago(40)), GapItem("overdue_recurrence", "rec-2", ago(10))]),
        Src("after_the_fact_review", [GapItem("after_the_fact_review", "atf-1", ago(3))]),
        Src("unresolved_barrier", [GapItem("unresolved_barrier", "bar-1", ago(20)), GapItem("unresolved_barrier", "bar-2", None)]),
    ]


# ---- recurrence rules (9.3): clinician data, generic engine --------------------------------------------------------------------------------------------------------


def test_an_interval_rule_is_due_that_many_days_after_the_last_completion():
    rule = RecurrenceRule("DEMO-r", 7, interval_days=100)
    assert next_due(rule, date(2026, 1, 1)) == date(2026, 4, 11)
    assert is_overdue(rule, date(2026, 1, 1), date(2026, 4, 18)) is False  # due plus the 7 days of grace is not yet passed
    assert is_overdue(rule, date(2026, 1, 1), date(2026, 4, 19)) is True


def test_an_annual_month_rule_is_due_next_time_that_month_comes_round():
    rule = RecurrenceRule("DEMO-r", 0, annual_month=3)
    assert next_due(rule, date(2025, 3, 20)) == date(2026, 3, 1)
    assert next_due(rule, date(2025, 11, 2)) == date(2026, 3, 1)
    assert next_due(rule, date(2025, 1, 5)) == date(2025, 3, 1)


def test_a_never_done_patient_is_unknown_not_overdue_and_not_fine():
    assert is_overdue(RecurrenceRule("DEMO-r", 0, interval_days=30), None, date(2026, 11, 6)) is None


@pytest.mark.parametrize(
    "rule",
    [RecurrenceRule("r", 0), RecurrenceRule("r", 0, interval_days=3, annual_month=3), RecurrenceRule("r", 0, interval_days=0), RecurrenceRule("r", 0, annual_month=13), RecurrenceRule("r", -1, interval_days=3)],
)
def test_a_malformed_rule_has_problems_and_can_never_make_anything_overdue(rule):
    assert rule_problems(rule)
    assert next_due(rule, date(2026, 1, 1)) is None and is_overdue(rule, date(2026, 1, 1), date(2030, 1, 1)) is None


# ---- the panel ---------------------------------------------------------------------------------------------------------------------------------------------------


async def test_the_panel_counts_by_source_with_ages_and_the_oldest_first():
    panel = await build_panel(seed(), now=NOW, after_the_fact_error_rate=0.01)
    assert [(r.source, r.item_id, r.age_days) for r in panel.rows] == [
        ("overdue_recurrence", "rec-1", 40), ("unresolved_barrier", "bar-1", 20), ("overdue_recurrence", "rec-2", 10), ("after_the_fact_review", "atf-1", 3), ("unresolved_barrier", "bar-2", None),
    ]
    by = {s.source: s for s in panel.summaries}
    assert by["overdue_recurrence"].open_count == 2 and by["overdue_recurrence"].median_age_days == 25.0
    assert by["unresolved_barrier"].open_count == 2 and by["unresolved_barrier"].undated == 1 and by["unresolved_barrier"].median_age_days == 20.0
    assert panel.after_the_fact_error_rate == 0.01 and panel.unreadable == ()


async def test_a_closed_item_is_not_on_the_panel():
    sources = seed()
    sources[0].items.append(GapItem("overdue_recurrence", "rec-closed", ago(99), is_open=False))
    panel = await build_panel(sources, now=NOW)
    assert "rec-closed" not in {r.item_id for r in panel.rows}


async def test_an_unreadable_source_is_unknown_never_zero_and_does_not_hide_the_others():
    sources = seed()
    sources[1] = Src("after_the_fact_review", [], fails=True)
    panel = await build_panel(sources, now=NOW)
    by = {s.source: s for s in panel.summaries}
    assert by["after_the_fact_review"].open_count is None and "after_the_fact_review" in panel.unreadable
    assert by["overdue_recurrence"].open_count == 2
    assert metric_values(panel)["care_gap_open_count"]["after_the_fact_review"] is None


async def test_a_source_nobody_wired_is_unknown_not_empty():
    panel = await build_panel(seed()[:2], now=NOW)
    assert "unresolved_barrier" in panel.unreadable and panel.count("unresolved_barrier") is None


async def test_a_source_that_returns_items_of_another_source_does_not_leak_them_in():
    sources = seed()
    sources[0].items.append(GapItem("unresolved_barrier", "wrong-place", ago(5)))
    panel = await build_panel(sources, now=NOW)
    assert "wrong-place" not in {r.item_id for r in panel.rows}


async def test_a_row_is_only_a_source_an_opaque_id_and_an_age():
    panel = await build_panel(seed(), now=NOW)
    for row in panel.rows:
        assert set(vars(row)) == {"source", "item_id", "age_days"}


async def test_the_metric_values_use_the_catalogs_ids_and_every_source():
    catalog = yaml.safe_load((Path(__file__).resolve().parent.parent / "metrics-catalog.yaml").read_text(encoding="utf-8"))
    ids = {m["id"] for m in catalog["metrics"]}
    values = metric_values(await build_panel(seed(), now=NOW))
    assert set(values) <= ids and set(values) == {"care_gap_open_count", "care_gap_median_age"}
    assert set(values["care_gap_open_count"]) == set(SOURCES)
    assert values["care_gap_open_count"] == {"overdue_recurrence": 2, "after_the_fact_review": 1, "unresolved_barrier": 2}


# ---- the alert ----------------------------------------------------------------------------------------------------------------------------------------------------


async def test_the_overdue_alert_uses_the_existing_class_one_per_old_item_and_a_second_pass_has_the_same_keys():
    panel = await build_panel(seed(), now=NOW)
    check = alerts_for(panel, clinic_id="c1", origin="test", alert_after_days=20)
    assert [(a.alert_class, a.ref) for a in check.alerts] == [("task_overdue", "rec-1"), ("task_overdue", "bar-1")] and not check.alert_unconfigured
    assert [a.key for a in check.alerts] == [a.key for a in alerts_for(panel, clinic_id="c1", origin="test", alert_after_days=20).alerts]
    assert all(a.audience_role == "nurse" for a in check.alerts)


@pytest.mark.parametrize("limit", [None, 0, -3])
async def test_without_a_configured_limit_the_alert_check_does_not_run_and_says_so(limit):
    check = alerts_for(await build_panel(seed(), now=NOW), clinic_id="c1", origin="test", alert_after_days=limit)
    assert check.alerts == [] and check.alert_unconfigured is True


async def test_an_undated_item_raises_no_alert_but_stays_on_the_panel():
    check = alerts_for(await build_panel(seed(), now=NOW), clinic_id="c1", origin="test", alert_after_days=1)
    assert "bar-2" not in {a.ref for a in check.alerts}


# ---- the reachability gate -------------------------------------------------------------------------------------------------------------------------------------


async def test_every_row_resolves_to_a_real_open_item_and_none_is_missing_from_the_panel():
    sources = seed()
    assert await verify_reachable(await build_panel(sources, now=NOW), sources) == []


async def test_a_ghost_row_or_a_missing_item_fails_the_gate():
    sources = seed()
    panel = await build_panel(sources, now=NOW)
    sources[0].items = [i for i in sources[0].items if i.item_id != "rec-1"]  # the record vanished after the panel was built
    assert any("rec-1" in p for p in await verify_reachable(panel, sources))
    sources = seed()
    panel = await build_panel(sources, now=NOW)
    sources[1].items.append(GapItem("after_the_fact_review", "atf-new", ago(1)))  # a real open item the panel did not show
    assert any("atf-new" in p and "missing" in p for p in await verify_reachable(panel, sources))


async def test_the_panel_module_has_no_way_to_close_or_approve_anything():
    text = Path(care_gaps.__file__).read_text(encoding="utf-8")
    assert not any(word in text for word in ("update ", "insert ", "delete ", "def close", "def approve", "def resolve_item"))
