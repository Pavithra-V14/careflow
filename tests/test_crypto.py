"""Demographic encryption and identity tokens (careflow/crypto.py). Pure: no network, no database.

Test keys are generated per run from os.urandom; no key, real or fake, is committed.
"""

import base64
import hashlib
import hmac
import os
import uuid

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from careflow.crypto import (
    CryptoConfigError,
    DecryptionError,
    DemographicVault,
    UnknownKeyVersionError,
    normalize_for_token,
)


def _b64(n: int = 32) -> str:
    return base64.b64encode(os.urandom(n)).decode()


@pytest.fixture
def env():
    return {
        "CAREFLOW_ENC_SECRET_V1": _b64(),
        "CAREFLOW_HMAC_SECRET_V1": _b64(),
        "CAREFLOW_ENC_SECRET_V2": _b64(),
        "CAREFLOW_HMAC_SECRET_V2": _b64(),
    }


@pytest.fixture
def vault(env):
    return DemographicVault.from_env(env)


# ---- encryption ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, text",
    [("name", "Asha Venkatesh"), ("dob", "1987-04-23"), ("phone", "+91 98765 43210"), ("name", "தமிழ் பெயர்"), ("name", "Zoë Müller")],
)
def test_roundtrip_under_the_correct_version(vault, field, text):
    for version in (1, 2):
        blob = vault.encrypt(text, field=field, key_version=version)
        assert vault.decrypt(blob, field=field, key_version=version) == text


def test_every_call_uses_a_fresh_nonce_so_equal_inputs_give_different_blobs(vault):
    blobs = [vault.encrypt("Asha", field="name", key_version=1) for _ in range(200)]
    assert len(set(blobs)) == 200
    assert len({b[:12] for b in blobs}) == 200  # the nonces themselves are all distinct


def test_blob_is_nonce_then_ciphertext_then_tag(vault):
    blob = vault.encrypt("Asha", field="name", key_version=1)
    assert len(blob) == 12 + len(b"Asha") + 16
    assert b"Asha" not in blob


def test_line_endings_are_cleaned_to_lf_before_encrypting(vault):
    blob = vault.encrypt("line one\r\nline two\rline three", field="name", key_version=1)
    assert vault.decrypt(blob, field="name", key_version=1) == "line one\nline two\nline three"


def test_text_is_otherwise_preserved_exactly(vault):
    text = "  Mixed Case  Name "
    assert vault.decrypt(vault.encrypt(text, field="name", key_version=1), field="name", key_version=1) == text


def test_decrypting_under_the_wrong_key_version_fails_clearly(vault):
    blob = vault.encrypt("Asha", field="name", key_version=1)
    with pytest.raises(DecryptionError, match="wrong key version"):
        vault.decrypt(blob, field="name", key_version=2)


def test_an_unloaded_key_version_is_a_distinct_error(vault):
    with pytest.raises(UnknownKeyVersionError):
        vault.encrypt("Asha", field="name", key_version=9)
    blob = vault.encrypt("Asha", field="name", key_version=1)
    with pytest.raises(UnknownKeyVersionError):
        vault.decrypt(blob, field="name", key_version=9)


def test_a_ciphertext_cannot_be_moved_to_another_field(vault):
    blob = vault.encrypt("1987-04-23", field="dob", key_version=1)
    with pytest.raises(DecryptionError):
        vault.decrypt(blob, field="name", key_version=1)


