"""The structural checker for agent graphs (BUILD-SPEC.md §4 and §7.2). Used by tests/test_front_door_structure.py as a hard CI gate, and reusable by every
later graph.

It reads each node's SOURCE (not its behaviour) and compares it with the node's declaration:

  * the side-effect ports the node touches (`deps.<name>` where <name> is a side-effect port) must equal the node's declared `effects`;
  * a node that calls `interrupt(` must be declared `pauses`, and a node declared `pauses` must call it;
  * NO node may both pause and have a side effect (the single most common source of duplicate bookings and messages in agent systems: a paused node
    re-executes from its first line on resume);
  * a node that touches a model port must be declared `models`;
  * every `deps.<name>` a node touches must be classified (side-effect, model or neutral): an unclassified port is a violation, so a new port cannot
    slip in without someone deciding which kind it is.

Returns a list of violation strings (empty means the graph passes). It never executes a node.
"""

import ast
import inspect
import textwrap
from collections.abc import Callable, Mapping
from typing import Any


def _deps_attributes(fn: Callable[..., Any]) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "deps":
            found.add(node.attr)
    return found


def _calls_interrupt(fn: Callable[..., Any]) -> bool:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            name = target.id if isinstance(target, ast.Name) else target.attr if isinstance(target, ast.Attribute) else None
            if name == "interrupt":
                return True
    return False


def violations(
    functions: Mapping[str, Callable[..., Any]], specs: Mapping[str, Any], side_effect_ports: Mapping[str, str], model_ports: Mapping[str, str],
    neutral_ports: set[str] | frozenset[str],
) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(functions) ^ set(specs)):
        problems.append(f"{name}: a node without a declaration, or a declaration without a node")
    for name, fn in functions.items():
        spec = specs.get(name)
        if spec is None:
            continue
        attrs = _deps_attributes(fn)
        unclassified = attrs - set(side_effect_ports) - set(model_ports) - set(neutral_ports)
        if unclassified:
            problems.append(f"{name}: touches unclassified port(s) {sorted(unclassified)}")
        effects = {side_effect_ports[a] for a in attrs if a in side_effect_ports}
        if effects != set(spec.effects):
            problems.append(f"{name}: declared effects {sorted(spec.effects)} but its source touches {sorted(effects)}")
        pauses = _calls_interrupt(fn)
        if pauses != spec.pauses:
            problems.append(f"{name}: declared pauses={spec.pauses} but its source {'calls' if pauses else 'does not call'} interrupt()")
        if (pauses or spec.pauses) and (effects or spec.effects):
            problems.append(f"{name}: a node that pauses must never also have a side effect (BUILD-SPEC.md 7.2)")
        uses_model = any(a in model_ports for a in attrs)
        if uses_model != spec.models:
            problems.append(f"{name}: declared models={spec.models} but its source {'touches' if uses_model else 'does not touch'} a model port")
    return problems
