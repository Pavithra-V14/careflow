"""The session spend ledger client (careflow/session_spend.py), offline, with a fake engine and a FAKE provider price: no database, no model, no network."""

import uuid
from decimal import Decimal

import pytest

from careflow import session_spend
from careflow.gateway import SpendEntry

pytestmark = pytest.mark.asyncio


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one(self):
        return self._v


class _Conn:
    def __init__(self, log, total):
        self.log, self.total = log, total

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def begin(self):
        return self

    async def execute(self, statement, params=None):
        self.log.append((str(statement), dict(params or {})))
        return _Result(self.total)


class _Engine:
    def __init__(self, total=Decimal("1.5")):
        self.log, self.total = [], total

    def connect(self):
        return _Conn(self.log, self.total)


def entry(**o):
    base = {"clinic_id": uuid.uuid4(), "role": "decision", "provider": "fake", "model_id": "fake-1", "call_class": "ordinary", "usd_actual": Decimal("0.01"), "usd_shadow": Decimal("0.01"), "origin": "test", "idempotency_key": "k1"}
    return SpendEntry(**{**base, **o})


@pytest.fixture(autouse=True)
def _claims_recorder(monkeypatch):
    seen = []

    async def fake_set(conn, claims):
        seen.append(claims)

    monkeypatch.setattr(session_spend, "set_request_claims", fake_set)
    return seen


async def test_a_record_goes_through_the_function_with_the_callers_claims_only(_claims_recorder):
    engine = _Engine()
    ledger = session_spend.SessionSpendLedger(engine, {"sub": "someone", "aal": "aal2"})
    await ledger.record(entry())
    assert "ops.record_session_spend" in engine.log[0][0] and "insert" not in engine.log[0][0].lower()  # the function, never a table write
    assert _claims_recorder == [{"sub": "someone", "aal": "aal2"}]


async def test_the_total_comes_from_the_function():
    ledger = session_spend.SessionSpendLedger(_Engine(Decimal("2.25")), {"sub": "x"})
    assert await ledger.spent_today(uuid.uuid4(), "test") == Decimal("2.25")


@pytest.mark.parametrize("bad", [{"call_class": "safety"}, {"role": "llm_large"}, {"role": "voice_live"}])
async def test_only_the_decision_tiers_ordinary_calls_are_recorded_here_and_nothing_is_sent_otherwise(bad):
    engine = _Engine()
    with pytest.raises(session_spend.SpendRefused):
        await session_spend.SessionSpendLedger(engine, {"sub": "x"}).record(entry(**bad))
    assert engine.log == []  # refused before any database call


async def test_a_database_refusal_is_a_spend_refused_never_a_silent_pass():
    from sqlalchemy.exc import DBAPIError

    class Orig(Exception):
        sqlstate = "42501"

    class Refusing(_Conn):
        async def execute(self, statement, params=None):
            raise DBAPIError("stmt", {}, Orig())

    class RefusingEngine(_Engine):
        def connect(self):
            return Refusing(self.log, self.total)

    with pytest.raises(session_spend.SpendRefused):
        await session_spend.SessionSpendLedger(RefusingEngine(), {"sub": "x"}).record(entry())
    with pytest.raises(session_spend.SpendRefused):
        await session_spend.SessionSpendLedger(RefusingEngine(), {"sub": "x"}).spent_today(uuid.uuid4(), "test")
