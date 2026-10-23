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


# ---- screen_stored_message: decrypt, screen, record, return ids only (route D1). Fake connection and engine, real encryption ---------


class _Res:
    def __init__(self, row=None, scalar=None):
        self._row, self._scalar = row, scalar

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._scalar


class _ScreenConn:
    def __init__(self, row, reply="not_required"):
        self.row, self.reply, self.sql = row, reply, []

    async def execute(self, statement, params=None):
        text_ = str(statement)
        self.sql.append(text_)
        return _Res(row=self.row) if "from msg.messages" in text_ else _Res(scalar=self.reply)


class _ScreenEngine:
    def __init__(self, outcome="red_flag"):
        self.calls, self.outcome, self.sid = [], outcome, uuid.uuid4()

    async def evaluate_and_escalate(self, conn, clinic_id, origin, text_content, message_id, *, profile, idempotency_key):
        from careflow.rules import Evaluation, ScreeningResult

        self.calls.append(("evaluate", text_content, profile, idempotency_key))
        return Evaluation(self.outcome, (), profile, 0), ScreeningResult(self.sid, False, self.outcome)  # type: ignore[arg-type]

    async def record_screening(self, conn, clinic_id, origin, message_id, evaluation, *, idempotency_key):
        from careflow.rules import ScreeningResult

        self.calls.append(("record", evaluation.outcome, evaluation.profile, idempotency_key))
        return ScreeningResult(self.sid, False, evaluation.outcome)  # type: ignore[arg-type]


def _stored(vault, text_="some private words", *, version=1, erased=False, bind_to=None):
    from types import SimpleNamespace

    ids = {"clinic_id": uuid.uuid4(), "thread_id": uuid.uuid4(), "message_id": uuid.uuid4()}
    enc = encrypt_body(vault, text_, key_version=version, clinic_id=ids["clinic_id"], thread_id=ids["thread_id"], message_id=bind_to or ids["message_id"])
    row = SimpleNamespace(id=ids["message_id"], clinic_id=ids["clinic_id"], origin="test", thread_id=ids["thread_id"],
                          body_ct=None if erased else enc.body_ct, body_key_version=None if erased else version,
                          erased_at=object() if erased else None)
    return row


def _screen(conn, vault, engine, message_id, **kw):
    from careflow.messages import screen_stored_message

    return asyncio.run(screen_stored_message(conn, vault, engine, message_id, idempotency_key=kw.pop("idempotency_key", "k1"), **kw))


def test_a_missing_or_invisible_message_is_not_found_and_nothing_is_screened(vault):
    from careflow.messages import MessageNotFound

    engine = _ScreenEngine()
    with pytest.raises(MessageNotFound):
        _screen(_ScreenConn(None), vault, engine, uuid.uuid4())
    assert engine.calls == []


def test_an_erased_message_cannot_be_screened(vault):
    from careflow.messages import MessageErased

    row = _stored(vault, erased=True)
    engine = _ScreenEngine()
    with pytest.raises(MessageErased):
        _screen(_ScreenConn(row), vault, engine, row.id)
    assert engine.calls == []


def test_a_key_version_that_is_not_loaded_is_recorded_unassessable_not_left_unscreened(vault):
    """Unknown is not clear and not silent: the message gets a nurse task, and the result names the error CLASS for the route's log."""
    bigger = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V{v}": _b64() for v in (1, 9) for k in ("ENC", "HMAC")})
    row = _stored(bigger, version=9)
    engine = _ScreenEngine("unassessable")
    out = _screen(_ScreenConn(row), vault, engine, row.id, profile="clinical")  # `vault` only has versions 1 and 2
    assert [c[:2] for c in engine.calls] == [("record", "unassessable")]
    assert (out.outcome, out.unreadable_reason, out.clinic_id) == ("unassessable", "UnknownKeyVersionError", row.clinic_id)


def test_a_body_that_fails_authentication_is_recorded_unassessable_not_raised(vault):
    """Unknown is not clear: a tampered or mis-bound body must reach a nurse, not disappear into an error."""
    row = _stored(vault, bind_to=uuid.uuid4())  # ciphertext bound to ANOTHER message id
    engine = _ScreenEngine("unassessable")
    out = _screen(_ScreenConn(row, reply="not_required"), vault, engine, row.id, profile="clinical")
    assert [c[0] for c in engine.calls] == ["record"] and engine.calls[0][1:3] == ("unassessable", "clinical")
    assert out.outcome == "unassessable" and out.screening_id == engine.sid and out.unreadable_reason == "DecryptionError"


def test_the_decrypted_text_goes_to_the_rule_engine_only_and_never_into_the_result_or_the_logs(vault, caplog):
    secret = "my-very-private-sentence-9981"
    row = _stored(vault, secret)
    engine = _ScreenEngine("red_flag")
    caplog.set_level(logging.DEBUG)
    out = _screen(_ScreenConn(row, reply="pending_clinician_template"), vault, engine, row.id, profile="clinical", idempotency_key="abc")
    assert engine.calls == [("evaluate", secret, "clinical", "abc")]
    assert (out.outcome, out.patient_reply, out.replayed, out.message_id) == ("red_flag", "pending_clinician_template", False, row.id)
    assert out.unreadable_reason is None
    assert secret not in repr(out) and secret not in caplog.text


def test_the_screening_result_has_only_ids_and_enums():
    from dataclasses import fields

    from careflow.messages import ScreenedMessage

    assert [f.name for f in fields(ScreenedMessage)] == [
        "message_id", "screening_id", "outcome", "replayed", "patient_reply", "clinic_id", "unreadable_reason"]


def test_screening_never_calls_a_model_or_the_network():
    tree = ast.parse((ROOT / "careflow" / "messages.py").read_text(encoding="utf-8"))
    roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    roots |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert roots & {"httpx", "requests", "aiohttp", "urllib", "socket", "anthropic", "openai", "google", "langchain"} == set()
