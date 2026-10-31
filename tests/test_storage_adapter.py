"""careflow/adapters/storage.py (the Supabase Storage `UploadPort`), offline against a DOCUMENTED MOCK: `httpx.MockTransport` answers in the shapes the adapter's header describes. The real service has
NOT been called (Appendix B discipline): that is `scripts/storage_spike.py`, run by the owner's go-ahead against the dev project once the bucket exists. What is proven here is the adapter's own
behaviour: it builds only document paths, never hands out a URL for another host, bounds what it reads, maps every failure to fixed words, and never lets a key, path or body into a message."""

import base64
import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from careflow.adapters.storage import (
    ASSUMED_SIGNED_URL_SECONDS,
    BUCKET_RE,
    SupabaseUploadAdapter,
)
from careflow.documents import DocumentIntake, document_id_for, storage_path
from careflow.ports import PermanentError, TransientError

BASE, KEY, BUCKET = "https://abcd.supabase.test", "sb_secret_TESTKEY_NOT_REAL", "careflow-documents"
CLINIC, DOC = uuid.uuid4(), uuid.uuid4()
PATH = storage_path(CLINIC, DOC)
NOW = datetime(2030, 1, 1, tzinfo=UTC)


def jwt_with_exp(exp: int) -> str:
    part = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'exp': exp})}.signature"


class Mock:
    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def transport(self):
        def run(request: httpx.Request):
            self.requests.append(request)
            return self._handler(request)

        return httpx.MockTransport(run)


def adapter(handler, **kw):
    mock = Mock(handler)
    http = httpx.AsyncClient(transport=mock.transport())
    return SupabaseUploadAdapter(BASE, KEY, BUCKET, http=http, now=lambda: NOW, **kw), mock


# ---- construction ---------------------------------------------------------------------------------------------------------------------------


def test_construction_refuses_a_plain_http_host_a_missing_key_and_a_bad_bucket_and_never_prints_the_key():
    for args in (("http://abcd.supabase.test", KEY, BUCKET), (BASE, "", BUCKET), (BASE, KEY, "Bad Bucket"), (BASE, KEY, "")):
        with pytest.raises(ValueError):
            SupabaseUploadAdapter(*args)
    assert SupabaseUploadAdapter("http://localhost:54321", KEY, BUCKET)  # local development only
    built = SupabaseUploadAdapter(BASE, KEY, BUCKET)
    assert KEY not in repr(built) and BUCKET_RE.match(BUCKET)


# ---- signing --------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_signed_upload_is_built_for_our_own_host_with_the_expiry_the_service_put_in_the_token():
    exp = int(NOW.timestamp()) + 900
    storage, mock = adapter(lambda r: httpx.Response(200, json={"url": f"/object/upload/sign/{BUCKET}/{PATH}?token={jwt_with_exp(exp)}"}))
    signed = await storage.create_signed_upload(path=PATH, content_type="application/pdf", max_bytes=20_000_000, expires_in_seconds=600)
    (request,) = mock.requests
    assert request.method == "POST" and request.url.path == f"/storage/v1/object/upload/sign/{BUCKET}/{PATH}" and request.headers["apikey"] == KEY
    assert signed.url.startswith(f"{BASE}/storage/v1/object/upload/sign/{BUCKET}/{PATH}?token=") and signed.path == PATH
    assert signed.expires_at == datetime.fromtimestamp(exp, UTC)  # the service's own expiry, not the caller's wish


@pytest.mark.asyncio
async def test_when_the_token_cannot_be_read_the_conservative_assumption_is_used_and_it_is_named_as_one():
    storage, _ = adapter(lambda r: httpx.Response(200, json={"url": f"/object/upload/sign/{BUCKET}/{PATH}?token=opaque"}))
    signed = await storage.create_signed_upload(path=PATH, content_type="application/pdf", max_bytes=1, expires_in_seconds=1)
    assert (signed.expires_at - NOW).total_seconds() == ASSUMED_SIGNED_URL_SECONDS


@pytest.mark.asyncio
async def test_a_url_for_another_host_or_a_malformed_answer_is_refused_and_the_browser_is_never_handed_it():
    for body in ({"url": "https://evil.example.test/steal?token=x"}, {"nourl": 1}, {"url": 5}):
        storage, _ = adapter(lambda r, b=body: httpx.Response(200, json=b))
        with pytest.raises(PermanentError):
            await storage.create_signed_upload(path=PATH, content_type="application/pdf", max_bytes=1, expires_in_seconds=1)
    storage, _ = adapter(lambda r: httpx.Response(200, text="not json"))
    with pytest.raises(PermanentError):
        await storage.create_signed_upload(path=PATH, content_type="application/pdf", max_bytes=1, expires_in_seconds=1)


