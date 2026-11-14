"""Patient messaging routes (migration 0082), offline: the feature is OFF unless the owner says `on`; who you are is checked before the setting; a patient never sends on `safety`; the screening engine for a patient session reads its rules through the
definer function and changes nothing else; the text round-trips through the same encryption as every other message body and a wrong binding does not decrypt. The database behaviour is in tests/test_patient_messaging_db_live.py. Synthetic text only."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from careflow import app as app_module
from careflow import messages as messages_mod
from careflow import portal_messages_sql
from careflow.crypto import DemographicVault
from careflow.rules import RedFlagEngine


@pytest.mark.parametrize("raw", [None, "", "off", "true", "1", "yes", "ON "])
def test_the_feature_is_on_only_for_the_exact_word_on(raw):
    environ = {} if raw is None else {portal_messages_sql.SETTING: raw}
    assert portal_messages_sql.enabled(environ) is (raw is not None and raw.strip().lower() == "on")


def test_unset_is_off():
    assert portal_messages_sql.enabled({}) is False


def test_a_patient_may_send_only_on_care_and_appointments():
    assert portal_messages_sql.SEND_TOPICS == ("care", "appointments")
    with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError
        app_module.PatientMessageIn(clinic_id=uuid.uuid4(), topic="safety", text="x")  # type: ignore[arg-type]


def test_the_patient_engine_changes_only_the_query_that_reads_the_rules():
    assert issubclass(portal_messages_sql.PatientRuleEngine, RedFlagEngine)
    assert "kb.patient_screening_rules" in portal_messages_sql.PatientRuleEngine.RULES_SQL and "kb.red_flag_rules" in RedFlagEngine.RULES_SQL
    assert portal_messages_sql.PatientRuleEngine.evaluate_and_escalate is RedFlagEngine.evaluate_and_escalate  # the rules still run first and alone


class _Conn:
    """A stand-in transaction whose first query (the account lookup) answers a patient account."""

    def __init__(self, account_type="patient", status="active"):
        self.row = SimpleNamespace(account_type=account_type, status=status, origin="test")

    async def execute(self, *a, **k):
        return SimpleNamespace(first=lambda: self.row)


def _request(vault=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(vault=vault, active_key_version=1)))


def _body():
    return app_module.PatientMessageIn(clinic_id=uuid.uuid4(), topic="appointments", text="SYNTHETIC message")


@pytest.mark.asyncio
async def test_with_the_feature_off_a_patient_is_told_it_is_not_set_up_and_a_non_patient_is_refused_first(monkeypatch):
    monkeypatch.delenv(portal_messages_sql.SETTING, raising=False)
    claims = {"sub": str(uuid.uuid4())}
    with pytest.raises(HTTPException) as e:
        await app_module.send_patient_message(_body(), "k", _request(), _Conn(), claims, None)  # type: ignore[arg-type]
    assert e.value.status_code == 503
    with pytest.raises(HTTPException) as e:
        await app_module.list_my_message_threads(_Conn(), claims)  # type: ignore[arg-type]
    assert e.value.status_code == 503
    with pytest.raises(HTTPException) as refused:
        await app_module.list_my_message_threads(_Conn(account_type="staff"), claims)  # type: ignore[arg-type]
    assert refused.value.status_code == 403  # who you are first, whatever the setting


@pytest.mark.asyncio
async def test_with_the_feature_on_sending_still_needs_the_keys_and_a_per_minute_limit(monkeypatch):
    monkeypatch.setenv(portal_messages_sql.SETTING, "on")
    claims = {"sub": str(uuid.uuid4())}
    for vault, limiter in ((None, object()), (object(), None)):
        with pytest.raises(HTTPException) as e:
            await app_module.send_patient_message(_body(), "k", _request(vault), _Conn(), claims, limiter)  # type: ignore[arg-type]
        assert e.value.status_code == 503


def test_a_body_round_trips_and_is_bound_to_its_thread_and_message():
    vault = DemographicVault({1: bytes(range(32))}, {1: bytes(range(32, 64))})
    clinic, thread, mid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sealed = messages_mod.encrypt_body(vault, "SYNTHETIC hello", clinic_id=clinic, thread_id=thread, key_version=1, message_id=mid)
    assert messages_mod.decrypt_body(vault, sealed.body_ct, clinic_id=clinic, thread_id=thread, message_id=mid, key_version=1) == "SYNTHETIC hello"
    with pytest.raises(messages_mod.DecryptionError):
        messages_mod.decrypt_body(vault, sealed.body_ct, clinic_id=clinic, thread_id=uuid.uuid4(), message_id=mid, key_version=1)  # another thread
