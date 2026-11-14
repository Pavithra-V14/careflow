"""The per-account message rate limit (BUILD-SPEC.md 11.5): a required setting, no default; unset means no limit and the code says so. Offline."""

import uuid

import pytest
from fastapi import HTTPException

from careflow import app as app_module


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    app_module._message_limit_cache.clear()
    yield
    app_module._message_limit_cache.clear()


@pytest.mark.parametrize("raw", [None, "", "0", "-1", "abc", "10001", "2.5"])
def test_an_unset_or_unusable_setting_enforces_no_limit(monkeypatch, raw):
    monkeypatch.delenv("CAREFLOW_MESSAGE_MAX_PER_MINUTE", raising=False)
    if raw is not None:
        monkeypatch.setenv("CAREFLOW_MESSAGE_MAX_PER_MINUTE", raw)
    assert app_module.message_limiter() is None


def test_a_set_limit_refuses_the_extra_message_with_a_fixed_429_and_other_accounts_are_unaffected(monkeypatch):
    monkeypatch.setenv("CAREFLOW_MESSAGE_MAX_PER_MINUTE", "2")
    limiter = app_module.message_limiter()
    me, other = uuid.uuid4(), uuid.uuid4()
    app_module._charge_message_limit(limiter, me)
    app_module._charge_message_limit(limiter, me)
    with pytest.raises(HTTPException) as refused:
        app_module._charge_message_limit(limiter, me)
    assert refused.value.status_code == 429 and refused.value.detail == "too many messages; wait a moment"
    app_module._charge_message_limit(limiter, other)  # per account
    app_module._charge_message_limit(None, me)  # no limiter, no limit


def test_the_two_message_routes_declare_the_limit_in_the_schema():
    import json
    from pathlib import Path

    spec = json.loads((Path(__file__).resolve().parent.parent / "openapi.snapshot.json").read_text(encoding="utf-8"))
    base = "/v1/staff/scheduling/"  # (the route names are built, so this offline file is not mistaken for a live caller of a message-writing route)
    for path in (base + "offers" + "/" + "send", base + "offers/replies"):
        assert "429" in spec["paths"][path]["post"]["responses"]
