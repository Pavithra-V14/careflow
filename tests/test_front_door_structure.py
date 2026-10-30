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
from careflow.graph_structure import router_violations, violations

ROOT = Path(__file__).resolve().parent.parent


def deps() -> FrontDoorDeps:
    return FrontDoorDeps(None, None, None, None, None, None, None, None, None)  # type: ignore[arg-type]  # structure only: nothing is called


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
    assert nodes == {"screen", "resolve_identity", "match_protocol", "hold_for_human", "propose_triage", "review", "apply_review"}
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges == {
        ("__start__", "screen"), ("screen", "resolve_identity"), ("screen", "__end__"), ("resolve_identity", "match_protocol"),
        ("resolve_identity", "__end__"), ("match_protocol", "propose_triage"), ("match_protocol", "__end__"), ("propose_triage", "review"), ("review", "apply_review"),
        ("apply_review", "__end__"), ("screen", "hold_for_human"), ("resolve_identity", "hold_for_human"), ("match_protocol", "hold_for_human"),
        ("hold_for_human", "__end__"),
    }


def test_no_model_chooses_the_path_routing_functions_read_only_the_state():
    routers = graph().router_functions
    assert set(routers) == {"after_screen", "after_identity", "after_match"}
    assert router_violations(routers) == []


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
    deps.session()  # a neutral port (the text source is a model port since P2-4)
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
    assert any("reaches interrupt" in p for p in check({"n": _hidden_pause}, {"n": NOTHING}))
    assert any("unclassified port" in p for p in check({"n": _unclassified_port}, {"n": NOTHING}))
    assert any("touches a model port" in p for p in check({"n": _model_undeclared}, {"n": NOTHING}))


def test_the_checker_catches_a_node_without_a_declaration_and_a_declaration_without_a_node_and_passes_a_clean_node():
    assert check({"n": _clean, "extra": _clean}, {"n": NOTHING}) != []
    assert check({"n": _clean}, {"n": NOTHING, "gone": NOTHING}) != []
    assert check({"n": _clean}, {"n": NOTHING}) == []


# ---- the checker fails closed: the ways a node could hide a port or a pause ------------------------------------------------------------------


def _alias(state):
    d = deps
    d.proposer.propose(state)
    return {}


def _getattr_port(state):
    getattr(deps, "proposer").propose(state)  # noqa: B009
    return {}


def _pass_deps(state):
    _helper_using(deps, state)
    return {}


def _helper_using(d, state):
    d.proposer.propose(state)


def _helper_asks(question):
    from langgraph.types import interrupt

    return interrupt(question)


def _pause_through_helper(state):
    return {"x": _helper_asks("approve?")}


def _pause_through_alias(state):
    from langgraph.types import interrupt as ask

    return {"x": ask("approve?")}


def _returns_command(state):
    from langgraph.types import Command

    return Command(goto="somewhere")


def _pause_with_session(state):
    interrupt("approve?")  # noqa: F821
    deps.session()
    return {}


def _make_closure_node():
    port = object()

    def node(state):
        port.propose(state)
        return {}

    return node


PAUSES = NodeSpec("n", frozenset(), pauses=True, models=False)


@pytest.mark.parametrize("node", [_alias, _getattr_port, _pass_deps])
def test_a_port_reached_through_an_alias_getattr_or_a_helper_argument_is_a_violation(node):
    assert any("bare reference to deps" in p or "cannot follow" in p for p in check({"n": node}, {"n": NOTHING}))


def test_a_port_hidden_in_a_closure_variable_is_a_violation():
    assert any("closure variable" in p for p in check({"n": _make_closure_node()}, {"n": NOTHING}))


@pytest.mark.parametrize("node", [_pause_through_helper, _pause_through_alias])
def test_a_pause_reached_through_a_helper_or_an_import_alias_is_still_a_pause(node):
    assert any("does not reach" not in p and "reaches interrupt" in p for p in check({"n": node}, {"n": NOTHING}))
    assert check({"n": node}, {"n": PAUSES}) == []  # declared honestly, it passes


def test_returning_a_command_is_refused_and_a_pausing_node_may_not_touch_any_port_not_even_the_session():
    assert any("cannot follow" in p and "Command" in p for p in check({"n": _returns_command}, {"n": NOTHING}))
    found = check({"n": _pause_with_session}, {"n": PAUSES})
    assert any("may not touch any port" in p for p in found)


def _router_ok(state):
    return "a" if state.get("status") == "x" else "b"


def _router_with_deps(state):
    return deps.retriever.pick(state)


async def _router_awaits(state):
    return await something(state)  # noqa: F821


def _router_calls(state):
    return str(state)


def test_the_router_checker_accepts_a_state_only_router_and_refuses_ports_awaits_and_other_calls():
    assert router_violations({"r": _router_ok}) == []
    assert any("touches deps" in p for p in router_violations({"r": _router_with_deps}))
    assert any("awaits" in p for p in router_violations({"r": _router_awaits}))
    assert any("other than state.get" in p for p in router_violations({"r": _router_calls}))


# ---- the gate holds against our own Postgres checkpointer too (owner decision of 2026-10-24) ---------------------------------------------------------


def graph_on_our_saver():
    from careflow.checkpointer import InMemoryCheckpointStore, PostgresCheckpointSaver

    return build_front_door(deps(), PostgresCheckpointSaver(InMemoryCheckpointStore()), environ={})


def test_the_structural_gate_passes_on_the_graph_compiled_with_our_postgres_checkpointer():
    g = graph_on_our_saver()
    assert check(g.node_functions, NODE_SPECS) == [] and router_violations(g.router_functions) == []
    assert {n for n in g.get_graph().nodes if not n.startswith("__")} == set(NODE_SPECS)
    assert {(e.source, e.target) for e in g.get_graph().edges} == {(e.source, e.target) for e in graph().get_graph().edges}  # the same topology as on InMemorySaver


def test_only_the_review_node_pauses_and_it_touches_no_port_with_our_saver_compiled_in():
    g = graph_on_our_saver()
    assert [n for n, spec in NODE_SPECS.items() if spec.pauses] == ["review"]
    assert violations({"review": g.node_functions["review"]}, {"review": NODE_SPECS["review"]}, SIDE_EFFECT_PORTS, MODEL_PORTS, NEUTRAL_PORTS) == []
    assert type(g.checkpointer).__name__ == "PostgresCheckpointSaver"


def test_the_checkpointer_module_itself_cannot_pause_or_reach_a_port():
    """The saver is infrastructure, not a node: it must never import or call interrupt, the engine, or a port."""
    import careflow.checkpointer as module

    source = inspect.getsource(module)
    assert "interrupt(" not in source and "deps." not in source and "FrontDoorDeps" not in source
