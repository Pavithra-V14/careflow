"""Live proof that the browser-side database roles cannot touch the document bucket (db/migrations/0027, owner decision 2026-10-30).

An ANON session (the publishable key alone) and an AUTHENTICATED session (a real token for a throwaway Auth user) are each refused for list, read, upload and delete on
`careflow-documents`. The proof does not trust the HTTP status alone: after every attempt the object is looked at again with the server-side key (still there, unchanged;
nothing new appeared). The server-side key is used ONLY here as the trusted writer/observer, read from the env name already in .env.example, and never printed.

Needs the bucket `careflow-documents` to exist in the project (the owner creates it in the dashboard). If it does not, the test is SKIPPED with that reason: an absent
bucket would make "refused" vacuously true, so the skip is the honest outcome, and the CI project's bucket step is recorded in docs/design/uploads-supabase-storage.md.
Synthetic bytes only; every object is deleted at the end.
"""

import os
import uuid
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

from careflow.adapters.storage import DOCUMENT_BUCKET

_ALL = {**dotenv_values(Path(__file__).resolve().parent.parent / ".env"), **os.environ}
ENV = {k: v for k, v in _ALL.items() if k.startswith("SUPABASE_") or k == "DATABASE_URL_MIGRATOR"}  # nothing else from .env is held in this module
NEEDED = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY")

pytestmark = pytest.mark.skipif(not all(ENV.get(k) for k in NEEDED), reason="Supabase project credentials not configured")

BODY = b"%PDF-1.4\n% synthetic storage policy probe, no patient data\n%%EOF\n"


def _base() -> str:
    return ENV["SUPABASE_URL"].rstrip("/") + "/storage/v1"


def _service_headers() -> dict:
    key = ENV["SUPABASE_SECRET_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}"} if key.startswith("eyJ") else {"apikey": key}


def _path() -> str:
    return f"clinic/{uuid.uuid4()}/doc/{uuid.uuid4()}"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=30, follow_redirects=False) as c:
        yield c


@pytest.fixture(scope="module")
def bucket_exists(client):
    r = client.get(f"{_base()}/bucket/{DOCUMENT_BUCKET}", headers=_service_headers())
    if r.status_code != 200:
        pytest.skip(f"the bucket {DOCUMENT_BUCKET} does not exist in this project (HTTP {r.status_code}); the owner creates it first")
    assert r.json().get("public") is False, "the document bucket must be private"


@pytest.fixture(scope="module")
def sessions():
    """Two browser-side sessions: anon, and authenticated (a throwaway Auth user, deleted at the end)."""
    base = ENV["SUPABASE_URL"].rstrip("/")
    anon = {"apikey": ENV["SUPABASE_PUBLISHABLE_KEY"], "Authorization": f"Bearer {ENV['SUPABASE_PUBLISHABLE_KEY']}"}
    email, password = f"careflow-storage-{uuid.uuid4().hex[:12]}@example.com", f"Pw-{uuid.uuid4().hex}-{uuid.uuid4().hex}"
    admin = {**_service_headers(), "Content-Type": "application/json"}
    created = httpx.post(f"{base}/auth/v1/admin/users", headers=admin, json={"email": email, "password": password, "email_confirm": True}, timeout=30)
    assert created.status_code in (200, 201), f"could not create the throwaway Auth user: HTTP {created.status_code}"
    user_id = created.json()["id"]
    try:
        signed = httpx.post(f"{base}/auth/v1/token?grant_type=password", headers={"apikey": ENV["SUPABASE_PUBLISHABLE_KEY"], "Content-Type": "application/json"}, json={"email": email, "password": password}, timeout=30)
        assert signed.status_code == 200, f"password sign-in failed: HTTP {signed.status_code}"
        authed = {"apikey": ENV["SUPABASE_PUBLISHABLE_KEY"], "Authorization": f"Bearer {signed.json()['access_token']}"}
        yield {"anon": anon, "authenticated": authed}
    finally:
        httpx.delete(f"{base}/auth/v1/admin/users/{user_id}", headers=admin, timeout=30)


@pytest.fixture
def stored(client, bucket_exists):
    """One synthetic object put there by the trusted writer (the server-side key); removed afterwards."""
    path = _path()
    up = client.post(f"{_base()}/object/{DOCUMENT_BUCKET}/{path}", headers={**_service_headers(), "content-type": "application/pdf"}, content=BODY)
    assert up.status_code in (200, 201), f"the trusted writer could not store the probe: HTTP {up.status_code}"
    control = client.post(f"{_base()}/object/list/{DOCUMENT_BUCKET}", headers=_service_headers(), json={"prefix": path.rsplit("/", 2)[0], "limit": 10})
    assert control.status_code == 200 and control.json(), "positive control: the trusted writer must see the probe in the same listing the weak sessions are refused"
    extra: list[str] = [path]
    yield path, extra
    client.request("DELETE", f"{_base()}/object/{DOCUMENT_BUCKET}", headers=_service_headers(), json={"prefixes": extra})


def _server_reads(client, path):
    r = client.get(f"{_base()}/object/authenticated/{DOCUMENT_BUCKET}/{path}", headers=_service_headers())
    return r.content if r.status_code == 200 else None


