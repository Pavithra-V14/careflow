"""HARD CI GATE (BUILD-SPEC.md §4, §7.2; CLAUDE.md rule 4): the structure of the agent graphs.

  * no node both pauses and has a side effect;
  * the declarations match the node SOURCE (which ports it touches, whether it calls interrupt);
  * the compiled topology is exactly the Front Door of §1;
  * one graph only: no fourth agent, no Care Journey graph or node while FEATURE_CARE_JOURNEY is off (and turning the flag on before it exists fails loudly);
  * the checker itself works: deliberately bad nodes are caught (a gate that cannot fail is not a gate).

Offline: no database, no model. `InMemorySaver` stands in for the checkpointer."""

import inspect
import re
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from careflow.front_door import (
    AGENT_INVENTORY,
    BUILT_AGENTS,
    MODEL_PORTS,
    NEUTRAL_PORTS,
    NODE_SPECS,
    SIDE_EFFECT_PORTS,
    FrontDoorDeps,
    FrontDoorError,
    NodeSpec,
    agents_to_build,
    build_front_door,
)
from careflow.graph_structure import violations

ROOT = Path(__file__).resolve().parent.parent


def deps() -> FrontDoorDeps:
    return FrontDoorDeps(None, None, None, None, None, None, None, None)  # type: ignore[arg-type]  # structure only: nothing is called


def graph():
    return build_front_door(deps(), InMemorySaver(), environ={})


def check(functions, specs):
    return violations(functions, specs, SIDE_EFFECT_PORTS, MODEL_PORTS, NEUTRAL_PORTS)


# ---- the real graph passes ----------------------------------------------------------------------------------------------------------------


def test_every_front_door_node_matches_its_declaration_and_none_both_pauses_and_has_a_side_effect():
    g = graph()
    assert check(g.node_functions, NODE_SPECS) == []
    for spec in NODE_SPECS.values():
        assert not (spec.pauses and spec.effects), spec.name


def test_the_review_node_is_the_only_pause_and_the_step_before_it_holds_the_side_effect():
    pausing = [s.name for s in NODE_SPECS.values() if s.pauses]
    assert pausing == ["review"]
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert ("propose_triage", "review") in edges
    assert NODE_SPECS["propose_triage"].effects == {"triage_proposal"}  # §7.2: the effect is the step immediately before the pause


def test_the_compiled_topology_is_exactly_the_front_door():
    drawn = graph().get_graph()
    nodes = {n for n in drawn.nodes if not n.startswith("__")}
    assert nodes == {"screen", "resolve_identity", "match_protocol", "propose_triage", "review", "apply_review"}
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges == {
        ("__start__", "screen"), ("screen", "resolve_identity"), ("screen", "__end__"), ("resolve_identity", "match_protocol"),
        ("resolve_identity", "__end__"), ("match_protocol", "propose_triage"), ("propose_triage", "review"), ("review", "apply_review"),
        ("apply_review", "__end__"),
    }


def test_no_model_chooses_the_path_routing_functions_read_only_the_state():
    src = inspect.getsource(build_front_door)
    for router in ("after_screen", "after_identity"):
        body = src.split(f"def {router}")[1].split("\n\n")[0]
        assert "deps." not in body and "await" not in body, router


# ---- one agent, no fourth, no Care Journey while the flag is off -----------------------------------------------------------------------------


def test_the_inventory_is_the_four_agents_of_the_spec_and_only_front_door_is_built():
    assert AGENT_INVENTORY == ("front_door", "scheduling", "care_journey", "visit_readiness")
    assert BUILT_AGENTS == ("front_door",) and agents_to_build({}) == ("front_door",)


def test_no_other_graph_is_defined_anywhere_in_the_package():
    graph_makers = sorted(p.name for p in (ROOT / "careflow").rglob("*.py") if re.search(r"\bStateGraph\(", p.read_text(encoding="utf-8")))
    assert graph_makers == ["front_door.py"]  # a second StateGraph is a fourth agent (or an unreviewed graph)


def test_no_care_journey_scheduling_or_readiness_node_exists_in_the_front_door():
    for name in NODE_SPECS:
        assert not re.search(r"care|journey|schedul|readiness|book", name), name


@pytest.mark.parametrize("flag", ["FEATURE_CARE_JOURNEY", "FEATURE_VISIT_READINESS"])
def test_a_feature_flag_on_for_an_agent_that_does_not_exist_fails_loudly_and_off_is_the_default(flag):
    assert agents_to_build({flag: "false"}) == ("front_door",) and agents_to_build({}) == ("front_door",)
    for value in ("1", "true", "TRUE", "yes", "on"):
        with pytest.raises(FrontDoorError):
            agents_to_build({flag: value})


def test_a_graph_without_a_checkpointer_cannot_be_built_and_hosted_tracing_is_refused():
    with pytest.raises(FrontDoorError):
        build_front_door(deps(), None, environ={})
    for variable in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        with pytest.raises(FrontDoorError):
            build_front_door(deps(), InMemorySaver(), environ={variable: "true"})


# ---- the checker catches what it exists to catch ----------------------------------------------------------------------------------------------


def _bad_pause_and_effect(state):
    answer = interrupt("approve?")  # noqa: F821
    deps.proposer.propose(answer)
    return {}


def _undeclared_effect(state):
    deps.ingestor.ingest(state)
    return {}


def _hidden_pause(state):
    interrupt("x")  # noqa: F821
    return {}


def _unclassified_port(state):
    deps.mailer.send(state)
    return {}


def _model_undeclared(state):
    deps.extractor.extract(state)
    return {}


def _clean(state):
    deps.texts.text_for(state)
    return {}


NOTHING = NodeSpec("n", frozenset(), pauses=False, models=False)


def test_the_checker_catches_a_node_that_pauses_and_has_a_side_effect():
    specs = {"n": NodeSpec("n", frozenset({"triage_proposal"}), pauses=True, models=False)}
    found = check({"n": _bad_pause_and_effect}, specs)
    assert any("pauses must never also have a side effect" in p for p in found)


def test_the_checker_catches_a_pause_declared_with_an_effect_even_if_the_source_hides_it():
    # The declaration alone is enough to fail: a node declared as both is wrong whatever its source says.
    specs = {"n": NodeSpec("n", frozenset({"identity"}), pauses=True, models=False)}
    assert any("pauses must never also have a side effect" in p for p in check({"n": _hidden_pause}, specs))


def test_the_checker_catches_an_undeclared_side_effect_an_undeclared_pause_an_unclassified_port_and_an_undeclared_model():
    assert any("touches ['identity']" in p for p in check({"n": _undeclared_effect}, {"n": NOTHING}))
    assert any("calls interrupt" in p for p in check({"n": _hidden_pause}, {"n": NOTHING}))
    assert any("unclassified port" in p for p in check({"n": _unclassified_port}, {"n": NOTHING}))
    assert any("touches a model port" in p for p in check({"n": _model_undeclared}, {"n": NOTHING}))


def test_the_checker_catches_a_node_without_a_declaration_and_a_declaration_without_a_node_and_passes_a_clean_node():
    assert check({"n": _clean, "extra": _clean}, {"n": NOTHING}) != []
    assert check({"n": _clean}, {"n": NOTHING, "gone": NOTHING}) != []
    assert check({"n": _clean}, {"n": NOTHING}) == []
