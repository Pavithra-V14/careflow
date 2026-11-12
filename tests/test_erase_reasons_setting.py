"""The erasure reasons list (the required setting CAREFLOW_ERASE_REASONS): unset means no list (the request route and the new reasons route answer 503), the demo pack supplies a labelled placeholder
ONLY under demo + synthetic_only, and an explicit setting always wins. Offline: the app state after startup."""

import pytest
from fastapi.testclient import TestClient

from careflow.app import Settings, create_app
from careflow.auth import JWKSClient

PROJECT = "https://example-project.supabase.co"


def _state(monkeypatch, env, **settings):
    for name in ("CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY", "CAREFLOW_DEMO_PHASE4", "CAREFLOW_ERASE_REASONS", "CAREFLOW_REQUIRED_ROLES", "CAREFLOW_WORKER_ACCOUNT_ID", "CAREFLOW_RUN_WORKER"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused", **settings), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object())  # type: ignore[arg-type]
    with TestClient(app):
        return app.state.erase_reasons


def test_unset_means_no_list(monkeypatch):
    assert _state(monkeypatch, {"CAREFLOW_PROFILE": "demo"}) is None


def test_the_demo_pack_supplies_a_placeholder_only_under_demo_and_synthetic_only(monkeypatch):
    demo = {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only", "CAREFLOW_DEMO_PHASE4": "1"}
    assert _state(monkeypatch, demo) == frozenset({"demo_reason"})
    with pytest.raises(Exception, match="refusing to start"):
        _state(monkeypatch, {"CAREFLOW_PROFILE": "clinical", "CAREFLOW_DATA_POLICY": "synthetic_only", "CAREFLOW_DEMO_PHASE4": "1"})


def test_an_explicit_setting_wins_over_the_placeholder(monkeypatch):
    demo = {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only", "CAREFLOW_DEMO_PHASE4": "1"}
    assert _state(monkeypatch, demo, erase_reasons="entered_in_error, duplicate_record") == frozenset({"entered_in_error", "duplicate_record"})
