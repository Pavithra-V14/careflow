"""The learning loop's label rules (BUILD-SPEC.md 12.6; CLAUDE.md rule 9).

* ONLY A HUMAN DECISION IS A LABEL. A decision made automatically (an auto-approval, a timeout, a template) is refused as a label (`AutoApprovedNotALabel`), so the model can never confirm itself.
* The label carries the decision and its reason code and nothing else about the case: no text, no patient id. The proposal is named by an opaque id.
* THE REVIEWER IS PSEUDONYMIZED in any export: HMAC-SHA256 of the account id under a REQUIRED secret (`CAREFLOW_LABEL_PSEUDONYM_KEY`), truncated; unset secret means no export (the stable id would
  otherwise be a bare hash of a guessable uuid). Rotating the secret breaks linkage across exports, by design.
* AFTER-THE-FACT SAMPLING. About `CAREFLOW_AUTO_REVIEW_SAMPLE_RATE` (the spec says ~5%, a REQUIRED setting with no default) of auto-approved cases are pulled for a person to review afterwards, so the
  error rate of the automatic path is measured and not assumed. Unset means none are sampled and the report says so. The rate must be greater than 0 and at most 1.
* Canary and test-origin decisions are never labels (they are not real cases).
"""

import hashlib
import hmac
import os
import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final

LABEL_ORIGINS: Final = ("live",)
DECISIONS: Final = ("approve", "edit", "reject")


class LabelError(Exception):
    """Fixed words only."""


class AutoApprovedNotALabel(LabelError):
    """A decision no human made."""


@dataclass(frozen=True)
class Decision:
    proposal_id: str
    decision: str
    reason_code: str | None
    decided_by: str | None  # an account id; None when nobody decided
    origin: str
    automatic: bool = False


@dataclass(frozen=True)
class Label:
    proposal_id: str
    decision: str
    reason_code: str | None
    reviewer: str  # the pseudonym


def pseudonym(account_id: str, key: str) -> str:
    if not key:
        raise LabelError("the pseudonym key is not configured")
    return hmac.new(key.encode(), account_id.encode(), hashlib.sha256).hexdigest()[:16]


def key_from(environ: Mapping[str, str] | None = None) -> str | None:
    raw = (environ if environ is not None else os.environ).get("CAREFLOW_LABEL_PSEUDONYM_KEY")
    return raw if raw and len(raw) >= 16 else None


def label_from(decision: Decision, key: str | None) -> Label:
    if decision.automatic or decision.decided_by is None:
        raise AutoApprovedNotALabel("only a human decision is a label")
    if decision.origin not in LABEL_ORIGINS:
        raise LabelError("only a live case is a label")
    if decision.decision not in DECISIONS:
        raise LabelError("unknown decision")
    if key is None:
        raise LabelError("the pseudonym key is not configured")
    return Label(decision.proposal_id, decision.decision, decision.reason_code, pseudonym(decision.decided_by, key))


def export(decisions: Iterable[Decision], key: str | None) -> tuple[list[Label], int]:
    """(labels, how many were skipped as not human or not live). Without a key nothing is exported at all."""
    if key is None:
        raise LabelError("the pseudonym key is not configured")
    labels: list[Label] = []
    skipped = 0
    for d in decisions:
        try:
            labels.append(label_from(d, key))
        except LabelError:
            skipped += 1
    return labels, skipped


def sample_rate_from(environ: Mapping[str, str] | None = None) -> float | None:
    raw = (environ if environ is not None else os.environ).get("CAREFLOW_AUTO_REVIEW_SAMPLE_RATE")
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        raise LabelError("CAREFLOW_AUTO_REVIEW_SAMPLE_RATE is not a number") from None
    if not 0 < value <= 1:
        raise LabelError("CAREFLOW_AUTO_REVIEW_SAMPLE_RATE must be greater than 0 and at most 1")
    return value


def sample_for_review(auto_approved_ids: Iterable[str], rate: float | None, draw: Callable[[], float] = random.random) -> list[str]:
    """The auto-approved cases pulled for an after-the-fact human review. No rate means none (and the caller reports that the automatic path is unmeasured)."""
    if rate is None:
        return []
    return [i for i in auto_approved_ids if draw() < rate]
