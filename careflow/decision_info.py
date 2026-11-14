"""What a decision engine's answer leaves on a triage proposal, as CLOSED fields only (BUILD-SPEC.md 12.2; CLAUDE.md rule 6).

A proposal holds card ids and versions and nothing a person typed. This module is the one gate for the few extra fields a real decision may leave beside them so the review screen can show them: for each candidate
its `probability`; and on the top-ranked candidate the `engine` label, the `model_id` that answered, its `confidence`, and the `rule` the answer passed. Every field is a number in [0, 1] or a short word that
matches a closed pattern: no free text can pass, whatever a model said. A field that is missing or malformed is DROPPED (the review view then shows it as not recorded: unknown is not a value, an absent probability
is never shown as 0).
"""

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

_WORD: Final = re.compile(r"\A[a-z][a-z0-9_+.-]{0,39}\Z")
_MODEL: Final = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/+-]{0,99}\Z")
RULE_TOP_RANKED: Final = "chosen_option_ranks_first"  # the validation rule every accepted answer passed: the model's choice has the strictly highest probability


def _unit(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return float(value)


def clean_extra(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """The allowed decision fields of `raw`, validated; everything else (and anything malformed) is dropped."""
    out: dict[str, Any] = {}
    if not raw:
        return out
    for name in ("probability", "confidence"):
        v = _unit(raw.get(name))
        if v is not None:
            out[name] = v
    for name, pattern in (("engine", _WORD), ("rule", _WORD), ("model_id", _MODEL)):
        v = raw.get(name)
        if isinstance(v, str) and pattern.fullmatch(v):
            out[name] = v
    return out


def candidate_entry(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """One stored candidate: the card id and version (as before) plus only the allowed decision fields."""
    return {"card_id": str(candidate["card_id"]), "version": int(candidate["version"]), **clean_extra(candidate)}


def proposal_info(candidates: Sequence[Mapping[str, Any]] | None) -> dict[str, Any | None]:
    """engine, model_id, confidence and rule of a stored proposal (they ride on its top-ranked candidate), each None when not recorded."""
    first = candidates[0] if candidates else {}
    info = clean_extra(first if isinstance(first, Mapping) else {})
    return {"engine": info.get("engine"), "model_id": info.get("model_id"), "confidence": info.get("confidence"), "rule": info.get("rule")}
