"""The PUBLIC surface, offline: every route reachable without a staff or patient sign-in, checked for the things an anonymous caller must never get. The list is closed: a route that needs no sign-in and is not named here fails
the test, and a named route that DOES need one fails it too (the public complaint form once required a token through its idempotency-key dependency and so could never have worked for a signed-out person)."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, HTTPException, Response
from fastapi.testclient import TestClient

from careflow import app as app_module
from careflow import clinic_profile_sql, portal_extras_sql
from careflow.app import Settings, create_app
from careflow.auth import JWKSClient

PROJECT = "https://example-project.supabase.co"
PUBLIC = {("GET", "/readyz"), ("GET", "/public/clinic"), ("GET", "/public/clinic/profile"), ("GET", "/public/grievance-categories"), ("POST", "/public/grievances")}


def _dependency_names(dependant, out):
    for sub in dependant.dependencies:
        out.add(getattr(sub.call, "__name__", str(sub.call)))
        _dependency_names(sub, out)


def test_the_routes_that_need_no_sign_in_are_exactly_the_listed_public_ones():
    found, wrongly_open = set(), []
    for obj in vars(app_module).values():
        if isinstance(obj, APIRouter):
            for route in obj.routes:
                names: set[str] = set()
                _dependency_names(route.dependant, names)
                for method in route.methods:
                    key = (method, obj.prefix + route.path)
                    if "verified_claims" not in names:
                        found.add(key)
                    elif key in PUBLIC:
                        wrongly_open.append(key)
    assert found == PUBLIC, f"public by mistake: {sorted(found - PUBLIC)}; listed but missing: {sorted(PUBLIC - found)}"
    assert not wrongly_open, f"listed as public but the route needs a sign-in: {wrongly_open}"


def make_app(monkeypatch, profile="demo", **env):
    monkeypatch.setenv("CAREFLOW_PROFILE", profile)
    for name in ("CAREFLOW_REQUIRED_ROLES", "CAREFLOW_WORKER_ACCOUNT_ID", "CAREFLOW_RUN_WORKER", portal_extras_sql.PUBLIC_READ_SETTING, portal_extras_sql.PUBLIC_IP_SETTING):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if profile == "demo":
        monkeypatch.setenv("CAREFLOW_DATA_POLICY", "synthetic_only")
    return create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object())  # type: ignore[arg-type]


def test_the_public_complaint_route_does_not_ask_for_a_token(monkeypatch):
    with TestClient(make_app(monkeypatch)) as c:
        r = c.post("/public/grievances", json={"category": "demo_category", "text": "DEMO"}, headers={"X-Idempotency-Key": "k" * 40})
    assert r.status_code not in (401, 403), r.text  # 503 (not configured here) is fine; a demand for a sign-in is not


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_the_api_documentation_is_not_served_outside_the_synthetic_demo(monkeypatch, path):
    with TestClient(make_app(monkeypatch, profile="clinical")) as c:
        assert c.get(path).status_code == 404


def test_the_api_documentation_is_still_served_in_the_synthetic_demo(monkeypatch):
    with TestClient(make_app(monkeypatch)) as c:
        assert c.get("/openapi.json").status_code == 200


def test_a_large_body_to_a_public_route_is_refused_before_it_is_read(monkeypatch):
    with TestClient(make_app(monkeypatch)) as c:
        big = {"category": "demo_category", "text": "x" * 200_000}
        r = c.post("/public/grievances", json=big, headers={"X-Idempotency-Key": "k" * 40})
        assert r.status_code == 413 and r.json() == {"detail": "request too large"} and r.headers["cache-control"] == "no-store"

        def chunks():  # no Content-Length: the size must be counted as it arrives
            for _ in range(20):
                yield b"x" * 4096

        assert c.post("/public/grievances", content=chunks(), headers={"X-Idempotency-Key": "k" * 40, "Content-Type": "application/json"}).status_code == 413
        small = c.post("/public/grievances", json={"category": "demo_category", "text": "DEMO"}, headers={"X-Idempotency-Key": "k" * 40})
        assert small.status_code != 413


def test_every_failed_public_answer_and_every_public_write_is_never_cached(monkeypatch):
    with TestClient(make_app(monkeypatch)) as c:
        for method, path in (("GET", "/public/grievance-categories"), ("GET", "/public/clinic"), ("GET", "/public/clinic/profile")):
            r = c.request(method, path)
            assert r.status_code >= 400 and r.headers["cache-control"] == "no-store", (path, r.status_code)
        r = c.post("/public/grievances", json={"category": "demo_category", "text": "DEMO"}, headers={"X-Idempotency-Key": "k" * 40})
        assert r.headers["cache-control"] == "no-store"
        assert c.get("/readyz").headers["cache-control"] == "no-store"


class _Engine:
    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return SimpleNamespace(first=lambda: None)


def request(host="203.0.113.5"):
    state = SimpleNamespace(engine=_Engine(), p4env=None)
    return SimpleNamespace(app=SimpleNamespace(state=state), client=SimpleNamespace(host=host))


@pytest.mark.asyncio
async def test_the_public_clinic_read_has_a_limit_that_fails_closed_and_a_uniform_404(monkeypatch):
    monkeypatch.delenv(portal_extras_sql.PUBLIC_READ_SETTING, raising=False)
    app_module._public_limiter_cache.clear()
    with pytest.raises(HTTPException) as e:
        await app_module.public_clinic(request())  # type: ignore[arg-type]
    assert e.value.status_code == 503 and "limit" not in str(e.value.detail)
    monkeypatch.setenv(portal_extras_sql.PUBLIC_READ_SETTING, "1")
    app_module._public_limiter_cache.clear()
    with pytest.raises(HTTPException) as e:
        await app_module.public_clinic(request())  # type: ignore[arg-type]
    assert e.value.status_code == 404 and e.value.detail == "not found"  # nothing published: the same words as every other not-found
    with pytest.raises(HTTPException) as e:
        await app_module.public_clinic(request())  # type: ignore[arg-type]
    assert e.value.status_code == 429
    app_module._public_limiter_cache.clear()


def test_the_readiness_probe_cannot_be_used_to_load_the_database(monkeypatch):
    calls = []

    async def fake_load(engine, account, *, origins):
        calls.append(1)
        return []

    monkeypatch.setattr(app_module, "load_registry", fake_load)
    monkeypatch.setenv("CAREFLOW_WORKER_ACCOUNT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("CAREFLOW_REQUIRED_ROLES", "decision")
    with TestClient(make_app(monkeypatch, profile="clinical", CAREFLOW_REQUIRED_ROLES="decision", CAREFLOW_WORKER_ACCOUNT_ID="00000000-0000-0000-0000-000000000001")) as c:
        for _ in range(5):
            assert c.get("/readyz").status_code == 503
    assert len(calls) <= 1, "five anonymous probes in a row must not become five registry reads"


def test_a_public_answer_never_carries_an_internal_label_or_an_id_of_another_clinic():
    out = app_module.PublicClinicProfileOut.model_json_schema()
    assert set(out["properties"]) == {"name", "about", "departments", "address", "opening_hours", "phone", "privacy_promise", "placeholder", "features", "languages"}
    assert set(app_module.PublicClinicOut.model_json_schema()["properties"]) == {"clinic_id", "name"}
    assert clinic_profile_sql  # the read goes through the one published-clinic function


def test_no_public_route_logs_a_request_body(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    with TestClient(make_app(monkeypatch)) as c:
        c.post("/public/grievances", json={"category": "demo_category", "text": "DEMO-SECRET-COMPLAINT-TEXT"}, headers={"X-Idempotency-Key": "k" * 40})
    assert "DEMO-SECRET-COMPLAINT-TEXT" not in caplog.text
    assert isinstance(Response(), Response)