@pytest.mark.parametrize("bad", ["", "../etc/passwd", f"clinic/{CLINIC}/doc/{DOC}/extra", f"clinic/{CLINIC}/Asha_Rao_letter.pdf", f"clinic/{CLINIC}/doc/{DOC}?x=1", f"/clinic/{CLINIC}/doc/{DOC}", "clinic/x/doc/y"])
@pytest.mark.asyncio
async def test_only_a_document_path_ever_reaches_a_request(bad):
    storage, mock = adapter(lambda r: httpx.Response(200, json={"url": "/x"}))
    for call in (
        storage.create_signed_upload(path=bad, content_type="application/pdf", max_bytes=1, expires_in_seconds=1), storage.describe(bad), storage.read(bad), storage.delete(bad),
    ):
        with pytest.raises(PermanentError):
            await call
    assert mock.requests == []


# ---- reading --------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_reads_the_stored_bytes_back_and_reports_what_the_storage_holds_not_what_anyone_claimed():
    content = b"%PDF-1.7\n" + b"x" * 300
    storage, mock = adapter(lambda r: httpx.Response(200, content=content, headers={"content-type": "Application/PDF; charset=binary"}))
    stored = await storage.describe(PATH)
    assert (stored.size, stored.content_type, stored.head) == (len(content), "application/pdf", content[:16]) and len(stored.sha256) == 64
    assert mock.requests[0].url.path == f"/storage/v1/object/authenticated/{BUCKET}/{PATH}" and mock.requests[0].headers["apikey"] == KEY
    assert await storage.read(PATH) == content


@pytest.mark.asyncio
async def test_a_missing_object_is_none_whether_the_service_says_404_or_400_not_found():
    for response in (httpx.Response(404, json={"error": "x"}), httpx.Response(400, json={"error": "Not Found", "message": "Object not found"})):
        storage, _ = adapter(lambda r, x=response: x)
        assert await storage.describe(PATH) is None and await storage.read(PATH) is None


@pytest.mark.asyncio
async def test_an_oversize_object_is_cut_one_byte_past_the_cap_so_it_is_never_held_whole_and_read_refuses_it():
    storage, _ = adapter(lambda r: httpx.Response(200, content=b"%PDF-" + b"x" * 5000, headers={"content-type": "application/pdf"}), max_object_bytes=1000)
    stored = await storage.describe(PATH)
    assert stored.size == 1001  # the caller sees "larger than allowed" (verify_stored: too_large) and rejects it
    with pytest.raises(PermanentError):
        await storage.read(PATH)


# ---- delete ---------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_names_the_path_in_the_body_and_treats_already_gone_as_done():
    storage, mock = adapter(lambda r: httpx.Response(200, json=[]))
    await storage.delete(PATH)
    request = mock.requests[0]
    assert request.method == "DELETE" and request.url.path == f"/storage/v1/object/{BUCKET}" and json.loads(request.content) == {"prefixes": [PATH]}
    for gone in (httpx.Response(404), httpx.Response(400, json={"message": "Object not found"})):
        await adapter(lambda r, x=gone: x)[0].delete(PATH)


# ---- failures are fixed words ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("status", "kind"), [(401, PermanentError), (403, PermanentError), (400, PermanentError), (422, PermanentError), (429, TransientError), (500, TransientError), (503, TransientError)])
@pytest.mark.asyncio
async def test_every_failure_status_maps_to_a_fixed_error_that_carries_no_path_key_or_body(status, kind):
    secret_body = f"SECRET-BODY {PATH} {KEY}"
    for call in ("create_signed_upload", "describe", "read", "delete"):
        storage, _ = adapter(lambda r: httpx.Response(status, text=secret_body))
        args = {"path": PATH, "content_type": "application/pdf", "max_bytes": 1, "expires_in_seconds": 1} if call == "create_signed_upload" else {"path": PATH}
        with pytest.raises(kind) as raised:
            await getattr(storage, call)(**args) if call == "create_signed_upload" else await getattr(storage, call)(PATH)
        text = str(raised.value)
        assert KEY not in text and str(CLINIC) not in text and "SECRET-BODY" not in text


@pytest.mark.asyncio
async def test_a_network_failure_or_a_timeout_is_transient_never_a_hang_or_a_leak():
    def boom(request):
        raise httpx.ConnectTimeout("took too long for " + str(request.url))

    storage, _ = adapter(boom)
    for call in (storage.describe(PATH), storage.read(PATH), storage.delete(PATH), storage.create_signed_upload(path=PATH, content_type="application/pdf", max_bytes=1, expires_in_seconds=1)):
        with pytest.raises(TransientError) as raised:
            await call
        assert str(CLINIC) not in str(raised.value) and "took too long" not in str(raised.value)


