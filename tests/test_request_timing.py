"""Request timing in the access log (demo profile only): one line of method, route pattern, status and milliseconds, and NOTHING else. Offline."""

import logging
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from careflow.app import Settings, create_app
from careflow.auth import JWKSClient
from careflow.request_timing import LOGGER_NAME

PROJECT = "https://example-project.supabase.co"
LINE = re.compile(r"^timing (GET|POST) \S+ \d{3} \d+ms$")


@pytest.fixture
def lines():
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    log = logging.getLogger(LOGGER_NAME)
    handler = Grab()
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    yield records
    log.removeHandler(handler)


def _client(monkeypatch, profile):
    for name in ("CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY", "CAREFLOW_DEMO_PHASE4", "CAREFLOW_REQUIRED_ROLES", "CAREFLOW_WORKER_ACCOUNT_ID", "CAREFLOW_RUN_WORKER"):
        monkeypatch.delenv(name, raising=False)
    if profile:
        monkeypatch.setenv("CAREFLOW_PROFILE", profile)
    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object())  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


def test_the_line_has_method_pattern_status_and_milliseconds_and_nothing_else(monkeypatch, lines):
    slot, secret_query, token = uuid.uuid4(), "patient_name=Jane%20Doe&nhs=943-476-5919", "eyJhbGciOi.SECRET.token"
    with _client(monkeypatch, "demo") as client:
        client.post(f"/v1/staff/scheduling/backfills/{slot}/decide?{secret_query}", json={"action": "send", "note": "secret body text"}, headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": "secret-key-123"})
        client.get(f"/no/such/route/{slot}?{secret_query}")
    assert len(lines) >= 2
    decide = [x for x in lines if "/decide" in x]
    assert decide and decide[0].startswith("timing POST /v1/staff/scheduling/backfills/{slot_id}/decide ")
    assert any(x.startswith("timing GET <unmatched> 404 ") for x in lines)
    for line in lines:
        assert LINE.match(line), line
    joined = "\n".join(lines)
    for forbidden in (str(slot), "Jane", "943", "patient_name", "secret", "eyJ", "Bearer", "?", "="):
        assert forbidden not in joined, forbidden


def test_it_is_off_outside_the_demo_profile(monkeypatch, lines):
    for profile in ("clinical", None):
        with _client(monkeypatch, profile) as client:
            client.get("/readyz")
        assert lines == []
