"""Message bodies: encrypt before they reach the database, decrypt in exactly one place (BUILD-SPEC.md §9.1, §17.1,
§17.2, §17.6; CLAUDE.md rules 6 and 7).

`msg.messages.body_ct` holds ciphertext only (migration 0011). This module is the ONLY one, besides
`careflow/crypto.py` (which defines the primitive) and `careflow/rotation.py` (which re-encrypts under a new key
version), that may call `DemographicVault.decrypt_message_body`; `tests/test_messages.py` enforces that with an AST
scan. There is deliberately no read route yet: when a staff read route is added it calls `decrypt_body` here, and it
adds its own case to the role-matrix test in the same change.

What this module never does: log a body, put a body in an exception message, return a body from anything but
`decrypt_body`, or keep one after the call. Errors name the problem ("too long", "blank"), never the text.

The length cap is a technical guard (it bounds the ciphertext column and the work done per message), not a policy:
10,000 characters of at most 4 UTF-8 bytes each, plus the 28 bytes of nonce and tag, is 40,028 bytes, which fits the
`octet_length(body_ct) <= 40100` CHECK in 0011.
"""

import uuid
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.crypto import DecryptionError, DemographicVault, UnknownKeyVersionError
from careflow.rules import (
    EMPTY_RULE_SET_HASH,
    Evaluation,
    RedFlagEngine,
    resolve_profile,
)

MAX_BODY_CHARS: Final = 10_000


class MessageError(Exception):
    """The body was refused. The message never contains the body."""


class MessageNotFound(MessageError):
    """No such message, or the caller's role cannot see it (the two look the same on purpose)."""


class MessageErased(MessageError):
    """The body was erased; there is nothing left to screen."""


@dataclass(frozen=True)
class EncryptedBody:
    """What goes into `msg.messages`: the client-chosen message id (the ciphertext is bound to it), the ciphertext
    and the key version. `repr` hides the ciphertext so it cannot end up in a log line by accident."""

    message_id: uuid.UUID
    body_ct: bytes = field(repr=False)
    body_key_version: int


def encrypt_body(
    vault: DemographicVault,
    plaintext: str,
    *,
    clinic_id: uuid.UUID,
    thread_id: uuid.UUID,
    key_version: int,
    message_id: uuid.UUID | None = None,
) -> EncryptedBody:
    """Validate and encrypt a message body for insertion. The id is generated here unless the caller supplies one
    (a retry must reuse the id it first used, so that a replay recognises the same message)."""
    if not isinstance(plaintext, str):
        raise TypeError("a message body must be a str")
    if not plaintext.strip():
        raise MessageError("a message body cannot be blank")
    if len(plaintext) > MAX_BODY_CHARS:
        raise MessageError(f"a message body is limited to {MAX_BODY_CHARS} characters")
    mid = message_id or uuid.uuid4()
    try:
        blob = vault.encrypt_message_body(plaintext, clinic_id=clinic_id, thread_id=thread_id, message_id=mid, key_version=key_version)
    except UnicodeEncodeError:
        # A lone surrogate cannot be encoded. The built-in error carries the offending text, so it is replaced here.
        raise MessageError("a message body must be valid text") from None
    return EncryptedBody(mid, blob, key_version)


def decrypt_body(
    vault: DemographicVault,
    body_ct: bytes,
    *,
    clinic_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    key_version: int,
) -> str:
    """The one place a stored body is turned back into text. The caller owns the result and must not log it."""
    return vault.decrypt_message_body(
        bytes(body_ct), clinic_id=clinic_id, thread_id=thread_id, message_id=message_id, key_version=key_version
    )


@dataclass(frozen=True)
class ScreenedMessage:
    """What a screening of a stored message produced: ids and enums only. There is no text, rule phrase or patient detail here."""

    message_id: uuid.UUID
    screening_id: uuid.UUID
    outcome: str  # what the row holds: red_flag, unassessable or clear
    replayed: bool
    patient_reply: str | None  # not_required, or pending_clinician_template: NO reply is sent by anything yet
    # For the CALLER's operational log, never for a response: the clinic, and the CLASS NAME of the error when the body could not be read
    # (DecryptionError for a tampered or mis-bound body, UnknownKeyVersionError for a key version that is not loaded). Never any text.
    clinic_id: uuid.UUID
    unreadable_reason: str | None = None


async def screen_stored_message(
    conn: AsyncConnection,
    vault: DemographicVault,
    engine: RedFlagEngine,
    message_id: uuid.UUID,
    *,
    idempotency_key: str,
    profile: str | None = None,
) -> ScreenedMessage:
    """Screen a STORED message for red flags and record the result (BUILD-SPEC.md §5.2, §12.1; CLAUDE.md rules 2 and 3).

    The body is decrypted here, held in memory only for the rule engine, and dropped: it is never logged, never put in an error and
    never returned. The deterministic rules run first and alone (no model). A red flag raises the immediate nurse task through
    msg.record_screening; NO emergency reply is sent or written (a clinician-approved template does not exist yet, so the screening
    row records patient_reply = pending_clinician_template).

    Unknown is not clear: a body that cannot be read, whether it fails authentication (tampering, a mis-bound ciphertext) or was
    encrypted under a key version this process does not have, is recorded as UNASSESSABLE, which raises a nurse task, rather than
    raising an error nobody sees or leaving the message unscreened. The result then carries `unreadable_reason` (an error CLASS NAME
    only) so the route layer can log a warning; this module logs nothing.

    `conn` is inside a transaction with the verified claims set; row-level security decides which messages the caller can see, so an
    invisible message and a missing one are both MessageNotFound.
    """
    row = (
        await conn.execute(
            text(
                "select id, clinic_id, origin, thread_id, body_ct, body_key_version, erased_at from msg.messages where id = :i"
            ),
            {"i": message_id},
        )
    ).first()
    if row is None:
        raise MessageNotFound("message not found")
    if row.erased_at is not None or row.body_ct is None:
        raise MessageErased("the message body was erased")
    resolved = profile or resolve_profile()
    unreadable: str | None = None
    try:
        plaintext = decrypt_body(
            vault, row.body_ct, clinic_id=row.clinic_id, thread_id=row.thread_id, message_id=row.id, key_version=row.body_key_version
        )
    except (UnknownKeyVersionError, DecryptionError) as exc:
        unreadable = type(exc).__name__  # the class only: the exception text is never kept
        evaluation = Evaluation("unassessable", (), resolved, 0, 0, ("message body could not be read",), EMPTY_RULE_SET_HASH)  # type: ignore[arg-type]
        result = await engine.record_screening(conn, row.clinic_id, row.origin, message_id, evaluation, idempotency_key=idempotency_key)
    else:
        _, result = await engine.evaluate_and_escalate(
            conn, row.clinic_id, row.origin, plaintext, message_id, profile=resolved, idempotency_key=idempotency_key  # type: ignore[arg-type]
        )
        del plaintext
    reply = (
        await conn.execute(text("select patient_reply from msg.screenings where id = :s"), {"s": result.screening_id})
    ).scalar_one_or_none()
    return ScreenedMessage(message_id, result.screening_id, result.outcome, result.replayed, reply, row.clinic_id, unreadable)
