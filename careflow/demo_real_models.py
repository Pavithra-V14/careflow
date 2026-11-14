"""The three hosted providers wired into the DEMO Front Door and the translation draft (owner request 2026-11-14). DEMO PROFILE, SYNTHETIC DATA ONLY; every model output is a SUGGESTION and nothing is sent to a patient.

WHEN IT IS ON (all must hold, checked again here, so a missing gate means the FAKES stay, never a crash and never a guess):
  profile `demo`, `CAREFLOW_DATA_POLICY=synthetic_only`, `CAREFLOW_DEMO_REAL_MODELS` on, the demo Front Door components already built (careflow/demo_front_door.py), and the provider itself built by
  `careflow.provider_setup.build_providers` (not CI; its key; its recorded live-check date; its required daily ceiling and the per-run cap). Each stage upgrades on its own: a stage whose provider is missing keeps
  its fake and says so.

WHAT IT DOES, IN ORDER (the order is the safety design; CLAUDE.md rule 2):
  1. TEXT. The stored document is read through the normal extraction pipeline (careflow/extraction.py): a PDF with a text layer is read locally with no model; a scan or photo is transcribed by Gemini through the
     GATEWAY (the durable spend ledger, the per-role ceiling, the per-run cap). This transcription is the one step the rules allow BEFORE them (the rules need text to read, §5.2, §10.4); an empty or failed
     transcription is never treated as clear.
  2. RULES. The unchanged Front Door `screen` node runs the deterministic red-flag rules on that text. A flagged letter ends there with the nurse task: no other model is asked.
  3. IDENTITY. `LetterExtractor` (a regular expression, not a model) reads "PATIENT: <name>, DOB <date>" from the letter; every claim carries a quote that is verified to be in the letter.
  4. PROPOSAL. A clear letter reaches `DecisionRankedRetriever`: the fictional demo cards are the options and the DECISION model (Jev via OpenRouter, through the gateway) is asked which one fits the MASKED letter.
     Its answer only reorders the fictional card ids in a triage proposal that WAITS FOR A PERSON (the demo mode is shadow: nothing is applied, no training label is made from it). If the model cannot answer (no route, a spend
     ceiling, an error), the proposal keeps the fake order and a person still decides: an unknown answer is not a value.
  5. DRAFT. `suggest_translation` turns the invented, labelled DEMO DRAFT sentence into Tamil with Sarvam as a suggestion. Nothing is stored or sent. Sarvam sits behind the per-role daily ceiling (in memory) AND, when it is
     given the clinic, its spend is recorded in the durable ledger (role `translation`, migration 0086).

HONEST LIMITS. "Synthetic" is a declared label, not an inspection (docs/design/hosted-providers.md). Jev's downstream model is not pinned, and its output is never evidence (`careflow.adapters.decision.NOT_EVIDENCE_ENGINES`). A
document the demo seed wrote has NO stored file: with this switch on, running one ends in a visible failure (a dead-letter task), not in a made-up letter (only a canary's fabricated letter is still made up). The cards are
the fictional `demo-synthetic-card-A/B`; no clinical card exists.
"""

import hashlib
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final

from careflow import demo_phase3
from careflow.adapters.pdf import PypdfExtractor
from careflow.budget import BudgetExceeded, CallClass
from careflow.decision import (
    DecisionRecorder,
    DecisionService,
    InMemoryDecisionRecorder,
)
from careflow.decision_info import RULE_TOP_RANKED
from careflow.demo_front_door import DemoTexts
from careflow.extraction import ExtractionPipeline, PipelineTextSource
from careflow.front_door_runner import FrontDoorComponents
from careflow.gateway import AllRoutesFailed, NoApprovedRoute, SpendCeilingReached
from careflow.ports import (
    DataClass,
    DecisionRequest,
    PermanentError,
    TransientError,
    TranslationRequest,
)
from careflow.referral import ExtractedReferral
from careflow.retrieval import Candidate, Retrieval
from careflow.text_guard import mask_pii

