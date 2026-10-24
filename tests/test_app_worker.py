"""The in-process worker is OFF by default and starts only when asked for (careflow/app.py lifespan; BUILD-SPEC.md 7.4). Offline."""

import asyncio
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from careflow import app as app_module
from careflow.app import Settings, create_app
from careflow.auth import JWKSClient
from careflow.tick import Poker

PROJECT = "https://example-project.supabase.co"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("CAREFLOW_RUN_WORKER", "CAREFLOW_WORKER_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)


def client(monkeypatch, started, **settings):
    async def fake_loop(engine, queue, handlers, poker, *, worker_account_id, worker_id, **kw):
        started.append((worker_account_id, handlers, type(poker).__name__))
        try:
            await asyncio.sleep(3600)
        finally:
            started.append("stopped")

    monkeypatch.setattr(app_module, "worker_loop", fake_loop)
    app = create_app(
        Settings(supabase_url=PROJECT, database_url_app="postgresql://unused", **settings),
        jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(),  # type: ignore[arg-type]
    )
    return TestClient(app, raise_server_exceptions=False), app


def test_by_default_no_worker_starts_but_the_poker_exists(monkeypatch):
    started = []
    c, app = client(monkeypatch, started)
    with c:
        assert app.state.worker is None and isinstance(app.state.poker, Poker)
    assert started == []


def test_asked_for_with_an_account_the_worker_starts_and_is_stopped_on_shutdown(monkeypatch):
    started, account = [], uuid.uuid4()
    c, app = client(monkeypatch, started, run_worker=True, worker_account_id=account)
    with c:
        assert app.state.worker is not None
    assert started[0][0] == account and started[0][2] == "Poker" and started[-1] == "stopped"


def test_asked_for_without_an_account_it_does_not_start_and_says_so(monkeypatch, caplog):
    started = []
    caplog.set_level(logging.ERROR)
    c, app = client(monkeypatch, started, run_worker=True)
    with c:
        assert app.state.worker is None
    assert started == [] and "WORKER_ACCOUNT_ID" in caplog.text
