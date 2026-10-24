"""Fake CLIENTS for the three decision engines (docs/design/p2-2-decision-tier.md): no model, no weights, no network, no key.

The adapters in `careflow.adapters.decision` are thin classes over client Protocols; these satisfy the Protocols with scripted answers so the
adapters, the gateway and the contract test run offline. `Scripted*` clients answer from a table keyed by (question_type, text); anything not in
the table gets a deterministic hash-based answer, so a test never needs a real model but can still tell clients apart. They record what they
were asked (checkpoint, option count) but never keep the request text.
"""

import hashlib
from collections.abc import Mapping, Sequence

from careflow.adapters.decision import LlmCompletion

Table = Mapping[tuple[str, str], str]  # (question_type, text) -> the option to favour


def _hash_pick(question_type: str, text: str, options: Sequence[str], salt: str) -> str:
    return options[hashlib.sha256(f"{salt}|{question_type}|{text}".encode()).digest()[0] % len(options)]


def _spread(options: Sequence[str], favoured: str, weight: float) -> dict[str, float]:
    rest = (1.0 - weight) / (len(options) - 1)
    return {o: (weight if o == favoured else rest) for o in options}


class ScriptedLayaClient:
    def __init__(self, table: Table | None = None, weight: float = 0.7) -> None:
        self.table, self.weight = dict(table or {}), weight
        self.calls: list[tuple[str, int]] = []  # (checkpoint, option count) only

    async def classify(self, *, checkpoint: str, question_type: str, text: str, options: Sequence[str]) -> Mapping[str, float]:
        self.calls.append((checkpoint, len(options)))
        favoured = self.table.get((question_type, text)) or _hash_pick(question_type, text, options, "laya")
        return _spread(options, favoured, self.weight)


class ScriptedJevClient:
    def __init__(self, table: Table | None = None, weight: float = 0.6) -> None:
        self.table, self.weight = dict(table or {}), weight
        self.calls = 0

    async def decide(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.calls += 1
        options = list(payload["options"])  # type: ignore[call-overload]
        favoured = self.table.get((str(payload["question"]), str(payload["input"]))) or _hash_pick(str(payload["question"]), str(payload["input"]), options, "jev")
        return {"probabilities": _spread(options, favoured, self.weight)}


class ScriptedLlmClient:
    def __init__(self, table: Table | None = None, confidence: float = 0.8) -> None:
        self.table, self.confidence = dict(table or {}), confidence
        self.prompts: list[tuple[str, str]] = []  # kept so a test can inspect the rendered prompt it was given

    async def complete(self, *, system: str, user: str) -> LlmCompletion:
        self.prompts.append((system, user))
        # The user message is "Question type: Q\nOptions:\n1. a\n2. b\nInput text:\nTEXT\n"
        head, _, text = user.partition("Input text:\n")
        question_type = head.splitlines()[0].removeprefix("Question type: ")
        options = [line.split(". ", 1)[1] for line in head.split("Options:\n", 1)[1].splitlines() if ". " in line]
        favoured = self.table.get((question_type, text.rstrip("\n"))) or _hash_pick(question_type, text.rstrip("\n"), options, "llm")
        import json

        return LlmCompletion(json.dumps({"choice": favoured, "confidence": self.confidence}), input_tokens=len(user.split()), output_tokens=8)
