"""Structural guarantees of careflow/scheduling.py. Pure: reads the source, touches no database.

These make the module's operational promises executable rather than comments:
  B. no patient notification is created by a scheduling state change (BUILD-SPEC.md §12.1)
  C. no pause/interrupt machinery and no side effect outside Postgres (CLAUDE.md rule 4)
"""

import ast
import re
from pathlib import Path

SOURCE = (Path(__file__).resolve().parent.parent / "careflow" / "scheduling.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

# Anything that could send a message, call out of the process, or pause an orchestration graph.
FORBIDDEN_IMPORT_ROOTS = {
    "httpx", "requests", "aiohttp", "urllib", "smtplib", "email", "socket", "subprocess", "asyncio",
    "langgraph", "langchain", "twilio", "sendgrid", "celery", "redis", "careflow",
}


def _imported_roots() -> set[str]:
    roots = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_module_imports_no_messaging_network_or_orchestration_library():
    assert _imported_roots() & FORBIDDEN_IMPORT_ROOTS == set()


def test_every_sql_statement_touches_only_the_sched_schema():
    """No write to msg, ops (outbox), agents or any other schema: a state change cannot enqueue a message."""
    strings = [n.value for n in ast.walk(TREE) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    sql = [t for t in strings if re.match(r"\s*(select|insert|update|delete)\b", t, flags=re.IGNORECASE)]
    assert len(sql) >= 15  # the scan really is looking at the module's statements
    schemas = set()
    for statement in sql:
        schemas |= set(re.findall(r"\b(?:from|into|update|join)\s+([a-z_]+)\.[a-z_]+", statement, flags=re.IGNORECASE))
    assert schemas == {"sched"}, schemas


def test_no_interrupt_or_wait_primitives():
    banned = {"interrupt", "sleep", "wait_for", "create_task", "gather"}
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(TREE) if isinstance(n, ast.Call)}
    assert called & banned == set()


def test_every_public_write_takes_an_idempotency_key():
    keyed = {"hold_slot", "book_appointment", "cancel_appointment", "join_waitlist"}
    seen = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in keyed:
            args = {a.arg for a in node.args.args + node.args.kwonlyargs}
            assert "idempotency_key" in args, node.name
            seen.add(node.name)
    assert seen == keyed
