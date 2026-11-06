"""Every staff route demands a verified second factor (aal2 + a well-formed TOTP amr); ON by default; off only in demo + synthetic_only (careflow/auth.py mfa_failure, careflow/app.py). Offline."""

import inspect
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from careflow import app as app_module
from careflow.app import MfaSettingRefused, Settings, create_app
from careflow.auth import JWKSClient, mfa_failure
from tests.test_api_role_matrix import ISSUER, SIGNING, SIGNING_JWK

NOW = 1_800_000_000.0
PROJECT = "https://example-project.supabase.co"
DEMO = {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}


def claims(**over):
    base = {"aal": "aal2", "amr": [{"method": "totp", "timestamp": int(NOW) - 600}]}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


@pytest.mark.parametrize(
    ("c", "reason"),
    [
        (claims(), None),
        (claims(amr=[{"method": "totp", "timestamp": int(NOW) - 10**6}]), None),  # no recency rule here: that is step-up's job
        (claims(aal=None), "aal_not_aal2"),
        (claims(aal="aal1"), "aal_not_aal2"),
        (claims(aal="AAL2"), "aal_not_aal2"),
        (claims(amr=None), "amr_missing"),
        (claims(amr=[]), "amr_missing"),
        (claims(amr=["totp"]), "amr_malformed"),
        (claims(amr=[{"method": "totp", "timestamp": "1"}]), "amr_malformed"),
        (claims(amr=[{"method": "totp", "timestamp": True}]), "amr_malformed"),
        (claims(amr=[{"method": "totp", "timestamp": 1, "extra": 1}]), "amr_malformed"),
        (claims(amr=[{"method": "password", "timestamp": int(NOW) - 5}]), "no_totp_entry"),
        (claims(amr=[{"method": "totp", "timestamp": int(NOW) + 100}]), "timestamp_future"),
    ],
)
def test_mfa_failure_reads_only_the_documented_shape_and_fails_closed(c, reason):
    assert mfa_failure(c, now=NOW) == reason


def test_the_reason_never_echoes_a_claim_value():
    sentinel = "SENTINEL-MFA-4471"
    for c in (claims(aal=sentinel), claims(amr=[{"method": sentinel, "timestamp": int(NOW)}]), claims(amr=[sentinel])):
        assert sentinel not in (mfa_failure(c, now=NOW) or "")


def test_the_setting_is_on_by_default():
    assert Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://u:p@h/d").require_aal2 is True


def _settings(**over):
    return Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://u:p@h/d", **over)


@pytest.mark.parametrize("env", [{}, {"CAREFLOW_PROFILE": "clinical", "CAREFLOW_DATA_POLICY": "synthetic_only"}, {"CAREFLOW_PROFILE": "demo"}, {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "phi_approved"}])
def test_turning_it_off_outside_demo_with_synthetic_data_refuses_to_build_the_app(monkeypatch, env):
    for name in ("CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY", "CAREFLOW_REQUIRE_AAL2"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(MfaSettingRefused):
        create_app(_settings(require_aal2=False))
    monkeypatch.setenv("CAREFLOW_REQUIRE_AAL2", "false")  # the same through the environment
    with pytest.raises(MfaSettingRefused):
        create_app()


def test_the_lifespan_also_refuses_a_value_that_arrives_after_the_app_was_built(monkeypatch):
    """A setting read from .env at startup (not visible when the app is built from the process environment) is caught when the lifespan starts."""
    for name in ("CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY", "CAREFLOW_REQUIRE_AAL2"):
        monkeypatch.delenv(name, raising=False)
    app = create_app(jwks=_jwks(), engine=object())  # type: ignore[arg-type]  # built from the environment: nothing set, so it builds
    monkeypatch.setattr(app_module, "Settings", lambda: _settings(require_aal2=False))  # as if .env turned it off
    with pytest.raises(MfaSettingRefused), TestClient(app):
        pass


def test_turning_it_off_is_allowed_in_demo_with_synthetic_data(monkeypatch):
    for name, value in DEMO.items():
        monkeypatch.setenv(name, value)
    app = create_app(_settings(require_aal2=False), jwks=_jwks(), engine=object())  # type: ignore[arg-type]
    assert app is not None


def _jwks() -> JWKSClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [SIGNING_JWK]}))
    return JWKSClient(ISSUER + "/.well-known/jwks.json", http=httpx.AsyncClient(transport=transport))


