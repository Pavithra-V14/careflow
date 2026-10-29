"""One end-to-end check with a REAL token from the project's own Supabase Auth (BUILD-SPEC.md §18.1, §21).

The role matrix (test_api_role_matrix.py) uses locally signed tokens so it is deterministic. This test
closes the loop on the facts that only the real service can confirm (CLAUDE.md, Appendix B discipline):
that the real JWKS document is served as expected, that a token the real Auth issues is ES256 with a `kid`
that the real JWKS contains, and that the app accepts it and rejects a tampered copy.

The secret key (sb_secret_...) is used here ONLY in fixture setup to create and delete a throwaway Auth
user (the one-time-setup role §21 reserves for it); no assertion depends on it. The user is removed in
teardown, together with its iam rows. Requires password sign-in to be enabled on the project.
"""

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import httpx
import jwt
import pytest
from dotenv import dotenv_values
from starlette.testclient import TestClient

from careflow.app import Settings, create_app

ENV = {**dotenv_values(Path(__file__).resolve().parent.parent / ".env"), **{k: v for k, v in os.environ.items() if k.startswith(("SUPABASE_", "DATABASE_URL_"))}}
NEEDED = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY", "DATABASE_URL_MIGRATOR", "DATABASE_URL_APP")

pytestmark = pytest.mark.skipif(not all(ENV.get(k) for k in NEEDED), reason="Supabase project credentials not configured")


def _dsn(url):
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture(scope="module")
def real_user():
    base = ENV["SUPABASE_URL"].rstrip("/")
    email, password = f"careflow-test-{uuid.uuid4().hex[:12]}@example.com", f"Pw-{uuid.uuid4().hex}-{uuid.uuid4().hex}"
    admin = {"apikey": ENV["SUPABASE_SECRET_KEY"], "Content-Type": "application/json"}
    created = httpx.post(f"{base}/auth/v1/admin/users", headers=admin, json={"email": email, "password": password, "email_confirm": True}, timeout=30)
    assert created.status_code in (200, 201), f"could not create the throwaway Auth user: HTTP {created.status_code}"
    user_id = uuid.UUID(created.json()["id"])
    clinic = uuid.uuid4()

    async def seed():
        c = await asyncpg.connect(_dsn(ENV["DATABASE_URL_MIGRATOR"]), statement_cache_size=0)
        try:
            await c.execute("insert into iam.clinics (id, name, origin) values ($1, 'Real Token Clinic', 'test')", clinic)
            await c.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", user_id)
            await c.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, 'nurse', 'test')", user_id, clinic)
        finally:
            await c.close()

    async def cleanup():
        c = await asyncpg.connect(_dsn(ENV["DATABASE_URL_MIGRATOR"]), statement_cache_size=0)
        try:
            await c.execute("delete from iam.staff_roles where clinic_id = $1", clinic)
            await c.execute("delete from iam.accounts where id = $1", user_id)
            await c.execute("delete from iam.clinics where id = $1", clinic)
        finally:
            await c.close()

    try:
        asyncio.run(seed())
        yield user_id, clinic, email, password
    finally:
        asyncio.run(cleanup())
        httpx.delete(f"{base}/auth/v1/admin/users/{user_id}", headers=admin, timeout=30)


def _sign_in(email, password):
    base = ENV["SUPABASE_URL"].rstrip("/")
    r = httpx.post(
        f"{base}/auth/v1/token?grant_type=password",
        headers={"apikey": ENV["SUPABASE_PUBLISHABLE_KEY"], "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=30,
    )
    assert r.status_code == 200, f"password sign-in failed: HTTP {r.status_code} (is password sign-in enabled on the project?)"
    return r.json()["access_token"]


def test_a_token_issued_by_the_real_auth_is_es256_with_a_kid_the_real_jwks_serves(real_user):
    _, _, email, password = real_user
    token = _sign_in(email, password)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256" and header.get("kid")
    jwks_url = ENV.get("SUPABASE_JWKS_URL") or ENV["SUPABASE_URL"].rstrip("/") + "/auth/v1/.well-known/jwks.json"
    served = httpx.get(jwks_url, timeout=15).json()["keys"]
    assert header["kid"] in {k["kid"] for k in served}


def test_the_app_accepts_a_real_token_and_rejects_a_tampered_copy(real_user):
    user_id, clinic, email, password = real_user
    token = _sign_in(email, password)
    app = create_app(Settings(supabase_url=ENV["SUPABASE_URL"], database_url_app=ENV["DATABASE_URL_APP"], supabase_jwks_url=ENV.get("SUPABASE_JWKS_URL") or None))
    with TestClient(app, raise_server_exceptions=False) as client:
        ok = client.get("/v1/iam/me", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"account_id": str(user_id), "account_type": "staff", "status": "active", "roles": [{"clinic_id": str(clinic), "role": "nurse", "clinic_name": "Real Token Clinic", "clinic_origin": "test"}]}
        header, payload, signature = token.split(".")
        flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
        assert client.get("/v1/iam/me", headers={"Authorization": f"Bearer {header}.{payload}.{flipped}"}).status_code == 401
        assert client.get("/v1/iam/me").status_code == 401
