"""Document intake: a signed upload, server-side re-verification, and registration (BUILD-SPEC.md §10.4, §11.5, §17.3; CLAUDE.md rules 5, 6, 7).
Package P2-3. Table: docs.documents (migration 0017). Storage: behind `UploadPort` (docs/design/uploads-supabase-storage.md).

THE PROMISE (§17.3: "server-side re-verification of the file ... never trust the client"): a document row exists ONLY for an object the SERVER has
looked at and accepted. The browser is handed a signed URL for a path the server chose; it may PUT anything there. Nothing it says counts:

  * the PATH is derived from the clinic and the idempotency key (document id = uuid5 of both), never supplied by the client, so a client can neither
    name another clinic's object nor register a path of its choosing;
  * the SIZE, the TYPE and the CONTENT are read back from the storage by `UploadPort.describe`: the stored size must be 1 byte to MAX_UPLOAD_BYTES,
    the stored type must be on the allowlist (PDF, JPEG, PNG), and the first bytes must carry that type's magic number (a ".pdf" that is a script,
    or a PNG labelled PDF, is refused);
  * the object must EXIST at the claimed path (a registration for nothing is refused).

NOTHING HALF-REGISTERED. Registration is verify, then one INSERT. If verification fails, or the insert is refused (row-level security, a conflicting
key, anything), the stored object is DELETED (best effort) and no row exists. Only the case where the caller's own transaction fails to COMMIT after
a successful insert can leave an object with no row; a periodic reconciliation of storage against `docs.documents` is the answer and is recorded as an
open item. A replay (same clinic and key) returns the original and touches nothing in storage; a replay with different parameters is a conflict.

REAL UPLOADS ARE DISABLED OUTSIDE THE DEMO PROFILE. BUILD-SPEC §11.5: malware scanning on uploads is required before real use, and Supabase Storage has
none. Unless a scanner PORT is wired (`scanner=` a `MalwareScannerPort`; none is wired anywhere yet, only the fake exists) any profile other than `demo` (an unrecognised
one counts as clinical) refuses both steps with `UploadsDisabled`.

WITH A SCANNER WIRED: registration scans the stored bytes (`infected` -> the object is deleted and the upload is refused as `malware_detected`; `not_scanned` -> the document is
registered but HELD) and every read for use (`read_verified`, after the re-hash) scans again, because signatures change. A verdict other than an explicit `clean` raises
`DocumentHeld` and the bytes are never returned. In `demo` with no scanner the documents are synthetic only and are read unscanned (`scan` is reported as `not_checked`).

Nothing here logs. Error codes are short fixed words; no file name, content, path, hash or size ever appears in an exception message.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.malware import MalwareScannerPort, hold_reason, scan_or_hold
from careflow.ports import SignedUpload, StoredObject, TransientError, UploadPort

# Content type -> the magic numbers its bytes must start with. Anything not here is refused.
ALLOWED_TYPES: Final[dict[str, tuple[bytes, ...]]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}
KINDS: Final = ("referral", "report", "other")
ORIGINS: Final = ("live", "test", "eval", "canary")
MAX_UPLOAD_BYTES: Final = 20 * 1024 * 1024  # a technical bound; the table's hard ceiling is 100 MiB
SIGNED_URL_SECONDS: Final = 600
_ID_NAMESPACE: Final = uuid.UUID("5f0b8f3e-6d0e-4c52-9a53-0f8b6f3f0d11")  # fixed: document ids are uuid5(namespace, "clinic|key")
_UNIQUE_VIOLATION: Final = "23505"
_INSUFFICIENT_PRIVILEGE: Final = "42501"


class DocumentError(Exception):
    """Base class. Messages are fixed words; they never carry a name, path, hash, size or any content."""


class UploadsDisabled(DocumentError):
    """Real uploads are not allowed in this profile (no malware scanner is configured)."""


class UploadRejected(DocumentError):
    """The file or the request failed verification. `code` is one of: invalid_request, type_not_allowed, empty, too_large, not_found,
    content_mismatch. `cleanup_failed` is True if the stored object could not be removed (it is then an orphan to reconcile)."""

    def __init__(self, code: str, *, cleanup_failed: bool = False) -> None:
        super().__init__(code)
        self.code, self.cleanup_failed = code, cleanup_failed


class UploadUnavailable(DocumentError):
    """The storage could not be reached; nothing was registered and nothing needs cleaning. Retry."""


class NotPermitted(DocumentError):
    """The caller may not register documents in this clinic (only front desk and nurse may)."""


class DocumentNotFound(DocumentError):
    """No registered document with this id is visible to the caller, or its object is gone from storage."""


class DocumentHeld(DocumentError):
    """The scanner did not say `clean` (it found something, or it could not examine the file): the document is held for a person and its bytes are NOT returned. `reason` is a
    fixed word: `malware_suspected` or `not_scanned`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ContentChanged(DocumentError):
    """The stored bytes no longer match the size and sha256 recorded at registration. The bytes are NOT returned."""