def _token(**over):
    payload = {"sub": "00000000-0000-4000-8000-000000000001", "iss": ISSUER, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 600, **over}
    return jwt.encode({k: v for k, v in payload.items() if v is not None}, SIGNING, algorithm="ES256", headers={"kid": "matrix-key"})


def test_a_password_only_token_gets_the_one_fixed_401_on_a_staff_route_before_any_database_work(monkeypatch):
    for name in ("CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY", "CAREFLOW_REQUIRE_AAL2"):
        monkeypatch.delenv(name, raising=False)
    touched = []

    class Engine:  # any use means the check did not run first
        def connect(self):
            touched.append(1)
            raise AssertionError("the database was reached")

    app = create_app(_settings(), jwks=_jwks(), engine=Engine())  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as c:
        for over in ({}, {"aal": "aal1"}, {"aal": "aal2"}, {"aal": "aal2", "amr": [{"method": "password", "timestamp": 1}]}):
            r = c.get("/v1/staff/scheduling/slots", headers={"Authorization": f"Bearer {_token(**over)}"})
            assert (r.status_code, r.json(), r.headers.get("www-authenticate")) == (401, {"detail": "multi-factor sign-in required"}, "mfa"), over
    assert touched == []


def test_with_the_setting_off_in_demo_a_password_only_token_reaches_the_database(monkeypatch):
    for name, value in DEMO.items():
        monkeypatch.setenv(name, value)
    reached = []

    class Engine:
        def connect(self):
            reached.append(1)
            raise RuntimeError("stop here: the check was skipped, as configured")

    app = create_app(_settings(require_aal2=False), jwks=_jwks(), engine=Engine())  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/v1/staff/scheduling/slots", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code != 401 and reached == [1]


def _api_routes(routes):
    """Every route with a dependency tree, however deeply the routers were mounted."""
    for route in routes:
        if hasattr(route, "dependant"):
            yield route
        original = getattr(route, "original_router", None)  # an included router (FastAPI keeps it lazily)
        yield from _api_routes(original.routes if original is not None else getattr(route, "routes", None) or [])


def _uses_db_conn(dependant) -> bool:
    return any(d.call is app_module.db_conn or _uses_db_conn(d) for d in dependant.dependencies)


def test_every_v1_route_except_me_goes_through_the_connection_dependency_that_enforces_it():
    """Structural: the check lives in db_conn, so a route that does not depend on it would silently skip the rule."""
    found = {route.path: route for route in _api_routes(create_app(_settings()).routes) if route.path.startswith("/v1")}
    assert "/v1/iam/me" in found and len(found) > 30, sorted(found)
    portal = {path for path in found if path.startswith("/v1/portal/")}  # PATIENT-account routes: staff multi-factor is a staff rule (patients sign in by one-time code, BUILD-SPEC.md 17.2, 18.2)
    missing = [path for path, route in found.items() if path != "/v1/iam/me" and path not in portal and not _uses_db_conn(route.dependant)]
    assert not missing, f"routes that skip the second-factor rule: {missing}"
    assert not _uses_db_conn(found["/v1/iam/me"].dependant)  # the one exemption for staff routes
    for path in portal:  # the portal exemption is narrow: each such route must refuse anything but an active patient account before it does anything else
        assert not _uses_db_conn(found[path].dependant)
        assert "_patient_account(" in inspect.getsource(found[path].endpoint), path