SWITCH: Final = "CAREFLOW_DEMO_REAL_MODELS"
_TRUE: Final = {"1", "true", "yes", "on"}
DEMO_LABEL: Final = "DEMO ONLY - NOT A CLINICAL DECISION"
QUESTION: Final = "demo_card_choice"
# An invented, non-clinical sentence labelled as a demo draft. It says nothing medical and names no one; it exists so the translation stage has something patient-facing-shaped to translate. NOT SENT ANYWHERE.
DRAFT_EN: Final = "DEMO DRAFT, NOT SENT: we have received your referral and will contact you about the next step."
_DECISION_FAILURES: Final = (NoApprovedRoute, SpendCeilingReached, AllRoutesFailed, BudgetExceeded, TransientError, PermanentError)
_LETTER: Final = re.compile(r"PATIENT:\s*(?P<name>[A-Z][A-Z' -]{1,60}?)\s*,\s*(?P<dobq>DOB\s*(?P<dob>\d{4}-\d{2}-\d{2}))", re.IGNORECASE)
MEMO: Final = 64
_ATTEMPT_WORDS: Final = {
    "ok": "answered", "timeout": "timed out", "failed_transient": "provider error (retry-worthy)", "failed_permanent": "provider refused the request",
    "skipped_spend": "budget refused (a spend ceiling)", "skipped_breaker_open": "route paused after repeated failures", "skipped_no_adapter": "gate missing (no adapter built)",
    "skipped_not_applicable": "route not applicable to this input", "answer_rejected": "answer rejected",
}


def failure_reason(exc: BaseException) -> str:
    """A FIXED-WORDS reason for a stage that failed or was skipped: budget refused, timed out, provider error with its status, gate missing. Metadata only: never content, a URL or a key (the adapters' own messages are
    fixed words, proved by tests/test_providers.py)."""
    if isinstance(exc, NoApprovedRoute):
        return "gate missing: no approved route for this data"
    if isinstance(exc, SpendCeilingReached):
        return "budget refused: a spend ceiling was reached"
    if isinstance(exc, BudgetExceeded):
        return "budget refused: the per-run limit was reached"
    if isinstance(exc, AllRoutesFailed):
        parts = []
        for a in exc.attempts:
            runs: list[list[Any]] = []
            for outcome in a.history or (a.outcome,) * max(a.calls, 1):  # each call's outcome in order, run-length counted: "timed out x2, then provider error (retry-worthy)"
                if runs and runs[-1][0] == outcome:
                    runs[-1][1] += 1
                else:
                    runs.append([outcome, 1])
            if a.outcome == "answer_rejected" and a.detail:
                parts.append(a.detail)  # the provider ANSWERED and our validation rejected it: the detail already says so (and why); it is not a provider error
                continue
            words = [_ATTEMPT_WORDS.get(o, "failed") + (f" x{n}" if n > 1 else "") for o, n in runs]
            parts.append(", then ".join(words) + (f" [{a.detail}]" if a.detail else ""))
        return "; ".join(parts) or "no route was tried"
    if isinstance(exc, TransientError | PermanentError):
        return str(exc)[:120]
    return "unexpected error"


def switched_on(environ: Mapping[str, str]) -> bool:
    return (environ.get(SWITCH) or "").strip().lower() in _TRUE


def allowed(environ: Mapping[str, str], profile: str) -> bool:
    """demo + synthetic_only + the switch. Anything else: the fakes stay."""
    return demo_phase3.demo_allowed(profile, demo_phase3.data_policy(environ)) and switched_on(environ)


def _digest(masked: str) -> str:
    return hashlib.sha256(masked.encode("utf-8")).hexdigest()


class ClinicMemo:
    """Which clinic a letter belongs to, keyed by the digest of its MASKED text (the retriever is handed only the masked text, not the clinic). In this process only, bounded, never holds text."""

    def __init__(self) -> None:
        self._rows: OrderedDict[str, uuid.UUID] = OrderedDict()

    def remember(self, letter_text: str, clinic_id: uuid.UUID) -> None:
        self._rows[_digest(mask_pii(letter_text).text)] = clinic_id
        while len(self._rows) > MEMO:
            self._rows.popitem(last=False)

    def clinic_for(self, masked_text: str) -> uuid.UUID | None:
        return self._rows.get(_digest(masked_text))


class LetterExtractor:
    """Reads "PATIENT: <name>, DOB <yyyy-mm-dd>" from the letter's text, any letter case (a transcription of a capital-letter scan comes back in capitals). A regular expression, NOT a model. Each quote is a slice of
    the letter itself, so the grounding check passes only on what is really there. It also remembers the letter's clinic for the decision stage. Nothing found: nothing claimed (the pipeline routes the letter to a person)."""

    def __init__(self, memo: ClinicMemo | None = None) -> None:
        self._memo = memo

    async def extract(self, letter_text: str, clearance, *, clinic_id: uuid.UUID) -> ExtractedReferral:
        if self._memo is not None:
            self._memo.remember(letter_text, clinic_id)
        match = _LETTER.search(letter_text)
        if match is None:
            return ExtractedReferral()
        name = " ".join(match.group("name").split()).title()
        return ExtractedReferral(
            name=name, name_quote=letter_text[match.start() : match.end("name")], dob=match.group("dob"), dob_quote=match.group("dobq"),
        )


