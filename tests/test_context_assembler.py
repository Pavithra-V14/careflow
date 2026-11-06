"""careflow/context_assembler.py (BUILD-SPEC.md 9.2): priority order, whole-item dropping from the lowest priority up, reported drops, the instruction never dropped."""

import pytest

from careflow.context_assembler import (
    BudgetTooSmall,
    ContextError,
    assemble,
    render_state,
)


def tok(text: str) -> int:
    return len(text.split())  # one token per word: exact arithmetic in the tests


def test_everything_fits_in_priority_order_with_history_chronological():
    a = assemble("do the task", budget=100, patient_facts=[("f1", "fact one")], evidence=[("e1", "evidence one")], history=[("h1", "old turn"), ("h2", "new turn")], count_tokens=tok)
    assert a.included == ("instruction", "f1", "e1", "h1", "h2") and a.dropped == ()
    assert a.text.index("[instruction]") < a.text.index("[patient_facts]") < a.text.index("[evidence]") < a.text.index("[history]")


def test_what_does_not_fit_is_dropped_from_the_lowest_priority_up_and_reported_by_label_only():
    a = assemble("do it", budget=6, patient_facts=[("f1", "a b")], evidence=[("e1", "c d e f")], history=[("h1", "g h")], count_tokens=tok)
    assert a.included == ("instruction", "f1", "h1") and a.dropped == ("e1",)  # the evidence did not fit; the small history turn still did
    b = assemble("do it", budget=4, patient_facts=[("f1", "a b")], evidence=[("e1", "c d")], history=[("h1", "g h")], count_tokens=tok)
    assert b.included == ("instruction", "f1") and set(b.dropped) == {"e1", "h1"}
    assert a.tokens <= a.budget and b.tokens <= b.budget


def test_the_newest_history_turns_are_kept_first_and_turns_beyond_the_window_are_reported():
    turns = [(f"h{i}", "word") for i in range(10)]
    a = assemble("go", budget=1000, history=turns, history_turns=6, count_tokens=tok)
    assert a.included == ("instruction", *(f"h{i}" for i in range(4, 10))) and set(a.dropped) == {f"h{i}" for i in range(4)}
    tight = assemble("go", budget=3, history=turns[:4], count_tokens=tok)
    assert tight.included == ("instruction", "h2", "h3") and set(tight.dropped) == {"h0", "h1"}


def test_the_instruction_is_never_dropped_and_a_budget_too_small_for_it_refuses():
    with pytest.raises(BudgetTooSmall):
        assemble("one two three", budget=2, count_tokens=tok)


def test_state_is_rendered_exactly_and_kept_with_the_patient_facts_or_dropped_whole():
    assert render_state({"b": 1, "a": {"y": 2, "x": 1}}) == '{"a":{"x":1,"y":2},"b":1}'
    a = assemble("go", budget=50, state={"awaiting": "reply"}, count_tokens=tok)
    assert "state" in a.included and '"awaiting":"reply"' in a.text
    b = assemble("go", budget=1, state={"awaiting": "reply"}, count_tokens=tok)
    assert b.included == ("instruction",) and b.dropped == ("state",)


def test_bad_requests_are_refused_and_the_default_estimate_is_pessimistic():
    for bad in ("", "   "):
        with pytest.raises(ContextError):
            assemble(bad, budget=10)
    for budget in (0, -1, True):
        with pytest.raises(ContextError):
            assemble("x", budget=budget)  # type: ignore[arg-type]
    assert assemble("abcdef", budget=10).tokens == 2
    assert assemble("a" * 30, budget=10).tokens == 10
