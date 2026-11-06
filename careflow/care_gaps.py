"""The Care Gaps panel (BUILD-SPEC.md 24, 9.3, 12.6, 22.4; Appendix D Phase 9; docs/design/phase9-care-gaps.md). MECHANISM ONLY, no new agent and no new automated action.

One surface, three sources, all things a human already needs to look at:
  * overdue_recurrence      a care episode's recurrence rule (9.3: "annual review due each March") whose due date has passed with no completion;
  * after_the_fact_review   a sample of auto-approved decisions (12.6) still waiting for a person to re-check;
  * unresolved_barrier      a social or resource barrier (22.4) still open.

NOTHING ON THE PANEL AUTO-RESOLVES. The panel reads; it never closes, retries or approves. The only thing it can do is raise the existing overdue alert (`task_overdue`, careflow/watchdog.py) for an item
older than a limit.

NO LIMIT AND NO RULE IS INVENTED HERE. A recurrence rule is clinician-authored data (`RecurrenceRule`: its cycle and its `overdue_after_days` come with it, never defaulted); the alert age
(`CAREFLOW_CARE_GAP_ALERT_DAYS`-style, passed in by the caller) is a required setting: unset means the alert check does not run and the result says so (`alert_unconfigured`), because a silent skip
would look like health.

UNKNOWN IS NOT A VALUE. A source that cannot be read is listed as `unreadable`, and its count is None, never 0 (a zero from a broken query would read as "no gaps"). An item with no known open date is
listed as `undated` and has no age; it is still counted.

PRIVACY. A row is a source, an opaque item id, an age in days and nothing else: no name, no diagnosis, no value. The reader opens the underlying record with their own role.

REACHABILITY (the phase's gate): every row must resolve, through its source, to a real open item (`verify_reachable`), and an item that is closed or missing must not be on the panel. The test seeds
all three sources and checks both directions.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final, Literal, Protocol

from careflow.watchdog import Alert

SOURCES: Final = ("overdue_recurrence", "after_the_fact_review", "unresolved_barrier")
Source = Literal["overdue_recurrence", "after_the_fact_review", "unresolved_barrier"]


@dataclass(frozen=True)
class GapItem:
    source: str
    item_id: str  # opaque
    opened_at: datetime | None  # when it became a gap (due date, sample time, barrier time); None = unknown
    is_open: bool = True


@dataclass(frozen=True)
class RecurrenceRule:
    """Clinician-authored (9.3). Either a fixed interval or an annual month; `overdue_after_days` is the grace after the due date and has NO default."""

    rule_id: str
    overdue_after_days: int
    interval_days: int | None = None
    annual_month: int | None = None  # 1-12


def rule_problems(rule: RecurrenceRule) -> list[str]:
    problems: list[str] = []
    if (rule.interval_days is None) == (rule.annual_month is None):
        problems.append("one_cycle_only")
    if rule.interval_days is not None and rule.interval_days < 1:
        problems.append("interval")
    if rule.annual_month is not None and not 1 <= rule.annual_month <= 12:
        problems.append("month")
    if rule.overdue_after_days < 0:
        problems.append("overdue_after_days")
    return problems


def next_due(rule: RecurrenceRule, last_done: date) -> date | None:
    """The next due date after the last completion. None for a rule with problems (it can never make something overdue)."""
    if rule_problems(rule):
        return None
    if rule.interval_days is not None:
        return date.fromordinal(last_done.toordinal() + rule.interval_days)
    month = rule.annual_month
    assert month is not None
    year = last_done.year if last_done.month < month else last_done.year + 1
    return date(year, month, 1)  # due from the first of the month; the grace period decides when it is OVERDUE


def is_overdue(rule: RecurrenceRule, last_done: date | None, today: date) -> bool | None:
    """True once the due date plus the rule's grace has passed. A patient never done (last_done None) is UNKNOWN here (None), not overdue and not fine: the episode's start date decides, and the caller
    must give it as `last_done`."""
    if last_done is None:
        return None
    due = next_due(rule, last_done)
    if due is None:
        return None
    return today.toordinal() > due.toordinal() + rule.overdue_after_days


# ---- the sources ----------------------------------------------------------------------------------------------------------------------------------------------------


class CareGapSource(Protocol):
    source: str

    async def open_items(self) -> Sequence[GapItem]: ...

    async def resolve(self, item_id: str) -> GapItem | None:
        """The underlying record for an opaque id, or None if it no longer exists."""
        ...


@dataclass(frozen=True)
class Row:
    source: str
    item_id: str
    age_days: int | None  # None = no known open date


@dataclass(frozen=True)
class SourceSummary:
    source: str
    open_count: int | None  # None = the source could not be read (never 0)
    median_age_days: float | None
    undated: int = 0


@dataclass(frozen=True)
class Panel:
    rows: tuple[Row, ...]
    summaries: tuple[SourceSummary, ...]
    unreadable: tuple[str, ...]
    after_the_fact_error_rate: float | None  # from the reviewer statistics; None = unknown (never 0)

    def count(self, source: str) -> int | None:
        return next((s.open_count for s in self.summaries if s.source == source), None)


def _age_days(item: GapItem, now: datetime) -> int | None:
    if item.opened_at is None:
        return None
    return max(0, (now - item.opened_at).days)


async def build_panel(sources: Sequence[CareGapSource], *, now: datetime, after_the_fact_error_rate: float | None = None) -> Panel:
    """Read every source and assemble the panel. One unreadable source does not hide the others, and is reported."""
    rows: list[Row] = []
    summaries: list[SourceSummary] = []
    unreadable: list[str] = []
    seen = set()
    for src in sources:
        seen.add(src.source)
        try:
            items = [i for i in await src.open_items() if i.is_open and i.source == src.source]
        except Exception:  # noqa: BLE001 - a broken source is reported, never counted as zero
            unreadable.append(src.source)
            summaries.append(SourceSummary(src.source, None, None))
            continue
        ages = [(_age_days(i, now), i) for i in items]
        rows += [Row(i.source, i.item_id, a) for a, i in ages]
        dated = [a for a, _ in ages if a is not None]
        summaries.append(SourceSummary(src.source, len(items), float(statistics.median(dated)) if dated else None, len(items) - len(dated)))
    for missing in (s for s in SOURCES if s not in seen):  # a source nobody wired is unknown, not empty
        unreadable.append(missing)
        summaries.append(SourceSummary(missing, None, None))
    rows.sort(key=lambda r: (r.age_days is None, -(r.age_days or 0), r.source, r.item_id))
    return Panel(tuple(rows), tuple(sorted(summaries, key=lambda s: SOURCES.index(s.source))), tuple(sorted(set(unreadable))), after_the_fact_error_rate)


def metric_values(panel: Panel) -> dict[str, dict[str, float | int | None]]:
    """The values for the two live catalog metrics, keyed by metric id then by source (metrics-catalog.yaml: care_gap_open_count, care_gap_median_age). None where unknown."""
    return {
        "care_gap_open_count": {s.source: s.open_count for s in panel.summaries},
        "care_gap_median_age": {s.source: s.median_age_days for s in panel.summaries},
    }


# ---- the overdue alert ----------------------------------------------------------------------------------------------------------------------------------------------


@dataclass
class AlertCheck:
    alerts: list[Alert] = field(default_factory=list)
    alert_unconfigured: bool = False


def alerts_for(panel: Panel, *, clinic_id: str, origin: str, alert_after_days: int | None) -> AlertCheck:
    """The existing overdue alert (`task_overdue`) for each row older than the limit. The limit is a required setting: unset means the check does not run and says so. An undated row has no age and
    raises nothing here (it is shown on the panel as undated). The key is per item, so a second pass raises nothing new."""
    if alert_after_days is None or alert_after_days < 1:
        return AlertCheck(alert_unconfigured=True)
    out = [
        Alert(clinic_id, origin, "task_overdue", 1, r.item_id, f"care-gap-overdue:{r.source}:{r.item_id}")
        for r in panel.rows
        if r.age_days is not None and r.age_days >= alert_after_days
    ]
    return AlertCheck(out)


# ---- the reachability gate ------------------------------------------------------------------------------------------------------------------------------------------


async def verify_reachable(panel: Panel, sources: Sequence[CareGapSource]) -> list[str]:
    """Problems, empty when sound. EVERY ROW resolves through its source to a real, still-open item with the same source; a row that does not is a ghost. (The other direction, that no real open item is
    missing from the panel, is checked against the source's own list.)"""
    by_source = {s.source: s for s in sources}
    problems: list[str] = []
    for row in panel.rows:
        src = by_source.get(row.source)
        if src is None:
            problems.append(f"no source for row {row.source}")
            continue
        found = await src.resolve(row.item_id)
        if found is None or not found.is_open or found.source != row.source:
            problems.append(f"row {row.source}:{row.item_id} does not resolve to an open item")
    for src in sources:
        listed = {r.item_id for r in panel.rows if r.source == src.source}
        for item in await src.open_items():
            if item.is_open and item.source == src.source and item.item_id not in listed and src.source not in panel.unreadable:
                problems.append(f"open item {src.source}:{item.item_id} is missing from the panel")
    return problems

