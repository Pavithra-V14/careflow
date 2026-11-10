"""The unauthenticated clinic route and the consent labels (migration 0070). Offline: the database is replaced by a fake engine; the real function is in tests/test_public_clinic_db_live.py."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from careflow import consent_settings
from careflow.app import Settings, create_app
from careflow.auth import JWKSClient

PROJECT = "https://example-project.supabase.co"


class _Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Engine:
    def __init__(self, row):
        self.row = row

    @asynccontextmanager
    async def connect(self):
        row = self.row

        class _Conn:
            async def execute(self, *args, **kwargs):
                return _Result(row)

        yield _Conn()


def _client(row):
    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object())  # type: ignore[arg-type]
    client = TestClient(app)
    client.row = row  # type: ignore[attr-defined]
    return client


def test_the_public_clinic_is_served_without_a_token():
    cid = uuid.uuid4()
    with _client(SimpleNamespace(clinic_id=cid, name="Demo Clinic")) as c:
        c.app.state.engine = _Engine(c.row)  # type: ignore[attr-defined]  # after startup, which sets the real one
        r = c.get("/public/clinic")
    assert (r.status_code, r.json()) == (200, {"clinic_id": str(cid), "name": "Demo Clinic"})


def test_no_published_clinic_is_a_404_not_an_empty_answer():
    with _client(None) as c:
        c.app.state.engine = _Engine(None)  # type: ignore[attr-defined]
        r = c.get("/public/clinic")
    assert r.status_code == 404


def test_the_consent_labels_setting_takes_only_known_purposes_and_short_text():
    ok = '{"care_delivery": "Care", "ai_processing": " AI help "}'
    assert consent_settings.parse_labels(ok) == {"care_delivery": "Care", "ai_processing": "AI help"}
    assert consent_settings.parse_labels(None) == {} and consent_settings.parse_labels("  ") == {} and consent_settings.parse_labels("not json") == {}
    assert consent_settings.parse_labels('{"made_up": "x"}') == {}
    assert consent_settings.parse_labels('{"care_delivery": ""}') == {} and consent_settings.parse_labels('{"care_delivery": "' + "x" * 81 + '"}') == {}
