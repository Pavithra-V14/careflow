"""A name used twice at module level in careflow/app.py is a bug waiting for a call site (2026-10-30: a second `HoldOut` for the referral queue silently replaced the scheduling hold response model
that the hold route builds by name, so every hold answered 500, and OpenAPI would have carried two schemas under one name). Offline."""

import ast
import collections
from pathlib import Path

from careflow.app import create_app

SOURCE = (Path(__file__).resolve().parent.parent / "careflow" / "app.py").read_text(encoding="utf-8")


def duplicates(kinds) -> list[str]:
    names = [n.name for n in ast.parse(SOURCE).body if isinstance(n, kinds)]
    return sorted(k for k, v in collections.Counter(names).items() if v > 1)


def test_no_class_in_app_py_is_defined_twice():
    assert duplicates(ast.ClassDef) == []


def test_no_module_level_function_in_app_py_is_defined_twice():
    assert duplicates((ast.FunctionDef, ast.AsyncFunctionDef)) == []


def test_every_openapi_schema_has_a_plain_unique_name():
    schemas = create_app().openapi()["components"]["schemas"]
    assert not [name for name in schemas if "__" in name], "a schema name was disambiguated by pydantic: two models share a name"
