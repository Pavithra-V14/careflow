"""The API role matrix (BUILD-SPEC.md §18.4, §21; CLAUDE.md rule 8).

Drives the real FastAPI app over HTTP with Starlette's TestClient. Tokens are real ES256 JWTs signed by a
key generated for this run; the app verifies them against a fake JWKS endpoint, so there is no committed
key and no dependency on a live Auth service for the matrix itself (one separate test, in
test_api_real_token_live.py, uses the project's real Auth). The database is real: the app connects as the
restricted `careflow_app` role through the transaction-mode pooler, so row-level security is genuinely in
the path. Accounts are committed under origin='test' and removed in teardown.

How the harness works. A route's expectations are listed once in MATRIX, as (label, how to authenticate,
expected status, expected body). `check_route_matrix` runs them and reports every mismatch. To prove the
harness really catches a violation, a test points it at a deliberately unprotected route and requires it to
fail. `test_every_route_is_in_the_matrix` fails if the app has a route the matrix does not cover, so adding
a route without adding its matrix case breaks the build.
"""

import asyncio
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import asyncpg
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from dotenv import dotenv_values
from fastapi import FastAPI
from starlette.testclient import TestClient

from careflow.app import Settings, create_app
from careflow.auth import JWKSClient

ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or ENV.get("DATABASE_URL_MIGRATOR")
APP_URL = os.environ.get("DATABASE_URL_APP") or ENV.get("DATABASE_URL_APP")
PROJECT = "https://example-project.supabase.co"
ISSUER = PROJECT + "/auth/v1"

pytestmark = pytest.mark.skipif(not (MIGRATOR and APP_URL), reason="database URLs not configured")

STAFF_ROLES = ("front_desk", "nurse", "doctor", "admin", "business_owner", "auditor")


# ---- signing and a fake key endpoint ---------------------------------------------------------------


def _key(kid):
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
    return private, jwk


SIGNING, SIGNING_JWK = _key("matrix-key")
ATTACKER, _ = _key("matrix-key")  # same kid, different key material


def mint(sub, *, key=SIGNING, kid="matrix-key", alg="ES256", **over):
    claims = {"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 600, **over}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def fake_jwks_client() -> JWKSClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [SIGNING_JWK]}))
    return JWKSClient(ISSUER + "/.well-known/jwks.json", http=httpx.AsyncClient(transport=transport))


# ---- committed fixture: real rows the route reads under RLS ----------------------------------------


class Identities:
    pass


def _run(coro):
    return asyncio.run(coro)


async def _seed() -> Identities:
    ids = Identities()
    ids.clinic_a, ids.clinic_b = uuid.uuid4(), uuid.uuid4()
    ids.people = {}  # key -> (account_id, account_type, status, [(clinic, role)])
    spec = [(f"{r}_a", "staff", "active", [(ids.clinic_a, r)]) for r in STAFF_ROLES]
    spec += [
        ("front_desk_b", "staff", "active", [(ids.clinic_b, "front_desk")]),
        ("multi", "staff", "active", [(ids.clinic_a, "nurse"), (ids.clinic_b, "doctor")]),
        ("patient", "patient", "active", []),
        ("guardian", "guardian", "active", []),
        ("suspended", "staff", "suspended", [(ids.clinic_a, "nurse")]),
        ("invited", "staff", "invited", [(ids.clinic_a, "nurse")]),
        ("closed", "patient", "closed", []),
    ]
    for key, kind, status, grants in spec:
        ids.people[key] = (uuid.uuid4(), kind, status, grants)
    ids.no_account = uuid.uuid4()  # a valid Auth user with no iam.accounts row
    conn = await asyncpg.connect((MIGRATOR or "").replace("+asyncpg", ""), statement_cache_size=0)
    try:
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, 'Matrix A', 'test'), ($2, 'Matrix B', 'test')", ids.clinic_a, ids.clinic_b)
        await conn.executemany(
            "insert into iam.accounts (id, account_type, status, origin) values ($1, $2, $3, 'test')",
            [(a, k, s) for a, k, s, _ in ids.people.values()],
        )
        await conn.executemany(
            "insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')",
            [(a, c, r) for a, _, _, grants in ids.people.values() for c, r in grants],
        )
    except Exception:
        await _cleanup(ids, conn)
        await conn.close()
        raise
    await conn.close()
    return ids


