"""docs/config-reference.md lists every setting and secret NAME the code reads, and nothing the code no longer reads. Names only: no value is read, printed or compared (rule: never print a secret)."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "config-reference.md").read_text(encoding="utf-8")
NAME = re.compile(r"(?:CAREFLOW_|FEATURE_|DATABASE_URL_|SUPABASE_|GEMINI_API_KEY|OPENROUTER_API_KEY|SARVAM_API_KEY|CORS_ALLOWED_ORIGINS)[A-Z0-9_]*")
SOURCES = [*sorted((ROOT / "careflow").rglob("*.py")), *sorted((ROOT / "scripts").glob("*.py"))]


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def names_read_by_code() -> set[str]:
    found: set[str] = set()
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and NAME.fullmatch(node.value):
                found.add(node.value)
    from careflow.app import Settings

    for field, info in Settings.model_fields.items():
        alias = getattr(info.validation_alias, "choices", None)
        found.add(next((c for c in alias if c.isupper()), field.upper()) if alias else field.upper())
    from pydantic_settings import BaseSettings

    from careflow import patient_view

    for obj in vars(patient_view).values():  # settings classes that read their names through an env_prefix
        if isinstance(obj, type) and issubclass(obj, BaseSettings) and obj is not BaseSettings:
            prefix = obj.model_config.get("env_prefix", "").upper()
            if prefix:
                found.update(prefix + field.upper() for field in obj.model_fields)
    found.update(NAME.findall((ROOT / ".env.example").read_text(encoding="utf-8").replace("<", " ")))
    return {n for n in found if len(n) > 5}


def names_in_doc() -> set[str]:
    return {m for token in re.findall(r"`([^`]+)`", DOC) for m in NAME.findall(token)}


def test_every_name_the_code_reads_is_in_the_reference():
    missing = sorted(n for n in names_read_by_code() if n not in DOC)
    assert not missing, f"read in code but missing from docs/config-reference.md: {missing}"


def test_the_reference_lists_nothing_the_code_no_longer_reads():
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in SOURCES) + (ROOT / ".env.example").read_text(encoding="utf-8")
    stale = sorted(n for n in names_in_doc() if n not in corpus)
    assert not stale, f"in docs/config-reference.md but not in the code: {stale}"


def test_the_reference_carries_no_value_shaped_secret():
    assert not re.search(r"sb_secret_[A-Za-z0-9]|eyJ[A-Za-z0-9_-]{20,}|postgres(ql)?://\S+:\S+@", DOC)
