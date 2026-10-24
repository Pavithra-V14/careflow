"""careflow.documents: signed upload, server-side re-verification and registration (BUILD-SPEC.md §17.3; package P2-3). Offline: a fake storage
and a fake connection. The promise under test: a row exists only for an object the SERVER verified, and a failure leaves nothing registered."""

import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from careflow.adapters.fake import FakeUpload
from careflow.documents import (
    ALLOWED_TYPES,
    DocumentIntake,
    IdempotencyConflict,
    NotPermitted,
    UploadRejected,
    UploadsDisabled,
    UploadUnavailable,
    document_id_for,
    storage_path,
    verify_stored,
)
from careflow.ports import StoredObject, TransientError

pytestmark = pytest.mark.asyncio

CLINIC, OTHER_CLINIC, STAFF = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
PDF = b"%PDF-1.7\n" + b"x" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 200
SENTINEL = "SENTINEL-FILE-CONTENT-3310"


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeConn:
    def __init__(self, *, allowed=True, existing=None, insert_error=None, existing_after_error=None):
        self.allowed, self.existing, self.insert_error, self.existing_after_error = allowed, existing, insert_error, existing_after_error
        self.inserts: list[dict] = []
        self.selects = 0

    def begin_nested(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def cm():
            yield self

        return cm()

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "from iam.staff_roles" in sql:
            return _Result(1 if self.allowed else None)
        if "insert into docs.documents" in sql:
            if self.insert_error is not None:
                raise self.insert_error
            self.inserts.append(params)
            return _Result(None)
        if "from docs.documents" in sql:
            self.selects += 1
            row = self.existing if (self.insert_error is None or self.selects == 1) else self.existing_after_error
            return _Result(row)
        raise AssertionError(sql)


def db_error(state: str) -> DBAPIError:
    class Orig(Exception):
        sqlstate = state

    return DBAPIError("insert", {}, Orig("x"))


def intake(uploads=None, **kw):
    kw.setdefault("profile", "demo")
    return DocumentIntake(uploads or FakeUpload(), **kw)


async def upload(uploads, key, content, ctype, *, clinic=CLINIC):
    """The browser: gets a ticket, then PUTs whatever it likes at the ticket's path."""
    ticket = await DocumentIntake(uploads, profile="demo").begin(clinic, "referral", ctype if ctype in ALLOWED_TYPES else "application/pdf", key)
    uploads.client_put(ticket.path, content, ctype)
    return ticket


# ---- begin: the server chooses the path --------------------------------------------------------------------------------------------------------


async def test_the_ticket_carries_a_server_chosen_path_derived_from_the_clinic_and_the_key():
    uploads = FakeUpload()
    t1 = await intake(uploads).begin(CLINIC, "referral", "application/pdf", "key-1")
    t1b = await intake(uploads).begin(CLINIC, "referral", "application/pdf", "key-1")
    t2 = await intake(uploads).begin(CLINIC, "referral", "application/pdf", "key-2")
    t3 = await intake(uploads).begin(OTHER_CLINIC, "referral", "application/pdf", "key-1")
    assert t1.document_id == t1b.document_id == document_id_for(CLINIC, "key-1")  # a retry gets the same ticket
    assert len({t1.document_id, t2.document_id, t3.document_id}) == 3 and t1.path == storage_path(CLINIC, t1.document_id)
    assert t1.path.startswith(f"clinic/{CLINIC}/doc/") and t3.path.startswith(f"clinic/{OTHER_CLINIC}/doc/")
    assert uploads.signed[0] == {"path": t1.path, "content_type": "application/pdf", "max_bytes": 20 * 1024 * 1024, "expires_in_seconds": 600}


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"content_type": "text/html"}, "type_not_allowed"),
        ({"content_type": "application/x-msdownload"}, "type_not_allowed"),
        ({"kind": "selfie"}, "invalid_request"),
        ({"idempotency_key": "   "}, "invalid_request"),
        ({"idempotency_key": "k" * 201}, "invalid_request"),
        ({"size_hint": 0}, "empty"),
        ({"size_hint": True}, "empty"),
        ({"size_hint": 20 * 1024 * 1024 + 1}, "too_large"),
    ],
)
async def test_begin_refuses_a_request_that_could_never_be_registered(kwargs, code):
    args = {"kind": "referral", "content_type": "application/pdf", "idempotency_key": "k1"} | kwargs
    uploads = FakeUpload()
    with pytest.raises(UploadRejected) as exc:
        await intake(uploads).begin(CLINIC, args["kind"], args["content_type"], args["idempotency_key"], size_hint=args.get("size_hint"))
    assert exc.value.code == code and uploads.signed == []