async def _cleanup(ids, conn=None):
    own = conn is None
    conn = conn or await asyncpg.connect((MIGRATOR or "").replace("+asyncpg", ""), statement_cache_size=0)
    try:
        accounts = [a for a, *_ in ids.people.values()]
        await conn.execute("delete from iam.staff_roles where clinic_id = any($1)", [ids.clinic_a, ids.clinic_b])
        await conn.execute("delete from iam.accounts where id = any($1)", accounts)
        await conn.execute("delete from iam.clinics where id = any($1)", [ids.clinic_a, ids.clinic_b])
    finally:
        if own:
            await conn.close()


@pytest.fixture(scope="module")
def ids():
    seeded = _run(_seed())
    yield seeded
    _run(_cleanup(seeded))


@pytest.fixture(scope="module")
def app() -> FastAPI:
    settings = Settings(supabase_url=PROJECT, database_url_app=APP_URL or "")
    return create_app(settings, jwks=fake_jwks_client())


@pytest.fixture(scope="module")
def client(app, ids):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def expected_me(ids, key):
    account_id, kind, status, grants = ids.people[key]
    return {
        "account_id": str(account_id),
        "account_type": kind,
        "status": status,
        "roles": sorted(({"clinic_id": str(c), "role": r} for c, r in grants), key=lambda g: (g["clinic_id"], g["role"])),
    }


# ---- the harness -----------------------------------------------------------------------------------


def check_route_matrix(client, method, path, cases):
    """Run every (label, headers, status, body|None) case and raise ONE AssertionError listing all failures."""
    failures = []
    for label, headers, status, body in cases:
        response = client.request(method, path, headers=headers)
        if response.status_code != status:
            failures.append(f"{label}: expected {status}, got {response.status_code}")
        elif body is not None and response.json() != body:
            failures.append(f"{label}: unexpected body {response.json()}")
    assert not failures, f"{method} {path}:\n  " + "\n  ".join(failures)


def me_cases(ids) -> list[tuple]:
    allowed = [
        (f"{key} may read their own profile", bearer(mint(ids.people[key][0])), 200, expected_me(ids, key))
        for key, (_, _, status, _) in ids.people.items()
        if status == "active"
    ]
    refused_account = [
        (f"{key} ({ids.people[key][2]} account) is refused", bearer(mint(ids.people[key][0])), 403, {"detail": "no active account"})
        for key in ("suspended", "invited", "closed")
    ]
    refused_account.append(("valid user with no iam account is refused", bearer(mint(ids.no_account)), 403, {"detail": "no active account"}))
    sub = ids.people["nurse_a"][0]
    rejected_token = [
        ("no Authorization header", {}, 401, None),
        ("empty bearer", {"Authorization": "Bearer "}, 401, None),
        ("basic scheme", {"Authorization": "Basic YTpi"}, 401, None),
        ("garbage token", bearer("not.a.jwt"), 401, None),
        ("expired token", bearer(mint(sub, exp=int(time.time()) - 3600)), 401, None),
        ("signed by another key under the right kid", bearer(mint(sub, key=ATTACKER)), 401, None),
        ("unknown kid", bearer(mint(sub, kid="somebody-elses")), 401, None),
        ("HS256 downgrade", bearer(jwt.encode({"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated",
                                               "exp": int(time.time()) + 600}, "x" * 64, algorithm="HS256", headers={"kid": "matrix-key"})), 401, None),
        ("alg none", bearer(jwt.encode({"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated",
                                        "exp": int(time.time()) + 600}, None, algorithm="none", headers={"kid": "matrix-key"})), 401, None),
        ("wrong audience", bearer(mint(sub, aud="service_role")), 401, None),
        ("wrong issuer", bearer(mint(sub, iss="https://evil.example/auth/v1")), 401, None),
        ("anonymous role", bearer(mint(sub, role="anon")), 401, None),
        ("service role claim", bearer(mint(sub, role="service_role")), 401, None),
        ("no subject", bearer(mint_without(sub, "sub")), 401, None),
        ("no expiry", bearer(mint_without(sub, "exp")), 401, None),
    ]
    return allowed + refused_account + rejected_token