@pytest.mark.parametrize("who", ["anon", "authenticated"])
def test_a_browser_side_session_cannot_list_read_upload_or_delete_in_the_document_bucket(client, sessions, stored, who):
    path, extra = stored
    weak = sessions[who]
    prefix = path.rsplit("/", 2)[0]

    # LIST: refused, or an empty answer; the probe is never named.
    listed = client.post(f"{_base()}/object/list/{DOCUMENT_BUCKET}", headers=weak, json={"prefix": prefix, "limit": 10})
    assert listed.status_code >= 400 or (listed.status_code == 200 and listed.json() == []), f"{who} saw a listing: HTTP {listed.status_code}"

    # SIGNED URLS: neither session may mint a signed read or signed upload URL.
    for route in (f"object/sign/{DOCUMENT_BUCKET}/{path}", f"object/upload/sign/{DOCUMENT_BUCKET}/{path}"):
        minted = client.post(f"{_base()}/{route}", headers=weak, json={"expiresIn": 60})
        assert minted.status_code >= 400 and "token" not in minted.text and "signedURL" not in minted.text, f"{who} minted a signed URL via {route.split('/')[1]}: HTTP {minted.status_code}"

    # READ: refused on every read route, and no bytes come back.
    for route in (f"object/authenticated/{DOCUMENT_BUCKET}/{path}", f"object/{DOCUMENT_BUCKET}/{path}", f"object/public/{DOCUMENT_BUCKET}/{path}"):
        read = client.get(f"{_base()}/{route}", headers=weak)
        assert read.status_code >= 400 and BODY not in read.content, f"{who} could read through {route.split('/')[0]}/...: HTTP {read.status_code}"

    # UPLOAD: refused (a new path and an overwrite of the existing one); the stored bytes are unchanged and nothing new exists.
    fresh = _path()
    extra.append(fresh)
    new = client.post(f"{_base()}/object/{DOCUMENT_BUCKET}/{fresh}", headers={**weak, "content-type": "application/pdf"}, content=BODY)
    over = client.put(f"{_base()}/object/{DOCUMENT_BUCKET}/{path}", headers={**weak, "content-type": "application/pdf"}, content=b"%PDF-1.4\n% overwritten\n%%EOF\n")
    assert new.status_code >= 400 and over.status_code >= 400, f"{who} upload statuses {new.status_code}/{over.status_code}"
    assert _server_reads(client, fresh) is None, f"{who} created an object"
    assert _server_reads(client, path) == BODY, f"{who} changed the stored object"

    # DELETE: refused, and (since a filtered delete can answer 200 with an empty list) the object is still there afterwards.
    client.request("DELETE", f"{_base()}/object/{DOCUMENT_BUCKET}", headers=weak, json={"prefixes": [path]})
    client.request("DELETE", f"{_base()}/object/{DOCUMENT_BUCKET}/{path}", headers=weak)
    assert _server_reads(client, path) == BODY, f"{who} deleted the stored object"


@pytest.mark.parametrize("who", ["anon", "authenticated"])
def test_a_browser_side_session_cannot_change_the_bucket_itself(client, sessions, bucket_exists, who):
    weak = sessions[who]
    seen = client.get(f"{_base()}/bucket/{DOCUMENT_BUCKET}", headers=weak)
    changed = client.put(f"{_base()}/bucket/{DOCUMENT_BUCKET}", headers=weak, json={"public": True})
    assert seen.status_code >= 400, f"{who} could read the bucket row: HTTP {seen.status_code}"
    assert changed.status_code >= 400, f"{who} could update the bucket: HTTP {changed.status_code}"
    after = httpx.get(f"{_base()}/bucket/{DOCUMENT_BUCKET}", headers=_service_headers(), timeout=30).json()
    assert after.get("public") is False


def test_the_five_deny_policies_exist_as_restrictive_false_and_row_level_security_is_on():
    """The refusals above also hold by default-deny; this proves the migration's own policies are what is in force (it needs the migrator connection)."""
    import asyncio

    import asyncpg

    if not ENV.get("DATABASE_URL_MIGRATOR"):
        pytest.skip("DATABASE_URL_MIGRATOR not configured")

    async def look():
        c = await asyncpg.connect(ENV["DATABASE_URL_MIGRATOR"].replace("postgresql+asyncpg://", "postgresql://", 1), statement_cache_size=0)
        try:
            pols = await c.fetch(r"select policyname, permissive, roles::text[] as roles, cmd, qual, with_check from pg_policies where schemaname = 'storage' and policyname like 'storage\_%\_deny\_%'")
            rls = await c.fetch("select relname, relrowsecurity from pg_class where oid in ('storage.objects'::regclass, 'storage.buckets'::regclass)")
            return pols, rls
        finally:
            await c.close()

    pols, rls = asyncio.run(look())
    assert {p["policyname"] for p in pols} == {"storage_objects_deny_select", "storage_objects_deny_insert", "storage_objects_deny_update", "storage_objects_deny_delete", "storage_buckets_deny_all"}
    for p in pols:
        assert p["permissive"] == "RESTRICTIVE" and set(p["roles"]) == {"anon", "authenticated"}
        assert (p["qual"] in (None, "false")) and (p["with_check"] in (None, "false"))
    assert all(r["relrowsecurity"] for r in rls)
