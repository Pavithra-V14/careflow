"""The structural checker for agent graphs (BUILD-SPEC.md §4 and §7.2). Used by tests/test_front_door_structure.py as a hard CI gate, and reusable by every
later graph.

It reads each node's SOURCE (not its behaviour) and compares it with the node's declaration. It FAILS CLOSED: anything it cannot classify is a violation.

  * the side-effect ports a node touches (`deps.<name>` where <name> is a side-effect port) must equal its declared `effects`;
  * every `deps.<name>` must be classified (side-effect, model or neutral): an unclassified port is a violation;
  * `deps` may appear ONLY as `deps.<name>`: an alias (`d = deps`), `getattr(deps, ...)`, `vars(deps)`, or passing `deps` to a helper is a violation, because the
    checker could no longer see which port is used. Likewise a closure variable other than `deps` (a port bound outside the node body) is a violation;
  * a node that reaches `interrupt` pauses, however it is spelled (`import ... as ask`, a helper in this package that calls it). A node must not name
    `Command`, `GraphInterrupt` or `NodeInterrupt` (routing or pausing by another door);
  * a node that pauses must TOUCH NO PORT AT ALL (not even the neutral `session`, which opens a database transaction): it may only ask and validate;
  * no node may both pause and have a side effect (a paused node re-executes from its first line on resume);
  * a node that touches a model port must be declared `models`, and the reverse.

`router_violations` holds the routing functions to the same standard: they may read only the state: no `deps`, no `await`, no call except `state.get`.
Returns lists of violation strings (empty means the graph passes). It never executes a node.
"""

import ast
import inspect
import textwrap
from collections.abc import Callable, Mapping
from typing import Any

from langgraph.types import interrupt as _langgraph_interrupt

_FORBIDDEN_NAMES = {"Command", "GraphInterrupt", "NodeInterrupt", "getattr", "setattr", "vars", "globals", "locals", "eval", "exec", "__import__"}
_MAX_DEPTH = 4


def _tree(fn: Callable[..., Any]) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def _deps_findings(fn: Callable[..., Any]) -> tuple[set[str], list[str]]:
    """The `deps.<name>` attributes used, and the bare uses of `deps` (anything that is not the object of an attribute access)."""
    tree = _tree(fn)
    attrs: set[str] = set()
    attribute_objects: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "deps":
            attrs.add(node.attr)
            attribute_objects.add(id(node.value))
    bare = [f"a bare reference to deps at line {n.lineno}" for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "deps" and id(n) not in attribute_objects]
    return attrs, bare


def _reaches_interrupt(fn: Callable[..., Any], depth: int = 0, seen: set[int] | None = None) -> bool:
    seen = seen if seen is not None else set()
    if id(fn) in seen or depth > _MAX_DEPTH:
        return False
    seen.add(id(fn))
    tree = _tree(fn)
    # `from langgraph.types import interrupt as ask` inside the function: remember every local name that stands for interrupt.
    local_aliases = {
        (a.asname or a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names if a.name == "interrupt"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute) and target.attr == "interrupt":
            return True
        if isinstance(target, ast.Name):
            resolved = fn.__globals__.get(target.id)
            if target.id == "interrupt" or target.id in local_aliases or resolved is _langgraph_interrupt:
                return True
            # A helper may hide the call: follow any plain function (not LangGraph's own) that the node calls.
            if inspect.isfunction(resolved) and not resolved.__module__.startswith("langgraph") and _reaches_interrupt(resolved, depth + 1, seen):
                return True
    return False


def _forbidden_names(fn: Callable[..., Any]) -> set[str]:
    return {n.id for n in ast.walk(_tree(fn)) if isinstance(n, ast.Name) and n.id in _FORBIDDEN_NAMES} | {
        n.attr for n in ast.walk(_tree(fn)) if isinstance(n, ast.Attribute) and n.attr in _FORBIDDEN_NAMES
    }


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
        attrs, bare = _deps_findings(fn)
        problems.extend(f"{name}: {b}" for b in bare)
        closure = set(fn.__code__.co_freevars) - {"deps"}
        if closure:
            problems.append(f"{name}: uses closure variable(s) {sorted(closure)} (a port could hide there)")
        forbidden = _forbidden_names(fn)
        if forbidden:
            problems.append(f"{name}: uses {sorted(forbidden)}, which the checker cannot follow")
        unclassified = attrs - set(side_effect_ports) - set(model_ports) - set(neutral_ports)
        if unclassified:
            problems.append(f"{name}: touches unclassified port(s) {sorted(unclassified)}")
        effects = {side_effect_ports[a] for a in attrs if a in side_effect_ports}
        if effects != set(spec.effects):
            problems.append(f"{name}: declared effects {sorted(spec.effects)} but its source touches {sorted(effects)}")
        pauses = _reaches_interrupt(fn)
        if pauses != spec.pauses:
            problems.append(f"{name}: declared pauses={spec.pauses} but its source {'reaches' if pauses else 'does not reach'} interrupt()")
        if (pauses or spec.pauses) and (effects or spec.effects):
            problems.append(f"{name}: a node that pauses must never also have a side effect (BUILD-SPEC.md 7.2)")
        if (pauses or spec.pauses) and attrs:
            problems.append(f"{name}: a node that pauses may not touch any port (it touches {sorted(attrs)}); it may only ask and validate")
        uses_model = any(a in model_ports for a in attrs)
        if uses_model != spec.models:
            problems.append(f"{name}: declared models={spec.models} but its source {'touches' if uses_model else 'does not touch'} a model port")
    return problems


def router_violations(routers: Mapping[str, Callable[..., Any]]) -> list[str]:
    """A routing function may read only the state: no ports, no await, no call except state.get (no model chooses the path, BUILD-SPEC.md 7.1)."""
    problems: list[str] = []
    for name, fn in routers.items():
        tree = _tree(fn)
        if any(isinstance(n, ast.Name) and n.id == "deps" for n in ast.walk(tree)) or "deps" in fn.__code__.co_freevars:
            problems.append(f"{name}: a router touches deps")
        if any(isinstance(n, (ast.Await, ast.AsyncFunctionDef)) for n in ast.walk(tree)):
            problems.append(f"{name}: a router awaits")
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and not (isinstance(n.func, ast.Attribute) and n.func.attr == "get" and isinstance(n.func.value, ast.Name) and n.func.value.id == "state"):
                problems.append(f"{name}: a router calls something other than state.get")
                break
    return problems
