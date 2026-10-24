"""The three `decision` engines as thin adapters over injectable clients (BUILD-SPEC.md §5.2). Design: docs/design/p2-2-decision-tier.md.

    LayaAdapter  local, self-hosted (so it may see patient text), routed by script BEFORE the call
    JevAdapter   hosted zero-shot via OpenRouter; synthetic data ONLY: it refuses a PHI request itself
    LlmAdapter   the general `llm_fast` fallback: a fixed prompt, a JSON reply that must name exactly one offered option

Each adapter is a callable (`await adapter(request)`) returning `AdapterResult[DecisionResult]`, which is what the gateway expects, and
validates what comes back from its client the same way, so that a client that returns something malformed fails the call instead of
producing a choice: probabilities must cover exactly the offered options, be finite and in [0, 1], sum to 1, and have ONE top option (a tie
is no decision). Every result names the engine, the confidence of the chosen option, whether that confidence is calibrated, and the route
(checkpoint or prompt version) that answered.

No client is built here. `LayaClient`, `JevClient` and `LlmClient` are Protocols; the tests satisfy them with fakes and no model, weight,
runtime, key or network call exists. When a real client is approved it is written against these Protocols and the adapter does not change.
Nothing in this module logs; the request text has `repr=False` and never appears in an error message.
"""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from careflow.ports import (
    MAX_DECISION_OPTIONS,
    AdapterResult,
    DataClass,
    DecisionRequest,
    DecisionResult,
    PermanentError,
    RouteNotApplicable,
    Usage,
)
from careflow.prompts import PromptSet
from careflow.script import laya_checkpoint

SUM_TOLERANCE: Final = 1e-6
TIE_TOLERANCE: Final = 1e-9
MAX_LAYA_CHARS: Final = 2000  # a technical bound (the checkpoints read at most 1024 tokens); longer text is skipped, never silently truncated


# ---- shared validation -------------------------------------------------------------------------------------------------------------------


def _validated(options: Sequence[str], probabilities: Mapping[str, float]) -> dict[str, float]:
    if set(probabilities) != set(options) or len(probabilities) != len(options):
        raise PermanentError("the engine's probabilities do not cover exactly the offered options")
    out = {}
    for option in options:
        p = probabilities[option]
        if isinstance(p, bool) or not isinstance(p, int | float) or not math.isfinite(p) or p < 0 or p > 1:
            raise PermanentError("the engine returned a probability outside 0..1")
        out[option] = float(p)
    if abs(sum(out.values()) - 1.0) > SUM_TOLERANCE:
        raise PermanentError("the engine's probabilities do not sum to 1")
    return out


def _decide_from(probs: dict[str, float], *, engine: str, calibrated: bool, route: str) -> DecisionResult:
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] <= TIE_TOLERANCE:
        raise PermanentError("no unique top option")  # a tie is not a decision (unknown is not a value)
    choice, confidence = ranked[0]
    return DecisionResult(choice, probs, confidence, engine, calibrated, route)


def _check_options(request: DecisionRequest) -> None:
    if len(request.options) > MAX_DECISION_OPTIONS:  # also enforced by DecisionRequest; an adapter never relies on its caller
        raise PermanentError(f"a decision call takes at most {MAX_DECISION_OPTIONS} options")


# ---- calibration (§5.2: stored per (engine, question_type); temperature scaling) ---------------------------------------------------------------


class CalibrationStore(Protocol):
    def temperature(self, engine: str, question_type: str) -> float | None: ...


class InMemoryCalibrationStore:
    """For tests and the first runs. The versioned, approved calibration table is a later migration."""

    def __init__(self, temperatures: Mapping[tuple[str, str], float] | None = None) -> None:
        self._t = dict(temperatures or {})

    def temperature(self, engine: str, question_type: str) -> float | None:
        return self._t.get((engine, question_type))


def temperature_scale(probs: dict[str, float], temperature: float) -> dict[str, float]:
    """Temperature scaling of probabilities: p_i^(1/T), renormalised. T > 1 softens, T < 1 sharpens, T = 1 changes nothing."""
    if not math.isfinite(temperature) or temperature <= 0:
        raise PermanentError("a calibration temperature must be a positive number")
    if len(probs) < 2:
        return probs
    powered = {k: (v ** (1.0 / temperature) if v > 0 else 0.0) for k, v in probs.items()}
    total = sum(powered.values())
    if total <= 0:
        raise PermanentError("calibration produced no probability mass")
    return {k: v / total for k, v in powered.items()}


# ---- Laya --------------------------------------------------------------------------------------------------------------------------------


class LayaClient(Protocol):
    async def classify(self, *, checkpoint: str, question_type: str, text: str, options: Sequence[str]) -> Mapping[str, float]:
        """The raw probability of each option from the named checkpoint ("english" or "multilingual")."""
        ...


