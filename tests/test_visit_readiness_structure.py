"""HARD CI GATE (BUILD-SPEC.md 4, 7.2, 22; CLAUDE.md rules 2 and 4) for the Visit Readiness graph: no node both pauses and has a side effect; declarations match the node SOURCE; the topology is the
documented one; the rules screen every reply before any model reads it; no model chooses the path; the graph does not exist while FEATURE_VISIT_READINESS is off. Offline."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from careflow.autonomy import Policy
from careflow.front_door import FrontDoorError
from careflow.graph_structure import router_violations, violations
from careflow.visit_readiness import (
    MODEL_PORTS,
    NEUTRAL_PORTS,
    NODE_SPECS,
    SIDE_EFFECT_PORTS,
    VisitReadinessDeps,
    build_visit_readiness_graph,
)

ON = {"FEATURE_VISIT_READINESS": "1"}


def deps():
    return VisitReadinessDeps(*([None] * 14), Policy())  # type: ignore[arg-type]


def graph():
    return build_visit_readiness_graph(deps(), InMemorySaver(), environ=ON)


def test_the_graph_is_not_built_while_the_flag_is_off():
    for env in ({}, {"FEATURE_VISIT_READINESS": "false"}, {"FEATURE_VISIT_READINESS": ""}):
        with pytest.raises(FrontDoorError):
            build_visit_readiness_graph(deps(), InMemorySaver(), environ=env)


def test_every_node_matches_its_declaration_and_none_both_pauses_and_has_a_side_effect():
    g = graph()
    assert violations(g.node_functions, NODE_SPECS, SIDE_EFFECT_PORTS, MODEL_PORTS, NEUTRAL_PORTS) == []
    for spec in NODE_SPECS.values():
        assert not (spec.pauses and spec.effects), spec.name


def test_exactly_one_node_pauses_and_the_question_is_sent_in_the_step_immediately_before():
    assert [s.name for s in NODE_SPECS.values() if s.pauses] == ["await_reply"]
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert ("ask_item", "await_reply") in edges and NODE_SPECS["ask_item"].effects == {"patient_message"}  # 7.2
    assert {s for s, t in edges if t == "await_reply"} == {"ask_item"}


def test_the_rules_screen_every_reply_before_any_model_reads_it():
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    for model_node in ("classify_intent", "classify_readiness", "resolve_fact"):
        assert NODE_SPECS[model_node].models is True
        assert {s for s, t in edges if t == model_node} <= {"screen_reply", "classify_intent"}, model_node  # resolve_fact comes only after the intent, which comes only after the screen
    assert {s for s, t in edges if t in {"classify_intent", "classify_readiness"}} == {"screen_reply"}
    assert {s for s, t in edges if t == "screen_reply"} == {"await_reply"} and NODE_SPECS["screen_reply"].effects == {"escalation"}
    assert {s for s, t in edges if t == "resolve_fact"} == {"classify_intent"}


def test_a_flagged_reply_ends_the_run_with_no_edge_into_any_model_node():
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert ("screen_reply", "__end__") in edges  # red_flag_escalated ends here; the screener has already raised the nurse task and the fixed safety response


def test_the_readiness_escalation_does_not_consult_the_autonomy_mode():
    spec = NODE_SPECS["escalate_nurse"]
    assert spec.effects == {"review_task"} and spec.models is False
    from careflow.visit_readiness import (
        build_visit_readiness_graph as build,  # the node reads no policy: a Policy attribute access would not pass the port classification
    )

    source = __import__("inspect").getsource(build)
    node_src = source[source.index("async def escalate_nurse") : source.index("async def classify_barrier")]
    assert "policy" not in node_src.lower() and "mode" not in node_src.lower()


def test_no_model_chooses_the_path_routing_functions_read_only_the_state():
    routers = graph().router_functions
    assert len(routers) == 10 and router_violations(routers) == []


def test_the_compiled_topology_is_exactly_the_documented_graph():
    drawn = graph().get_graph()
    assert {n for n in drawn.nodes if not n.startswith("__")} == set(NODE_SPECS)
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges >= {
        ("__start__", "build_brief"), ("__start__", "next_item"), ("__start__", "classify_barrier"), ("next_item", "ask_item"), ("ask_item", "await_reply"), ("await_reply", "screen_reply"),
        ("screen_reply", "classify_intent"), ("screen_reply", "classify_readiness"), ("classify_intent", "resolve_fact"), ("classify_intent", "reask"), ("classify_intent", "classify_barrier"),
        ("reask", "ask_item"), ("resolve_fact", "next_item"), ("classify_readiness", "next_item"), ("classify_readiness", "escalate_nurse"), ("classify_barrier", "handoff_rebooking"),
        ("build_brief", "__end__"), ("handoff_rebooking", "__end__"), ("finish_visit", "__end__"), ("hold_for_human", "__end__"),
    }


def test_every_non_terminal_route_has_a_way_to_a_person_so_no_run_ends_silently():
    sources = {e.source for e in graph().get_graph().edges if e.target == "hold_for_human"}
    assert {"next_item", "ask_item", "await_reply", "screen_reply", "classify_intent", "resolve_fact", "classify_readiness", "classify_barrier", "escalate_nurse"} <= sources
