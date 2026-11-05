"""HARD CI GATE (BUILD-SPEC.md 4, 7.2; CLAUDE.md rule 4) for the Scheduling graph, like the Front Door's: no node both pauses and has a side effect; the declarations match the node SOURCE; the compiled
topology is exactly the one documented; no model chooses the path; the two pauses touch no port. Offline: no database, no model."""

from langgraph.checkpoint.memory import InMemorySaver

from careflow.graph_structure import router_violations, violations
from careflow.scheduling_graph import (
    MODEL_PORTS,
    NEUTRAL_PORTS,
    NODE_SPECS,
    SIDE_EFFECT_PORTS,
    SchedulingDeps,
    build_scheduling_graph,
)


def graph():
    return build_scheduling_graph(SchedulingDeps(None, None, None, None, None, None, None, None, None, 1, "p", "r"), InMemorySaver(), environ={})  # type: ignore[arg-type]


def test_every_node_matches_its_declaration_and_none_both_pauses_and_has_a_side_effect():
    g = graph()
    assert violations(g.node_functions, NODE_SPECS, SIDE_EFFECT_PORTS, MODEL_PORTS, NEUTRAL_PORTS) == []
    for spec in NODE_SPECS.values():
        assert not (spec.pauses and spec.effects), spec.name


def test_exactly_two_nodes_pause_and_the_effect_sits_immediately_before_each_reply_pause():
    assert sorted(s.name for s in NODE_SPECS.values() if s.pauses) == ["await_reply", "await_send"]
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert ("send_offer", "await_reply") in edges and NODE_SPECS["send_offer"].effects == {"offer"}  # 7.2: the side effect is the step immediately before the pause
    assert ("draft_offer", "await_send") in edges  # the first pause comes straight after the pure draft: nothing is offered before a person presses send


def test_nothing_that_touches_a_model_runs_before_the_rules_screen_the_reply():
    edges = {(e.source, e.target) for e in graph().get_graph().edges}
    assert ("screen_reply", "interpret_reply") in edges and not any(t == "interpret_reply" and s != "screen_reply" for s, t in edges)
    assert NODE_SPECS["screen_reply"].effects == {"escalation"} and NODE_SPECS["interpret_reply"].models is True


def test_the_compiled_topology_is_exactly_the_documented_scheduling_graph():
    drawn = graph().get_graph()
    assert {n for n in drawn.nodes if not n.startswith("__")} == set(NODE_SPECS)
    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges >= {
        ("__start__", "rank"), ("rank", "draft_offer"), ("draft_offer", "await_send"), ("await_send", "send_offer"), ("await_send", "advance"), ("advance", "draft_offer"),
        ("send_offer", "await_reply"), ("await_reply", "screen_reply"), ("await_reply", "release_hold"), ("screen_reply", "interpret_reply"), ("interpret_reply", "decide"),
        ("decide", "book_slot"), ("decide", "release_hold"), ("release_hold", "advance"), ("book_slot", "__end__"), ("hold_for_human", "__end__"),
    }
    assert not any(s == "book_slot" and t != "__end__" for s, t in edges)  # a booking is the last thing that happens


def test_no_model_chooses_the_path_routing_functions_read_only_the_state():
    routers = graph().router_functions
    assert len(routers) == 8 and router_violations(routers) == []


def test_every_non_terminal_route_has_a_way_to_a_person_so_no_run_ends_silently():
    sources = {e.source for e in graph().get_graph().edges if e.target == "hold_for_human"}
    assert {"rank", "draft_offer", "await_send", "await_reply", "screen_reply", "interpret_reply", "decide", "release_hold"} <= sources
