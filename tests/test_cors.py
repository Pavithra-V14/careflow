"""CORS (careflow/cors.py): exact origins only, fail-closed, refuse to start with a wildcard outside the demo profile, credentials off, preflight behaviour. Offline: no database,
no network; the app is built but its lifespan never runs."""

import pytest
from fastapi.testclient import TestClient

from careflow.app import Settings, create_app
from careflow.cors import ALLOW_HEADERS, ALLOW_METHODS, CorsConfigError, parse_origins

DEMO, CLINICAL = "demo", "clinical"
ORIGIN = "https://app.example.test"


def settings(origins):
    return Settings(supabase_url="https://x.supabase.test", database_url_app="postgresql://u:p@h/d", cors_allowed_origins=origins)  # type: ignore[call-arg]


# ---- parsing --------------------------------------------------------------------------------------------------------------------------------


def test_unset_or_empty_allows_no_origin_in_any_profile():
    for raw in (None, "", "   ", " , ,"):
        assert parse_origins(raw, DEMO) == [] and parse_origins(raw, CLINICAL) == []


def test_exact_origins_are_normalised_deduplicated_and_kept_in_order():
    assert parse_origins(" https://App.Example.test , http://localhost:5173 ,https://app.example.test", CLINICAL) == ["https://app.example.test", "http://localhost:5173"]


@pytest.mark.parametrize(
    "bad",
    ["https://*.example.test", "*.example.test", "https://app.example.test/", "https://app.example.test/path", "app.example.test", "ftp://app.example.test",
     "https://user:pw@app.example.test", "https://app.example.test:99999", "https://app.example.test:", "https://", "javascript:alert(1)", "https://a b.example.test"],
)
def test_anything_but_an_exact_origin_is_refused_in_every_profile(bad):
    for profile in (DEMO, CLINICAL):
        with pytest.raises(CorsConfigError):
            parse_origins(bad, profile)


def test_the_bare_wildcard_is_refused_unless_the_profile_is_demo():
    assert parse_origins("*", DEMO) == ["*"]
    with pytest.raises(CorsConfigError, match="demo"):
        parse_origins("*", CLINICAL)
    with pytest.raises(CorsConfigError):
        parse_origins("https://app.example.test,*", CLINICAL)


def test_plain_http_is_only_for_local_hosts_outside_the_demo_profile():
    assert parse_origins("http://localhost:5173,http://127.0.0.1:5173", CLINICAL) == ["http://localhost:5173", "http://127.0.0.1:5173"]
    with pytest.raises(CorsConfigError, match="https"):
        parse_origins("http://app.example.test", CLINICAL)
    assert parse_origins("http://app.example.test", DEMO) == ["http://app.example.test"]


# ---- the app --------------------------------------------------------------------------------------------------------------------------------


def client(monkeypatch, origins, profile="clinical"):
    monkeypatch.setenv("CAREFLOW_PROFILE", profile)
    return TestClient(create_app(settings(origins)))  # no `with`: the lifespan (database, keys) never runs; the CORS layer answers before routing


def preflight(c, origin, method="POST", headers="authorization,content-type,x-idempotency-key"):
    return c.options("/v1/staff/patients", headers={"Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": headers})


def test_a_listed_origin_gets_a_preflight_answer_with_the_minimum_methods_and_headers_and_no_credentials(monkeypatch):
    r = preflight(client(monkeypatch, ORIGIN), ORIGIN)
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in r.headers  # bearer tokens in a header need no credentialed CORS
    assert set(r.headers["access-control-allow-methods"].replace(" ", "").split(",")) == set(ALLOW_METHODS)
    safelisted = {"accept", "accept-language", "content-language"}  # Starlette always adds the CORS-safelisted request headers; nothing else is allowed
    assert {h.strip().lower() for h in r.headers["access-control-allow-headers"].split(",")} == {h.lower() for h in ALLOW_HEADERS} | safelisted
    assert r.headers["access-control-max-age"] == "600" and r.headers["access-control-allow-origin"] != "*"


def test_an_unlisted_origin_is_refused_at_the_preflight_and_gets_no_allow_origin_header(monkeypatch):
    c = client(monkeypatch, ORIGIN)
    r = preflight(c, "https://evil.example.test")
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in c.get("/openapi.json", headers={"Origin": "https://evil.example.test"}).headers


def test_a_header_or_method_outside_the_list_is_refused(monkeypatch):
    c = client(monkeypatch, ORIGIN)
    assert preflight(c, ORIGIN, method="DELETE").status_code == 400
    assert preflight(c, ORIGIN, headers="x-forwarded-for").status_code == 400


def test_a_normal_request_from_a_listed_origin_carries_the_allow_origin_header_and_no_credentials_header(monkeypatch):
    r = client(monkeypatch, ORIGIN).get("/openapi.json", headers={"Origin": ORIGIN})
    assert r.headers["access-control-allow-origin"] == ORIGIN and "access-control-allow-credentials" not in r.headers


def test_www_authenticate_is_exposed_to_a_listed_origin_so_the_browser_can_read_a_401s_signal(monkeypatch):
    r = client(monkeypatch, ORIGIN).get("/openapi.json", headers={"Origin": ORIGIN})
    exposed = {h.strip().lower() for h in r.headers["access-control-expose-headers"].split(",")}
    assert exposed == {"www-authenticate"}  # exactly that one, nothing else widened


def test_with_nothing_configured_there_is_no_cors_layer_at_all(monkeypatch):
    c = client(monkeypatch, None)
    assert "access-control-allow-origin" not in c.get("/openapi.json", headers={"Origin": ORIGIN}).headers
    assert preflight(c, ORIGIN).status_code == 405  # no middleware answers OPTIONS


def test_the_app_refuses_to_be_built_with_a_wildcard_outside_the_demo_profile_and_builds_in_demo(monkeypatch):
    monkeypatch.setenv("CAREFLOW_PROFILE", "clinical")
    with pytest.raises(CorsConfigError):
        create_app(settings("*"))
    monkeypatch.delenv("CAREFLOW_PROFILE")  # unset = clinical
    with pytest.raises(CorsConfigError):
        create_app(settings("*"))
    monkeypatch.setenv("CAREFLOW_PROFILE", "demo")
    r = preflight(TestClient(create_app(settings("*"))), "https://anything.test")
    assert r.status_code == 200 and "access-control-allow-credentials" not in r.headers  # even a demo wildcard never allows credentials


def test_the_app_reads_the_environment_when_no_settings_are_passed_and_refuses_a_bad_value(monkeypatch):
    monkeypatch.setenv("CAREFLOW_PROFILE", "clinical")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://*.example.test")
    with pytest.raises(CorsConfigError):
        create_app()
