"""Structural guarantees of careflow/queue.py. Pure: reads the source, touches no database."""

import ast
import re
from pathlib import Path

SOURCE = (Path(__file__).resolve().parent.parent / "careflow" / "queue.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
FORBIDDEN_IMPORT_ROOTS = {
    "httpx", "requests", "aiohttp", "urllib", "smtplib", "socket", "subprocess", "langgraph", "langchain",
    "twilio", "sendgrid", "redis", "celery", "boto3",
}


def _imports() -> set[str]:
    roots = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _sql() -> list[str]:
    strings = [n.value for n in ast.walk(TREE) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return [t for t in strings if re.match(r"\s*(select|insert|update|delete|with)\b", t, flags=re.IGNORECASE)]


def test_the_queue_makes_no_network_or_messaging_call():
    assert _imports() & FORBIDDEN_IMPORT_ROOTS == set()


def test_every_statement_stays_inside_the_ops_schema():
    sql = _sql()
    assert len(sql) >= 8
    schemas = set()
    for statement in sql:
        schemas |= set(re.findall(r"\b(?:from|into|update|join)\s+([a-z_]+)\.[a-z_]+", statement, flags=re.IGNORECASE))
    assert schemas == {"ops"}, schemas


def test_nothing_ever_deletes_a_row():
    assert not [s for s in _sql() if re.match(r"\s*delete\b", s, flags=re.IGNORECASE)]


def test_claim_uses_skip_locked_and_the_wall_clock():
    claim = next(s for s in _sql() if "with next as" in s)
    assert "for update skip locked" in claim.lower()
    assert "clock_timestamp()" in claim and "now()" not in claim


def test_the_ceiling_is_five_and_matches_the_spec():
    from careflow import queue

    assert queue.MAX_ATTEMPTS == 5  # BUILD-SPEC.md §7.3


def test_the_port_has_exactly_the_four_spec_operations():
    from careflow.queue import PostgresQueue, QueuePort

    ops = {n for n in vars(QueuePort) if not n.startswith("_")}
    assert ops == {"enqueue", "claim", "acknowledge", "dead_letter"}
    assert ops <= {n for n in vars(PostgresQueue)}


def test_every_write_method_requires_an_idempotency_key_or_a_claim_owner():
    wanted = {"enqueue": "idempotency_key", "record_event": "idempotency_key", "acknowledge": "worker_id", "dead_letter": "worker_id"}
    seen = {}
    for node in ast.walk(TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in wanted:
            seen[node.name] = {a.arg for a in node.args.args + node.args.kwonlyargs}
    for name, arg in wanted.items():
        assert arg in seen[name], name