# ---- through the intake (the adapter behind the real DocumentIntake) -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_intake_registers_what_the_adapter_reads_back_and_refuses_an_object_that_is_not_what_its_type_says():
    class Conn:
        def __init__(self):
            self.inserts = []

        def begin_nested(self):
            import contextlib

            @contextlib.asynccontextmanager
            async def cm():
                yield self

            return cm()

        async def execute(self, statement, params=None):
            sql = str(statement)

            class R:
                def first(self_inner):
                    return 1 if "staff_roles" in sql else None

            if "insert into docs.documents" in sql:
                self.inserts.append(params)
            return R()

    clinic, key = uuid.uuid4(), "k-1"
    path = storage_path(clinic, document_id_for(clinic, key))
    good = b"%PDF-1.7\n" + b"x" * 200
    for content, ok in ((good, True), (b"not a pdf at all, a script", False)):
        storage, _ = adapter(lambda r, c=content: httpx.Response(200, content=c, headers={"content-type": "application/pdf"}) if r.method == "GET" else httpx.Response(200, json=[]))
        conn = Conn()
        intake = DocumentIntake(storage, profile="demo")
        if ok:
            done = await intake.register(conn, clinic, "test", "referral", uuid.uuid4(), key)  # type: ignore[arg-type]
            assert done.size_bytes == len(good) and conn.inserts and conn.inserts[0]["p"] == path
        else:
            from careflow.documents import UploadRejected

            with pytest.raises(UploadRejected) as raised:
                await intake.register(conn, clinic, "test", "referral", uuid.uuid4(), key)  # type: ignore[arg-type]
            assert raised.value.code == "content_mismatch" and conn.inserts == []


# ---- wiring: the application builds the real adapter only when every condition holds -------------------------------------------------------------------------


