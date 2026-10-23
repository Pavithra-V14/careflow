"""GET /v1/staff/messages/{message_id}, offline: no database, no network, no real token (BUILD-SPEC.md 9.4; CLAUDE.md rules 6, 8).

The connection and `read_stored_message` are fakes, so this proves the ROUTE's own contract: the body only in the 200, `Cache-Control:
no-store`, one fixed body per refusal (404, 410, 500, 503), refusal log lines with ids only, and the body never in a log or error. The audit
row and the role scope are proved against the database in tests/test_api_role_matrix.py and tests/test_api_read_message_live.py.
"""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.auth import JWKSClient
from careflow.messages import (
    MessageErased,
    MessageNotFound,
    MessageUnreadable,
    ReadMessage,
)

PROJECT = "https://example-project.supabase.co"
MID, THREAD, CLINIC, USER = (uuid.uuid4() for _ in range(4))
PRIVATE = "my-private-sentence-4417"


class State:
    error: Exception | None = None
    calls: list = []  # noqa: RUF012


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    State.error, State.calls = None, []

    async def fake_read(conn, vault, message_id, *, actor_id):
        State.calls.append((message_id, actor_id))
        if State.error:
            raise State.error
        return ReadMessage(message_id, THREAD, CLINIC, "appointments", "patient", f"hello {PRIVATE}")

    monkeypatch.setattr("careflow.app.read_stored_message", fake_read)


_VAULT = object()


def client(*, vault=_VAULT):
    async def fake_db():
        yield object()

    async def fake_claims():
        return {"sub": str(USER), "role": "authenticated"}

    app = create_app(
        Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"),
        engine=object(), vault=vault,  # type: ignore[arg-type]
    )
    app.dependency_overrides[db_conn] = fake_db
    app.dependency_overrides[verified_claims] = fake_claims
    return TestClient(app, raise_server_exceptions=False)


def get(c, mid=MID):
    return c.get(f"/v1/staff/messages/{mid}")


def test_a_read_returns_the_message_with_no_store():
    with client() as c:
        r = get(c)
    assert r.status_code == 200
    assert r.json() == {"message_id": str(MID), "thread_id": str(THREAD), "topic": "appointments", "sender_kind": "patient", "body": f"hello {PRIVATE}"}
    assert r.headers["cache-control"] == "no-store" and State.calls == [(MID, USER)]


def test_the_body_is_never_logged_even_at_debug(caplog):
    caplog.set_level(logging.DEBUG)
    with client() as c:
        assert get(c).status_code == 200
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize(
    ("error", "status", "detail", "level"),
    [
        (MessageNotFound("x"), 404, "not found", logging.WARNING),
        (MessageErased("x"), 410, "the message body was erased", logging.INFO),
        (MessageUnreadable(MID, CLINIC, "DecryptionError"), 500, "this message cannot be read", logging.WARNING),
        (MessageUnreadable(MID, CLINIC, "UnknownKeyVersionError"), 503, "this message cannot be read right now", logging.ERROR),
    ],
)
def test_every_refusal_is_one_fixed_body_and_a_log_line_of_ids_only(error, status, detail, level, caplog):
    caplog.set_level(logging.DEBUG)
    State.error = error
    with client() as c:
        r = get(c)
    assert (r.status_code, r.json()) == (status, {"detail": detail})
    lines = [x for x in caplog.records if x.name == "careflow.app" and x.levelno == level]
    assert len(lines) == 1 and str(MID) in lines[0].getMessage() and str(USER) in lines[0].getMessage()
    assert PRIVATE not in caplog.text and PRIVATE not in r.text and "hello" not in r.text


def test_a_missing_key_vault_is_a_visible_503_before_any_read(caplog):
    with client(vault=None) as c:
        r = get(c)
    assert (r.status_code, r.json()) == (503, {"detail": "message reading is unavailable: encryption keys are not configured"})
    assert State.calls == [] and any(x.levelno == logging.ERROR for x in caplog.records)


def test_a_malformed_id_is_a_422_that_reaches_nothing():
    with client() as c:
        r = c.get("/v1/staff/messages/nope")
    assert r.status_code == 422 and State.calls == []


def test_a_refusal_never_carries_the_cache_header_or_a_body():
    State.error = MessageNotFound("x")
    with client() as c:
        r = get(c)
    assert "cache-control" not in r.headers and "body" not in r.json()


def test_the_read_needs_no_idempotency_key():
    with client() as c:
        assert get(c).status_code == 200
