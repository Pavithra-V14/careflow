"""Reviewer monitoring (BUILD-SPEC.md 12.4): per-reviewer approval rate and time-to-decide, and a rubber-stamp flag. The figures come from agents.reviewer_stats (migration 0040, admin only, aggregates).

THE THRESHOLDS ARE A REQUIRED SETTING with no default (`CAREFLOW_RUBBER_STAMP_POLICY`, JSON `{"min_decisions": n, "max_approval_rate": r, "min_median_seconds": s}`): what counts as too fast or
too agreeable is a clinical-governance decision (rule 1), so while it is unset NO reviewer is flagged and the report says `not_configured`. A reviewer with fewer decisions than `min_decisions` is
`too_few` (unknown is not a value: a short run proves nothing). A reviewer who approved a canary is flagged regardless of the rates.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["not_configured", "too_few", "ok", "review"]


class StatsError(Exception):
    """Fixed words only."""


@dataclass(frozen=True)
class RubberStampPolicy:
    min_decisions: int
    max_approval_rate: float
    min_median_seconds: float

    @classmethod
    def from_setting(cls, raw: str | None) -> "RubberStampPolicy | None":
        if raw is None or not raw.strip():
            return None
        try:
            d = json.loads(raw)
            policy = cls(int(d["min_decisions"]), float(d["max_approval_rate"]), float(d["min_median_seconds"]))
        except (ValueError, KeyError, TypeError):
            raise StatsError("the rubber-stamp policy setting is malformed") from None
        if policy.min_decisions < 1 or not 0 < policy.max_approval_rate <= 1 or policy.min_median_seconds < 0:
            raise StatsError("the rubber-stamp policy setting is out of range")
        return policy


@dataclass(frozen=True)
class ReviewerRow:
    reviewer: str
    decisions: int
    approvals: int
    edits: int
    rejections: int
    canaries_seen: int
    canaries_approved: int
    median_seconds: float | None

    @property
    def approval_rate(self) -> float | None:
        return self.approvals / self.decisions if self.decisions else None


@dataclass(frozen=True)
class ReviewerReport:
    row: ReviewerRow
    verdict: Verdict
    reasons: tuple[str, ...]


def assess(rows: Iterable[ReviewerRow], policy: RubberStampPolicy | None) -> list[ReviewerReport]:
    out: list[ReviewerReport] = []
    for row in rows:
        reasons: list[str] = []
        if row.canaries_approved > 0:
            reasons.append("approved_a_canary")
        if policy is None:
            out.append(ReviewerReport(row, "review" if reasons else "not_configured", tuple(reasons)))
            continue
        if row.decisions < policy.min_decisions:
            out.append(ReviewerReport(row, "review" if reasons else "too_few", tuple(reasons)))
            continue
        rate = row.approval_rate
        if rate is not None and rate > policy.max_approval_rate:
            reasons.append("approval_rate_above_limit")
        if row.median_seconds is not None and row.median_seconds < policy.min_median_seconds:
            reasons.append("median_time_below_limit")
        out.append(ReviewerReport(row, "review" if reasons else "ok", tuple(reasons)))
    return out
