"""HARD CI GATE (BUILD-SPEC.md 4, 7.2; CLAUDE.md rule 4) for the Care Journey graph, like the Front Door's and the Scheduling graph's: no node both pauses and has a side effect; the declarations match the
node SOURCE; the compiled topology is the documented one; no model chooses the path; the graph does not exist while FEATURE_CARE_JOURNEY is off. Offline."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from careflow.care_journey import (
    MODEL_PORTS,
    NEUTRAL_PORTS,
    NODE_SPECS,
    SIDE_EFFECT_PORTS,
    CareJourneyDeps,
    build_care_journey_graph,
)
from careflow.front_door import FrontDoorError
from careflow.graph_structure import router_violations, violations

ON = {"FEATURE_CARE_JOURNEY": "1"}


def graph():
    return build_care_journey_graph(CareJourneyDeps(None, None, None, None, None, None, None, None, None, None, "p", "r"), InMemorySaver(), environ=ON)  # type: ignore[arg-type]


def test_the_graph_is_not_built_while_the_flag_is_off():
    for env in ({}, {"FEATURE_CARE_JOURNEY": "false"}, {"FEATURE_CARE_JOURNEY": ""}):
        with pytest.raises(FrontDoorError):
            build_care_journey_graph(CareJourneyDeps(None, None, None, None, None, None, None, None, None, None, "p", "r"), InMemorySaver(), environ=env)  # type: ignore[arg-type]


def test_every_node_matches_its_declaration_and_none_both_pauses_and_has_a_side_effect():
    g = graph()
    assert violations(g.node_functions, NODE_SPECS, SIDE_EFFECT_PORTS, MODEL_PORTS, NEUTRAL_PORTS) == []
    for spec in NODE_SPECS.values():
        assert not (spec.pauses and spec.effects), spec.name


def test_exactly_two_nodes_pause_and_each_pause_comes_straight_after_its_effect():
    assert sorted(s.name for s in NODE_SPECS.values() if s.pauses) == ["await_confirmation", "await_result"]
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert ("request_confirmation", "await_confirmation") in edges and NODE_SPECS["request_confirmation"].effects == {"review_task"}  # 7.2
    assert ("next_wait", "await_result") in edges and NODE_SPECS["next_wait"].effects == frozenset()  # the waits were registered at activation, a step earlier


def test_nothing_executes_before_a_doctor_confirms():
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert not any(t == "activate_plan" and s != "await_confirmation" for s, t in edges)
    for node in ("activate_plan", "next_wait", "await_result", "claim_result", "propose_referral", "close_step"):
        assert node not in {t for s, t in edges if s in {"parse_plan", "request_confirmation"}}


def test_the_rules_screen_the_plan_before_the_model_reads_it():
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert ("screen_plan", "parse_plan") in edges and not any(t == "parse_plan" and s != "screen_plan" for s, t in edges)
    assert NODE_SPECS["screen_plan"].effects == {"escalation"} and NODE_SPECS["screen_plan"].models is False


def test_only_the_parse_touches_a_model_and_no_model_chooses_the_path():
    assert [s.name for s in NODE_SPECS.values() if s.models] == ["parse_plan"]
    routers = graph().router_functions
    assert len(routers) == 10 and router_violations(routers) == []


def test_the_compiled_topology_is_exactly_the_documented_care_journey_graph():
    drawn = graph().get_graph()
    assert {n for n in drawn.nodes if not n.startswith("__")} == set(NODE_SPECS)
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges >= {
        ("__start__", "screen_plan"), ("screen_plan", "parse_plan"), ("parse_plan", "request_confirmation"), ("request_confirmation", "await_confirmation"), ("await_confirmation", "activate_plan"),
        ("await_confirmation", "reject_plan"), ("activate_plan", "next_wait"), ("next_wait", "await_result"), ("next_wait", "complete_plan"), ("await_result", "claim_result"),
        ("claim_result", "evaluate_result"), ("claim_result", "await_result"), ("evaluate_result", "propose_referral"), ("evaluate_result", "close_step"),
        ("propose_referral", "next_wait"), ("close_step", "next_wait"), ("complete_plan", "__end__"), ("reject_plan", "__end__"), ("hold_for_human", "__end__"),
    }


def test_a_condition_that_cannot_be_decided_has_no_edge_to_close_step():
    # close_step is reachable only from evaluate_result; evaluate_result sends every `unknown` outcome to hold_for_human (behaviour is tested in test_care_journey.py).
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert {s for s, t in edges if t == "close_step"} == {"evaluate_result"}


def test_every_non_terminal_route_has_a_way_to_a_person_so_no_run_ends_silently():
    sources = {e.source for e in graph().get_graph().edges if e.target == "hold_for_human"}
    assert {"screen_plan", "parse_plan", "request_confirmation", "await_confirmation", "activate_plan", "next_wait", "await_result", "claim_result", "evaluate_result", "propose_referral", "close_step"} <= sources
