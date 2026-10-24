"""Referral letter to patient identity (BUILD-SPEC.md §5.2, §11.2, §17.2; CLAUDE.md rules 2, 3, 6, 7). Package P2-7.

THE ORDER IS THE POINT. For a referral letter, on every path and with no exception:

  1. the document must exist, be a referral and be visible to the caller (a database read, no model);
  2. the deterministic red-flag rules screen the letter text (`RedFlagEngine.evaluate_text`): rules, no model, no network;
  3. ONLY IF the rules returned `clear` is the extractor (a model) asked anything. `ReferralExtractor.extract` takes a `Clearance`, which only
     `screen_referral` can mint, so there is no call site that reaches a model without having been through the rules; and an extractor that
     is handed no clearance is a TypeError in the tests;
  4. every identity fact the extractor returns must come with a quote that really occurs in the letter, and the value is then checked against that
     quote (tier 1 grounding, careflow/grounding.py). LIMIT, stated: that the quoted name belongs to the PATIENT (not the referrer) is not verified;
  5. only verified facts reach `PatientIngestionPipeline.ingest` (the existing mpi module: it canonicalises, tokenises, matches and queues
     near-matches for human review; this module adds nothing to identity resolution).

A model's opinion can never undo what the rules found: a `red_flag` escalates and STOPS (no model call on that letter); `unassessable` (no usable
rules, a rule that failed to compile) is NOT clear and also STOPS. If the rules cannot run at all (a database error) the error propagates and the
extractor is never called. Unknown is not clear (rule 3).

Failure to extract is not a failure to be safe: no approved route, a spend ceiling or a model outage returns `extraction_unavailable` for the
front desk to key the letter by hand (§2, clinical profile). A fact that is missing or unverified is never guessed: the letter goes to the front
desk (`needs_front_desk`) with the reasons. The phone number is optional: an unverified phone is dropped (matching on name and date of birth
alone is weaker, never wrong) and the drop is reported. Name and date of birth are required.

What this module does NOT do: it does not read a file (extracting text from the stored document is P2-4; the text is an argument), it does not
store the letter text or any extracted value, and it never logs. Letter text and extracted values are patient data: `repr=False` everywhere, never in
an exception message. The idempotency key of the identity write is derived from the document id, so reprocessing a letter cannot create a
second patient.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.gateway import AllRoutesFailed, NoApprovedRoute, SpendCeilingReached
from careflow.grounding import StructuredKind, canonical_date, verify_structured, words
from careflow.mpi import IngestionError, IngestResult
from careflow.ports import PermanentError, TransientError
from careflow.rules import Evaluation, Origin, Profile, RedFlagEngine

Status = Literal["red_flag_escalated", "held_unassessable", "extraction_unavailable", "needs_front_desk", "identity_resolved"]


class ReferralError(Exception):
    """Fixed words only; never a name, date, phone or any letter text."""


class ReferralDocumentNotFound(ReferralError):
    """No referral document with this id is visible to the caller (missing, another clinic's, or not a referral): one answer for all three."""


_MINT: Final = object()


@dataclass(frozen=True)
class Clearance:
    """Proof that the red-flag rules ran on this letter and returned `clear`. Only `screen_referral` mints one. It carries no patient text."""

    document_id: uuid.UUID
    rule_set_hash: str
    profile: str
    text_digest: str  # sha256 of the screened text: the clearance is for THIS text, not for whatever is passed to the model later
    _mint: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._mint is not _MINT:
            raise ReferralError("a clearance can only come from screen_referral")


@dataclass(frozen=True)
class ExtractedReferral:
    """What the extractor claims the letter says. Every field is a CLAIM until grounded against its quote. Absent = None."""

    name: str | None = field(repr=False, default=None)
    name_quote: str | None = field(repr=False, default=None)
    dob: str | None = field(repr=False, default=None)
    dob_quote: str | None = field(repr=False, default=None)
    phone: str | None = field(repr=False, default=None)
    phone_quote: str | None = field(repr=False, default=None)


class ReferralExtractor(Protocol):
    """The model-backed extractor (through the gateway when it exists). It is handed a Clearance: no clearance, no call."""

    async def extract(self, letter_text: str, clearance: Clearance, *, clinic_id: uuid.UUID) -> ExtractedReferral: ...


@dataclass(frozen=True)
class ReferralResult:
    status: Status
    document_id: uuid.UUID
    reasons: tuple[str, ...] = ()  # fixed words
    task_id: uuid.UUID | None = None  # the nurse task, for red_flag_escalated
    identity: IngestResult | None = None


@dataclass(frozen=True)
class Screened:
    """The outcome of the rules step. Exactly one of `clearance` and `result` is set."""

    evaluation: Evaluation
    clearance: Clearance | None = None
    result: ReferralResult | None = None


class _Ingestor(Protocol):
    async def ingest(
        self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: Origin, name: str, dob: str, phone: str | None, idempotency_key: str
    ) -> IngestResult: ...


def text_digest(letter_text: str) -> str:
    return hashlib.sha256(letter_text.encode("utf-8")).hexdigest()


async def _load_referral(conn: AsyncConnection, document_id: uuid.UUID):
    row = (await conn.execute(text("select id, clinic_id, origin, kind from docs.documents where id = :i"), {"i": document_id})).first()
    if row is None or row.kind != "referral":
        raise ReferralDocumentNotFound("no referral document with this id")
    return row


async def screen_referral(
    conn: AsyncConnection, engine: RedFlagEngine, document_id: uuid.UUID, letter_text: str, *, profile: Profile | None = None
) -> Screened:
    """Steps 1 and 2. No model is consulted here or before here. A red flag is escalated (nurse task) and returned as a final result; an
    unassessable screening is returned as a final result; only `clear` mints a Clearance. A database or escalation error propagates, and so
    nothing downstream runs."""
    if not isinstance(letter_text, str) or not letter_text.strip():
        raise ReferralError("the letter text is empty")
    row = await _load_referral(conn, document_id)
    evaluation = await engine.evaluate_text(conn, row.clinic_id, row.origin, letter_text, profile=profile)
    if evaluation.outcome == "red_flag":
        escalation = await engine.escalate_red_flag(conn, row.clinic_id, row.origin, evaluation, f"document:{document_id}")
        return Screened(evaluation, result=ReferralResult("red_flag_escalated", document_id, task_id=escalation.task_id))
    if evaluation.outcome == "clear" and evaluation.profile == "demo" and row.origin == "live":
        # Same guard as RedFlagEngine.record_screening: a demo-profile (drafts allowed) clear on live data vouches for nothing.
        return Screened(evaluation, result=ReferralResult("held_unassessable", document_id, reasons=("demo_profile_on_live_origin",)))
    if evaluation.outcome != "clear":
        # unassessable: not clear. There is NO nurse-task path for a non-message source yet (open item, docs/design/deferred-items.md), so the
        # result itself is the signal and the caller must not drop it.
        return Screened(evaluation, result=ReferralResult("held_unassessable", document_id, reasons=("screening_unassessable",)))
    return Screened(evaluation, clearance=Clearance(document_id, evaluation.rule_set_hash, evaluation.profile, text_digest(letter_text), _MINT))


async def process_referral(
    conn: AsyncConnection, engine: RedFlagEngine, extractor: ReferralExtractor, ingestor: _Ingestor, document_id: uuid.UUID, letter_text: str,
    *, profile: Profile | None = None,
) -> ReferralResult:
    screened = await screen_referral(conn, engine, document_id, letter_text, profile=profile)
    if screened.clearance is None:
        assert screened.result is not None
        return screened.result
    row = await _load_referral(conn, document_id)
    if screened.clearance.text_digest != text_digest(letter_text):
        raise ReferralError("the clearance is for different text")
    try:
        extracted = await extractor.extract(letter_text, screened.clearance, clinic_id=row.clinic_id)
    except (NoApprovedRoute, SpendCeilingReached, AllRoutesFailed, TransientError, PermanentError):
        return ReferralResult("extraction_unavailable", document_id, reasons=("extraction_unavailable",))

    reasons: list[str] = []
    letter_words = words(letter_text)
    name = _grounded("name", StructuredKind.NAME, extracted.name, extracted.name_quote, reasons, letter_words)
    dob_value = _grounded("dob", StructuredKind.DATE, extracted.dob, extracted.dob_quote, reasons, letter_words)
    dob = canonical_date(dob_value) if dob_value is not None else None
    if dob_value is not None and dob is None:
        reasons.append("dob_unreadable")
    phone = _grounded("phone", StructuredKind.PHONE, extracted.phone, extracted.phone_quote, reasons, letter_words, optional=True)
    if name is None or dob is None:
        return ReferralResult("needs_front_desk", document_id, reasons=tuple(reasons))
    try:
        identity = await ingestor.ingest(
            conn, clinic_id=row.clinic_id, origin=row.origin, name=name, dob=dob.isoformat(), phone=phone, idempotency_key=f"referral:{document_id}"
        )
    except IngestionError:
        return ReferralResult("needs_front_desk", document_id, reasons=(*reasons, "identity_refused"))
    return ReferralResult("identity_resolved", document_id, reasons=tuple(reasons), identity=identity)


def _grounded(
    field_name: str, kind: StructuredKind, value: str | None, quote: str | None, reasons: list[str], letter_words: tuple[str, ...],
    *, optional: bool = False
) -> str | None:
    """The value if it is verified against its quote; otherwise None and a reason. Never returns an unverified value."""
    if value is None or quote is None:
        if not optional or value is not None:
            reasons.append(f"{field_name}_missing")
        return None
    # The quote must be the letter's own words, or a model that invents a name AND a matching quote would verify itself.
    quote_words = words(quote)
    n = len(quote_words)
    if n == 0 or not any(letter_words[i : i + n] == quote_words for i in range(len(letter_words) - n + 1)):
        reasons.append(f"{field_name}_quote_not_in_letter" if not optional else f"{field_name}_dropped_quote_not_in_letter")
        return None
    fact = verify_structured(field_name, kind, value, quote)
    if not fact.usable_by_rules:
        reasons.append(f"{field_name}_{fact.reason}" if not optional else f"{field_name}_dropped_{fact.reason}")
        return None
    return value