@dataclass
class DecisionTrace:
    """What the decision stage did, as labels and numbers only (never text): for the demo script's printout and for tests."""

    outcome: str = "not_asked"  # not_asked | suggested | unavailable | unknown_clinic
    chosen: str | None = None
    confidence: float | None = None
    engine: str | None = None
    seconds: float | None = None
    reason: str | None = None  # a fixed-words reason when the stage did not answer
    probabilities: dict[str, float] | None = None  # each option's probability as the model gave it (option ids are the fictional card ids): metadata
    model_id: str | None = None


class DecisionRankedRetriever:
    """Wraps the fake retriever. Its candidates are the fictional cards; the decision model is asked which fits the MASKED letter and the answer MOVES that card first. It never adds, removes or invents a card, and
    it never approves anything: the proposal that follows waits for a person. Any failure keeps the inner order."""

    def __init__(self, inner: Any, decisions: DecisionService, memo: ClinicMemo) -> None:
        self._inner, self._decisions, self._memo = inner, decisions, memo
        self.last = DecisionTrace()

    async def retrieve(self, query: str, *, scope: dict[str, str]) -> Retrieval:
        found = await self._inner.retrieve(query, scope=scope)
        self.last = DecisionTrace()
        if found.abstained or len(found.candidates) < 2:
            return found
        clinic_id = self._memo.clinic_for(query)
        if clinic_id is None:
            self.last = DecisionTrace("unknown_clinic", reason="the clinic of the letter is not known, so no call was made")  # the spend cannot be attributed: no call is made
            return found
        options = tuple(c.card_id for c in found.candidates)
        started = time.monotonic()
        try:
            result = await self._decisions.decide(DecisionRequest(QUESTION, query, options, DataClass.SYNTHETIC), clinic_id=clinic_id, call_class=CallClass.ORDINARY)
        except _DECISION_FAILURES as exc:
            self.last = DecisionTrace("unavailable", seconds=time.monotonic() - started, reason=failure_reason(exc))
            return found
        seconds = time.monotonic() - started
        choice = result.value.choice
        if choice not in options:
            self.last = DecisionTrace("unavailable", seconds=seconds, reason="the answer was not one of the options")  # an answer that is not one of the options is not an answer
            return found
        first = next(c for c in found.candidates if c.card_id == choice)
        ordered = [first, *(c for c in found.candidates if c is not first)]
        probs = result.value.probabilities
        stamped: list[Candidate] = []
        for rank, c in enumerate(ordered):  # the decision's fields ride on the candidates (closed fields only, careflow.decision_info): each one's probability; on the top one the engine, model, confidence and rule
            extra: dict[str, object] = {"probability": probs[c.card_id]}
            if rank == 0:
                extra |= {"engine": result.value.engine, "model_id": result.model_id, "confidence": result.value.confidence, "rule": RULE_TOP_RANKED}
            stamped.append(replace(c, extra=extra))
        self.last = DecisionTrace("suggested", choice, result.value.confidence, result.value.engine, seconds, probabilities=dict(probs), model_id=result.model_id)
        return replace(found, candidates=tuple(stamped))



