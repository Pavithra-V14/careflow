"""POST /v1/staff/messages/{message_id}/screen, offline: no database, no network, no real token (BUILD-SPEC.md §5.2, §12.1; CLAUDE.md
rules 2, 5, 7, 8). The token check and the database transaction are replaced by fakes so this proves the ROUTE's own contract: the
required X-Idempotency-Key, the exact response shape (ids and enums only), the status mapping, and the visible 503 when the key vault is
absent. The role matrix against the real database is in tests/test_api_screen_message_live.py.
"""

import base64
import os
import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from careflow import app as app_module
from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.auth import JWKSClient
from careflow.crypto import DemographicVault
from careflow.messages import (
    KeyNotLoaded,
    MessageErased,
    MessageNotFound,
    ScreenedMessage,
)
from careflow.rules import NotPermitted

PROJECT = "https://example-project.supabase.co"
PATH = "/v1/staff/messages/{}/screen"
SUB = uuid.uuid4()
SECRET_TEXT = "my unique private sentence 5521"


def _vault() -> DemographicVault:
    return DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})


async def _fake_db(claims=Depends(verified_claims)):  # like the real db_conn, it needs a verified identity first
    yield object()  # the route only hands it to screen_stored_message, which each test replaces


async def _fake_claims():
    return {"sub": str(SUB), "role": "authenticated"}


def make_client(*, vault="default", authenticated=True):
    app = create_app(
        Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"),
        jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(),  # type: ignore[arg-type]
        vault=_vault() if vault == "default" else vault,
    )
    app.dependency_overrides[db_conn] = _fake_db
    if authenticated:
        app.dependency_overrides[verified_claims] = _fake_claims
    return app


def _result(**kw):
    base = {"message_id": uuid.uuid4(), "screening_id": uuid.uuid4(), "outcome": "unassessable", "replayed": False, "patient_reply": "not_required"}
    base.update(kw)
    return ScreenedMessage(**base)


@pytest.fixture
def client(monkeypatch):
    async def fake(conn, vault, engine, message_id, *, idempotency_key, profile=None):
        return _result(message_id=message_id)

    monkeypatch.setattr(app_module, "screen_stored_message", fake)
    with TestClient(make_client()) as c:
        yield c


def headers(key="k-1"):
    return {"X-Idempotency-Key": key} if key is not None else {}


def test_a_screening_returns_exactly_ids_and_enums(client):
    mid = uuid.uuid4()
    r = client.post(PATH.format(mid), headers=headers())
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"message_id", "screening_id", "outcome", "replayed", "patient_reply"}
    assert body["message_id"] == str(mid) and body["outcome"] == "unassessable" and body["replayed"] is False


@pytest.mark.parametrize("key", [None, "", "   "])
def test_the_idempotency_key_is_required_on_this_write(client, key):
    assert client.post(PATH.format(uuid.uuid4()), headers=headers(key)).status_code == 422


def test_a_path_that_is_not_a_uuid_is_rejected(client):
    assert client.post(PATH.format("nope"), headers=headers()).status_code == 422


def test_no_token_is_401_before_anything_else():
    with TestClient(make_client(authenticated=False)) as c:
        assert c.post(PATH.format(uuid.uuid4()), headers=headers()).status_code == 401
        assert c.post(PATH.format(uuid.uuid4())).status_code == 401  # authentication comes before the missing key


def test_the_idempotency_key_reaches_the_screening(monkeypatch):
    seen = {}

    async def fake(conn, vault, engine, message_id, *, idempotency_key, profile=None):
        seen["key"] = idempotency_key
        return _result(message_id=message_id)

    monkeypatch.setattr(app_module, "screen_stored_message", fake)
    with TestClient(make_client()) as c:
        assert c.post(PATH.format(uuid.uuid4()), headers=headers("abc-123")).status_code == 200
    assert seen == {"key": "abc-123"}


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (MessageNotFound("x"), 404, "not found"),
        (NotPermitted("your role may not screen this message"), 404, "not found"),  # indistinguishable from a missing message
        (MessageErased("x"), 409, "the message body was erased"),
        (KeyNotLoaded("x"), 503, "message screening is unavailable: the encryption key is not loaded"),
    ],
)
def test_errors_map_to_clean_statuses_with_fixed_bodies(monkeypatch, error, status, detail):
    async def fake(*args, **kwargs):
        raise error

    monkeypatch.setattr(app_module, "screen_stored_message", fake)
    with TestClient(make_client()) as c:
        r = c.post(PATH.format(uuid.uuid4()), headers=headers())
    assert (r.status_code, r.json()) == (status, {"detail": detail})


def test_a_missing_message_and_a_forbidden_one_have_identical_responses(monkeypatch):
    bodies = []
    for error in (MessageNotFound("a"), NotPermitted("b")):
        async def fake(*args, _e=error, **kwargs):
            raise _e

        monkeypatch.setattr(app_module, "screen_stored_message", fake)
        with TestClient(make_client()) as c:
            r = c.post(PATH.format(uuid.uuid4()), headers=headers())
        bodies.append((r.status_code, r.text))
    assert bodies[0] == bodies[1]


def test_without_a_key_vault_the_route_answers_503_visibly_and_never_screens(monkeypatch):
    called = []

    async def fake(*args, **kwargs):
        called.append(1)

    monkeypatch.setattr(app_module, "screen_stored_message", fake)
    monkeypatch.setattr(app_module, "_load_vault", lambda: None)
    with TestClient(make_client(vault=None)) as c:
        r = c.post(PATH.format(uuid.uuid4()), headers=headers())
    assert r.status_code == 503 and "encryption keys are not configured" in r.json()["detail"] and called == []


def test_an_unexpected_failure_is_a_500_without_echoing_anything(monkeypatch):
    async def fake(*args, **kwargs):
        raise RuntimeError(SECRET_TEXT)

    monkeypatch.setattr(app_module, "screen_stored_message", fake)
    with TestClient(make_client(), raise_server_exceptions=False) as c:
        r = c.post(PATH.format(uuid.uuid4()), headers=headers())
    assert r.status_code == 500 and SECRET_TEXT not in r.text


def test_the_response_model_forbids_extra_fields_so_a_new_column_cannot_leak():
    from careflow.app import ScreenOut

    assert ScreenOut.model_config.get("extra") == "forbid"
    assert set(ScreenOut.model_fields) == {"message_id", "screening_id", "outcome", "replayed", "patient_reply"}


def test_the_route_is_in_the_openapi_snapshot_as_a_post_with_the_required_header():
    spec = create_app(Settings(supabase_url=PROJECT, database_url_app="postgresql://unused")).openapi()
    op = spec["paths"]["/v1/staff/messages/{message_id}/screen"]["post"]
    header = [p for p in op["parameters"] if p["in"] == "header"]
    assert [p["name"] for p in header] == ["X-Idempotency-Key"] and header[0]["required"] is True
