"""The real `UploadPort`: Supabase Storage over its REST API (docs/design/uploads-supabase-storage.md; owner decision 2026-10-30: dev and CI projects, SYNTHETIC data only).

WHAT IT IS. A private bucket, never a public URL. The server (this adapter, holding the project's secret key) hands the browser a short-lived SIGNED UPLOAD URL for a path THE SERVER chose; the
browser PUTs straight to Supabase; the server then reads the object back with its own credentials (describe/read) and checks it (careflow/documents.py). The object path is `clinic/<clinic id>/doc/<document id>`:
ids only, no patient text, no file name. Nothing here logs, and no error message carries a path, a body, a key or a URL.

WHAT IT DOES NOT DO. It does not scan (the scanner port does, before anything is shown as clean: careflow/malware.py), it does not decide the bucket's limits (the bucket is created in the dashboard
with a size limit and allowed types, and the server re-verifies size, type and magic number itself), and it cannot make a signed upload URL single-use or short: the URL's lifetime is Supabase's, and an
object can be overwritten while the URL is valid. That is why `DocumentIntake.read_verified` re-hashes every read (ContentChanged) and why registration verifies what is stored, not what was promised.

NOT YET VERIFIED AGAINST THE REAL SERVICE (Appendix B discipline, CLAUDE.md): the endpoint paths and response shapes below are from the documented REST API and are exercised only against a documented
mock (tests/test_storage_adapter.py). Nothing in the application builds this adapter until `CAREFLOW_STORAGE_VERIFIED` names a date, which the owner sets after `scripts/storage_spike.py` has been run against the
dev project (it makes the real calls and prints what it saw: signing, the PUT, read-back, overwrite while the URL is valid, the bucket's size and type limits, an unauthenticated read being refused, delete).
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import quote, urlsplit

import httpx

from careflow.ports import PermanentError, SignedUpload, StoredObject, TransientError

DOCUMENT_BUCKET: Final = "careflow-documents"  # the one bucket the storage policies (db/migrations/0027) protect; the wiring refuses any other name
# The only path shape a document ever has (careflow/documents.py `storage_path`). Anything else is refused BEFORE a request is built.
PATH_RE: Final = re.compile(r"\Aclinic/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/doc/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
BUCKET_RE: Final = re.compile(r"\A[a-z0-9][a-z0-9_-]{2,62}\Z")
# Explicit timeouts (BUILD-SPEC 16.1 sets one for every outbound call): short to connect, bounded to move 20 MiB, and a hard ceiling on one whole download.
TIMEOUT: Final = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
WHOLE_CALL_SECONDS: Final = 60.0
DEFAULT_MAX_OBJECT_BYTES: Final = 20 * 1024 * 1024
ASSUMED_SIGNED_URL_SECONDS: Final = 2 * 60 * 60  # used when the signed token cannot be read, and as the CEILING of a lifetime the browser is told: an assumption to be confirmed by the spike

# httpx logs every request URL at INFO and httpcore may log headers at DEBUG: this module logs nothing itself, so keep the transport quiet too (the key is a header).
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _fail(response: httpx.Response) -> Exception:
    """A fixed-words error for a response that is not a success; never the body, the URL or a header."""
    if response.status_code in (401, 403):
        return PermanentError("storage refused the server's credentials")
    if response.status_code == 429 or response.status_code >= 500:
        return TransientError("storage is unavailable")
    return PermanentError("storage rejected the request")


def _missing(response: httpx.Response) -> bool:
    """A missing OBJECT: 404, or 400 whose JSON says the object was not found. A missing BUCKET ("Bucket not found") is a misconfiguration, never "no object" (it would make delete a silent no-op)."""
    text = response.text.lower()[:400]
    if "bucket" in text and "not found" in text:
        return False
    if response.status_code == 404:
        return "bucket" not in text
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    message = f"{body.get('error', '')} {body.get('message', '')}".lower()
    return "object not found" in message or (str(body.get("statusCode")) == "404" and "not_found" in message.replace(" ", "_"))


def _token_expiry(token: str) -> datetime | None:
    """The `exp` claim of the signed-upload token the SERVICE issued (read, not verified: it is our own request's answer)."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (IndexError, ValueError, KeyError, TypeError):
        return None


class SupabaseUploadAdapter:
    def __init__(
        self, base_url: str, secret_key: str, bucket: str, *, http: httpx.AsyncClient | None = None, max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
        now: Any = None,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")):
            raise ValueError("the storage base URL must be https")
        if not secret_key or not BUCKET_RE.fullmatch(bucket or ""):
            raise ValueError("a secret key and a valid bucket name are required")
        self._base = base_url.rstrip("/") + "/storage/v1"
        self._host = parts.netloc
        self._bucket = bucket
        self._max = max_object_bytes
        self._now = now or (lambda: datetime.now(UTC))
        # The `apikey` header is what the platform's gateway reads; a legacy JWT-format key is also sent as a bearer token.
        self._headers = {"apikey": secret_key, **({"Authorization": f"Bearer {secret_key}"} if secret_key.startswith("eyJ") else {})}
        if http is not None and http.follow_redirects:
            raise ValueError("the storage client must not follow redirects (the apikey header would travel to another host)")
        self._http = http or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        self._scheme = parts.scheme

    def __repr__(self) -> str:  # never the key
        return f"SupabaseUploadAdapter(bucket={self._bucket!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- helpers ------------------------------------------------------------------------------------------------------------------------------

    def _check(self, path: str) -> str:
        if not isinstance(path, str) or not PATH_RE.fullmatch(path):
            raise PermanentError("storage path is not a document path")
        return quote(path, safe="/")

    async def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        try:
            return await asyncio.wait_for(self._http.request(method, url, headers=self._headers, **kw), WHOLE_CALL_SECONDS)
        except (httpx.HTTPError, TimeoutError):
            raise TransientError("storage is unreachable") from None

    # ---- UploadPort -----------------------------------------------------------------------------------------------------------------------------

    async def create_signed_upload(self, *, path: str, content_type: str, max_bytes: int, expires_in_seconds: int) -> SignedUpload:
        """A signed URL the browser may PUT ONE object to, at `path`. `content_type`, `max_bytes` and `expires_in_seconds` are the CALLER's wishes: the service enforces its own lifetime and the
        bucket's limits, and the server re-verifies everything it later reads, so none of them is relied on."""
        quoted = self._check(path)
        response = await self._request("POST", f"{self._base}/object/upload/sign/{self._bucket}/{quoted}")
        if response.status_code != 200:
            raise _fail(response)
        try:
            body = response.json()
            relative = body["url"]
            if not isinstance(relative, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            raise PermanentError("storage answered with an unexpected shape") from None
        absolute = relative if relative.startswith(("https://", "http://")) else self._base + (relative if relative.startswith("/") else "/" + relative)
        target = urlsplit(absolute)
        if target.netloc != self._host or target.scheme != self._scheme or not target.path.startswith(f"/storage/v1/object/upload/sign/{self._bucket}/"):
            raise PermanentError("storage answered with a URL that is not a signed upload for our own bucket")  # the browser is never handed a URL we did not expect
        found = re.search(r"[?&]token=([^&]+)", absolute)
        token = str(body.get("token") or (found.group(1) if found else ""))
        now = self._now()
        claimed = _token_expiry(token)
        # The token's `exp` is read, not verified (it is our own request's answer over TLS): it only informs what the browser is TOLD, so it is clamped to a moment after now and no later than the assumed ceiling.
        expires_at = min(claimed, now + timedelta(seconds=ASSUMED_SIGNED_URL_SECONDS)) if claimed is not None and claimed > now else now + timedelta(seconds=ASSUMED_SIGNED_URL_SECONDS)
        return SignedUpload(absolute, path, expires_at)

    async def _download(self, path: str) -> tuple[bytes, str, bool] | None:
        """(bytes up to the cap, content type, truncated) or None if there is no object. Streams and stops one byte past the cap: an oversize object is never held in memory."""
        quoted = self._check(path)
        url = f"{self._base}/object/authenticated/{self._bucket}/{quoted}"
        try:
            async with asyncio.timeout(WHOLE_CALL_SECONDS), self._http.stream("GET", url, headers=self._headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    if _missing(response):
                        return None
                    raise _fail(response)
                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > self._max:
                        return bytes(buffer[: self._max + 1]), response.headers.get("content-type", "").split(";")[0].strip().lower(), True
                return bytes(buffer), response.headers.get("content-type", "").split(";")[0].strip().lower(), False
        except (httpx.HTTPError, TimeoutError):
            raise TransientError("storage is unreachable") from None

    async def describe(self, path: str) -> StoredObject | None:
        got = await self._download(path)
        if got is None:
            return None
        data, content_type, _ = got
        return StoredObject(path, len(data), content_type, data[:16], hashlib.sha256(data).hexdigest())

    async def read(self, path: str) -> bytes | None:
        got = await self._download(path)
        if got is None:
            return None
        data, _, truncated = got
        if truncated:
            raise PermanentError("stored object is larger than the allowed size")
        return data

    async def delete(self, path: str) -> None:
        self._check(path)
        response = await self._request("DELETE", f"{self._base}/object/{self._bucket}", json={"prefixes": [path]})
        if response.status_code == 200 or _missing(response):
            return  # already gone is the same as deleted
        raise _fail(response)