class VisionProbe:
    """Wraps the vision port and remembers what happened, as METADATA: how long each read took, how many came back and with how many characters, and a fixed-words reason when one failed. It re-raises, so the
    pipeline behaves exactly as before; it is how the demo script can say WHY a page was not read (timed out, budget refused, provider error status, gate missing) instead of only that extraction was unavailable."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0
        self.seconds = 0.0
        self.answered_chars: int | None = None
        self.reason: str | None = None

    async def read(self, request: Any) -> Any:
        self.calls += 1
        started = time.monotonic()
        try:
            result = await self._inner.read(request)
        except BaseException as exc:
            self.reason = failure_reason(exc)
            raise
        else:
            self.answered_chars = len(result.text)
            return result
        finally:
            self.seconds += time.monotonic() - started


class RealTexts:
    """The text source when the stored document is read for real: the extraction pipeline (local PDF text layer, Gemini vision for a scan) behind the gateway. A canary's fabricated letter is still made up, exactly
    as before (the canary has no file); every other document is read from its stored bytes, or fails visibly."""

    def __init__(self, source: PipelineTextSource) -> None:
        self._source, self._fallback = source, DemoTexts()
        self.first_read_seconds: float | None = None  # the first (and only billed) read of a document: the source memoises the rest

    async def text_for(self, document_id: uuid.UUID) -> str:
        return await self._timed(document_id)

    async def _timed(self, document_id: uuid.UUID) -> str:
        started = time.monotonic()
        try:
            return await self._source.text_for(document_id)
        finally:
            if self.first_read_seconds is None:
                self.first_read_seconds = time.monotonic() - started

    async def text_for_origin(self, document_id: uuid.UUID, origin: str) -> str:
        if origin == "canary":
            return await self._fallback.text_for_origin(document_id, origin)
        return await self._timed(document_id)


@dataclass
class Upgrade:
    """What was upgraded, by stage, in fixed words: `real` or the reason it stayed fake."""

    components: FrontDoorComponents
    text: str = "fake: not asked"
    decision: str = "fake: not asked"
    notes: list[str] = field(default_factory=list)
    decision_recorder: DecisionRecorder | None = None
    ranker: DecisionRankedRetriever | None = None
    vision_probe: VisionProbe | None = None


def has_role(gateway: Any, role: str) -> bool:
    return gateway is not None and any(r.role == role and r.enabled for r in gateway.registry)


def upgrade_components(
    components: FrontDoorComponents, providers: Any, *, loader: Callable[[uuid.UUID], Awaitable[tuple[bytes, str, uuid.UUID]]] | None, environ: Mapping[str, str], profile: str,
    recorder: DecisionRecorder | None = None,
) -> Upgrade:
    """The demo components with every stage whose provider passes its gates swapped for the real one. Returns the SAME components when nothing is allowed. Never raises for a missing gate."""
    out = Upgrade(components)
    if not allowed(environ, profile):
        out.text = out.decision = "fake: CAREFLOW_DEMO_REAL_MODELS is off or the deployment is not demo + synthetic_only"
        return out
    memo = ClinicMemo()
    upgraded = components
    if providers is not None and providers.vision is not None and loader is not None:
        out.vision_probe = VisionProbe(providers.vision)
        source = PipelineTextSource(ExtractionPipeline(PypdfExtractor(), vision=out.vision_probe), loader)
        upgraded = replace(upgraded, texts=RealTexts(source), extractor=LetterExtractor(memo))
        out.text = "real: gemini vision through the gateway"
    else:
        why = (providers.unavailable.get("gemini") if providers is not None else None) or ("no way to read the stored document" if loader is None else "gemini is not built")
        out.text = f"fake: {why}"
        upgraded = replace(upgraded, extractor=LetterExtractor(memo))  # (the same reader: it also knows the fake letter's shape, and remembers the clinic)
    if providers is not None and has_role(providers.gateway, "decision"):
        out.decision_recorder = recorder or InMemoryDecisionRecorder()
        out.ranker = DecisionRankedRetriever(upgraded.retriever, DecisionService(providers.gateway, out.decision_recorder), memo)
        upgraded = replace(upgraded, retriever=out.ranker)
        out.decision = "real: jev via openrouter through the gateway"
    else:
        out.decision = f"fake: {(providers.unavailable.get('jev') if providers is not None else None) or 'jev is not built'}"
    out.components = upgraded
    return out


# ---- the translation draft -----------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TranslationSuggestion:
    status: str  # suggested | unavailable | refused | failed
    target: str
    characters: int = 0
    text: str | None = field(default=None, repr=False)
    reason: str | None = None  # fixed words when it was not suggested
    label: str = DEMO_LABEL  # a SUGGESTION: nothing is stored or sent to a patient


async def suggest_translation(translation: Any, *, text: str = DRAFT_EN, target: str = "ta", clinic_id: uuid.UUID | None = None) -> TranslationSuggestion:
    """Translate a draft with Sarvam as a SUGGESTION. `translation` is the built adapter or None. A missing adapter, a spend ceiling or a failure is a named status, never a crash and never invented text."""
    if translation is None:
        return TranslationSuggestion("unavailable", target, reason="gate missing: the translation provider was not built")
    try:
        answer = await translation.translate(TranslationRequest(text, "en", target, DataClass.SYNTHETIC, clinic_id))
    except SpendCeilingReached as exc:
        return TranslationSuggestion("refused", target, reason=failure_reason(exc))
    except (TransientError, PermanentError) as exc:
        return TranslationSuggestion("failed", target, reason=failure_reason(exc))
    return TranslationSuggestion("suggested", target, len(text), answer.value.text)


def stored_document_loader(engine: Any, worker_account_id: uuid.UUID, intake: Any, *, session_for: Callable[..., Any] | None = None) -> Callable[[uuid.UUID], Awaitable[tuple[bytes, str, uuid.UUID]]]:
    """document id -> (verified bytes, content type, clinic id), read AS THE WORKER account through `DocumentIntake.read_verified` (the bytes are re-hashed and, where a scanner is wired, scanned again). RLS decides
    what the worker can see."""
    from careflow.front_door_runner import worker_session

    make = session_for or worker_session

    async def load(document_id: uuid.UUID) -> tuple[bytes, str, uuid.UUID]:
        async with make(engine, worker_account_id)() as conn:
            document = await intake.read_verified(conn, document_id)
        return document.content, document.content_type, document.clinic_id

    return load
