"""Demographic encryption and identity tokens (BUILD-SPEC.md §17.2; CLAUDE.md rules 6 and 7).

Everything here is local and pure: no network call, no database. Patient plaintext (name, date of
birth, phone) exists only in application memory; `mpi.patients` stores the ciphertext produced by
`DemographicVault.encrypt` and `mpi.identity_tokens` stores the keyed-HMAC tokens produced by
`DemographicVault.identity_token`. The database never sees a demographic in the clear, and the tokens
make lookups possible without a plaintext index.

Keys come from the environment only, never from source control or a file this module writes:

    CAREFLOW_ENC_SECRET_V<n>    32 random bytes, base64: the AES-256-GCM key for key_version n
    CAREFLOW_HMAC_SECRET_V<n>   >= 32 random bytes, base64: the HMAC-SHA256 key for key_version n

Generate one with:  python -c "import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"

Both secrets of a version must be present and must differ from each other. Several versions can be
loaded at once, which is what a manual rotation needs (re-encrypt under the new version while the old
key is still available). The key store and the rotation runbook are separate Phase 1 work; this module
only reads what the environment gives it.

Ciphertext layout (the key_version lives in its own database column, not in the blob):

    blob = nonce (12 bytes, fresh random per call) || AES-256-GCM(ciphertext || 16-byte tag)

A random 96-bit nonce is safe for roughly 2**32 encryptions under one key, far beyond one clinic's
patient count; rotate keys long before that.

Each ciphertext is bound to the field it belongs to (`name`, `dob` or `phone`) as GCM associated data,
so a ciphertext cannot be moved from one column to another and still decrypt.

Message bodies (`msg.messages.body_ct`) use a separate method pair, `encrypt_message_body` /
`decrypt_message_body`, and `message_body` is deliberately NOT one of `FIELDS`, so the generic
`encrypt`/`decrypt` refuse it. Messages grow far faster than patients, so instead of one long-lived AES key
with random nonces, each message gets its OWN key:

    root    = HKDF-SHA256(master_v, info = "careflow/message_body/root/v1")
    subkey  = HKDF-SHA256(root,     info = "careflow/message_body/key/v1/" + message_id)

and the AES-GCM associated data is "careflow/message_body/v1/" + clinic_id + "/" + thread_id + "/" + message_id,
so a ciphertext cannot be moved to another message, thread or clinic. The distinct HKDF labels give domain
separation from the demographic use of the same master key; they do not remove the shared secret (the root is
still derived from it). The version's master key therefore selects both, and `body_key_version` names it. A retry
with the same message id and different plaintext reuses that message's subkey with a new random nonce (harmless).
"""

import base64
import binascii
import hashlib
import hmac
import os
import re
import unicodedata
import uuid
from collections.abc import Mapping
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENC_KEY_BYTES: Final = 32
HMAC_MIN_KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
TAG_BYTES: Final = 16
FIELDS: Final = frozenset({"name", "dob", "phone"})
_KIND = re.compile(r"^[a-z][a-z0-9_]*$")  # identical to the check on mpi.identity_tokens.kind
_ENV_ENC = re.compile(r"^CAREFLOW_ENC_SECRET_V(\d+)$")
_ENV_HMAC = re.compile(r"^CAREFLOW_HMAC_SECRET_V(\d+)$")
_AAD_PREFIX: Final = b"careflow/demographic/v1/"
_MSG_ROOT_INFO: Final = b"careflow/message_body/root/v1"
_MSG_KEY_INFO: Final = b"careflow/message_body/key/v1/"
_MSG_AAD_PREFIX: Final = b"careflow/message_body/v1/"


class CryptoError(Exception):
    """Base class for every failure raised by this module."""


class CryptoConfigError(CryptoError):
    """A key is missing, malformed, or reused; the vault refuses to start rather than run degraded."""


class UnknownKeyVersionError(CryptoError):
    """The requested key_version has no key loaded."""


class DecryptionError(CryptoError):
    """The blob did not authenticate: wrong key version, wrong field, truncated or tampered data."""


def _decode_secret(name: str, raw: str, *, min_bytes: int, exact: bool) -> bytes:
    text = raw.strip()
    if not text:
        raise CryptoConfigError(f"{name} is empty")
    # Accept standard or URL-safe base64, with or without padding; nothing else.
    text = text.replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    try:
        key = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise CryptoConfigError(f"{name} is not valid base64") from None
    if exact and len(key) != min_bytes:
        raise CryptoConfigError(f"{name} must decode to exactly {min_bytes} bytes, got {len(key)}")
    if not exact and len(key) < min_bytes:
        raise CryptoConfigError(f"{name} must decode to at least {min_bytes} bytes, got {len(key)}")
    if len(set(key)) == 1:
        raise CryptoConfigError(f"{name} is a single repeated byte, not a random key")
    return key