class IdempotencyConflict(DocumentError):
    """This idempotency key was already used for a different document request."""


@dataclass(frozen=True)
class UploadTicket:
    document_id: uuid.UUID
    path: str
    signed: SignedUpload


@dataclass(frozen=True)
class RegisteredDocument:
    document_id: uuid.UUID
    kind: str
    content_type: str
    size_bytes: int
    sha256: str
    replayed: bool = False
    scan: str = "not_checked"  # clean | not_scanned (registered but held) | not_checked (demo, no scanner wired)


@dataclass(frozen=True)
class VerifiedDocument:
    document_id: uuid.UUID
    clinic_id: uuid.UUID
    content_type: str
    content: bytes = field(repr=False)  # patient data: never in a repr


def document_id_for(clinic_id: uuid.UUID, idempotency_key: str) -> uuid.UUID:
    return uuid.uuid5(_ID_NAMESPACE, f"{clinic_id}|{idempotency_key}")


def storage_path(clinic_id: uuid.UUID, document_id: uuid.UUID) -> str:
    """The ONLY path a document ever has. 0017's CHECK requires exactly this shape, so a row cannot point anywhere else."""
    return f"clinic/{clinic_id}/doc/{document_id}"


def _key(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise UploadRejected("invalid_request")
    return value


def _check_kind(kind: object) -> str:
    if kind not in KINDS:
        raise UploadRejected("invalid_request")
    return kind  # type: ignore[return-value]


def verify_stored(obj: StoredObject, *, max_bytes: int) -> str | None:
    """None if the stored object is acceptable, otherwise the rejection code. Everything is judged from what the storage holds."""
    if obj.size <= 0:
        return "empty"
    if obj.size > max_bytes:
        return "too_large"
    magic = ALLOWED_TYPES.get(obj.content_type)
    if magic is None:
        return "type_not_allowed"
    if not any(obj.head.startswith(m) for m in magic):
        return "content_mismatch"  # the bytes are not what the label says
    if len(obj.sha256) != 64 or any(c not in "0123456789abcdef" for c in obj.sha256):
        return "content_mismatch"
    return None


class DocumentIntake:
    def __init__(
        self, uploads: UploadPort, *, profile: str, scanner: MalwareScannerPort | None = None, max_bytes: int = MAX_UPLOAD_BYTES,
        expires_in_seconds: int = SIGNED_URL_SECONDS,
    ) -> None:
        self._uploads, self._scanner, self._max, self._expires = uploads, scanner, max_bytes, expires_in_seconds
        # Only `demo` is allowed without a scanner; anything else, an unrecognised name included, is treated as clinical.
        self._enabled = (profile or "").strip().lower() == "demo" or scanner is not None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def scanner_configured(self) -> bool:
        return self._scanner is not None

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise UploadsDisabled("uploads are disabled in this profile: no malware scanner is configured")

    # ---- step 1: hand the browser a signed URL ---------------------------------------------------------------------------------------------

    async def begin(self, clinic_id: uuid.UUID, kind: str, content_type: str, idempotency_key: str, *, size_hint: int | None = None) -> UploadTicket:
        self._require_enabled()
        _check_kind(kind)
        key = _key(idempotency_key)
        if content_type not in ALLOWED_TYPES:
            raise UploadRejected("type_not_allowed")
        if size_hint is not None:
            if isinstance(size_hint, bool) or not isinstance(size_hint, int) or size_hint <= 0:
                raise UploadRejected("empty")
            if size_hint > self._max:
                raise UploadRejected("too_large")
        document_id = document_id_for(clinic_id, key)
        path = storage_path(clinic_id, document_id)
        try:
            signed = await self._uploads.create_signed_upload(path=path, content_type=content_type, max_bytes=self._max, expires_in_seconds=self._expires)
        except TransientError:
            raise UploadUnavailable("storage is unavailable") from None
        return UploadTicket(document_id, path, signed)

    # ---- step 2: verify what was uploaded, then register it ---------------------------------------------------------------------------------

    async def register(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str, kind: str, registered_by: uuid.UUID, idempotency_key: str
    ) -> RegisteredDocument:
        self._require_enabled()
        _check_kind(kind)
        key = _key(idempotency_key)
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}")
        document_id = document_id_for(clinic_id, key)
        path = storage_path(clinic_id, document_id)

        # Authorise BEFORE touching storage, so a caller who may not register here cannot even make the server look at (or delete) an object.
        # Row-level security remains the real enforcement; this keeps a refused caller away from storage.
        allowed = (
            await conn.execute(
                text(
                    "select 1 from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id "
                    "where sr.account_id = :u and sr.clinic_id = :c and sr.role in ('front_desk', 'nurse') and sr.revoked_at is null "
                    "and a.status = 'active' and a.account_type = 'staff'"
                ),
                {"u": registered_by, "c": clinic_id},
            )
        ).first()
        if allowed is None:
            raise NotPermitted("only front desk and nurse of the clinic may register documents")

        prior = await self._existing(conn, clinic_id, key)
        if prior is not None:
            return self._replay(prior, document_id, kind)

        try:
            stored = await self._uploads.describe(path)
        except TransientError:
            raise UploadUnavailable("storage is unavailable") from None
        if stored is None:
            raise UploadRejected("not_found")  # nothing to clean: there is no object
        problem = verify_stored(stored, max_bytes=self._max)
        if problem is not None:
            raise UploadRejected(problem, cleanup_failed=not await self._cleanup(path))
        scan = await self._scan_stored(path)
        if scan == "infected":
            raise UploadRejected("malware_detected", cleanup_failed=not await self._cleanup(path))

        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, "
                        "idempotency_key) values (:i, :c, :o, :k, :p, :t, :s, :h, :u, :ik)"
                    ),
                    {"i": document_id, "c": clinic_id, "o": origin, "k": kind, "p": path, "t": stored.content_type, "s": stored.size,
                     "h": stored.sha256, "u": registered_by, "ik": key},
                )
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state == _UNIQUE_VIOLATION:  # a concurrent registration with the same key won: its object is THIS object, so do not delete it
                again = await self._existing(conn, clinic_id, key)
                if again is not None:
                    return self._replay(again, document_id, kind)
            cleaned = await self._cleanup(path)  # the insert did not happen: leave no object behind
            if state == _INSUFFICIENT_PRIVILEGE:
                raise NotPermitted("only front desk and nurse of the clinic may register documents") from None
            if state == _UNIQUE_VIOLATION:
                raise IdempotencyConflict("this idempotency key was already used by another request") from None
            if not cleaned:
                exc.add_note("the stored object could not be removed")  # no content in the note; the caller's error handling sees the DB error
            raise
        return RegisteredDocument(document_id, kind, stored.content_type, stored.size, stored.sha256, scan=scan)

    # ---- step 3: any worker that is about to USE the document ----------------------------------------------------------------------------

    async def read_verified(self, conn: AsyncConnection, document_id: uuid.UUID) -> VerifiedDocument:
        """The bytes of a registered document, but only if they still hash to what was recorded at registration. Covers the window in which a
        signed upload URL might still overwrite the object (BUILD-SPEC.md Appendix B): bytes that changed are refused (`ContentChanged`) and
        never returned. `conn` carries the caller's claims, so row-level security decides whether the document is visible; a missing,
        invisible or vanished document is one answer."""
        self._require_enabled()
        row = (
            await conn.execute(
                text("select id, clinic_id, storage_path, content_type, size_bytes, sha256 from docs.documents where id = :i"), {"i": document_id}
            )
        ).first()
        if row is None:
            raise DocumentNotFound("no such document")
        try:
            content = await self._uploads.read(row.storage_path)
        except TransientError:
            raise UploadUnavailable("storage is unavailable") from None
        if content is None:
            raise DocumentNotFound("no such document")
        if len(content) != row.size_bytes or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), row.sha256):
            raise ContentChanged("the stored content no longer matches what was registered")
        if self._scanner is not None:  # signatures change, so every read for use is scanned, and only an explicit `clean` returns the bytes
            verdict = await scan_or_hold(self._scanner, content)
            if not verdict.clean:
                raise DocumentHeld(hold_reason(verdict))
        return VerifiedDocument(row.id, row.clinic_id, row.content_type, content)

    # ---- helpers -----------------------------------------------------------------------------------------------------------------------------

    async def _scan_stored(self, path: str) -> str:
        """`clean`, `infected`, `not_scanned`, or `not_checked` (no scanner wired). A storage failure while fetching the bytes is `not_scanned`: never a pass."""
        if self._scanner is None:
            return "not_checked"
        try:
            content = await self._uploads.read(path)
        except TransientError:
            return "not_scanned"
        if content is None:
            return "not_scanned"
        return (await scan_or_hold(self._scanner, content)).verdict


    @staticmethod
    async def _existing(conn: AsyncConnection, clinic_id: uuid.UUID, key: str):
        return (
            await conn.execute(
                text("select id, kind, content_type, size_bytes, sha256 from docs.documents where clinic_id = :c and idempotency_key = :k"),
                {"c": clinic_id, "k": key},
            )
        ).first()

    @staticmethod
    def _replay(row, document_id: uuid.UUID, kind: str) -> RegisteredDocument:
        if row.id != document_id or row.kind != kind:
            raise IdempotencyConflict("this idempotency key was used for a different request")
        return RegisteredDocument(row.id, row.kind, row.content_type, row.size_bytes, row.sha256, replayed=True)

    async def _cleanup(self, path: str) -> bool:
        """Remove a stored object that must not outlive a failed registration. True if it is gone (or was never there)."""
        try:
            await self._uploads.delete(path)
        except TransientError:
            return False
        return True
