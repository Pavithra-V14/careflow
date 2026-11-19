"""The unwired broker queue adapter (spec 3, 7.4): it has the same four methods as QueuePort, refuses every call in fixed words, and nothing wires it."""

import inspect
import re
import uuid
from pathlib import Path

import pytest

from careflow.queue import PostgresQueue, QueueError, QueuePort
from careflow.queue_broker_stub import NOT_WIRED, BrokerQueueStub

ROOT = Path(__file__).resolve().parent.parent
PORT_METHODS = ("enqueue", "claim", "acknowledge", "dead_letter")


def test_it_has_exactly_the_port_methods_with_the_same_parameters():
    for name in PORT_METHODS:
        mine, real = inspect.signature(getattr(BrokerQueueStub, name)), inspect.signature(getattr(PostgresQueue, name))
        assert list(mine.parameters) == list(real.parameters), name
        assert inspect.iscoroutinefunction(getattr(BrokerQueueStub, name))
    assert set(PORT_METHODS) <= set(dir(QueuePort))


@pytest.mark.asyncio
async def test_every_call_refuses_in_fixed_words_and_never_pretends_to_queue():
    q = BrokerQueueStub()
    calls = [
        q.enqueue(None, uuid.uuid4(), "test", "t", {}, "k"),  # type: ignore[arg-type]
        q.claim(None, "w", 1.0),  # type: ignore[arg-type]
        q.acknowledge(None, uuid.uuid4(), worker_id="w"),  # type: ignore[arg-type]
        q.dead_letter(None, uuid.uuid4(), "e", worker_id="w"),  # type: ignore[arg-type]
    ]
    for call in calls:
        with pytest.raises(QueueError, match=re.escape(NOT_WIRED)):
            await call


def test_nothing_in_the_application_imports_it_and_it_imports_no_broker_library():
    for path in (ROOT / "careflow").glob("*.py"):
        if path.name != "queue_broker_stub.py":
            assert not re.search(r"^\s*(import|from)\s+careflow(\.queue_broker_stub|\s+import\s+queue_broker_stub)", path.read_text(encoding="utf-8"), re.MULTILINE), path.name
    code = (ROOT / "careflow" / "queue_broker_stub.py").read_text(encoding="utf-8").split('"""', 2)[2]
    assert not re.search(r"^\s*(import|from)\s+(redis|boto3|aioboto3|aiobotocore|kombu|celery)\b", code, re.MULTILINE)
