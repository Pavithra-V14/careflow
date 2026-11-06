"""The review console's pure parts (BUILD-SPEC.md 12.2, 12.3): the reviewer view, the reason-code picklist, task deadlines and the escalation chain. No database, no network, no model.

REVIEWER VIEW (12.2). One structure every review task is shown through: the proposal; the evidence (with the source-image crop reference for an image-derived field); the alternatives considered with their
probabilities; what changes on approve and on reject; which engine answered and its confidence; the specific rule that triggered review. A field the source did not provide is `None`: unknown is not a value
(an absent probability is never shown as 0, an absent confidence is never shown as high). What approve and reject DO is fixed wording about the system, not clinical content.

REASON CODES (12.2). A picklist, never free text alone. WHICH codes exist is the clinic's decision (they become training labels, 12.6), so the list is a REQUIRED setting with no default
(`CAREFLOW_REVIEW_REASON_CODES`, comma separated words): while it is unset no code is accepted and no decision is required to carry one. When it is set, a reject or an edit MUST carry a code from it and
an approve may.

DEADLINES AND ESCALATION (12.3). A deadline per task class, in minutes, is a REQUIRED setting (`CAREFLOW_REVIEW_DEADLINE_MINUTES`, JSON `{"<kind>": minutes}`): it is an operational/clinical-governance value
and has no default. A task of a class with no configured deadline never becomes overdue by this module (unknown is not a value) and is reported as `unconfigured` so the watchdog can say so. An overdue task
escalates up the chain nurse -> doctor -> admin, one step per further deadline of waiting. NOTHING HERE APPROVES, REJECTS OR RESOLVES ANYTHING: escalation only names who must be told; a structural test
(tests/test_review_console.py) fails if this module ever imports a writer. Nothing clinical is approved by timeout.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

CHAIN: Final = ("nurse", "doctor", "admin")
_WORD: Final = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
ON_APPROVE: Final = "The proposal is accepted as shown and the next step of the workflow continues."
ON_REJECT: Final = "The proposal is discarded; nothing it suggested is carried out, and a person takes the case from here."
ON_EDIT: Final = "Editing is not available yet; approve or reject."


class ReviewConsoleError(Exception):
    """Fixed words only."""


# ---- reason codes -----------------------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReasonCodes:
    codes: frozenset[str]

    @classmethod
    def from_setting(cls, raw: str | None) -> "ReasonCodes | None":
        """None while the setting is unset or empty (nothing is accepted); a malformed word is an error, never a silent drop."""
        if raw is None or not raw.strip():
            return None
        words = [w.strip() for w in raw.split(",") if w.strip()]
        if not words or any(not _WORD.match(w) for w in words):
            raise ReviewConsoleError("the reason code list holds a word that is not lower-case letters, digits and underscores")
        return cls(frozenset(words))

    def check(self, decision: str, code: str | None) -> str | None:
        """The code to store, or an error: a reject or an edit needs one from the list, an approve may carry one, any code must be on the list."""
        if code is not None and code not in self.codes:
            raise ReviewConsoleError("that reason code is not on the clinic's list")
        if code is None and decision in ("reject", "edit"):
            raise ReviewConsoleError("a reject or an edit needs a reason code")
        return code


def checked_reason(codes: "ReasonCodes | None", decision: str, code: str | None) -> str | None:
    """With no list configured no code is accepted (None is returned for None; a given code is refused): the clinic has not decided what the words mean."""
    if codes is None:
        if code is not None:
            raise ReviewConsoleError("reason codes are not configured")
        return None
    return codes.check(decision, code)


# ---- deadlines and escalation -----------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DeadlinePolicy:
    minutes_by_kind: Mapping[str, int]

    @classmethod
    def from_setting(cls, raw: str | None) -> "DeadlinePolicy | None":
        if raw is None or not raw.strip():
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            raise ReviewConsoleError("the deadline setting is not valid JSON") from None
        if not isinstance(data, dict) or not data:
            raise ReviewConsoleError("the deadline setting must be an object of task kind to minutes")
        out: dict[str, int] = {}
        for kind, minutes in data.items():
            if not isinstance(kind, str) or not _WORD.match(kind) or isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 60 * 24 * 30:
                raise ReviewConsoleError("each deadline must be a whole number of minutes between 1 and 43200 for a task kind word")
            out[kind] = minutes
        return cls(out)


@dataclass(frozen=True)
class OpenTask:
    task_id: str
    kind: str
    assigned_role: str
    created_at: datetime


@dataclass(frozen=True)
class Escalation:
    task_id: str
    kind: str
    level: int  # 1 = past its deadline, 2 = past a second deadline, ... (capped at the end of the chain)
    notify_role: str  # who must be told: the assignee at level 1 is already told; the NEXT role up the chain from level 2
    overdue_minutes: int


def chain_role(assigned_role: str, steps: int) -> str:
    """The role `steps` above `assigned_role` in nurse -> doctor -> admin, never beyond admin. A role outside the chain (front desk) starts below the nurse: its first escalation goes to the nurse."""
    start = CHAIN.index(assigned_role) if assigned_role in CHAIN else -1
    return CHAIN[min(start + steps, len(CHAIN) - 1)]


def escalations(tasks: Iterable[OpenTask], now: datetime, policy: DeadlinePolicy | None) -> tuple[list[Escalation], list[str]]:
    """(the escalations due now, the kinds that have no configured deadline). Pure; a task with no deadline never escalates here."""
    due: list[Escalation] = []
    unconfigured: set[str] = set()
    for task in tasks:
        minutes = None if policy is None else policy.minutes_by_kind.get(task.kind)
        if minutes is None:
            unconfigured.add(task.kind)
            continue
        waited = (now - task.created_at).total_seconds() / 60
        if waited < minutes:
            continue
        level = int(waited // minutes)
        due.append(Escalation(task.task_id, task.kind, level, chain_role(task.assigned_role, level), int(waited - minutes)))
    return due, sorted(unconfigured)


# ---- the reviewer view ------------------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    field_name: str
    source: str  # an opaque reference to where it came from (a document id, a page); never the text itself
    crop_ref: str | None = None  # the source-image crop reference for an image-derived field


@dataclass(frozen=True)
class Alternative:
    label: str  # an opaque id (a card id) or a closed word
    probability: float | None


@dataclass(frozen=True)
class ReviewerView:
    task_id: str
    kind: str
    proposal: Mapping[str, Any]
    evidence: Sequence[Evidence]
    alternatives: Sequence[Alternative]
    on_approve: str
    on_reject: str
    on_edit: str
    engine: str | None
    confidence: float | None
    triggering_rule: str | None
    is_canary: bool = False  # NEVER serialised to the reviewer: see `for_reviewer`
    reason_codes: tuple[str, ...] = field(default=())

    def for_reviewer(self) -> dict[str, Any]:
        """What the reviewer is sent. The canary flag is deliberately absent (12.4: the reviewer is never told)."""
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "proposal": dict(self.proposal),
            "evidence": [{"field": e.field_name, "source": e.source, "crop_ref": e.crop_ref} for e in self.evidence],
            "alternatives": [{"label": a.label, "probability": a.probability} for a in self.alternatives],
            "on_approve": self.on_approve,
            "on_reject": self.on_reject,
            "on_edit": self.on_edit,
            "engine": self.engine,
            "confidence": self.confidence,
            "triggering_rule": self.triggering_rule,
            "reason_codes": list(self.reason_codes),
        }


def _probability(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1 else None


def build_reviewer_view(
    *, task_id: str, kind: str, origin: str, candidates: Sequence[Mapping[str, Any]], abstained: bool, mode: str, engine: str | None = None, confidence: float | None = None,
    triggering_rule: str | None = None, evidence: Sequence[Evidence] = (), reason_codes: ReasonCodes | None = None,
) -> ReviewerView:
    """The view of one triage proposal. The proposal holds card ids and versions only (never text); alternatives are the other candidates with their probabilities WHEN the engine gave them."""
    alternatives = [Alternative(label=f"{c.get('card_id')}@{c.get('version')}", probability=_probability(c.get("probability"))) for c in candidates]
    return ReviewerView(
        task_id=task_id, kind=kind, proposal={"abstained": abstained, "mode": mode, "candidate_count": len(candidates)}, evidence=tuple(evidence), alternatives=tuple(alternatives),
        on_approve=ON_APPROVE, on_reject=ON_REJECT, on_edit=ON_EDIT, engine=engine, confidence=_probability(confidence), triggering_rule=triggering_rule, is_canary=origin == "canary",
        reason_codes=tuple(sorted(reason_codes.codes)) if reason_codes else (),
    )
