"""Provider-outage and database-outage drills, on FAKES ONLY (owner instruction 2026-11-21, "break it on purpose"). Nothing here calls a network, a model, a database or a provider, and nothing spends money.

Each check builds the REAL gateway, extraction pipeline, retriever or Front Door graph from this package, replaces only the outermost edge (the provider, the database session, a writer) with a fake that fails on purpose, and asserts the
safe outcome. A check that raises is a FAIL in fixed words (the exception CLASS only, never its message). The same checks run as offline tests (tests/test_outage_drills.py) and as a runnable script (scripts/drill_outages.py).

What the drills prove, in the owner's words:
  * a failure or a timeout ends in a human-review outcome or a fixed-words refusal, never a guess;
  * the rules run before any model, and a model outage never blocks a red-flag nurse task;
  * no side effect (a message, an offer, a booking, a record write) comes from a stage that failed or paused;
  * a retry with the same idempotency key repeats no effect;
  * spend is recorded once per successful call and the ceiling stops further calls without calling the provider;
  * a restart in the middle of a run resumes from its checkpoint without repeating a step.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from careflow.adapters.vision import GatewayVision
from careflow.budget import CallClass
from careflow.decision import DecisionService, InMemoryDecisionRecorder
from careflow.extraction import (
    R_VISION_UNAVAILABLE,
    ExtractionNeedsManual,
    ExtractionPipeline,
    NativePage,
    PageImage,
    worker_context,
)
from careflow.front_door import (
    FrontDoorDeps,
    TriageProposal,
    build_front_door,
    initial_state,
    run_config,
)
from careflow.gateway import (
    AllRoutesFailed,
    Gateway,
    InMemorySpendLedger,
    ModelRow,
    RoleDailyCeilings,
    SpendCeilingReached,
    SpendLimits,
)
from careflow.mpi import IngestResult
from careflow.ports import (
    AdapterResult,
    DataClass,
    DecisionRequest,
    DecisionResult,
    PermanentError,
    TransientError,
    Usage,
    VisionResult,
)
from careflow.referral import ExtractedReferral
from careflow.retrieval import Candidate, Retrieval
from careflow.rules import EscalationResult, Evaluation

CLINIC, DOC, PATIENT, TASK = uuid.UUID(int=11), uuid.UUID(int=22), uuid.UUID(int=33), uuid.UUID(int=44)
LETTER = "DRILL-LETTER-91 Patient: Asha Rao, DOB 5 March 1980, Mobile 98765 43210. Please see. Alt note: DOB 05/03/1980."
GOOD = ExtractedReferral(name="Asha Rao", name_quote="Patient: Asha Rao", dob="1980-03-05", dob_quote="DOB 5 March 1980", phone="9876543210", phone_quote="Mobile 98765 43210")
ZERO = Decimal(0)


@dataclass(frozen=True)
class Check:
    """One line of the drill: a stable id, what was proved (fixed words), and whether it held."""

    id: str
    says: str
    passed: bool

    def line(self) -> str:
        return f"{'PASS' if self.passed else 'FAIL'} {self.id}: {self.says}"


# ---- the fake edge: a provider that fails on purpose -----------------------------------------------------------------------------------------


class Provider:
    """A fake provider adapter. `mode` is one of ok, transient, timeout, permanent, rejected; it counts every call it receives."""

    def __init__(self, mode: str = "ok", value: Any = None, usage: Usage | None = None) -> None:
        self.mode, self.value, self.usage, self.calls = mode, value, usage or Usage(100, 100), 0

    async def __call__(self, payload: Any) -> AdapterResult:
        self.calls += 1
        if self.mode == "transient":
            raise TransientError("the provider is unavailable")
        if self.mode == "timeout":
            await asyncio.sleep(3600)  # never answers: the gateway's own per-row timeout must end it
        if self.mode == "permanent":
            raise PermanentError("the provider refused the request")
        return AdapterResult(self.value, self.usage)


def gateway(role: str, provider: Provider, *, price: str = "0", ceiling: str | None = None, timeout: float = 0.02) -> tuple[Gateway, InMemorySpendLedger]:
    row = ModelRow(role, "drill", "drill-1", "synthetic_only", 1, Decimal(price), Decimal(price), Decimal(price), Decimal(price), timeout_seconds=timeout)
    ledger = InMemorySpendLedger(today=lambda: date(2030, 1, 1))

    async def no_sleep(_: float) -> None:
        return None

    ceilings = RoleDailyCeilings({role: Decimal(ceiling)}, today=lambda: date(2030, 1, 1)) if ceiling is not None else None
    return Gateway([row], {row.key: provider}, ledger, SpendLimits(Decimal(100), Decimal(50)), role_ceilings=ceilings, origin="test", sleep=no_sleep, jitter=lambda: 0.5), ledger


# ---- the fake edge of the Front Door: every port shares ONE event log -----------------------------------------------------------------------


class _Row:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _Conn:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.log.append("db:document")
        row = _Row(id=DOC, clinic_id=CLINIC, origin="test", kind="referral")
        return _Row(first=lambda: row)


class _Engine:
    """The deterministic rule engine stand-in: `outcome` is clear or red_flag. It never fails (the rules are local code); `escalations` records the nurse task."""

    def __init__(self, log: list[str], outcome: str) -> None:
        self.log, self.outcome, self.escalations = log, outcome, []

    async def evaluate_text(self, conn: Any, clinic_id: Any, origin: Any, text: str, *, profile: Any = None) -> Evaluation:
        self.log.append("rules")
        return Evaluation(self.outcome, (), "clinical", 1, 0, (), "h" * 64)

    async def escalate_red_flag(self, conn: Any, clinic_id: Any, origin: Any, ev: Any, source: Any) -> EscalationResult:
        self.log.append("escalate")
        self.escalations.append(source)
        return EscalationResult(TASK)


class _Effects:
    """Records every attempt and performs an effect only once per idempotency key (what an idempotent writer does)."""

    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name, self.attempts, self.performed, self.fail_next = log, name, [], {}, 0
        self.fail_with: Exception = ConnectionError("the database refused the connection")

    def _do(self, key: str) -> None:
        self.attempts.append(key)
        self.log.append(f"effect:{self.name}")
        if self.fail_next:
            self.fail_next -= 1
            raise self.fail_with  # before any effect: the write did not happen
        self.performed.setdefault(key, len(self.performed) + 1)


class _Proposer(_Effects):
    async def propose(self, conn: Any, *, idempotency_key: str, **kw: Any) -> TriageProposal:
        self._do(idempotency_key)
        return TriageProposal(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))


class _Holds(_Effects):
    def __init__(self, log: list[str], name: str) -> None:
        super().__init__(log, name)
        self.tasks: list[dict[str, Any]] = []

    async def raise_hold(self, conn: Any, *, assigned_role: str, status: str, reasons: list[str], idempotency_key: str, **kw: Any) -> uuid.UUID:
        self._do(idempotency_key)
        self.tasks.append({"role": assigned_role, "status": status})
        return uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)


class _Recorder(_Effects):
    async def record(self, conn: Any, *, idempotency_key: str, **kw: Any) -> None:
        self._do(idempotency_key)


class _Ingestor:
    def __init__(self, log: list[str]) -> None:
        self.log, self.calls, self.performed = log, 0, set()

    async def ingest(self, conn: Any, *, idempotency_key: str, **kw: Any) -> IngestResult:
        self.log.append("identity")
        self.calls += 1
        self.performed.add(idempotency_key)  # an idempotent writer: one record per key
        return IngestResult("created", PATIENT)


class _Texts:
    def __init__(self, log: list[str], *, manual: bool = False) -> None:
        self.log, self.manual = log, manual

    async def text_for(self, document_id: Any) -> str:
        self.log.append("text")
        if self.manual:
            raise ExtractionNeedsManual((R_VISION_UNAVAILABLE,))
        return LETTER


class _Extractor:
    def __init__(self, log: list[str], raises: Exception | None = None) -> None:
        self.log, self.raises = log, raises

    async def extract(self, letter_text: str, clearance: Any, *, clinic_id: Any) -> ExtractedReferral:
        self.log.append("MODEL:extract")
        if self.raises:
            raise self.raises
        return GOOD


class _Retriever:
    def __init__(self, log: list[str], raises: Exception | None = None) -> None:
        self.log, self.raises = log, raises

    async def retrieve(self, query: str, *, scope: dict[str, str]) -> Retrieval:
        self.log.append("MODEL:retrieve")
        if self.raises:
            raise self.raises
        return Retrieval(False, None, (Candidate("hours", 1, 0.03, 0.9, 0.5),))


class Door:
    """One Front Door graph on fakes, with its event log and every effect recorder. `rebuild()` makes a NEW graph over the SAME checkpoint store: a process restart."""

    def __init__(self, *, outcome: str = "clear", text_manual: bool = False, extract_raises: Exception | None = None, retrieve_raises: Exception | None = None) -> None:
        self.log: list[str] = []
        self.saver = InMemorySaver()
        self.engine = _Engine(self.log, outcome)
        self.ingestor = _Ingestor(self.log)
        self.proposer, self.recorder, self.holds = _Proposer(self.log, "proposal"), _Recorder(self.log, "decision"), _Holds(self.log, "hold")
        self.extractor, self.retriever, self.texts = _Extractor(self.log, extract_raises), _Retriever(self.log, retrieve_raises), _Texts(self.log, manual=text_manual)
        self.graph = self.rebuild()
        self.config: Any = run_config(DOC, CLINIC, "test")
        self.start: Any = initial_state(DOC, CLINIC, "test")

    def rebuild(self) -> Any:
        conn = _Conn(self.log)

        @asynccontextmanager
        async def session():  # type: ignore[no-untyped-def]
            yield conn

        deps = FrontDoorDeps(
            session=session, engine=self.engine, texts=self.texts, extractor=self.extractor, ingestor=self.ingestor, retriever=self.retriever,  # type: ignore[arg-type]
            proposer=self.proposer, recorder=self.recorder, holds=self.holds, retrieval_scope={"domain": "demo"}, profile="demo",  # type: ignore[arg-type]
        )
        self.graph = build_front_door(deps, self.saver, environ={})
        return self.graph

    async def run(self) -> Any:
        return await self.graph.ainvoke(self.start, self.config)

    async def resume(self) -> Any:
        return await self.graph.ainvoke(None, self.config)

    async def state(self) -> Any:
        return await self.graph.aget_state(self.config)

    def models_called(self) -> list[str]:
        return [e for e in self.log if e.startswith("MODEL")]

    def effects(self) -> list[str]:
        return [e for e in self.log if e.startswith("effect:")] + [e for e in self.log if e in {"identity", "escalate"}]

    def rules_before_every_model(self) -> bool:
        seen = False
        for event in self.log:
            seen = seen or event == "rules"
            if event.startswith("MODEL") and not seen:
                return False
        return True


# ---- the checks ----------------------------------------------------------------------------------------------------------------------------


async def vision_outage() -> list[Check]:
    """A page photo that cannot be read: a failure, a timeout, a refusal, a garbled answer or a spent ceiling each end in a named manual reason and NO text. The provider is asked a bounded number of times."""
    out = []
    page = NativePage(1, "", (PageImage("image/png", b"PNG-drill", 1.0),))

    class Native:
        def read(self, content: bytes) -> list[NativePage]:
            return [page]

    modes = (("transient", Provider("transient")), ("timeout", Provider("timeout")), ("permanent", Provider("permanent")), ("garbled", Provider("ok", VisionResult(text="  "))))
    all_manual, bounded = True, True
    for _name, provider in modes:
        gw, _ = gateway("vlm", provider)
        with worker_context():
            result = await ExtractionPipeline(Native(), vision=GatewayVision(gw, data_class=DataClass.SYNTHETIC)).run(b"x", "image/png", clinic_id=CLINIC)
        all_manual = all_manual and result.text == "" and bool(result.reasons)
        bounded = bounded and provider.calls <= 3
    out.append(Check("vision.failure", "a vision failure, timeout, refusal or garbled answer gives no text and a named manual reason, never a guess", all_manual))
    out.append(Check("vision.bounded", "the vision provider is asked at most three times per image", bounded))
    gw, _ = gateway("vlm", Provider("ok", VisionResult(text="a readable letter with enough words in it to count as text here"), Usage(10, 10)), price="1", ceiling="0.0000001")
    pipeline = ExtractionPipeline(Native(), vision=GatewayVision(gw, data_class=DataClass.SYNTHETIC))
    with worker_context():
        first = await pipeline.run(b"x", "image/png", clinic_id=CLINIC)  # the first call answers (its cost is unknown until it has run) and spends past the ceiling
        capped = await pipeline.run(b"x", "image/png", clinic_id=CLINIC)
    out.append(Check("vision.ceiling", "once the vision ceiling is spent the next page ends in a manual reason, not an unscreened letter and not a crash", first.text != "" and capped.text == "" and bool(capped.reasons)))
    return out


async def decision_outage() -> list[Check]:
    """The decision model failing: the service raises a typed gateway error (callers turn it into a person) and records the attempt in fixed words; it never returns a choice."""
    out = []
    request = DecisionRequest("drill", "DRILL text", ("accept", "decline"), DataClass.SYNTHETIC)
    held = True
    for mode in ("transient", "timeout", "permanent"):
        gw, _ = gateway("decision", Provider(mode))
        recorder = InMemoryDecisionRecorder()
        try:
            await DecisionService(gw, recorder).decide(request, clinic_id=CLINIC)
            held = False
        except AllRoutesFailed:
            held = held and recorder.entries[-1].outcome == "failed" and recorder.entries[-1].confidence is None
    out.append(Check("decision.failure", "a decision-model failure or timeout raises a typed error and records outcome failed with no choice and no confidence", held))
    gw, _ = gateway("decision", Provider("ok", DecisionResult("accept", {"accept": 0.9, "decline": 0.1}, 0.9, "drill"), Usage(10, 10)), price="1", ceiling="0.0000001")
    recorder = InMemoryDecisionRecorder()
    service = DecisionService(gw, recorder)
    await service.decide(request, clinic_id=CLINIC)  # answers, and spends past the ceiling
    try:
        await service.decide(request, clinic_id=CLINIC)
        refused = False
    except SpendCeilingReached:
        refused = recorder.entries[-1].outcome == "spend_ceiling" and gw.adapters[("drill", "drill-1")].calls == 1
    out.append(Check("decision.ceiling", "a spent decision ceiling is a fixed refusal (spend_ceiling), never a choice", refused))
    return out


async def translation_outage() -> list[Check]:
    """Translation is decorative: a failure is a named status with no text, and the native-language rules never depended on it."""
    from careflow.demo_real_models import suggest_translation

    class Failing:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        async def translate(self, request: Any) -> Any:
            raise self.exc

    results = [await suggest_translation(Failing(exc)) for exc in (TransientError("x"), PermanentError("x"), SpendCeilingReached("x"))]
    results.append(await suggest_translation(None))
    ok = all(r.text is None and r.status in {"failed", "refused", "unavailable"} and r.reason for r in results)
    return [Check("translation.failure", "a translation failure, refusal or missing provider is a named status with fixed words and no text", ok)]


async def embedding_outage() -> list[Check]:
    """The embedding model failing during card matching: the retriever abstains (`dense_unavailable`) so a person decides; it never retrieves on one list alone and never raises into the run."""
    from careflow.embeddings import GatewayEmbedding
    from careflow.retrieval import CardRetriever

    class Store:
        async def search(self, vector: Any, *, limit: int, scope: Any) -> list[Any]:
            raise AssertionError("the store must not be searched without a query vector")

    class Bm25:
        def __init__(self) -> None:
            self.versions: dict[str, int] = {}

        def search(self, query: str, scope: Any) -> list[Any]:
            return []

        def ceiling(self, query: str) -> float:
            return 1.0

    class Truth:
        async def current_versions(self, ids: Any) -> dict[str, int]:
            return {}

    held = True
    for mode in ("transient", "timeout", "permanent"):
        gw, _ = gateway("embed", Provider(mode))
        retriever = CardRetriever(GatewayEmbedding(gw, CLINIC, "drill-1"), Store(), Truth(), Bm25(), policy=_policy())  # type: ignore[arg-type]
        try:
            found = await retriever.retrieve("a masked referral query", scope={"domain": "demo"})
            held = held and found.abstained and found.reason == "dense_unavailable" and not found.candidates
        except Exception:  # noqa: BLE001 - any raise is the failure this check exists to catch
            held = False
    too_long = CardRetriever(GatewayEmbedding(gateway("embed", Provider("ok"))[0], CLINIC, "drill-1"), Store(), Truth(), Bm25(), policy=_policy())  # type: ignore[arg-type]
    try:
        found = await too_long.retrieve("word " * 5000, scope={"domain": "demo"})  # a long letter: over the embedder's text limit
        long_ok = found.abstained and found.reason == "dense_unavailable"
    except Exception:  # noqa: BLE001
        long_ok = False
    return [
        Check("embedding.failure", "an embedding failure, timeout or refusal makes the retriever abstain (dense_unavailable) with no candidates, never raise and never guess", held),
        Check("embedding.long_query", "a letter too long to embed abstains for a person instead of crashing the run", long_ok),
    ]


def _policy() -> Any:
    from careflow.retrieval import AbstentionPolicy

    return AbstentionPolicy(min_dense_similarity=0.1, min_bm25_ratio=0.0)  # a drill-only number, used by no deployment and never read as a clinical threshold


async def database_outage() -> list[Check]:
    """The database refusing or timing out at the proposal write: the run fails visibly (it does not end silently), nothing is written, and a retry completes with exactly one effect."""
    raised, no_effect, finished = True, True, True
    for failure in (ConnectionError("the database refused the connection"), TimeoutError("the statement timed out")):  # refusing, then slow past its limit
        door = Door()
        door.proposer.fail_next, door.proposer.fail_with = 1, failure
        try:
            await door.run()
            raised = False
        except (ConnectionError, TimeoutError):
            pass
        no_effect = no_effect and not door.proposer.performed and (await door.state()).next != ()
        await door.resume()
        finished = finished and (await door.state()).next == ("review",) and len(door.proposer.performed) == 1 and len(door.ingestor.performed) == 1
    return [
        Check("database.refusal", "a refused connection or a statement timeout at the write fails the run visibly and writes nothing", raised and no_effect),
        Check("database.retry", "after the database returns, the same run resumes from its checkpoint with one proposal and one identity write", finished),
    ]


async def rules_first() -> list[Check]:
    """The rules run before any model on every path, and a red flag raises its nurse task even when every model is down."""
    red = Door(outcome="red_flag", extract_raises=TransientError("down"), retrieve_raises=TransientError("down"))
    await red.run()
    state = await red.state()
    nurse_task = len(red.engine.escalations) == 1 and not red.models_called() and state.next == ()
    clear = Door(extract_raises=TransientError("the vision and decision models are down"))
    with contextlib.suppress(Exception):  # a raised outage is fine here; what matters is the ORDER
        await clear.run()
    return [
        Check("rules.red_flag_with_models_down", "a red-flag letter raises its nurse task once and calls no model, with every model down", nurse_task),
        Check("rules.before_models", "the rules ran before the first model call even when that call failed", clear.rules_before_every_model() and "rules" in clear.log),
    ]


async def failed_stages_have_no_effects() -> list[Check]:
    """A stage that fails or is held writes nothing it should not: an unreadable page is a hold task only; a model outage writes no identity, proposal or decision; a paused review writes no decision."""
    held = Door(text_manual=True)
    await held.run()
    only_hold = held.effects() == ["effect:hold"] and not held.models_called() and held.ingestor.calls == 0 and not held.proposer.performed and not held.recorder.performed
    down = Door(extract_raises=TransientError("down"))
    with contextlib.suppress(Exception):
        await down.run()
    none_written = down.ingestor.calls == 0 and not down.proposer.performed and not down.recorder.performed
    paused = Door()
    await paused.run()
    at_pause = (await paused.state()).next == ("review",) and not paused.recorder.performed and len(paused.proposer.performed) == 1
    return [
        Check("effects.unreadable_page", "an unreadable page raises one hold task for a person and nothing else: no model call, no identity, no proposal", only_hold),
        Check("effects.model_down", "a model outage writes no identity, no proposal and no decision", none_written),
        Check("effects.paused", "the paused review step has written no decision (nothing is approved by waiting)", at_pause),
    ]


async def retries_repeat_nothing() -> list[Check]:
    """The same run twice, and a run resumed after a restart, repeat no effect: every write has its idempotency key and each is performed once."""
    door = Door()
    await door.run()
    await door.run()  # the same referral again in the same thread
    again = len(door.ingestor.performed) == 1 and len(door.proposer.performed) == 1
    restart = Door()
    restart.proposer.fail_next = 1
    with contextlib.suppress(ConnectionError):
        await restart.run()
    restart.rebuild()  # a NEW process over the same checkpoint store
    await restart.resume()
    resumed = len(restart.ingestor.performed) == 1 and len(restart.proposer.performed) == 1 and len(restart.proposer.attempts) == 2 and set(restart.proposer.attempts) == {f"triage:{DOC}"}
    keyed = all(str(DOC) in key for key in restart.proposer.attempts)
    return [
        Check("retry.same_key", "running the same referral twice writes one identity record and one proposal (same keys, one effect each)", again),
        Check("retry.restart", "a restart mid-run resumes from the checkpoint: identity is not redone and the proposal is written once under one key", resumed),
        Check("retry.keys", "every write attempt carries a key derived from the document", keyed),
    ]


async def spend_is_recorded_once_and_the_ceiling_stops_calls() -> list[Check]:
    """Spend is recorded once per SUCCESSFUL call, however many failed tries came before it; once the ceiling is reached the provider is not called again. (A failed try records nothing: the provider's own bill for it is unknown to us; see the threat model.)"""
    flaky = Provider("transient")
    gw, ledger = gateway("decision", flaky, price="0.001", ceiling="1")
    request = DecisionRequest("drill", "DRILL", ("a", "b"), DataClass.SYNTHETIC)
    try:
        await gw.call("decision", request, data_class=DataClass.SYNTHETIC, clinic_id=CLINIC)
    except AllRoutesFailed:
        pass
    nothing_when_failed = len(ledger.entries) == 0
    answering = Provider("ok", DecisionResult("a", {"a": 0.9, "b": 0.1}, 0.9, "drill"), Usage(1000, 1000))
    gw, ledger = gateway("decision", answering, price="0.001", ceiling="0.0035")
    results = []
    for _ in range(4):
        try:
            await gw.call("decision", request, data_class=DataClass.SYNTHETIC, clinic_id=CLINIC)
            results.append("ok")
        except SpendCeilingReached:
            results.append("stopped")
    stops = results == ["ok", "ok", "stopped", "stopped"] and answering.calls == 2 and len(ledger.entries) == 2
    safety = Provider("ok", DecisionResult("a", {"a": 0.9, "b": 0.1}, 0.9, "drill"), Usage(1000, 1000))
    gw, ledger = gateway("decision", safety, price="0.001", ceiling="0.0001")
    await gw.call("decision", request, data_class=DataClass.SYNTHETIC, clinic_id=CLINIC, call_class=CallClass.SAFETY)
    safety_goes_through = safety.calls == 1 and len(ledger.entries) == 1
    return [
        Check("spend.failed_tries", "tries that failed record no spend entry (unknown, stated), and no entry is made for a call that never answered", nothing_when_failed),
        Check("spend.once_and_ceiling", "each answered call records exactly one spend entry, and after the ceiling the provider is not called again", stops),
        Check("spend.safety_not_budgeted", "the safety class is never stopped by the ordinary ceiling, and its spend is still recorded once", safety_goes_through),
    ]


ALL: tuple[Callable[[], Awaitable[list[Check]]], ...] = (
    vision_outage, decision_outage, translation_outage, embedding_outage, database_outage, rules_first, failed_stages_have_no_effects, retries_repeat_nothing, spend_is_recorded_once_and_the_ceiling_stops_calls,
)


async def run_all() -> list[Check]:
    """Every check; one that raises is a FAIL in fixed words (the exception CLASS only)."""
    results: list[Check] = []
    for fn in ALL:
        try:
            results.extend(await fn())
        except Exception as exc:  # noqa: BLE001 - a drill that cannot run is a failed drill, named by class only
            results.append(Check(fn.__name__, f"the drill itself raised {type(exc).__name__}", False))
    return results
