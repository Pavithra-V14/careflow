"""The gold set: its case format, its loader and its review rules (BUILD-SPEC.md 14.1; docs/design/phase5-evaluation.md). MECHANISM ONLY.

NO CASE IS WRITTEN HERE. A case's expected card, priority, red-flag and urgency labels are clinical content (CLAUDE.md rule 1), so they come from `eval/gold/*.yaml`, authored by an LLM and
REVIEWED BY A NAMED CLINICIAN. This module loads them, checks them, and says which may gate a release:

  * AN UNREVIEWED CASE CANNOT GATE (14.1). A case gates only with a reviewer who is named, has the role `clinician`, and a review date. Everything else is loaded and counted but never scored toward a gate.
  * TWO SPLITS: `tuning` (free to look at while developing) and `held_out` (frozen: never used for tuning). The held-out cases are FROZEN by a lock file of content hashes (`eval/gold/held-out.lock`):
    editing one, or removing one, is a reported problem, so a held-out case cannot be quietly retuned. Only the held-out split gates a release.
  * THE TAMIL SUBSET (`ta`, `tanglish`) is counted and reported on its own, never blended into the English numbers.
  * THE STARTING SIZE (60 referral, 40 message, 30 adversarial, 20 care-plan) is a TARGET to report against, not a gate: a small set is shown as small, never as complete.
  * UNKNOWN IS NOT A VALUE: a label that is absent stays None; a metric over zero relevant cases has no value (careflow/evalmetrics.py), it is not 0 and not 1.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

GOLD_DIR: Final = Path(__file__).resolve().parent.parent / "eval" / "gold"
LOCK_FILE: Final = "held-out.lock"
KINDS: Final = frozenset({"referral", "message", "adversarial", "care_plan", "voice"})
SPLITS: Final = frozenset({"tuning", "held_out"})
LANGUAGES: Final = frozenset({"en", "ta", "tanglish"})
TAMIL: Final = frozenset({"ta", "tanglish"})
# BUILD-SPEC.md 14.1 "starting size": a target to report against, never a gate.
STARTING_SIZE: Final = {"referral": 60, "message": 40, "adversarial": 30, "care_plan": 20}
SYNTHETIC_ONLY = "every case is synthetic text: no real patient data is ever in a gold set"


@dataclass(frozen=True)
class GoldCase:
    id: str
    kind: str
    split: str
    language: str
    text: str
    expected: dict[str, Any]  # card_ids (list), priority, red_flag, urgent, abstain, identity_outcome, injection: any label the author gives; None/absent = no label
    authored_by: str
    reviewed_by: str | None = None
    reviewer_role: str | None = None
    reviewed_on: str | None = None
    tags: tuple[str, ...] = ()

    @property
    def reviewed(self) -> bool:
        return bool(self.reviewed_by) and self.reviewer_role == "clinician" and bool(self.reviewed_on) and self.reviewed_by != self.authored_by

    @property
    def gates(self) -> bool:
        """Only a reviewed case in the frozen held-out split may gate a release."""
        return self.reviewed and self.split == "held_out"

    def content_hash(self) -> str:
        body = json.dumps(
            {"id": self.id, "kind": self.kind, "language": self.language, "text": self.text, "expected": self.expected},
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass
class Loaded:
    cases: list[GoldCase] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _case(raw: object, where: str, problems: list[str]) -> GoldCase | None:
    if not isinstance(raw, dict):
        problems.append(f"{where}: a case must be a mapping")
        return None
    missing = [k for k in ("id", "kind", "split", "language", "text", "expected", "authored_by") if k not in raw]
    if missing:
        problems.append(f"{where}: missing {', '.join(missing)}")
        return None
    cid = raw["id"]
    if not isinstance(cid, str) or not cid.strip():
        problems.append(f"{where}: the id must be a non-empty string")
        return None
    bad = [
        name
        for name, ok in (
            ("kind", raw["kind"] in KINDS), ("split", raw["split"] in SPLITS), ("language", raw["language"] in LANGUAGES),
            ("text", isinstance(raw["text"], str) and bool(raw["text"].strip())), ("expected", isinstance(raw["expected"], dict)),
            ("authored_by", isinstance(raw["authored_by"], str) and bool(raw["authored_by"].strip())),
        )
        if not ok
    ]
    if bad:
        problems.append(f"{where} ({cid}): invalid {', '.join(bad)}")
        return None
    review = raw.get("review") or {}
    if not isinstance(review, dict):
        problems.append(f"{where} ({cid}): review must be a mapping")
        return None
    return GoldCase(
        id=cid, kind=raw["kind"], split=raw["split"], language=raw["language"], text=raw["text"], expected=dict(raw["expected"]), authored_by=raw["authored_by"],
        reviewed_by=review.get("by"), reviewer_role=review.get("role"), reviewed_on=str(review["on"]) if review.get("on") else None,
        tags=tuple(str(t) for t in raw.get("tags") or ()),
    )


def load(directory: Path = GOLD_DIR) -> Loaded:
    """Every `*.yaml` in the folder. A malformed case is a PROBLEM and is left out; a duplicate id is a problem and the later one is left out. A missing folder is an empty set, not an error."""
    out = Loaded()
    seen: set[str] = set()
    for path in sorted(directory.glob("*.yaml")) if directory.is_dir() else []:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = doc.get("cases") if isinstance(doc, dict) else None
        if not isinstance(items, list):
            out.problems.append(f"{path.name}: needs a `cases` list")
            continue
        for i, raw in enumerate(items):
            case = _case(raw, f"{path.name}[{i}]", out.problems)
            if case is None:
                continue
            if case.id in seen:
                out.problems.append(f"{path.name}[{i}] ({case.id}): duplicate id")
                continue
            seen.add(case.id)
            out.cases.append(case)
    return out


def freeze_manifest(cases: Iterable[GoldCase]) -> dict[str, str]:
    """The lock file's content: id -> content hash for every held-out case."""
    return {c.id: c.content_hash() for c in sorted(cases, key=lambda c: c.id) if c.split == "held_out"}


