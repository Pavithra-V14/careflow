"""Gate: no test may build a settings object that reads a developer's `.env`. An offline test that does passes on one machine and fails on another (the worker tests failed
because a local `.env` set the worker variables). Every `...Settings(...)` call in tests/ must say `_env_file=None`, and the settings classes the app builds on its own
(a route's default dependency) are isolated for offline modules by the fixture in tests/conftest.py."""

import ast
import pathlib

TESTS = pathlib.Path(__file__).parent


def _unisolated_calls(folder: pathlib.Path = TESTS) -> list[str]:
    found = []
    for path in sorted(folder.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name.endswith("Settings") and not any(k.arg == "_env_file" for k in node.keywords):
                found.append(f"{path.name}:{node.lineno} {name}(...)")
    return found


def test_every_settings_construction_in_the_tests_isolates_the_env_file():
    assert _unisolated_calls() == []


def test_the_gate_sees_an_unisolated_call(tmp_path):
    """The scan itself works: a file with a bare `FooSettings(x=1)` is reported, one with `_env_file=None` is not."""
    source = "FooSettings(x=1)\nm.BarSettings(y=2)\nOkSettings(_env_file=None, z=3)\n"
    (tmp_path / "bad.py").write_text(source, encoding="utf-8")
    assert _unisolated_calls(tmp_path) == ["bad.py:1 FooSettings(...)", "bad.py:2 BarSettings(...)"]
