"""Triage routing policy, the pure part (BUILD-SPEC.md §2, §12.1, §12.3, §12.6; CLAUDE.md rules 1, 3, 9). Package P2-9.

NOTHING CLINICAL IS DECIDED HERE. No priority, no risk class, no confidence threshold and no deadline exists in this module: each is a clinician-owned value that
is passed IN, required, with no default. What is here is the SHAPE of the rules the spec fixes:

  * `resolve_mode`: the approval mode (`shadow`, `assisted`, `autonomous`). Unset means `assisted` (the spec's default); an UNRECOGNISED value means `shadow`
    (the strictest: a human decides everything), never a more permissive one.
  * `route_proposal`: whether a proposal could be eligible for the assisted-mode auto-approval of "one confident low-risk card" (§2, §12.1). It answers `human`
    unless EVERY condition holds, and each condition's input may be missing: a missing input is a reason for a human (unknown is not a value). Today no card
    carries a risk class or a priority and no confidence is calibrated, so every proposal routes to a human. This module never approves anything: the
    approval path for an automatic decision does not exist (migration 0018 can only hold human decisions).
  * `next_escalation`: an overdue task escalates up the chain nurse -> doctor -> admin. It NEVER approves; "nothing clinical is ever approved by timeout" (§12.3).
    The deadline is required (per task class, set by the clinic).
  * `is_training_label_source`: only a HUMAN decision may become a training label (§12.6, rule 9).
"""

import math
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

ESCALATION_CHAIN: Final = ("nurse", "doctor", "admin")
HUMAN_SOURCE: Final = "human"


class ApprovalMode(StrEnum):
    SHADOW = "shadow"
    ASSISTED = "assisted"
    AUTONOMOUS = "autonomous"


def resolve_mode(value: str | None) -> ApprovalMode:
    """Unset or blank: assisted (the spec's default). Anything unrecognised: shadow, the most restrictive. Never more permissive on a typo."""
    if value is None or not value.strip():
        return ApprovalMode.ASSISTED
    try:
        return ApprovalMode(value.strip().lower())
    except ValueError:
        return ApprovalMode.SHADOW


@dataclass(frozen=True)
class CardFacts:
    """Clinician-authored attributes of a matched card, supplied by the card lifecycle (which does not exist yet). Any may be None = unknown."""

    card_id: str
    version: int
    risk_class: str | None = None
    priority: str | None = None


@dataclass(frozen=True)
class AutoApprovalPolicy:
    """ALL required, NO defaults: the clinic's clinical lead sets them (§2: 'a confident, low-risk protocol match at routine/soon priority').
    `low_risk_value` and `allowed_priorities` are the card vocabulary's own labels; `min_confidence` is a CALIBRATED probability threshold."""

    low_risk_value: str
    allowed_priorities: frozenset[str]
    min_confidence: float

    def __post_init__(self) -> None:
        if not self.low_risk_value or not self.allowed_priorities or not (0.0 < self.min_confidence <= 1.0):
            raise ValueError("an auto-approval policy needs a low-risk label, allowed priorities and a confidence in (0, 1]")


@dataclass(frozen=True)
class Routing:
    auto_approval_eligible: bool
    reasons: tuple[str, ...]  # fixed words: why a human is needed (shown to the reviewer as 'the rule that triggered review', §12.2)


def route_proposal(
    *, mode: ApprovalMode, policy: AutoApprovalPolicy | None, candidates: Collection[CardFacts], abstained: bool, identity_resolved: bool,
    identity_uncertain: bool, confidence: float | None, confidence_calibrated: bool, unverified_facts_read: Collection[str],
) -> Routing:
    reasons: list[str] = []
    if mode is not ApprovalMode.ASSISTED:
        reasons.append("mode_requires_human")  # shadow: a human decides everything; autonomous: human unless the clinic opts in, which is not built
    if policy is None:
        reasons.append("no_auto_approval_policy")
    if abstained:
        reasons.append("retrieval_abstained")
    if not identity_resolved or identity_uncertain:
        reasons.append("identity_not_certain")  # 'uncertain-identity registration' is a locked human row (§12.1)
    if unverified_facts_read:
        reasons.append("unverified_fact_read_by_card")
    cards = list(candidates)
    if len(cards) != 1:
        reasons.append("not_exactly_one_candidate")
    if confidence is None or not math.isfinite(confidence) or not confidence_calibrated:
        reasons.append("confidence_unknown_or_uncalibrated")
    if policy is not None and len(cards) == 1:
        card = cards[0]
        if card.risk_class is None or card.priority is None:
            reasons.append("card_risk_or_priority_unknown")
        elif card.risk_class != policy.low_risk_value:
            reasons.append("card_not_low_risk")
        elif card.priority not in policy.allowed_priorities:
            reasons.append("card_priority_not_allowed")
        if confidence is not None and math.isfinite(confidence) and confidence_calibrated and not (confidence >= policy.min_confidence):
            reasons.append("confidence_below_policy")
    return Routing(not reasons, tuple(dict.fromkeys(reasons)))


def next_escalation(current_role: str, age_seconds: float, deadline_seconds: float) -> str | None:
    """The role an OVERDUE task escalates to, or None (not overdue, or already at the top of the chain). `deadline_seconds` is required: it is per task class
    and set by the clinic. This never returns anything that means 'approve': a timeout only moves the task up (§12.3)."""
    if not (deadline_seconds > 0) or not math.isfinite(deadline_seconds):
        raise ValueError("a deadline must be a positive number of seconds")
    if current_role not in ESCALATION_CHAIN or not (age_seconds > deadline_seconds):
        return None
    index = ESCALATION_CHAIN.index(current_role)
    return ESCALATION_CHAIN[index + 1] if index + 1 < len(ESCALATION_CHAIN) else None


def is_training_label_source(decision_source: str) -> bool:
    """Only a human decision may become a calibration/fine-tuning label; an auto-approved output never does (§12.6, CLAUDE.md rule 9)."""
    return decision_source == HUMAN_SOURCE