@dataclass
class LayaAdapter:
    client: LayaClient
    calibration: CalibrationStore | None = None
    name: str = "laya"

    def accepts(self, request: DecisionRequest) -> bool:
        """Decided from the TEXT, before any call: a supported script (Latin, Tamil), non-empty, not over-long. Never by confidence after."""
        return laya_checkpoint(request.text, request.language) is not None and 0 < len(request.text.strip()) <= MAX_LAYA_CHARS

    async def __call__(self, request: DecisionRequest) -> AdapterResult[DecisionResult]:
        _check_options(request)
        if not self.accepts(request):
            raise RouteNotApplicable("laya does not take this input")  # raised BEFORE the client is touched
        checkpoint = laya_checkpoint(request.text, request.language)
        assert checkpoint is not None  # accepts() just proved it
        raw = _validated(
            request.options,
            await self.client.classify(checkpoint=checkpoint, question_type=request.question_type, text=request.text, options=request.options),
        )
        temperature = self.calibration.temperature(self.name, request.question_type) if self.calibration else None
        probs = temperature_scale(raw, temperature) if temperature is not None else raw
        result = _decide_from(probs, engine=self.name, calibrated=temperature is not None, route=checkpoint)
        return AdapterResult(result, Usage(max(1, len(request.text.split())), 1))


# ---- Jev ---------------------------------------------------------------------------------------------------------------------------------


class JevClient(Protocol):
    async def decide(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


def _to_jev_payload(request: DecisionRequest) -> dict[str, object]:
    """UNVERIFIED (BUILD-SPEC.md Appendix B): Jev's request shape via OpenRouter is a "decisions" endpoint, distinct from /chat/completions.
    This is the ONE place that knows the shape, so a correction after a real call is a one-place change. Do not depend on it before then."""
    return {"task": "choice", "question": request.question_type, "input": request.text, "options": list(request.options)}


@dataclass
class JevAdapter:
    client: JevClient
    name: str = "jev"

    def accepts(self, request: DecisionRequest) -> bool:
        return request.data_class is DataClass.SYNTHETIC  # a third party: never patient text

    async def __call__(self, request: DecisionRequest) -> AdapterResult[DecisionResult]:
        if request.data_class is not DataClass.SYNTHETIC:
            # Defence in depth: the gateway only offers a PHI request to a phi_approved row, and Jev's row never is. This stops a wrong
            # registry row from ever sending patient text to a third party.
            raise PermanentError("jev handles synthetic data only")
        _check_options(request)
        reply = await self.client.decide(_to_jev_payload(request))
        probabilities = reply.get("probabilities") if isinstance(reply, Mapping) else None
        if not isinstance(probabilities, Mapping):
            raise PermanentError("jev's reply has no probabilities")
        probs = _validated(request.options, probabilities)  # type: ignore[arg-type]
        return AdapterResult(_decide_from(probs, engine=self.name, calibrated=False, route="jev"), Usage(max(1, len(request.text.split())), 1))


# ---- the general LLM fallback ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LlmCompletion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LlmClient(Protocol):
    async def complete(self, *, system: str, user: str) -> LlmCompletion: ...


def _parse_llm_reply(raw: str, options: Sequence[str]) -> tuple[str, float]:
    """The reply must be ONE JSON object with exactly the keys choice and confidence, choice EXACTLY one offered option. Anything else is not a
    choice. Text inside the input that tries to instruct the model cannot widen this: only an exact option string is ever returned."""
    try:
        obj = json.loads(raw.strip())
    except (ValueError, TypeError):
        raise PermanentError("the model's reply is not JSON") from None
    if not isinstance(obj, dict) or set(obj) != {"choice", "confidence"}:
        raise PermanentError("the model's reply does not have exactly the keys choice and confidence")
    choice, confidence = obj["choice"], obj["confidence"]
    if not isinstance(choice, str) or choice not in options:
        raise PermanentError("the model's choice is not one of the offered options")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise PermanentError("the model's confidence is not a number from 0 to 1")
    return choice, float(confidence)


@dataclass
class LlmAdapter:
    client: LlmClient
    prompt: PromptSet
    name: str = "llm_fast"

    async def __call__(self, request: DecisionRequest) -> AdapterResult[DecisionResult]:
        _check_options(request)
        options_block = "\n".join(f"{i}. {option}" for i, option in enumerate(request.options, start=1))
        user = self.prompt.render_user(question_type=request.question_type, options_block=options_block, input_text=request.text)
        completion = await self.client.complete(system=self.prompt.system, user=user)
        choice, confidence = _parse_llm_reply(completion.text, request.options)
        rest = (1.0 - confidence) / (len(request.options) - 1)
        probs = {o: (confidence if o == choice else rest) for o in request.options}
        probs = _validated(request.options, probs)
        # The model's own confidence is self-reported, never calibrated here. A tie (confidence exactly 1/n) is not a decision.
        result = _decide_from(probs, engine=self.name, calibrated=False, route=self.prompt.label)
        return AdapterResult(result, Usage(completion.input_tokens, completion.output_tokens))