def _wiring(monkeypatch, **env):
    for name in ("CAREFLOW_STORAGE_BUCKET", "CAREFLOW_STORAGE_VERIFIED", "SUPABASE_SECRET_KEY", "CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from careflow.app import _intake_from_environment

    return _intake_from_environment(BASE)


ALL = {"CAREFLOW_STORAGE_BUCKET": BUCKET, "CAREFLOW_STORAGE_VERIFIED": "2026-10-29", "SUPABASE_SECRET_KEY": KEY, "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}


def test_the_real_storage_is_built_only_when_the_bucket_the_spike_date_the_demo_gate_and_the_key_are_all_present(monkeypatch):
    intake, built = _wiring(monkeypatch, **ALL)
    assert intake is not None and built is not None and intake.enabled and intake.scanner_configured is False
    assert _wiring(monkeypatch) == (None, None)  # nothing configured: the upload routes answer 503
    for missing in ("CAREFLOW_STORAGE_BUCKET", "CAREFLOW_STORAGE_VERIFIED", "SUPABASE_SECRET_KEY", "CAREFLOW_PROFILE", "CAREFLOW_DATA_POLICY"):
        assert _wiring(monkeypatch, **{k: v for k, v in ALL.items() if k != missing}) == (None, None), missing


@pytest.mark.parametrize("bad", ["", "yesterday", "2026-13-40", "true", "1"])
def test_an_unrecorded_or_malformed_spike_date_keeps_the_real_storage_off(monkeypatch, bad):
    assert _wiring(monkeypatch, **{**ALL, "CAREFLOW_STORAGE_VERIFIED": bad}) == (None, None)


@pytest.mark.parametrize(("profile", "policy"), [("clinical", "synthetic_only"), ("demo", "phi_approved"), ("demo", ""), ("", "synthetic_only")])
def test_outside_the_demo_profile_with_synthetic_data_the_real_storage_is_never_built(monkeypatch, profile, policy):
    assert _wiring(monkeypatch, **{**ALL, "CAREFLOW_PROFILE": profile, "CAREFLOW_DATA_POLICY": policy}) == (None, None)


# ---- the reviewer's findings (2026-10-30) -------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_bucket_is_a_misconfiguration_never_no_object_so_a_delete_cannot_silently_do_nothing():
    for response in (httpx.Response(400, json={"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"}), httpx.Response(404, json={"error": "Bucket not found"})):
        for call in ("describe", "read", "delete"):
            storage, _ = adapter(lambda r, x=response: x)
            with pytest.raises(PermanentError):
                await getattr(storage, call)(PATH)


@pytest.mark.asyncio
async def test_a_400_without_an_object_not_found_message_is_not_a_missing_object():
    storage, _ = adapter(lambda r: httpx.Response(400, json={"error": "InvalidRequest", "message": "something else"}))
    with pytest.raises(PermanentError):
        await storage.describe(PATH)
    storage, _ = adapter(lambda r: httpx.Response(400, text="Object not found but not json"))
    with pytest.raises(PermanentError):
        await storage.describe(PATH)


@pytest.mark.asyncio
async def test_a_path_with_a_trailing_newline_or_a_trailing_space_never_reaches_a_request():
    storage, mock = adapter(lambda r: httpx.Response(200, json={"url": "/x"}))
    for bad in (PATH + "\n", PATH + " ", "\n" + PATH):
        for call in (storage.describe(bad), storage.delete(bad)):
            with pytest.raises(PermanentError):
                await call
    assert mock.requests == []


@pytest.mark.asyncio
async def test_a_signed_url_must_keep_our_scheme_host_and_the_signed_upload_path_of_our_bucket():
    good = f"/object/upload/sign/{BUCKET}/{PATH}?token=t"
    for hostile in (f"http://abcd.supabase.test/storage/v1{good}", f"{BASE}/storage/v1/object/upload/sign/other-bucket/{PATH}?token=t", f"{BASE}/storage/v1/object/{BUCKET}/{PATH}", f"{BASE}/elsewhere?token=t"):
        storage, _ = adapter(lambda r, u=hostile: httpx.Response(200, json={"url": u}))
        with pytest.raises(PermanentError):
            await storage.create_signed_upload(path=PATH, content_type="application/pdf", max_bytes=1, expires_in_seconds=1)
    storage, _ = adapter(lambda r: httpx.Response(200, json={"url": good}))
    assert (await storage.create_signed_upload(path=PATH, content_type="application/pdf", max_bytes=1, expires_in_seconds=1)).url.startswith(f"{BASE}/storage/v1/object/upload/sign/{BUCKET}/")


def test_a_client_that_follows_redirects_is_refused_because_the_apikey_header_would_travel_with_it_and_the_default_client_does_not():
    with pytest.raises(ValueError):
        SupabaseUploadAdapter(BASE, KEY, BUCKET, http=httpx.AsyncClient(follow_redirects=True))
    assert SupabaseUploadAdapter(BASE, KEY, BUCKET)._http.follow_redirects is False


@pytest.mark.asyncio
async def test_the_lifetime_the_browser_is_told_is_clamped_after_now_and_never_beyond_the_assumed_ceiling():
    for exp, expected in ((int(NOW.timestamp()) + 99 * 3600, ASSUMED_SIGNED_URL_SECONDS), (int(NOW.timestamp()) - 5, ASSUMED_SIGNED_URL_SECONDS), (int(NOW.timestamp()) + 60, 60)):
        storage, _ = adapter(lambda r, e=exp: httpx.Response(200, json={"url": f"/object/upload/sign/{BUCKET}/{PATH}?token={jwt_with_exp(e)}"}))
        signed = await storage.create_signed_upload(path=PATH, content_type="application/pdf", max_bytes=1, expires_in_seconds=1)
        assert (signed.expires_at - NOW).total_seconds() == expected, exp


def test_the_wiring_accepts_only_the_protected_bucket_name_and_no_date_in_the_future(monkeypatch):
    assert _wiring(monkeypatch, **{**ALL, "CAREFLOW_STORAGE_BUCKET": "some-other-bucket"}) == (None, None)
    assert _wiring(monkeypatch, **{**ALL, "CAREFLOW_STORAGE_VERIFIED": "2999-01-01"}) == (None, None)
    assert _wiring(monkeypatch, **ALL)[0] is not None


def test_a_document_path_is_only_clinic_and_document_ids_and_the_register_entry_says_synthetic_only_and_not_for_clinical():
    import uuid as _uuid
    from pathlib import Path as _Path

    import yaml as _yaml

    from careflow.adapters.storage import PATH_RE as _PATH_RE
    from careflow.documents import storage_path

    path = storage_path(_uuid.uuid4(), _uuid.uuid4())
    assert _PATH_RE.fullmatch(path) and path.count("/") == 3  # clinic/<id>/doc/<id>: no file name, no patient text, no person's id
    entry = next(c for c in _yaml.safe_load((_Path(__file__).resolve().parent.parent / "data-flow-register.yaml").read_text(encoding="utf-8"))["calls"] if c["id"] == "supabase-storage")
    assert entry["data_policy"] == "synthetic_only" and entry["permitted_in_clinical"] is False and entry["status"] == "approved" and entry["approved_by"]