def normalize_for_token(value: str) -> str:
    """The canonical form a token is computed over: Unicode NFKC, case-folded, outer whitespace
    stripped, inner whitespace runs collapsed to one space. Raises if nothing is left, because an
    empty value must never produce a token that every other empty value would also match."""
    if not isinstance(value, str):
        raise TypeError("value must be a str")
    text = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not text:
        raise ValueError("value is empty after normalisation; no token can be derived from nothing")
    return text


def _clean_plaintext(plaintext: str) -> str:
    """Line endings to LF, so the stored plaintext is identical whatever platform produced it. The text
    is otherwise preserved exactly (no case change, no stripping): it must read back as entered."""
    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be a str")
    cleaned = plaintext.replace("\r\n", "\n").replace("\r", "\n")
    if not cleaned.strip():
        raise ValueError("plaintext is empty; an absent value is stored as NULL, never as an encrypted blank")
    return cleaned


def _aad(field: str) -> bytes:
    if field not in FIELDS:
        raise ValueError(f"field must be one of {sorted(FIELDS)}, got {field!r}")
    return _AAD_PREFIX + field.encode("ascii")


class DemographicVault:
    """AES-256-GCM for demographics and HMAC-SHA256 for identity tokens, over one or more key versions."""

    def __init__(self, enc_keys: Mapping[int, bytes], hmac_keys: Mapping[int, bytes]) -> None:
        if not enc_keys:
            raise CryptoConfigError("no encryption keys configured")
        if set(enc_keys) != set(hmac_keys):
            raise CryptoConfigError(
                f"encryption and HMAC keys must exist for the same versions "
                f"(enc {sorted(enc_keys)}, hmac {sorted(hmac_keys)})"
            )
        for version in enc_keys:
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise CryptoConfigError(f"key versions are positive integers, got {version!r}")
            if len(enc_keys[version]) != ENC_KEY_BYTES:
                raise CryptoConfigError(f"encryption key V{version} must be {ENC_KEY_BYTES} bytes")
            if len(hmac_keys[version]) < HMAC_MIN_KEY_BYTES:
                raise CryptoConfigError(f"HMAC key V{version} must be at least {HMAC_MIN_KEY_BYTES} bytes")
            if enc_keys[version] == hmac_keys[version]:
                raise CryptoConfigError(f"V{version}: the encryption and HMAC secrets must be different values")
        self._aead = {v: AESGCM(k) for v, k in enc_keys.items()}
        # Only the derived per-version root is kept for message bodies; it cannot be turned back into the master.
        self._msg_root = {
            v: HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_MSG_ROOT_INFO).derive(k)
            for v, k in enc_keys.items()
        }
        self._hmac = {v: bytes(k) for v, k in hmac_keys.items()}

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "DemographicVault":
        """Load every CAREFLOW_ENC_SECRET_V<n> / CAREFLOW_HMAC_SECRET_V<n> pair from the environment.
        Raises CryptoConfigError on a missing, unpaired or malformed key; never returns a partial vault."""
        env = os.environ if environ is None else environ
        enc: dict[int, bytes] = {}
        mac: dict[int, bytes] = {}
        for name, raw in env.items():
            if m := _ENV_ENC.match(name):
                enc[int(m.group(1))] = _decode_secret(name, raw, min_bytes=ENC_KEY_BYTES, exact=True)
            elif m := _ENV_HMAC.match(name):
                mac[int(m.group(1))] = _decode_secret(name, raw, min_bytes=HMAC_MIN_KEY_BYTES, exact=False)
        if not enc and not mac:
            raise CryptoConfigError(
                "no keys found: set CAREFLOW_ENC_SECRET_V1 and CAREFLOW_HMAC_SECRET_V1 (see careflow/crypto.py)"
            )
        for v in sorted(set(enc) - set(mac)):
            raise CryptoConfigError(f"CAREFLOW_ENC_SECRET_V{v} is set but CAREFLOW_HMAC_SECRET_V{v} is missing")
        for v in sorted(set(mac) - set(enc)):
            raise CryptoConfigError(f"CAREFLOW_HMAC_SECRET_V{v} is set but CAREFLOW_ENC_SECRET_V{v} is missing")
        return cls(enc, mac)

    def __repr__(self) -> str:  # never include key material
        return f"DemographicVault(key_versions={self.key_versions})"

    @property
    def key_versions(self) -> list[int]:
        return sorted(self._aead)

    def _enc(self, key_version: int) -> AESGCM:
        try:
            return self._aead[key_version]
        except KeyError:
            raise UnknownKeyVersionError(f"no encryption key loaded for key_version {key_version!r}") from None

    def encrypt(self, plaintext: str, *, field: str, key_version: int) -> bytes:
        """Encrypt one demographic value. A fresh random nonce is generated on every call and packed in
        front of the ciphertext, so encrypting the same value twice gives different blobs."""
        aead = self._enc(key_version)
        data = _clean_plaintext(plaintext).encode("utf-8")
        nonce = os.urandom(NONCE_BYTES)
        return nonce + aead.encrypt(nonce, data, _aad(field))

    def decrypt(self, blob: bytes, *, field: str, key_version: int) -> str:
        """Reverse `encrypt`. Raises DecryptionError for anything that does not authenticate."""
        aead = self._enc(key_version)
        aad = _aad(field)
        if not isinstance(blob, bytes | bytearray | memoryview):
            raise TypeError("blob must be bytes")
        blob = bytes(blob)
        if len(blob) < NONCE_BYTES + TAG_BYTES + 1:
            raise DecryptionError("ciphertext is too short to be valid")
        try:
            return aead.decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad).decode("utf-8")
        except InvalidTag:
            raise DecryptionError(
                "ciphertext failed authentication: wrong key version, wrong field, or the data was altered"
            ) from None

    # ---- message bodies (separate API; see the module docstring) ---------------------------------------

    @staticmethod
    def _message_ids(clinic_id: uuid.UUID, thread_id: uuid.UUID, message_id: uuid.UUID) -> tuple[str, str, str]:
        for name, value in (("clinic_id", clinic_id), ("thread_id", thread_id), ("message_id", message_id)):
            if not isinstance(value, uuid.UUID):
                raise TypeError(f"{name} must be a uuid.UUID")
        return str(clinic_id), str(thread_id), str(message_id)  # canonical lowercase hyphenated text

    def _message_cipher(self, key_version: int, message_id: str) -> AESGCM:
        try:
            root = self._msg_root[key_version]
        except KeyError:
            raise UnknownKeyVersionError(f"no encryption key loaded for key_version {key_version!r}") from None
        subkey = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_MSG_KEY_INFO + message_id.encode("ascii")).derive(root)
        return AESGCM(subkey)

    def encrypt_message_body(
        self, plaintext: str, *, clinic_id: uuid.UUID, thread_id: uuid.UUID, message_id: uuid.UUID, key_version: int
    ) -> bytes:
        """Encrypt one message body: nonce (12 bytes) || AES-256-GCM(ciphertext || tag), under this message's own
        subkey, bound to (clinic, thread, message). Line endings are normalised to LF and blank text is refused."""
        clinic, thread, message = self._message_ids(clinic_id, thread_id, message_id)
        cipher = self._message_cipher(key_version, message)
        data = _clean_plaintext(plaintext).encode("utf-8")
        nonce = os.urandom(NONCE_BYTES)
        aad = _MSG_AAD_PREFIX + f"{clinic}/{thread}/{message}".encode("ascii")
        return nonce + cipher.encrypt(nonce, data, aad)

    def decrypt_message_body(
        self, blob: bytes, *, clinic_id: uuid.UUID, thread_id: uuid.UUID, message_id: uuid.UUID, key_version: int
    ) -> str:
        """Reverse `encrypt_message_body`. Raises DecryptionError for anything that does not authenticate (wrong
        version, wrong clinic/thread/message, truncated or altered). Callers: careflow/messages.py and
        careflow/rotation.py only (tests/test_messages.py enforces it); the text is never logged or put in an error."""
        clinic, thread, message = self._message_ids(clinic_id, thread_id, message_id)
        cipher = self._message_cipher(key_version, message)
        if not isinstance(blob, bytes | bytearray | memoryview):
            raise TypeError("blob must be bytes")
        blob = bytes(blob)
        if len(blob) < NONCE_BYTES + TAG_BYTES + 1:
            raise DecryptionError("ciphertext is too short to be valid")
        aad = _MSG_AAD_PREFIX + f"{clinic}/{thread}/{message}".encode("ascii")
        try:
            return cipher.decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad).decode("utf-8")
        except InvalidTag:
            raise DecryptionError(
                "ciphertext failed authentication: wrong key version, wrong message, or the data was altered"
            ) from None

    def identity_token(self, kind: str, value: str, *, key_version: int) -> bytes:
        """Deterministic keyed HMAC-SHA256 (32 bytes) of `value` after `normalize_for_token`, for
        `mpi.identity_tokens`. `kind` (e.g. 'phone') is mixed in so the same text under two kinds yields
        unrelated tokens. Which kinds exist, and how a phone number is canonicalised before it gets here,
        belongs to the matching code."""
        if not isinstance(kind, str) or not _KIND.match(kind):
            raise ValueError(f"kind must match {_KIND.pattern}, got {kind!r}")
        try:
            key = self._hmac[key_version]
        except KeyError:
            raise UnknownKeyVersionError(f"no HMAC key loaded for key_version {key_version!r}") from None
        message = kind.encode("ascii") + b"\x00" + normalize_for_token(value).encode("utf-8")
        return hmac.new(key, message, hashlib.sha256).digest()
