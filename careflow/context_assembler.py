"""The context assembler (BUILD-SPEC.md 9.2): priority-ordered, a DECLARED token budget per step, what did not fit is dropped from the lowest priority up, and WHAT WAS DROPPED IS REPORTED so the trace can log it.

Order of priority (highest first): task instruction, patient facts, retrieved evidence, history. The history is the last turns of the thread VERBATIM (never a summary: a generated summary of a clinical
conversation drifts) and the structured state is a typed object rendered exactly (sorted keys), not prose. The assembler never invents or trims text inside an item: an item either fits whole or is dropped whole
(the oldest history turn goes first). The task instruction is never dropped: if it alone exceeds the budget the assembly REFUSES (`BudgetTooSmall`) rather than send a model a call without its instruction.
Pure code: no model, no network, no framework.

The token count is an ESTIMATE supplied by the caller (`count_tokens`); the default is a deliberately pessimistic characters-per-token guess so the budget errs on the side of dropping, never of overrunning.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

PRIORITY: Final = ("instruction", "patient_facts", "evidence", "history")
DEFAULT_HISTORY_TURNS: Final = 6


class BudgetTooSmall(Exception):
    """The task instruction alone does not fit the declared budget. Fixed words."""


class ContextError(Exception):
    """A malformed request (an empty instruction, a budget below 1). Fixed words."""


def pessimistic_tokens(text: str) -> int:
    """About one token per 3 characters, rounded up: more tokens than most tokenisers give, so a budget built on it is conservative."""
    return (len(text) + 2) // 3


@dataclass(frozen=True)
class Item:
    section: str
    label: str  # an opaque id of the item for the trace (never its text)
    text: str
    tokens: int


@dataclass(frozen=True)
class Assembled:
    text: str
    tokens: int
    budget: int
    included: tuple[str, ...]  # labels, in output order
    dropped: tuple[str, ...]  # labels dropped for lack of room: ids only, never text


def render_state(state: Mapping[str, object]) -> str:
    """The structured state object rendered exactly: sorted keys, no prose."""
    return json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)


def assemble(
    instruction: str,
    *,
    budget: int,
    patient_facts: Sequence[tuple[str, str]] = (),
    evidence: Sequence[tuple[str, str]] = (),
    history: Sequence[tuple[str, str]] = (),
    state: Mapping[str, object] | None = None,
    history_turns: int = DEFAULT_HISTORY_TURNS,
    count_tokens: Callable[[str], int] = pessimistic_tokens,
) -> Assembled:
    if not isinstance(instruction, str) or not instruction.strip():
        raise ContextError("a task instruction is required")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise ContextError("a positive token budget is required")
    instruction_item = Item("instruction", "instruction", instruction, count_tokens(instruction))
    if instruction_item.tokens > budget:
        raise BudgetTooSmall("the task instruction does not fit the budget")

    facts: list[Item] = []
    if state is not None:
        rendered = render_state(state)
        facts.append(Item("patient_facts", "state", rendered, count_tokens(rendered)))  # the typed state travels with the patient facts: exact, and dropped whole if it cannot fit
    facts += [Item("patient_facts", label, text, count_tokens(text)) for label, text in patient_facts]
    proof = [Item("evidence", label, text, count_tokens(text)) for label, text in evidence]
    turns = list(history)
    window = turns[len(turns) - history_turns:] if history_turns > 0 else []
    beyond = turns[: len(turns) - len(window)]
    past = [Item("history", label, text, count_tokens(text)) for label, text in window]

    kept: list[Item] = [instruction_item]
    dropped: list[str] = []
    used = instruction_item.tokens
    for i in [*facts, *proof]:  # highest priority first
        if used + i.tokens <= budget:
            kept.append(i)
            used += i.tokens
        else:
            dropped.append(i.label)
    fitted: list[Item] = []
    for i in reversed(past):  # the newest turns matter most: fill from the newest backwards, then restore chronological order
        if used + i.tokens <= budget:
            fitted.append(i)
            used += i.tokens
        else:
            dropped.append(i.label)
    kept += list(reversed(fitted))
    dropped += [label for label, _ in beyond]  # turns outside the verbatim window are dropped by design and reported the same way
    text = "\n\n".join(f"[{i.section}] {i.text}" for i in kept)
    return Assembled(text=text, tokens=used, budget=budget, included=tuple(i.label for i in kept), dropped=tuple(dropped))
