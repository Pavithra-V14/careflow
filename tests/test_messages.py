"""Message bodies (careflow/messages.py, the message half of careflow/crypto.py and careflow/rotation.py; migration 0011).
Pure: no network, no database. Test keys are generated per run; nothing secret is committed.
"""

import ast
import asyncio
import base64
import logging
import os
import uuid
from pathlib import Path

import pytest

from careflow import rotation
from careflow.crypto import DecryptionError, DemographicVault, UnknownKeyVersionError
from careflow.messages import (
    MAX_BODY_CHARS,
    EncryptedBody,
    MessageError,
    decrypt_body,
    encrypt_body,
)

ROOT = Path(__file__).resolve().parent.parent


def _b64() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def vault():
    return DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V{v}": _b64() for v in (1, 2) for k in ("ENC", "HMAC")})


IDS = {"clinic_id": uuid.uuid4(), "thread_id": uuid.uuid4()}


def test_encrypt_then_decrypt_round_trips_and_returns_the_row_to_insert(vault):
    enc = encrypt_body(vault, "Can we move the appointment?", key_version=1, **IDS)
    assert isinstance(enc, EncryptedBody) and enc.body_key_version == 1
    assert decrypt_body(vault, enc.body_ct, message_id=enc.message_id, key_version=1, **IDS) == "Can we move the appointment?"


def test_a_retry_reuses_the_supplied_message_id(vault):
    mid = uuid.uuid4()
    assert encrypt_body(vault, "x", key_version=1, message_id=mid, **IDS).message_id == mid


def test_the_representation_never_shows_the_ciphertext(vault):
    enc = encrypt_body(vault, "private words", key_version=1, **IDS)
    assert "private words" not in repr(enc) and enc.body_ct.hex() not in repr(enc) and repr(enc.body_ct) not in repr(enc)


def test_the_length_cap_is_enforced_before_encryption_and_the_error_never_names_the_text(vault):
    assert encrypt_body(vault, "a" * MAX_BODY_CHARS, key_version=1, **IDS)
    secret = "TOP-SECRET " + "a" * MAX_BODY_CHARS
    with pytest.raises(MessageError) as exc:
        encrypt_body(vault, secret, key_version=1, **IDS)
    assert "TOP-SECRET" not in str(exc.value)


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_a_blank_body_is_refused(vault, bad):
    with pytest.raises(MessageError):
        encrypt_body(vault, bad, key_version=1, **IDS)


def test_a_non_string_body_is_a_type_error(vault):
    with pytest.raises(TypeError):
        encrypt_body(vault, b"bytes", key_version=1, **IDS)  # type: ignore[arg-type]


def test_the_largest_and_smallest_ciphertexts_fit_the_0011_column_check(vault):
    """octet_length(body_ct) between 29 and 40100 (see db/migrations/0011)."""
    biggest = encrypt_body(vault, "\U0001f600" * MAX_BODY_CHARS, key_version=1, **IDS)  # 4 UTF-8 bytes per character
    smallest = encrypt_body(vault, "x", key_version=1, **IDS)
    assert len(biggest.body_ct) == 12 + 4 * MAX_BODY_CHARS + 16 <= 40100
    assert len(smallest.body_ct) == 12 + 1 + 16 == 29


def test_plaintext_never_appears_in_logs_or_exceptions(vault, caplog):
    secret = "my-unique-private-sentence-7431"
    caplog.set_level(logging.DEBUG)
    enc = encrypt_body(vault, secret, key_version=1, **IDS)
    decrypt_body(vault, enc.body_ct, message_id=enc.message_id, key_version=1, **IDS)
    with pytest.raises(DecryptionError) as exc:
        decrypt_body(vault, enc.body_ct, message_id=uuid.uuid4(), key_version=1, **IDS)
    assert secret not in str(exc.value) and secret not in repr(exc.value)
    assert secret not in caplog.text


def _calls_to(names: set[str]) -> dict[str, set[str]]:
    """Which source files (outside tests) call any of `names`, as an attribute or a bare name."""
    found: dict[str, set[str]] = {}
    for path in [*(ROOT / "careflow").glob("*.py"), *(ROOT / "scripts").glob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
                if name in names:
                    found.setdefault(path.name, set()).add(name)
    return found


def test_only_the_allowed_modules_decrypt_a_message_body():
    """BUILD-SPEC 17.6 / CLAUDE.md rule 7: a decrypted body exists in one place. crypto.py defines the primitive,
    messages.py is the one read path, rotation.py re-encrypts under a new key version (never logs)."""
    callers = _calls_to({"decrypt_message_body", "decrypt_body"})
    assert set(callers) <= {"crypto.py", "messages.py", "rotation.py"}, callers
    assert "decrypt_body" not in {n for k, v in callers.items() if k != "messages.py" for n in v}


def test_no_logging_in_the_message_module():
    tree = ast.parse((ROOT / "careflow" / "messages.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "logging" not in imported and "print" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


# ---- rotation refuses a connection that does not bypass RLS (rule 3: unknown is not zero) ------------------------------


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Conn:
    """Answers the single role probe and records every statement, so a test can see that nothing else ran."""

    def __init__(self, bypass):
        self._bypass = bypass
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return _Result(self._bypass)


@pytest.mark.parametrize("bypass", [False, None])
def test_message_rotation_refuses_to_run_as_a_role_that_does_not_bypass_rls(vault, bypass):
    conn = _Conn(bypass)
    with pytest.raises(rotation.RotationError, match="bypasses row-level security"):
        asyncio.run(rotation.rotate_message_bodies(conn, vault, from_version=1, to_version=2))  # type: ignore[arg-type]
    assert len(conn.statements) == 1 and "rolbypassrls" in conn.statements[0]


@pytest.mark.parametrize("bypass", [False, None])
def test_the_message_usage_count_and_verify_refuse_rather_than_undercount(vault, bypass):
    for call in (
        rotation.message_version_usage(_Conn(bypass)),  # type: ignore[arg-type]
        rotation.verify_message_bodies(_Conn(bypass), vault, to_version=2),  # type: ignore[arg-type]
    ):
        with pytest.raises(rotation.RotationError, match="bypasses row-level security"):
            asyncio.run(call)


def test_message_rotation_validates_versions_and_batch_size_before_touching_the_database(vault):
    conn = _Conn(True)
    cases = [
        ({"from_version": 1, "to_version": 1}, rotation.RotationError),
        ({"from_version": 1, "to_version": 9}, UnknownKeyVersionError),
        ({"from_version": 1, "to_version": 2, "batch_size": 0}, rotation.RotationError),
    ]
    for kwargs, expected in cases:
        with pytest.raises(expected):
            asyncio.run(rotation.rotate_message_bodies(conn, vault, **kwargs))  # type: ignore[arg-type]
    assert conn.statements == []


def test_a_lone_surrogate_is_a_clean_error_that_does_not_echo_the_text(vault):
    with pytest.raises(MessageError) as exc:
        encrypt_body(vault, "before \ud800 after", key_version=1, **IDS)
    assert "before" not in str(exc.value) and "after" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