def mint_without(sub, claim):
    claims = {"sub": str(sub), "iss": ISSUER, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 600}
    claims.pop(claim)
    return jwt.encode(claims, SIGNING, algorithm="ES256", headers={"kid": "matrix-key"})


# One entry per route: add the new route's cases here in the same change that adds the route.
MATRIX: dict[tuple[str, str], Callable] = {("GET", "/v1/iam/me"): me_cases}


# ---- tests -----------------------------------------------------------------------------------------


def test_every_route_is_in_the_matrix(app):
    """Rule 8: a route with no role-matrix case is not done."""
    # The generated OpenAPI document lists every served operation, including routers added by include_router.
    served = {(m.upper(), path) for path, ops in app.openapi()["paths"].items() if path.startswith("/v1") for m in ops}
    assert served == set(MATRIX), f"routes without a matrix case: {served - set(MATRIX)}; stale cases: {set(MATRIX) - served}"


@pytest.mark.parametrize(("method", "path"), sorted(MATRIX))
def test_role_matrix(client, ids, method, path):
    check_route_matrix(client, method, path, MATRIX[(method, path)](ids))


def test_the_harness_catches_a_route_that_lets_everyone_in(ids):
    """The proof the harness has teeth: an unprotected route must make check_route_matrix fail."""
    leaky = FastAPI()

    @leaky.get("/v1/iam/me")
    async def open_door():
        return {"account_id": str(uuid.uuid4()), "account_type": "staff", "status": "active", "roles": []}

    with TestClient(leaky) as c, pytest.raises(AssertionError, match="expected 401, got 200"):
        check_route_matrix(c, "GET", "/v1/iam/me", me_cases(ids))


def test_a_caller_sees_only_their_own_grants_even_if_rls_would_let_an_admin_read_more(client, ids):
    """RLS lets a clinic admin SELECT every grant in their clinic; /me must still return only their own."""
    body = client.get("/v1/iam/me", headers=bearer(mint(ids.people["admin_a"][0]))).json()
    assert body["roles"] == [{"clinic_id": str(ids.clinic_a), "role": "admin"}]


def test_a_multi_clinic_account_gets_exactly_its_own_grants(client, ids):
    body = client.get("/v1/iam/me", headers=bearer(mint(ids.people["multi"][0]))).json()
    assert {(g["clinic_id"], g["role"]) for g in body["roles"]} == {(str(ids.clinic_a), "nurse"), (str(ids.clinic_b), "doctor")}


def test_error_bodies_never_say_why_a_token_failed(client, ids):
    bodies = {
        client.get("/v1/iam/me", headers=bearer(mint(ids.people["nurse_a"][0], exp=int(time.time()) - 3600))).text,
        client.get("/v1/iam/me", headers=bearer(mint(ids.people["nurse_a"][0], key=ATTACKER))).text,
        client.get("/v1/iam/me", headers=bearer("garbage")).text,
        client.get("/v1/iam/me").text,
    }
    assert bodies == {'{"detail":"invalid or missing credentials"}'}
    assert client.get("/v1/iam/me").headers["www-authenticate"] == "Bearer"


def test_identity_never_leaks_between_pooled_requests(app, ids):
    """Forty concurrent requests from six identities over a pool of five connections: each must get exactly
    its own profile back, which only holds if the claims are transaction-local and set before any query."""
    keys = ["front_desk_a", "nurse_a", "admin_a", "auditor_a", "patient", "front_desk_b"]

    async def run():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:

                async def call(i):
                    key = keys[i % len(keys)]
                    r = await http.get("/v1/iam/me", headers=bearer(mint(ids.people[key][0])))
                    return key, r.status_code, r.json()

                return await asyncio.gather(*(call(i) for i in range(40)))

    for key, status, body in _run(run()):
        assert status == 200, key
        assert body == expected_me(ids, key), key
