"""The pre-deployment go/no-go (BUILD-SPEC.md 14.6): the 12 checks and the production-readiness scorecard. MECHANISM ONLY; it measures nothing itself.

FAILS CLOSED, NO NUMBER INVENTED. Evidence is a file the team fills in (`ops/predeploy-evidence.yaml`); every claim needs a non-empty `evidence` pointer (a CI run, a document, a drill record) and a
`verified_on` date. A claim without them is NOT MEASURED, whatever it says. The default evidence is empty, so today the answer is NO-GO with every item NOT MEASURED, which is the truth.

THE 12 CHECKS take their bars from the spec text, and only those: the scorecard >= 70 with no category under 12 (of 20), a rollback drill under 5 minutes, a cost projection within +-20% of the dry
run, zero open critical/high security findings, 100% red-team and compliance and harness pass, and four yes/no hard gates. Nothing else is decided here.

THE SCORECARD RUBRIC (`config/readiness-rubric.yaml`) is engineering's STARTING rubric: five categories of four items at five points each. It is the owner's to edit; it asserts no clinical fact.
An item scores its points only with evidence; a missing or malformed claim scores zero.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final = Path(__file__).resolve().parent.parent
RUBRIC: Final = ROOT / "config" / "readiness-rubric.yaml"
EVIDENCE: Final = ROOT / "ops" / "predeploy-evidence.yaml"
CATEGORIES: Final = ("architecture", "observability", "security", "compliance", "operations")
CATEGORY_POINTS: Final = 20
TOTAL_BAR: Final = 70
CATEGORY_FLOOR: Final = 12
ROLLBACK_SECONDS: Final = 300
COST_TOLERANCE: Final = 0.20

CHECKS: Final = (
    ("scorecard", "Production-readiness scorecard >= 70/100, no category below 12/20"),
    ("load_test", "Load test at N x expected peak concurrency passes without SLA degradation"),
    ("security_scan", "Security scan: zero open critical/high findings"),
    ("red_team", "Guardrail red-team pass rate: 100% of known attack patterns blocked or safely handled"),
    ("compliance", "Compliance checklist: 100% of applicable items"),
    ("rollback_drill", "Rollback drill executed end-to-end, under 5 minutes"),
    ("cost_projection", "Cost projection vs dry-run actual within +-20%"),
    ("on_call_runbooks", "On-call rotation staffed, runbooks written (hard gate, yes/no)"),
    ("golden_set_and_band", "Golden eval set + variance band established (hard gate, yes/no)"),
    ("residency_access_audit", "Data residency / access-control audit passes"),
    ("harness_contract_chaos", "Harness, contract and chaos test suites: 100% pass"),
    ("business_owner_signoff", "Named business owner and success definition signed off (hard gate, yes/no)"),
)
CHECK_IDS: Final = tuple(c for c, _ in CHECKS)


@dataclass(frozen=True)
class Line:
    id: str
    status: str  # MET | NOT MET | NOT MEASURED
    reason: str  # fixed words


def _num(x: object) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) and x == x else None  # noqa: PLR0124


def _traced(entry: object) -> bool:
    """A claim counts only with a pointer to its evidence and a verification date."""
    if not isinstance(entry, Mapping):
        return False
    ev, on = entry.get("evidence"), entry.get("verified_on")
    if not (isinstance(ev, str) and ev.strip()):
        return False
    try:
        date.fromisoformat(str(on))
    except ValueError:
        return False
    return True


def load_rubric(path: Path = RUBRIC) -> dict[str, list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cats = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(cats, dict) or set(cats) != set(CATEGORIES):
        raise ValueError("the rubric needs exactly the five categories")
    for name, items in cats.items():
        if not isinstance(items, list) or sum(int(i["points"]) for i in items) != CATEGORY_POINTS or len({i["id"] for i in items}) != len(items):
            raise ValueError(f"category {name} must have unique items worth exactly {CATEGORY_POINTS} points")
    return {k: list(v) for k, v in cats.items()}


@dataclass(frozen=True)
class Score:
    by_category: dict[str, int]
    total: int
    unmeasured: tuple[str, ...]  # rubric item ids with no traced evidence

    @property
    def passes(self) -> bool:
        return self.total >= TOTAL_BAR and all(v >= CATEGORY_FLOOR for v in self.by_category.values())


def score(rubric: Mapping[str, list[dict[str, Any]]], evidence: Mapping[str, Any] | None) -> Score:
    claims = (evidence or {}).get("scorecard") or {}
    by_category: dict[str, int] = {}
    unmeasured: list[str] = []
    for name, items in rubric.items():
        points = 0
        for item in items:
            claim = claims.get(item["id"]) if isinstance(claims, Mapping) else None
            if isinstance(claim, Mapping) and _traced(claim) and claim.get("met") is True:
                points += int(item["points"])
            elif not (isinstance(claim, Mapping) and _traced(claim)):
                unmeasured.append(item["id"])
        by_category[name] = points
    return Score(by_category, sum(by_category.values()), tuple(unmeasured))


def _yes(entry: object) -> Line | None:
    if not _traced(entry):
        return Line("", "NOT MEASURED", "no traced evidence")
    met = entry.get("met") if isinstance(entry, Mapping) else None
    return None if isinstance(met, bool) and met else (Line("", "NOT MET", "not met") if isinstance(met, bool) else Line("", "NOT MEASURED", "no yes/no"))


def evaluate_checks(evidence: Mapping[str, Any] | None, rubric: Mapping[str, list[dict[str, Any]]]) -> list[Line]:
    ev = evidence or {}
    out: list[Line] = []

    def done(check: str, line: Line | None, ok: str = "met") -> None:
        out.append(Line(check, "MET", ok) if line is None else Line(check, line.status, line.reason))

    s = score(rubric, ev)
    out.append(
        Line("scorecard", "MET", f"{s.total}/100") if s.passes and not s.unmeasured else Line("scorecard", "NOT MET" if s.total else "NOT MEASURED", f"{s.total}/100 with {len(s.unmeasured)} items unevidenced")
    )
    for check in ("load_test", "red_team", "compliance", "harness_contract_chaos"):
        entry = ev.get(check)
        v = _num(entry.get("pass_rate")) if isinstance(entry, Mapping) else None
        if check == "load_test":
            done(check, _yes(entry))
        elif v is None or not _traced(entry):
            out.append(Line(check, "NOT MEASURED", "no traced pass rate"))
        else:
            out.append(Line(check, "MET", "100%") if v == 1.0 else Line(check, "NOT MET", "below 100%"))
    entry = ev.get("security_scan")
    crit, high = (_num(entry.get("critical_open")), _num(entry.get("high_open"))) if isinstance(entry, Mapping) else (None, None)
    out.append(
        Line("security_scan", "NOT MEASURED", "no traced counts") if crit is None or high is None or not _traced(entry)
        else Line("security_scan", "MET", "zero open") if crit == 0 and high == 0 else Line("security_scan", "NOT MET", "open findings")
    )
    entry = ev.get("rollback_drill")
    secs = _num(entry.get("seconds")) if isinstance(entry, Mapping) else None
    out.append(
        Line("rollback_drill", "NOT MEASURED", "no traced duration") if secs is None or not isinstance(entry, Mapping) or not _traced(entry) or entry.get("end_to_end") is not True
        else Line("rollback_drill", "MET", "under 5 minutes") if secs < ROLLBACK_SECONDS else Line("rollback_drill", "NOT MET", "5 minutes or more")
    )
    entry = ev.get("cost_projection")
    proj, actual = (_num(entry.get("projected")), _num(entry.get("actual"))) if isinstance(entry, Mapping) else (None, None)
    out.append(
        Line("cost_projection", "NOT MEASURED", "no traced figures") if proj is None or actual is None or actual <= 0 or not _traced(entry)
        else Line("cost_projection", "MET", "within 20%") if abs(proj - actual) / actual <= COST_TOLERANCE else Line("cost_projection", "NOT MET", "outside 20%")
    )
    for check in ("on_call_runbooks", "golden_set_and_band", "residency_access_audit", "business_owner_signoff"):
        done(check, _yes(ev.get(check)))
    order = {c: i for i, c in enumerate(CHECK_IDS)}
    return sorted(out, key=lambda ln: order[ln.id])


def go(lines: list[Line]) -> bool:
    """GO only when all 12 are MET. Anything not measured is NO-GO."""
    return len(lines) == len(CHECK_IDS) and all(ln.status == "MET" for ln in lines)


def render(lines: list[Line], sc: Score) -> str:
    text = [f"{'CHECK':26} {'STATUS':13} REASON"] + [f"{ln.id:26} {ln.status:13} {ln.reason}" for ln in lines]
    text.append("\nscorecard by category: " + ", ".join(f"{k} {v}/20" for k, v in sc.by_category.items()))
    text.append(f"\nDECISION: {'GO' if go(lines) else 'NO-GO'}")
    return "\n".join(text)
