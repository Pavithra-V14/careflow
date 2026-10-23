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

from careflow.crypto import DemographicVault

MAX_BODY_CHARS: Final = 10_000


class MessageError(Exception):
    """The body was refused. The message never contains the body."""


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