def read_lock(directory: Path = GOLD_DIR) -> dict[str, str]:
    path = directory / LOCK_FILE
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def held_out_problems(cases: Iterable[GoldCase], lock: dict[str, str]) -> list[str]:
    """A held-out case that was edited or removed since it was frozen, or one that moved into the split without being locked. Adding a NEW held-out case is a freeze step (scripts/gold_freeze.py), not an edit."""
    current = freeze_manifest(cases)
    problems = [f"held-out case {cid} changed since it was frozen" for cid, h in sorted(lock.items()) if cid in current and current[cid] != h]
    problems += [f"held-out case {cid} was removed since it was frozen" for cid in sorted(lock) if cid not in current]
    problems += [f"held-out case {cid} is not in the lock file (freeze it)" for cid in sorted(current) if cid not in lock]
    return problems


def report(cases: Iterable[GoldCase]) -> dict[str, Any]:
    """Counts only. The size against the starting target is REPORTED; nothing here passes or fails a release."""
    cases = list(cases)
    by_kind = {k: sum(c.kind == k for c in cases) for k in sorted(KINDS)}
    return {
        "total": len(cases),
        "reviewed": sum(c.reviewed for c in cases),
        "gating": sum(c.gates for c in cases),
        "unreviewed": sum(not c.reviewed for c in cases),
        "by_kind": by_kind,
        "against_starting_size": {k: f"{by_kind.get(k, 0)} of {n}" for k, n in STARTING_SIZE.items()},
        "tamil": {"total": sum(c.language in TAMIL for c in cases), "gating": sum(c.gates and c.language in TAMIL for c in cases)},
        "tuning": sum(c.split == "tuning" for c in cases),
        "held_out": sum(c.split == "held_out" for c in cases),
    }
