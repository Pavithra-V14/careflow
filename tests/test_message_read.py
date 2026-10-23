"""careflow.messages.read_stored_message, offline: fake connection, real encryption (BUILD-SPEC.md 9.4 audit principle; CLAUDE.md rules 5, 6).

Proves the ORDER and the SHAPE of a read: the row is found, an erased one refused, the authorising role named, the body decrypted, and only
then the audit row written (ids only, a server-made key); a failure anywhere before that writes no audit row and returns no text.
"""

import asyncio
import base64
import json
import os
import uuid
from types import SimpleNamespace

import pytest

from careflow.crypto import DemographicVault
from careflow.messages import (
    MessageErased,
    MessageNotFound,
    MessageUnreadable,
    encrypt_body,
    read_stored_message,
)

PRIVATE = "my-private-sentence-4417"
ACTOR = uuid.uuid4()


@pytest.fixture
def vault():
    return DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V{v}": base64.b64encode(os.urandom(32)).decode() for v in (1, 2) for k in ("ENC", "HMAC")})


class _Res:
    def __init__(self, row=None, rows=()):
        self._row, self._rows = row, rows

    def first(self):
        return self._row

    def all(self):
        return list(self._rows)


class Conn:
    def __init__(self, row, roles=("nurse",), fail_audit=False):
        self.row, self.roles, self.fail_audit, self.calls = row, roles, fail_audit, []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "from msg.messages" in sql:
            return _Res(row=self.row)
        if "from iam.staff_roles" in sql:
            return _Res(rows=[(r,) for r in self.roles])
        if "insert into audit.log" in sql:
            if self.fail_audit:
                raise RuntimeError("audit insert failed")
            return _Res()
        raise AssertionError(sql)

    @property
    def audits(self):
        return [p for sql, p in self.calls if "insert into audit.log" in sql]


def stored(vault, body=f"hello {PRIVATE}", *, topic="care", version=1, erased=False, bind_to=None):
    clinic, thread, mid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    enc = encrypt_body(vault, body, clinic_id=clinic, thread_id=thread, key_version=version, message_id=bind_to or mid)
    return SimpleNamespace(id=mid, clinic_id=clinic, origin="test", topic=topic, thread_id=thread, sender_kind="patient",
                           body_ct=None if erased else enc.body_ct, body_key_version=None if erased else version,
                           erased_at=object() if erased else None)


def read(conn, vault, mid=None):
    return asyncio.run(read_stored_message(conn, vault, mid or uuid.uuid4(), actor_id=ACTOR))


def test_a_read_returns_the_text_and_writes_one_audit_row_with_ids_only(vault):
    row = stored(vault, topic="safety")
    conn = Conn(row, roles=("front_desk",))
    m = read(conn, vault, row.id)
    assert m.body == f"hello {PRIVATE}" and (m.message_id, m.topic, m.sender_kind) == (row.id, "safety", "patient")
    (audit,) = conn.audits
    assert (audit["a"], audit["r"], audit["m"], audit["c"], audit["o"]) == (ACTOR, "front_desk", str(row.id), row.clinic_id, "test")
    assert json.loads(audit["d"]) == {"thread_id": str(row.thread_id), "topic": "safety"}
    assert audit["k"].startswith("message-read:") and PRIVATE not in json.dumps(audit, default=str)


def test_the_audit_row_is_written_after_the_decrypt_and_is_the_last_statement(vault):
    conn = Conn(stored(vault))
    read(conn, vault)
    kinds = ["audit" if "audit.log" in sql else "roles" if "staff_roles" in sql else "message" for sql, _ in conn.calls]
    assert kinds == ["message", "roles", "audit"]


def test_every_read_is_logged_again_with_a_new_key(vault):
    row = stored(vault)
    conn = Conn(row)
    read(conn, vault, row.id)
    read(conn, vault, row.id)
    assert len(conn.audits) == 2 and conn.audits[0]["k"] != conn.audits[1]["k"]


@pytest.mark.parametrize(("roles", "named"), [(("doctor", "front_desk"), "doctor"), (("front_desk", "nurse"), "nurse"), (("front_desk",), "front_desk")])
def test_the_audit_names_the_most_senior_qualifying_role(vault, roles, named):
    conn = Conn(stored(vault), roles=roles)
    read(conn, vault)
    assert conn.audits[0]["r"] == named


def test_an_invisible_message_and_a_caller_with_no_qualifying_grant_are_the_same_not_found_and_write_nothing(vault):
    for conn in (Conn(None), Conn(stored(vault), roles=())):
        with pytest.raises(MessageNotFound):
            read(conn, vault)
        assert conn.audits == []


def test_an_erased_message_is_refused_and_writes_nothing(vault):
    conn = Conn(stored(vault, erased=True))
    with pytest.raises(MessageErased):
        read(conn, vault)
    assert conn.audits == []


@pytest.mark.parametrize(("make", "reason"), [
    (lambda v: stored(v, version=2, bind_to=None), None),
    (lambda v: stored(v, bind_to=uuid.uuid4()), "DecryptionError"),
])
def test_an_unreadable_body_raises_with_the_class_name_only_and_writes_no_audit_row(vault, make, reason):
    row = make(vault)
    if reason is None:  # a key version this process does not have
        other = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
        conn = Conn(row)
        with pytest.raises(MessageUnreadable) as exc:
            read(conn, other, row.id)
        assert exc.value.reason == "UnknownKeyVersionError"
    else:
        conn = Conn(row)
        with pytest.raises(MessageUnreadable) as exc:
            read(conn, vault, row.id)
        assert exc.value.reason == reason
    assert PRIVATE not in str(exc.value) and PRIVATE not in repr(exc.value) and exc.value.__cause__ is None
    assert conn.audits == []


def test_if_the_audit_insert_fails_the_error_propagates_and_no_text_is_returned(vault):
    conn = Conn(stored(vault), fail_audit=True)
    with pytest.raises(RuntimeError):
        read(conn, vault)


def test_the_representation_never_shows_the_body(vault):
    m = read(Conn(stored(vault)), vault)
    assert PRIVATE not in repr(m)
