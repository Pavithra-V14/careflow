"""The cost canvas: Monthly cost ~= W x A x U + P + F (BUILD-SPEC.md 15.1), computed from MEASURED figures, never estimated in advance.

* W (workload), A (amplification: calls per workflow, measured, the ceiling is 12) and U (unit economics: cost per call) come from the spend ledger and counts (`Measured`). Each price has two columns,
  ACTUAL and the at-scale SHADOW price; they are never summed together (a free tier's zero is not a production rate).
* P (platform floor) and F's per-role reviewer rate are REQUIRED settings with no default (`CAREFLOW_COST_PLATFORM_FLOOR`, `CAREFLOW_COST_REVIEWER_RATES`): they are commercial figures the owner states.
  While either is unset, that term is `None` and the monthly total is `None` with the reason named: a missing term is never taken as zero (unknown is not a value).
* THE CANVAS IS NOT SHOWN AS A RESULT BEFORE A WEEK OF TRAFFIC (15.1, Appendix D: "computed from the first real week"): with fewer than 7 distinct days of measured traffic it reports `insufficient_data`.
* F (failure tax) = retry and escalation cost (measured from the ledger as the part of spend that repeated a step) + reviewer minutes x the per-role rate.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

MIN_DAYS: Final = 7
DAYS_PER_MONTH: Final = 30

State = Literal["ok", "insufficient_data", "unconfigured"]


class CostError(Exception):
    """Fixed words only."""


@dataclass(frozen=True)
class Measured:
    days_observed: int
    workflows: int  # completed workflows in the window
    calls: int  # model + tool calls in the window
    actual_usd: Decimal  # spend ledger, actual
    shadow_usd: Decimal  # spend ledger, shadow (the at-scale price)
    retry_actual_usd: Decimal | None  # the part of actual spend that re-ran a step; None while nothing measures it (the ledger does not mark a retry yet)
    retry_shadow_usd: Decimal | None
    reviewer_minutes_by_role: Mapping[str, Decimal] | None  # None while reviewer time is not measured


@dataclass(frozen=True)
class CostConfig:
    platform_floor_usd: Decimal | None  # P, per month
    reviewer_rate_usd_per_minute: Mapping[str, Decimal] | None  # per role


def settings_from(environ: Mapping[str, str] | None = None) -> CostConfig:
    env = environ if environ is not None else os.environ
    floor = None
    raw_floor = (env.get("CAREFLOW_COST_PLATFORM_FLOOR") or "").strip()
    if raw_floor:
        try:
            floor = Decimal(raw_floor)
        except ArithmeticError:
            raise CostError("CAREFLOW_COST_PLATFORM_FLOOR is not a number") from None
        if floor < 0:
            raise CostError("CAREFLOW_COST_PLATFORM_FLOOR cannot be negative")
    rates = None
    raw_rates = (env.get("CAREFLOW_COST_REVIEWER_RATES") or "").strip()
    if raw_rates:
        try:
            data = json.loads(raw_rates)
            rates = {str(k): Decimal(str(v)) for k, v in data.items()}
        except (ValueError, ArithmeticError, AttributeError):
            raise CostError("CAREFLOW_COST_REVIEWER_RATES must be a JSON object of role to rate per minute") from None
        if any(v < 0 for v in rates.values()):
            raise CostError("a reviewer rate cannot be negative")
    return CostConfig(floor, rates)


@dataclass(frozen=True)
class Column:
    w_workflows_per_day: Decimal
    a_calls_per_workflow: Decimal | None
    u_usd_per_call: Decimal | None
    wau_month: Decimal | None
    p_floor: Decimal | None
    f_failure: Decimal | None
    total_month: Decimal | None


@dataclass(frozen=True)
class Canvas:
    state: State
    reasons: tuple[str, ...]
    actual: Column | None
    shadow: Column | None
    amplification_over_ceiling: bool


def _column(m: Measured, s: CostConfig, spend: Decimal, retry: Decimal | None) -> Column:
    days = Decimal(m.days_observed)
    w = Decimal(m.workflows) / days
    a = Decimal(m.calls) / Decimal(m.workflows) if m.workflows else None
    u = spend / Decimal(m.calls) if m.calls else None
    wau = w * a * u * DAYS_PER_MONTH if a is not None and u is not None else None
    reviewer = None
    if m.reviewer_minutes_by_role is not None and s.reviewer_rate_usd_per_minute is not None and all(role in s.reviewer_rate_usd_per_minute for role in m.reviewer_minutes_by_role):
        reviewer = sum((minutes * s.reviewer_rate_usd_per_minute[role] for role, minutes in m.reviewer_minutes_by_role.items()), Decimal(0)) / days * DAYS_PER_MONTH
    f = None if reviewer is None or retry is None else retry / days * DAYS_PER_MONTH + reviewer
    total = None if wau is None or s.platform_floor_usd is None or f is None else wau + s.platform_floor_usd + f
    return Column(w, a, u, wau, s.platform_floor_usd, f, total)


def compute(m: Measured, s: CostConfig, *, ceiling: int = 12) -> Canvas:
    if m.days_observed < MIN_DAYS or m.workflows <= 0:
        return Canvas("insufficient_data", ("fewer than seven days of measured traffic",) if m.days_observed < MIN_DAYS else ("no completed workflows",), None, None, False)
    reasons: list[str] = []
    if s.platform_floor_usd is None:
        reasons.append("platform floor (P) is not configured")
    if s.reviewer_rate_usd_per_minute is None:
        reasons.append("reviewer rates are not configured")
    elif m.reviewer_minutes_by_role is not None and any(role not in s.reviewer_rate_usd_per_minute for role in m.reviewer_minutes_by_role):
        reasons.append("a reviewer role has no configured rate")
    if m.reviewer_minutes_by_role is None:
        reasons.append("reviewer minutes are not measured yet")
    if m.retry_actual_usd is None or m.retry_shadow_usd is None:
        reasons.append("retry cost is not measured yet")
    actual = _column(m, s, m.actual_usd, m.retry_actual_usd)
    shadow = _column(m, s, m.shadow_usd, m.retry_shadow_usd)
    over = actual.a_calls_per_workflow is not None and actual.a_calls_per_workflow > ceiling
    return Canvas("unconfigured" if reasons else "ok", tuple(reasons), actual, shadow, over)