async def test_begin_reports_an_unavailable_storage_without_a_ticket():
    uploads = FakeUpload()
    uploads.fail_next(1)
    with pytest.raises(UploadUnavailable):
        await intake(uploads).begin(CLINIC, "referral", "application/pdf", "k1")


# ---- register: the happy path, from what the storage actually holds ------------------------------------------------------------------------------


@pytest.mark.parametrize(("content", "ctype"), [(PDF, "application/pdf"), (PNG, "image/png"), (JPEG, "image/jpeg")])
async def test_a_verified_object_is_registered_with_the_size_type_and_hash_the_server_observed(content, ctype):
    uploads, conn = FakeUpload(), FakeConn()
    ticket = await upload(uploads, "k1", content, ctype)
    result = await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    (insert,) = conn.inserts
    assert (result.document_id, result.replayed, result.content_type, result.size_bytes) == (ticket.document_id, False, ctype, len(content))
    assert insert["p"] == ticket.path and insert["s"] == len(content) and insert["t"] == ctype and insert["u"] == STAFF and insert["o"] == "test"
    import hashlib

    assert insert["h"] == hashlib.sha256(content).hexdigest() == result.sha256
    assert uploads.deleted == []


# ---- register: every verification failure leaves NOTHING registered AND nothing stored ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "ctype", "code"),
    [
        (b"", "application/pdf", "empty"),
        (PDF + b"y" * 1000, "application/pdf", "too_large"),  # with max_bytes lowered below
        (b"<html><script>alert(1)</script></html>" + b"x" * 200, "text/html", "type_not_allowed"),
        (b"MZ\x90\x00" + b"x" * 200, "application/x-msdownload", "type_not_allowed"),
        (PNG, "application/pdf", "content_mismatch"),  # a PNG labelled PDF
        (b"#!/bin/sh\nrm -rf /\n" + b"x" * 200, "application/pdf", "content_mismatch"),  # a script labelled PDF
        (PDF, "image/png", "content_mismatch"),  # a PDF labelled PNG
        (b"%PDF" + b"x" * 200, "application/pdf", "content_mismatch"),  # a truncated magic number
    ],
)
async def test_an_object_that_fails_verification_is_refused_deleted_and_never_registered(content, ctype, code):
    uploads, conn = FakeUpload(), FakeConn()
    ticket = await upload(uploads, "k1", content, ctype)
    with pytest.raises(UploadRejected) as exc:
        await intake(uploads, max_bytes=1000).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    assert exc.value.code == code and exc.value.cleanup_failed is False
    assert conn.inserts == [] and uploads.deleted == [ticket.path] and ticket.path not in uploads.objects


async def test_registering_with_nothing_uploaded_is_refused_and_touches_no_object():
    uploads, conn = FakeUpload(), FakeConn()
    with pytest.raises(UploadRejected) as exc:
        await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "never-uploaded")
    assert exc.value.code == "not_found" and conn.inserts == [] and uploads.deleted == []


async def test_a_client_cannot_register_an_object_it_put_under_another_clinics_path():
    uploads, conn = FakeUpload(), FakeConn()
    their_path = storage_path(OTHER_CLINIC, document_id_for(OTHER_CLINIC, "k1"))
    uploads.client_put(their_path, PDF, "application/pdf")  # a hostile client writes into the other clinic's namespace
    with pytest.raises(UploadRejected) as exc:
        await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")  # and asks to register it as its own
    assert exc.value.code == "not_found" and conn.inserts == [] and uploads.deleted == []
    assert their_path in uploads.objects  # the other clinic's object is untouched


async def test_a_failed_cleanup_is_reported_as_an_orphan_to_reconcile():
    uploads, conn = FakeUpload(), FakeConn()
    await upload(uploads, "k1", PNG, "application/pdf")

    async def failing_delete(path):
        raise TransientError("storage down")

    uploads.delete = failing_delete  # type: ignore[method-assign]
    with pytest.raises(UploadRejected) as exc:
        await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    assert exc.value.code == "content_mismatch" and exc.value.cleanup_failed is True and conn.inserts == []