def test_any_altered_byte_is_detected(vault):
    blob = bytearray(vault.encrypt("Asha Venkatesh", field="name", key_version=1))
    for position in (0, 11, 12, len(blob) // 2, len(blob) - 1):  # nonce, ciphertext and tag bytes
        tampered = bytearray(blob)
        tampered[position] ^= 0x01
        with pytest.raises(DecryptionError):
            vault.decrypt(bytes(tampered), field="name", key_version=1)


@pytest.mark.parametrize("size", [0, 1, 12, 28])
def test_truncated_or_empty_blobs_are_rejected(vault, size):
    with pytest.raises(DecryptionError):
        vault.decrypt(b"\x00" * size, field="name", key_version=1)


def test_a_different_vault_with_other_keys_cannot_decrypt():
    one = DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": _b64(), "CAREFLOW_HMAC_SECRET_V1": _b64()})
    two = DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": _b64(), "CAREFLOW_HMAC_SECRET_V1": _b64()})
    blob = one.encrypt("Asha", field="name", key_version=1)
    with pytest.raises(DecryptionError):
        two.decrypt(blob, field="name", key_version=1)


@pytest.mark.parametrize("bad", ["", "   ", "\r\n"])
def test_blank_plaintext_is_refused_rather_than_stored_as_an_encrypted_blank(vault, bad):
    with pytest.raises(ValueError):
        vault.encrypt(bad, field="name", key_version=1)


def test_unknown_field_and_non_string_input_are_refused(vault):
    with pytest.raises(ValueError):
        vault.encrypt("x", field="address", key_version=1)
    with pytest.raises(TypeError):
        vault.encrypt(b"bytes", field="name", key_version=1)  # type: ignore[arg-type]


# ---- identity tokens -------------------------------------------------------------------------------


def test_token_is_deterministic_32_bytes(vault):
    t = vault.identity_token("phone", "+91 98765 43210", key_version=1)
    assert t == vault.identity_token("phone", "+91 98765 43210", key_version=1)
    assert isinstance(t, bytes) and len(t) == 32


def test_token_ignores_case_and_surrounding_or_repeated_whitespace(vault):
    base = vault.identity_token("name", "asha venkatesh", key_version=1)
    for variant in ("ASHA VENKATESH", "  Asha   Venkatesh ", "Asha\tVenkatesh\n", "ａｓｈａ venkatesh"):
        assert vault.identity_token("name", variant, key_version=1) == base, variant


def test_distinct_inputs_do_not_collide(vault):
    values = [f"person {i}" for i in range(2000)] + ["asha", "asha k", "ashak", "1987-04-23", "1987-04-24"]
    tokens = {vault.identity_token("name", v, key_version=1) for v in values}
    assert len(tokens) == len(values)


def test_same_text_under_two_kinds_gives_unrelated_tokens(vault):
    assert vault.identity_token("phone", "12345", key_version=1) != vault.identity_token("dob", "12345", key_version=1)


def test_same_text_under_two_key_versions_gives_different_tokens(vault):
    assert vault.identity_token("phone", "12345", key_version=1) != vault.identity_token("phone", "12345", key_version=2)


def test_token_matches_an_independent_hmac_sha256_computation(env):
    vault = DemographicVault.from_env(env)
    key = base64.b64decode(env["CAREFLOW_HMAC_SECRET_V1"])
    expected = hmac.new(key, b"phone\x00" + b"+91 98765 43210", hashlib.sha256).digest()
    assert vault.identity_token("phone", "  +91   98765 43210 ", key_version=1) == expected


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_nothing_to_hash_is_an_error_not_a_shared_empty_token(vault, bad):
    with pytest.raises(ValueError):
        vault.identity_token("name", bad, key_version=1)


def test_token_rejects_bad_kind_unknown_version_and_non_strings(vault):
    for kind in ("Phone", "1phone", "pho ne", ""):
        with pytest.raises(ValueError):
            vault.identity_token(kind, "x", key_version=1)
    with pytest.raises(UnknownKeyVersionError):
        vault.identity_token("phone", "x", key_version=9)
    with pytest.raises(TypeError):
        vault.identity_token("phone", 12345, key_version=1)  # type: ignore[arg-type]


def test_normalize_for_token_is_idempotent():
    once = normalize_for_token("  Ｓtraße   NAME ")
    assert normalize_for_token(once) == once


# ---- configuration: fail loudly, never run degraded ------------------------------------------------


def test_missing_keys_entirely():
    with pytest.raises(CryptoConfigError, match="no keys found"):
        DemographicVault.from_env({"PATH": "/usr/bin"})


def test_encryption_key_without_its_hmac_key():
    with pytest.raises(CryptoConfigError, match="HMAC_SECRET_V1 is missing"):
        DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": _b64()})


def test_hmac_key_without_its_encryption_key():
    with pytest.raises(CryptoConfigError, match="ENC_SECRET_V1 is missing"):
        DemographicVault.from_env({"CAREFLOW_HMAC_SECRET_V1": _b64()})


def test_a_version_missing_one_half_is_rejected_even_if_another_version_is_complete(env):
    del env["CAREFLOW_HMAC_SECRET_V2"]
    with pytest.raises(CryptoConfigError, match="V2"):
        DemographicVault.from_env(env)


@pytest.mark.parametrize("raw", ["", "   ", "not base64!!", "@@@@", "short"])
def test_malformed_secrets_are_rejected(raw):
    with pytest.raises(CryptoConfigError):
        DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": raw, "CAREFLOW_HMAC_SECRET_V1": _b64()})
    with pytest.raises(CryptoConfigError):
        DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": _b64(), "CAREFLOW_HMAC_SECRET_V1": raw})


@pytest.mark.parametrize("size", [16, 24, 31, 33, 64])
def test_encryption_key_must_be_exactly_32_bytes(size):
    with pytest.raises(CryptoConfigError, match="exactly 32"):
        DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": _b64(size), "CAREFLOW_HMAC_SECRET_V1": _b64()})


def test_hmac_key_must_be_at_least_32_bytes_but_may_be_longer():
    with pytest.raises(CryptoConfigError, match="at least 32"):
        DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": _b64(), "CAREFLOW_HMAC_SECRET_V1": _b64(16)})
    DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": _b64(), "CAREFLOW_HMAC_SECRET_V1": _b64(64)})


def test_the_two_secrets_may_not_be_the_same_value():
    same = _b64()
    with pytest.raises(CryptoConfigError, match="different"):
        DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": same, "CAREFLOW_HMAC_SECRET_V1": same})


def test_a_constant_byte_key_is_not_accepted():
    flat = base64.b64encode(b"\x00" * 32).decode()
    with pytest.raises(CryptoConfigError, match="random"):
        DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": flat, "CAREFLOW_HMAC_SECRET_V1": _b64()})


def test_url_safe_and_unpadded_base64_are_accepted():
    raw = os.urandom(32)
    urlsafe = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    v = DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": urlsafe, "CAREFLOW_HMAC_SECRET_V1": _b64()})
    assert v.decrypt(v.encrypt("x", field="name", key_version=1), field="name", key_version=1) == "x"


def test_versions_load_independently_and_error_messages_never_contain_a_secret(env):
    vault = DemographicVault.from_env(env)
    assert vault.key_versions == [1, 2]
    assert env["CAREFLOW_ENC_SECRET_V1"] not in repr(vault)
    assert env["CAREFLOW_HMAC_SECRET_V1"] not in repr(vault)
    leaky = {"CAREFLOW_ENC_SECRET_V1": "!!secret-looking-value!!", "CAREFLOW_HMAC_SECRET_V1": _b64()}
    with pytest.raises(CryptoConfigError) as exc:
        DemographicVault.from_env(leaky)
    assert "secret-looking-value" not in str(exc.value)


def test_from_env_defaults_to_the_process_environment(monkeypatch, env):
    for k in list(os.environ):
        if k.startswith(("CAREFLOW_ENC_SECRET_", "CAREFLOW_HMAC_SECRET_")):
            monkeypatch.delenv(k)
    with pytest.raises(CryptoConfigError):
        DemographicVault.from_env()
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert DemographicVault.from_env().key_versions == [1, 2]


# ---- message bodies (msg.messages.body_ct; migration 0011) ---------------------------------------------------------


def _message_ids():
    return {"clinic_id": uuid.uuid4(), "thread_id": uuid.uuid4(), "message_id": uuid.uuid4()}


def test_message_body_round_trip(vault):
    ids = _message_ids()
    blob = vault.encrypt_message_body("Please call me back about Tuesday.", key_version=1, **ids)
    assert vault.decrypt_message_body(blob, key_version=1, **ids) == "Please call me back about Tuesday."


def test_message_body_line_endings_are_cleaned_and_blank_is_refused(vault):
    ids = _message_ids()
    blob = vault.encrypt_message_body("a\r\nb\rc", key_version=1, **ids)
    assert vault.decrypt_message_body(blob, key_version=1, **ids) == "a\nb\nc"
    for bad in ("", "   ", "\n\t"):
        with pytest.raises(ValueError):
            vault.encrypt_message_body(bad, key_version=1, **ids)


@pytest.mark.parametrize("moved", ["message_id", "thread_id", "clinic_id"])
def test_message_body_is_bound_to_its_message_thread_and_clinic(vault, moved):
    ids = _message_ids()
    blob = vault.encrypt_message_body("secret words", key_version=1, **ids)
    with pytest.raises(DecryptionError):
        vault.decrypt_message_body(blob, key_version=1, **{**ids, moved: uuid.uuid4()})


def test_message_body_rejects_wrong_version_unloaded_version_and_tampering(vault):
    ids = _message_ids()
    blob = vault.encrypt_message_body("secret words", key_version=1, **ids)
    with pytest.raises(DecryptionError):
        vault.decrypt_message_body(blob, key_version=2, **ids)
    with pytest.raises(UnknownKeyVersionError):
        vault.decrypt_message_body(blob, key_version=9, **ids)
    with pytest.raises(UnknownKeyVersionError):
        vault.encrypt_message_body("x", key_version=9, **ids)
    for i in range(len(blob)):
        flipped = blob[:i] + bytes([blob[i] ^ 1]) + blob[i + 1 :]
        with pytest.raises(DecryptionError):
            vault.decrypt_message_body(flipped, key_version=1, **ids)
    for size in (0, 5, 27, 28):
        with pytest.raises(DecryptionError):
            vault.decrypt_message_body(blob[:size], key_version=1, **ids)


def test_message_body_arguments_must_be_uuids(vault):
    ids = _message_ids()
    with pytest.raises(TypeError):
        vault.encrypt_message_body("x", key_version=1, **{**ids, "message_id": str(ids["message_id"])})
    with pytest.raises(TypeError):
        vault.decrypt_message_body(b"x" * 40, key_version=1, **{**ids, "thread_id": None})


def test_each_message_gets_its_own_subkey_and_a_fresh_nonce(vault):
    a, b = _message_ids(), _message_ids()
    one = vault.encrypt_message_body("same text", key_version=1, **a)
    two = vault.encrypt_message_body("same text", key_version=1, **a)
    three = vault.encrypt_message_body("same text", key_version=1, **b)
    assert len({one, two, three}) == 3
    assert one[:12] != two[:12]


def test_message_body_construction_matches_an_independent_hkdf_aesgcm_computation(env):
    """Known-answer test: re-derive root, subkey and AAD with the library directly and decrypt the vault's output."""
    vault = DemographicVault.from_env(env)
    ids = _message_ids()
    blob = vault.encrypt_message_body("known answer", key_version=2, **ids)
    master = base64.b64decode(env["CAREFLOW_ENC_SECRET_V2"])
    root = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"careflow/message_body/root/v1").derive(master)
    sub = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"careflow/message_body/key/v1/" + str(ids["message_id"]).encode()).derive(root)
    aad = b"careflow/message_body/v1/" + f"{ids['clinic_id']}/{ids['thread_id']}/{ids['message_id']}".encode()
    assert AESGCM(sub).decrypt(blob[:12], blob[12:], aad) == b"known answer"
    assert len(blob) == 12 + len(b"known answer") + 16


def test_message_body_cannot_be_encrypted_or_decrypted_through_the_generic_field_api(vault):
    ids = _message_ids()
    with pytest.raises(ValueError):
        vault.encrypt("x", field="message_body", key_version=1)
    blob = vault.encrypt_message_body("x", key_version=1, **ids)
    with pytest.raises(ValueError):
        vault.decrypt(blob, field="message_body", key_version=1)


def test_demographic_ciphertext_format_is_unchanged_by_message_support(env):
    """Regression: name/dob/phone are still AES-256-GCM under the master key directly, with the old AAD."""
    vault = DemographicVault.from_env(env)
    blob = vault.encrypt("Asha Venkatesh", field="name", key_version=1)
    master = base64.b64decode(env["CAREFLOW_ENC_SECRET_V1"])
    assert AESGCM(master).decrypt(blob[:12], blob[12:], b"careflow/demographic/v1/name") == b"Asha Venkatesh"


def test_the_message_root_is_not_the_master_and_is_not_exposed(env):
    vault = DemographicVault.from_env(env)
    master = base64.b64decode(env["CAREFLOW_ENC_SECRET_V1"])
    assert master not in vault._msg_root.values()
    assert master.hex() not in repr(vault) and "root" not in repr(vault)
