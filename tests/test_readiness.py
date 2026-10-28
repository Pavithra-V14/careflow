"""Readiness: the clinical profile refuses to report ready if a required role has no phi_approved candidate (BUILD-SPEC.md §2). Offline."""

import logging
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from careflow import app as app_module
from careflow.app import Settings, create_app
from careflow.auth import JWKSClient
from careflow.gateway import ModelRow
from careflow.readiness import check_readiness, parse_required_roles
from careflow.registry import RegistryError

PROJECT = "https://example-project.supabase.co"
Z = Decimal(0)


def row(role="decision", policy="phi_approved", enabled=True, provider="local", model="laya-1", priority=1):
    return ModelRow(role, provider, model, policy, priority, Z, Z, Z, Z, unit="audio_tokens" if role == "voice_live" else "text_tokens", enabled=enabled)


# ---- the rule ----------------------------------------------------------------------------------------------------------------------------


def test_demo_is_ready_with_an_empty_registry_and_no_required_roles():
    assert check_readiness("demo", [], None).ready
    assert check_readiness("demo", None, None).ready


def test_clinical_is_ready_only_when_every_required_role_has_an_enabled_phi_approved_route():
    reg = [row("decision"), row("llm_large", policy="synthetic_only"), row("vlm", enabled=False)]
    assert check_readiness("clinical", reg, ["decision"], malware_scanner_configured=True).ready
    got = check_readiness("clinical", reg, ["decision", "llm_large", "vlm", "embed"])
    assert not got.ready and got.missing_roles == ("llm_large", "vlm", "embed")  # synthetic_only, disabled and absent all count as missing


def test_unknown_is_never_ready():
    for profile, reg, required in (
        ("clinical", [row()], None),  # the required roles are not configured
        ("clinical", [row()], []),  # an empty list would be vacuous: also not configured
        ("clinical", None, ["decision"]),  # the registry could not be read
        ("clinical", [row()], ["not_a_role"]),  # an unknown role name
        ("production", [row()], ["decision"]),  # an unrecognised profile is treated as clinical...
    ):
        got = check_readiness(profile, reg, required, malware_scanner_configured=True)
        if profile == "production":
            assert got.ready and got.profile == "clinical" and "unrecognised profile" in got.reasons[0]  # ...and can still be ready on the merits
        else:
            assert not got.ready, (profile, reg, required)
    assert not check_readiness("production", [row("decision", policy="synthetic_only")], ["decision"]).ready


def test_a_phi_approved_row_with_no_wired_adapter_does_not_count():
    reg = [row("decision")]
    assert not check_readiness("clinical", reg, ["decision"], wired=set()).ready
    assert check_readiness("clinical", reg, ["decision"], wired={("local", "laya-1")}, malware_scanner_configured=True).ready


def test_a_route_that_is_phi_approved_but_for_a_different_role_does_not_satisfy_this_one():
    assert not check_readiness("clinical", [row("embed")], ["decision"]).ready


def test_the_required_roles_setting_is_parsed_and_unset_stays_unset():
    assert parse_required_roles(None) is None
    assert parse_required_roles("   ") is None
    assert parse_required_roles(" decision, embed ,decision") == ("decision", "embed")
    assert parse_required_roles(",") is None


# ---- the endpoint -------------------------------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("CAREFLOW_REQUIRED_ROLES", "CAREFLOW_WORKER_ACCOUNT_ID", "CAREFLOW_RUN_WORKER"):
        monkeypatch.delenv(name, raising=False)


def client(monkeypatch, *, profile, rows=None, error=None, **settings):
    monkeypatch.setenv("CAREFLOW_PROFILE", profile)

    async def fake_load(engine, account, *, origins):
        assert origins == ("live",)
        if error:
            raise error
        return rows

    monkeypatch.setattr(app_module, "load_registry", fake_load)
    app = create_app(
        Settings(supabase_url=PROJECT, database_url_app="postgresql://unused", **settings),
        jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(),  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_the_endpoint_answers_only_a_boolean_and_demo_is_ready(monkeypatch):
    with client(monkeypatch, profile="demo") as c:
        r = c.get("/readyz")
    assert (r.status_code, r.json()) == (200, {"ready": True})


def test_a_clinical_deployment_with_nothing_configured_is_503_and_logs_why(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    with client(monkeypatch, profile="clinical") as c:
        r = c.get("/readyz")
    assert (r.status_code, r.json()) == (503, {"ready": False})
    assert "required roles are not configured" in caplog.text


def test_an_unset_profile_is_clinical_so_it_is_not_ready_either(monkeypatch):
    monkeypatch.delenv("CAREFLOW_PROFILE", raising=False)
    monkeypatch.setattr(app_module, "load_registry", lambda *a, **k: None)
    app = create_app(
        Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"),
        jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(),  # type: ignore[arg-type]
    )
    with TestClient(app) as c:
        assert c.get("/readyz").status_code == 503


def test_a_clinical_deployment_needs_the_worker_account_to_read_the_registry(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    with client(monkeypatch, profile="clinical", rows=[row()], required_roles="decision") as c:
        r = c.get("/readyz")
    assert r.status_code == 503 and "CAREFLOW_WORKER_ACCOUNT_ID" in caplog.text


def test_with_a_readable_registry_but_no_wired_adapter_a_clinical_deployment_is_still_not_ready(monkeypatch):
    with client(monkeypatch, profile="clinical", rows=[row()], required_roles="decision", worker_account_id=uuid.uuid4()) as c:
        assert c.get("/readyz").status_code == 503  # nothing is wired yet: honest, not a defect


def test_a_registry_that_cannot_be_read_is_not_ready_and_the_error_is_logged_by_class_only(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    err = OperationalError("select SENTINEL-SQL-6641", {}, Exception("SENTINEL-DB-ERROR"))
    with client(monkeypatch, profile="clinical", error=err, required_roles="decision", worker_account_id=uuid.uuid4()) as c:
        assert c.get("/readyz").status_code == 503
    assert "OperationalError" in caplog.text and "SENTINEL" not in caplog.text


def test_an_invalid_registry_row_is_not_ready(monkeypatch, caplog):
    with client(monkeypatch, profile="clinical", error=RegistryError("registry row x is not valid"), required_roles="decision",
                worker_account_id=uuid.uuid4()) as c:
        assert c.get("/readyz").status_code == 503


def test_clinical_is_not_ready_without_a_configured_malware_scanner_whatever_the_registry_says():
    reg = [row("decision")]
    got = check_readiness("clinical", reg, ["decision"])  # the scanner flag has no permissive default
    assert not got.ready and got.reasons == ("no malware scanner is configured for uploads",)
    assert not check_readiness("clinical", reg, ["decision"], malware_scanner_configured=False).ready
    assert not check_readiness("production", reg, ["decision"]).ready  # an unrecognised profile counts as clinical
    assert check_readiness("demo", None, None).ready  # demo is unaffected: uploads there are synthetic