async def test_an_unreachable_storage_registers_nothing_and_deletes_nothing():
    uploads, conn = FakeUpload(), FakeConn()
    await upload(uploads, "k1", PDF, "application/pdf")
    uploads.fail_next(1)
    with pytest.raises(UploadUnavailable):
        await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    assert conn.inserts == [] and uploads.deleted == []


# ---- register: authorisation, refusal by the database, replay and races ------------------------------------------------------------------------------


async def test_a_caller_who_may_not_register_never_makes_the_server_look_at_storage():
    uploads, conn = FakeUpload(), FakeConn(allowed=False)
    ticket = await upload(uploads, "k1", PDF, "application/pdf")
    calls_before = uploads.calls
    with pytest.raises(NotPermitted):
        await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    assert uploads.calls == calls_before and ticket.path in uploads.objects and conn.inserts == []


async def test_a_row_level_security_refusal_leaves_no_object_and_no_row():
    uploads, conn = FakeUpload(), FakeConn(insert_error=db_error("42501"))
    ticket = await upload(uploads, "k1", PDF, "application/pdf")
    with pytest.raises(NotPermitted):
        await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    assert ticket.path not in uploads.objects and uploads.deleted == [ticket.path] and conn.inserts == []


async def test_any_other_database_failure_also_leaves_no_object_behind_and_propagates():
    uploads, conn = FakeUpload(), FakeConn(insert_error=db_error("08006"))
    ticket = await upload(uploads, "k1", PDF, "application/pdf")
    with pytest.raises(DBAPIError):
        await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    assert ticket.path not in uploads.objects


async def test_a_replay_returns_the_original_and_touches_nothing_in_storage():
    uploads = FakeUpload()
    doc = document_id_for(CLINIC, "k1")
    conn = FakeConn(existing=Row(id=doc, kind="referral", content_type="application/pdf", size_bytes=209, sha256="a" * 64))
    result = await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    assert result.replayed and result.document_id == doc and uploads.calls == 0 and conn.inserts == []


async def test_the_same_key_for_a_different_kind_is_a_conflict():
    doc = document_id_for(CLINIC, "k1")
    conn = FakeConn(existing=Row(id=doc, kind="referral", content_type="application/pdf", size_bytes=1, sha256="a" * 64))
    with pytest.raises(IdempotencyConflict):
        await intake().register(conn, CLINIC, "test", "report", STAFF, "k1")


async def test_losing_a_race_on_the_same_key_returns_the_winners_document_and_does_not_delete_the_shared_object():
    uploads = FakeUpload()
    ticket = await upload(uploads, "k1", PDF, "application/pdf")
    winner = Row(id=ticket.document_id, kind="referral", content_type="application/pdf", size_bytes=len(PDF), sha256="b" * 64)
    conn = FakeConn(existing=None, insert_error=db_error("23505"), existing_after_error=winner)
    result = await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    assert result.replayed and result.document_id == ticket.document_id
    assert uploads.deleted == [] and ticket.path in uploads.objects  # that object belongs to the registered document


# ---- the profile gate ------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["clinical", "", "production", "Clinical ", "anything-else"])
async def test_outside_the_demo_profile_uploads_are_disabled_for_both_steps_and_storage_is_never_touched(profile):
    uploads, conn = FakeUpload(), FakeConn()
    service = DocumentIntake(uploads, profile=profile)
    assert service.enabled is False
    with pytest.raises(UploadsDisabled):
        await service.begin(CLINIC, "referral", "application/pdf", "k1")
    with pytest.raises(UploadsDisabled):
        await service.register(conn, CLINIC, "live", "referral", STAFF, "k1")
    assert uploads.calls == 0 and conn.inserts == []


async def test_demo_is_enabled_and_a_configured_scanner_enables_the_clinical_profile():
    assert DocumentIntake(FakeUpload(), profile=" DEMO ").enabled
    assert not DocumentIntake(FakeUpload(), profile="clinical").enabled
    assert DocumentIntake(FakeUpload(), profile="clinical", scanner_configured=True).enabled


