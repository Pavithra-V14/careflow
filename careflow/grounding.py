"""Tiered grounding of extracted facts (BUILD-SPEC.md §11.2; CLAUDE.md rules 2, 3, 6). Package P2-5. Plain code: no Guardrails AI (see
docs/design/p2-5-grounding.md for why).

THE PROMISE. A fact that a model extracted from a document reaches a rule only if it was checked against the document's own words.
How it is checked depends on what kind of fact it is:

  * TIER 1, structured values (a date, a phone number, a blood pressure, a quantity with its unit): the extracted value and the quoted source
    passage are each parsed to a canonical form and the canonical forms are compared EXACTLY. A number is never fuzzy-matched, never rounded,
    never converted between units. Anything the parser is not certain of (a day/month order that cannot be told, a range, a fraction, a decimal
    comma, two different candidates in the quote, a unit that is not spelt the same) is "ambiguous" and the fact stays unverified.
  * TIER 2, interpretive findings ("abnormal ECG", "exertional"): the cited passage and the claim go to the `decision` engine through the
    DecisionService (so the gateway, the data policy, the budget and the record all apply). Only a calibrated "supported" answer verifies. No
    confidence threshold is invented here: an UNCALIBRATED engine verifies nothing, because its confidence is not a probability (§5.2).
  * TIER 3, free-text summaries: never checked, always labelled `model_generated`, and never usable by a rule.

A FAILED CHECK MEANS "UNVERIFIED", NEVER "FALSE". There is no false/rejected status in this module: a mismatch, an ambiguity, an unreachable engine
and an engine that said "not supported" all end in the same place, `Grounding.UNVERIFIED`, with a short fixed-word `reason` for the reviewer. An
unverified fact satisfies no rule (rule 3): `usable_by_rules` is False and `rule_value()` returns the UNKNOWN sentinel, which refuses to be
read as true or false. Whether an unverified fact needs a human is decided by `needs_review`: only if a matched card's rules actually read it.

What this module does NOT do: it does not decide which facts a card reads (that is the card, clinician-authored), it does not extract anything,
and it does not call a model for tiers 1 and 3. It never logs. Fact values and quotes are patient data: they are `repr=False` everywhere and no
exception message contains them.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import IntEnum, StrEnum
from typing import Final, Protocol

from careflow.budget import CallClass, RunBudget
from careflow.gateway import (
    AllRoutesFailed,
    GatewayResult,
    NoApprovedRoute,
    SpendCeilingReached,
)
from careflow.mpi import InvalidPhone, canonicalize_phone
from careflow.ports import DataClass, DecisionRequest, DecisionResult


class FactTier(IntEnum):
    STRUCTURED = 1
    INTERPRETIVE = 2
    FREE_TEXT = 3


class Grounding(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"  # the ONLY outcome of a failed check. There is deliberately no "false" or "rejected".
    MODEL_GENERATED = "model_generated"  # tier 3: labelled, not checked


class StructuredKind(StrEnum):
    DATE = "date"
    PHONE = "phone"
    BLOOD_PRESSURE = "blood_pressure"
    QUANTITY = "quantity"  # a number with an optional unit: EF 55%, a dose, a weight


# Fixed words. These are the only reasons a fact can carry.
MATCH: Final = "match"
NOT_FOUND: Final = "not_found"  # the quote does not contain the value (in canonical form)
AMBIGUOUS: Final = "ambiguous"  # the quote could mean more than one thing; a parser that guesses would be wrong sometimes
UNPARSEABLE_VALUE: Final = "unparseable_value"  # the extracted value is not in a form this tier can canonicalise
EMPTY: Final = "empty"
NO_ENGINE: Final = "no_engine"  # tier 2: no approved route, spend ceiling, or every route failed
NOT_SUPPORTED: Final = "not_supported"
UNCALIBRATED: Final = "uncalibrated"  # tier 2: the engine's confidence is raw, so its "supported" is not trusted
INVALID_ANSWER: Final = "invalid_answer"
GENERATED: Final = "generated"


class _Unknown:
    """The value of a fact no rule may use. It is not true and not false: reading it as a boolean is a bug and raises."""

    __slots__ = ()

    def __bool__(self) -> bool:
        raise TypeError("UNKNOWN is neither true nor false (CLAUDE.md rule 3)")

    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN: Final = _Unknown()


@dataclass(frozen=True)
class GroundedFact:
    field_name: str
    tier: FactTier
    status: Grounding
    reason: str
    value: str = field(repr=False, default="")  # patient data: never in a repr

    @property
    def usable_by_rules(self) -> bool:
        """True only for a VERIFIED tier 1 or 2 fact. Tier 3 and every unverified fact: False, with no exception."""
        return self.status is Grounding.VERIFIED and self.tier is not FactTier.FREE_TEXT

    def rule_value(self) -> str | _Unknown:
        """The value, if a rule may read it; otherwise UNKNOWN, which satisfies nothing."""
        return self.value if self.usable_by_rules else UNKNOWN


def _unverified(name: str, tier: FactTier, reason: str, value: str = "") -> GroundedFact:
    return GroundedFact(name, tier, Grounding.UNVERIFIED, reason, value)


def needs_review(facts: Iterable[GroundedFact], fields_read_by_matched_rules: Iterable[str]) -> tuple[str, ...]:
    """Field names a human must look at: unverified facts that a matched card's rules actually read (§11.2). An unverified fact nothing reads
    raises no review (that is what keeps the queue from flooding); a fact a rule reads that has no grounded entry at all is reported too,
    because a missing fact is unknown and a rule that needs it cannot be satisfied."""
    read = set(fields_read_by_matched_rules)
    by_name: dict[str, list[GroundedFact]] = {}
    for f in facts:
        by_name.setdefault(f.field_name, []).append(f)
    # Two entries for one field (conflicting extractions) are usable only if EVERY one is: order must never hide an unverified entry.
    return tuple(sorted(n for n in read if n not in by_name or not all(f.usable_by_rules for f in by_name[n])))


# ---- tier 3 ----------------------------------------------------------------------------------------------------------------------------


def label_free_text(name: str, text: str) -> GroundedFact:
    """A free-text summary: labelled model-generated, never checked, never usable by a rule."""
    return GroundedFact(name, FactTier.FREE_TEXT, Grounding.MODEL_GENERATED, GENERATED, text)


# ---- tier 1 ----------------------------------------------------------------------------------------------------------------------------

_AMBIG: Final = object()  # a candidate the parser recognised but cannot read with certainty

_MONTHS: Final = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7,
    "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_RE: Final = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO: Final = re.compile(r"(?<![0-9/.\-])([0-9]{4})-([0-9]{2})-([0-9]{2})(?![0-9/.\-]*[0-9])", re.ASCII)
_NUMERIC_DATE: Final = re.compile(r"(?<![0-9/.\-])([0-9]{1,2})([/.\-])([0-9]{1,2})\2([0-9]{4})(?![0-9/])", re.ASCII)
_DAY_MONTH_NAME: Final = re.compile(rf"(?<![0-9A-Za-z])([0-9]{{1,2}})(?:st|nd|rd|th)?[\s\-]+({_MONTH_RE})\.?,?[\s\-]+([0-9]{{4}})(?![0-9])", re.ASCII | re.IGNORECASE)
_MONTH_NAME_DAY: Final = re.compile(rf"(?<![0-9A-Za-z])({_MONTH_RE})\.?\s+([0-9]{{1,2}})(?:st|nd|rd|th)?,?\s+([0-9]{{4}})(?![0-9])", re.ASCII | re.IGNORECASE)


def _make_date(y: int, m: int, d: int):
    try:
        return date(y, m, d)
    except ValueError:
        return _AMBIG  # not a real calendar date: do not read it as anything


def _date_candidates(text: str) -> list[tuple[int, int, object]]:
    """(start, end, date | _AMBIG) for every date-looking span. A numeric day/month order is taken only when one part is greater than 12
    (so it can only be one order) or both parts are equal; otherwise it is _AMBIG. Overlapping spans are collapsed."""
    found: list[tuple[int, int, object]] = []
    for m in _ISO.finditer(text):
        found.append((m.start(), m.end(), _make_date(int(m[1]), int(m[2]), int(m[3]))))
    for m in _NUMERIC_DATE.finditer(text):
        a, b, y = int(m[1]), int(m[3]), int(m[4])
        if a == b or (a > 12 >= b):
            found.append((m.start(), m.end(), _make_date(y, b, a)))  # day first
        elif b > 12 >= a:
            found.append((m.start(), m.end(), _make_date(y, a, b)))  # month first
        else:
            found.append((m.start(), m.end(), _AMBIG))
    for m in _DAY_MONTH_NAME.finditer(text):
        found.append((m.start(), m.end(), _make_date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))))
    for m in _MONTH_NAME_DAY.finditer(text):
        found.append((m.start(), m.end(), _make_date(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))))
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    kept: list[tuple[int, int, object]] = []
    for span in found:
        if kept and span[0] < kept[-1][1]:
            if span[1] > kept[-1][1]:  # partially overlapping spans: unreadable
                kept[-1] = (kept[-1][0], span[1], _AMBIG)
            continue
        kept.append(span)
    return kept


def _blank_dates(text: str) -> str:
    out = text
    for start, end, _ in reversed(_date_candidates(text)):
        out = out[:start] + " " * (end - start) + out[end:]
    return out


def _compare(candidates: Sequence[object], canonical: object) -> str:
    """MATCH iff the quote holds exactly one distinct readable candidate and it is the canonical value."""
    if not candidates:
        return NOT_FOUND
    if any(c is _AMBIG for c in candidates):
        return AMBIGUOUS
    distinct = set(candidates)
    if len(distinct) > 1:
        return AMBIGUOUS
    return MATCH if next(iter(distinct)) == canonical else NOT_FOUND


def _check_date(value: str, quote: str) -> str:
    spans = _date_candidates(value.strip())
    if len(spans) != 1 or spans[0][0] != 0 or spans[0][1] != len(value.strip()) or spans[0][2] is _AMBIG:
        return UNPARSEABLE_VALUE
    return _compare([c for _, _, c in _date_candidates(quote)], spans[0][2])


_PHONE_RUN: Final = re.compile(r"(?<![0-9])\+?[0-9][0-9\s\-().]{7,}[0-9](?![0-9])", re.ASCII)


def _check_phone(value: str, quote: str) -> str:
    try:
        canonical = canonicalize_phone(value)
    except (InvalidPhone, TypeError):
        return UNPARSEABLE_VALUE
    found: list[object] = []
    for run in _PHONE_RUN.finditer(_blank_dates(quote)):
        try:
            found.append(canonicalize_phone(run.group()))
        except InvalidPhone:
            continue  # a digit run that is not a phone number (an id, a date) is not a candidate
    return _compare(found, canonical)


_BP: Final = re.compile(r"(?<![0-9/.])([0-9]{2,3})\s*/\s*([0-9]{2,3})(?![0-9/])", re.ASCII)


def _check_bp(value: str, quote: str) -> str:
    whole = _BP.fullmatch(value.strip())
    if whole is None:
        return UNPARSEABLE_VALUE
    canonical = (int(whole[1]), int(whole[2]))
    return _compare([(int(m[1]), int(m[2])) for m in _BP.finditer(_blank_dates(quote))], canonical)


_NUMBER_UNIT: Final = re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)(?![0-9.]*[0-9])\s*([A-Za-z%µ°]+(?:/[A-Za-z]+)?)?")
_RANGE: Final = re.compile(r"[0-9]\s*(?:-|–|—|\bto\b)\s*[0-9]", re.IGNORECASE)
_FRACTION: Final = re.compile(r"[0-9]\s*/\s*[0-9]")
_DECIMAL_COMMA: Final = re.compile(r"[0-9],[0-9]")
# A bound, an approximation or a negative changes what the number means, so a quote carrying one is not read at all.
_QUALIFIER: Final = re.compile(
    r"[<>~\u2264\u2265\u2248\u00b1]|(?:^|\s)-\s*[0-9]|\b(?:about|approx|approximately|around|over|under|above|below|up to|less than|more than|at least|at most|nearly)\b",
    re.IGNORECASE,
)


def _quantity(raw: str):
    m = _NUMBER_UNIT.fullmatch(raw.strip())
    if m is None:
        return None
    try:
        number = Decimal(m[1])
    except InvalidOperation:
        return None
    return (number, m[2].casefold() if m[2] else None)


def _check_quantity(value: str, quote: str) -> str:
    wanted = _quantity(value)
    if wanted is None:
        return UNPARSEABLE_VALUE
    scrubbed = _blank_dates(quote)
    # A range ("5-10 mg"), a fraction ("1/2 tab"), a decimal comma ("5,5"), a bound ("<35%", "up to 5 mg") or a negative makes the numbers around it unreadable: ambiguous, never guessed.
    if _RANGE.search(scrubbed) or _FRACTION.search(scrubbed) or _DECIMAL_COMMA.search(scrubbed) or _QUALIFIER.search(scrubbed):
        return AMBIGUOUS
    candidates = [(Decimal(m[1]), m[2].casefold() if m[2] else None) for m in _NUMBER_UNIT.finditer(scrubbed)]
    # Only candidates in the value's own unit compete: "EF 35% (was 55%)" holds two percentages and so is ambiguous for either.
    # Units are compared as spelt (case-insensitively); there are no synonyms and no conversions.
    same_unit = [c for c in candidates if c[1] == wanted[1]]
    return _compare(same_unit, wanted)


_STRUCTURED_CHECKS: Final = {
    StructuredKind.DATE: _check_date,
    StructuredKind.PHONE: _check_phone,
    StructuredKind.BLOOD_PRESSURE: _check_bp,
    StructuredKind.QUANTITY: _check_quantity,
}


def verify_structured(name: str, kind: StructuredKind, value: str, source_quote: str) -> GroundedFact:
    """Tier 1. VERIFIED only if the canonical form of `value` is exactly the one canonical value in `source_quote`; every other outcome is
    UNVERIFIED with a fixed reason. Never raises on bad input: an unreadable value is unverified, not an error."""
    if not isinstance(value, str) or not isinstance(source_quote, str) or not value.strip() or not source_quote.strip():
        return _unverified(name, FactTier.STRUCTURED, EMPTY, value if isinstance(value, str) else "")
    reason = _STRUCTURED_CHECKS[StructuredKind(kind)](value, source_quote)
    status = Grounding.VERIFIED if reason == MATCH else Grounding.UNVERIFIED
    return GroundedFact(name, FactTier.STRUCTURED, status, reason, value)


# ---- tier 2 ----------------------------------------------------------------------------------------------------------------------------

SUPPORTED: Final = "supported"
NOT_SUPPORTED_OPTION: Final = "not_supported"
ENTAILMENT_QUESTION: Final = "grounding_entailment"


class Decider(Protocol):
    """What tier 2 needs from the decision service (careflow.decision.DecisionService satisfies it)."""

    async def decide(
        self, request: DecisionRequest, *, clinic_id: uuid.UUID, call_class: CallClass = ..., budget: RunBudget | None = ...
    ) -> GatewayResult[DecisionResult]: ...


async def verify_interpretive(
    decider: Decider, name: str, claim: str, passage: str, *, clinic_id: uuid.UUID, data_class: DataClass, language: str = "en",
    budget: RunBudget | None = None,
) -> GroundedFact:
    """Tier 2. Ask the decision engine whether `passage` supports `claim`. VERIFIED only if the engine answered "supported" AND its answer is
    calibrated for this question. Not-supported, uncalibrated, an answer outside the two options, and every gateway failure (no approved route,
    spend ceiling, all routes failed) all end UNVERIFIED. `data_class` is required: the caller says whether the text is patient data, and the
    gateway's data policy decides which route may see it. A BudgetExceeded is NOT swallowed: it stops the run."""
    if not isinstance(claim, str) or not isinstance(passage, str) or not claim.strip() or not passage.strip():
        return _unverified(name, FactTier.INTERPRETIVE, EMPTY, claim if isinstance(claim, str) else "")
    request = DecisionRequest(
        question_type=ENTAILMENT_QUESTION,
        # The passage is quoted data, the claim is quoted data; the adapter's fixed instructions are the only instructions (§11.1).
        text=f"PASSAGE:\n{passage}\n\nCLAIM:\n{claim}",
        options=(SUPPORTED, NOT_SUPPORTED_OPTION),
        data_class=data_class,
        language=language,
    )
    try:
        result = await decider.decide(request, clinic_id=clinic_id, budget=budget)
    except (NoApprovedRoute, SpendCeilingReached, AllRoutesFailed):
        return _unverified(name, FactTier.INTERPRETIVE, NO_ENGINE, claim)
    decision = result.value
    if decision.choice not in (SUPPORTED, NOT_SUPPORTED_OPTION):
        return _unverified(name, FactTier.INTERPRETIVE, INVALID_ANSWER, claim)
    if decision.choice != SUPPORTED:
        return _unverified(name, FactTier.INTERPRETIVE, NOT_SUPPORTED, claim)
    if not decision.calibrated:
        return _unverified(name, FactTier.INTERPRETIVE, UNCALIBRATED, claim)
    return GroundedFact(name, FactTier.INTERPRETIVE, Grounding.VERIFIED, MATCH, claim)