# ---- the pure verifier and the no-leak rule ------------------------------------------------------------------------------------------------------


def stored(**over):
    base = {"path": "p", "size": 100, "content_type": "application/pdf", "head": b"%PDF-1.4", "sha256": "a" * 64}
    return StoredObject(**(base | over))


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({}, None), ({"size": 0}, "empty"), ({"size": -1}, "empty"), ({"size": 1001}, "too_large"), ({"size": 1000}, None),
        ({"content_type": "image/gif", "head": b"GIF89a"}, "type_not_allowed"), ({"head": b"\x89PNG"}, "content_mismatch"),
        ({"sha256": "short"}, "content_mismatch"), ({"sha256": "A" * 64}, "content_mismatch"), ({"sha256": "g" * 64}, "content_mismatch"),
    ],
)
async def test_the_verifier_judges_only_what_the_storage_holds(over, code):
    assert verify_stored(stored(**over), max_bytes=1000) == code


async def test_no_error_or_representation_carries_content_a_path_or_a_hash():
    uploads, conn = FakeUpload(), FakeConn()
    ticket = await upload(uploads, "k1", SENTINEL.encode() + b"x" * 50, "application/pdf")
    with pytest.raises(UploadRejected) as exc:
        await intake(uploads).register(conn, CLINIC, "test", "referral", STAFF, "k1")
    text_ = repr(exc.value) + str(exc.value)
    assert SENTINEL not in text_ and ticket.path not in text_ and str(CLINIC) not in text_
    assert SENTINEL not in repr(stored(head=SENTINEL.encode()))  # the head bytes are hidden from the representation


# ---- reading: re-hash against what was registered (covers the signed-URL overwrite window, BUILD-SPEC.md Appendix B) ----------------------------


class ReadConn:
    def __init__(self, row):
        self.row = row

    async def execute(self, statement, params=None):
        assert "from docs.documents" in str(statement)
        return _Result(self.row)


async def _registered(uploads, content=PDF, key="rd-1"):
    import hashlib

    ticket = await upload(uploads, key, content, "application/pdf")
    row = Row(
        id=ticket.document_id, clinic_id=CLINIC, storage_path=ticket.path, content_type="application/pdf", size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    return ticket, row


async def test_unchanged_bytes_are_returned_after_the_rehash():
    uploads = FakeUpload()
    ticket, row = await _registered(uploads)
    doc = await intake(uploads).read_verified(ReadConn(row), ticket.document_id)
    assert doc.content == PDF and doc.clinic_id == CLINIC and SENTINEL not in repr(doc) and "%PDF" not in repr(doc)


async def test_bytes_overwritten_after_registration_are_refused_and_never_returned():
    from careflow.documents import ContentChanged

    uploads = FakeUpload()
    ticket, row = await _registered(uploads)
    uploads.client_put(ticket.path, PDF.replace(b"x", b"y"), "application/pdf")  # same size, different bytes: the overwrite window
    with pytest.raises(ContentChanged):
        await intake(uploads).read_verified(ReadConn(row), ticket.document_id)
    uploads.client_put(ticket.path, PDF + b"more", "application/pdf")  # a different size
    with pytest.raises(ContentChanged):
        await intake(uploads).read_verified(ReadConn(row), ticket.document_id)


async def test_a_missing_row_or_a_vanished_object_is_one_answer_and_a_disabled_profile_reads_nothing():
    from careflow.documents import DocumentNotFound

    uploads = FakeUpload()
    ticket, row = await _registered(uploads)
    with pytest.raises(DocumentNotFound):
        await intake(uploads).read_verified(ReadConn(None), ticket.document_id)
    uploads.objects.clear()
    with pytest.raises(DocumentNotFound):
        await intake(uploads).read_verified(ReadConn(row), ticket.document_id)
    with pytest.raises(UploadsDisabled):
        await DocumentIntake(uploads, profile="clinical").read_verified(ReadConn(row), ticket.document_id)


async def test_a_storage_outage_while_reading_is_unavailable_not_changed():
    uploads = FakeUpload()
    ticket, row = await _registered(uploads)
    uploads.fail_next()
    with pytest.raises(UploadUnavailable):
        await intake(uploads).read_verified(ReadConn(row), ticket.document_id)
