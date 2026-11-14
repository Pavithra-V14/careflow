"""Patient booking routes (migration 0083), offline: the feature is OFF unless the owner says `on`; who you are is checked before the setting; every database refusal maps to one fixed HTTP answer. The database behaviour is in
tests/test_patient_booking_db_live.py. Synthetic only."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from careflow import app as app_module
from careflow import portal_booking_sql


@pytest.mark.parametrize("raw", [None, "", "off", "true", "1", "ON "])
def test_on_only_for_the_exact_word_on(raw):
    environ = {} if raw is None else {portal_booking_sql.SETTING: raw}
    assert portal_booking_sql.enabled(environ) is (raw is not None and raw.strip().lower() == "on")


class _Conn:
    def __init__(self, account_type="patient"):
        self.row = SimpleNamespace(account_type=account_type, status="active", origin="test")

    async def execute(self, *a, **k):
        return SimpleNamespace(first=lambda: self.row)


CLAIMS = {"sub": str(uuid.uuid4())}


@pytest.mark.asyncio
async def test_with_the_feature_off_every_route_tells_a_patient_it_is_not_set_up_and_refuses_a_non_patient_first(monkeypatch):
    monkeypatch.delenv(portal_booking_sql.SETTING, raising=False)
    calls = (
        lambda c: app_module.list_bookable_slots(uuid.uuid4(), c, CLAIMS, 50),
        lambda c: app_module.book_my_slot(uuid.uuid4(), "k", c, CLAIMS),
        lambda c: app_module.list_my_bookings(c, CLAIMS),
        lambda c: app_module.cancel_my_booking(uuid.uuid4(), "k", c, CLAIMS),
    )
    for call in calls:
        with pytest.raises(HTTPException) as e:
            await call(_Conn())
        assert e.value.status_code == 503
        with pytest.raises(HTTPException) as refused:
            await call(_Conn("staff"))
        assert refused.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "status"),
    [(portal_booking_sql.Refused, 403), (portal_booking_sql.SlotNotOpen, 409), (portal_booking_sql.CapReached, 409), (portal_booking_sql.KeyReused, 409)],
)
async def test_every_database_refusal_is_one_fixed_answer(monkeypatch, raised, status):
    monkeypatch.setenv(portal_booking_sql.SETTING, "on")

    async def boom(*a, **k):
        raise raised

    monkeypatch.setattr(portal_booking_sql, "book", boom)
    with pytest.raises(HTTPException) as e:
        await app_module.book_my_slot(uuid.uuid4(), "k", _Conn(), CLAIMS)
    assert e.value.status_code == status


@pytest.mark.asyncio
async def test_cancel_answers_404_for_a_booking_that_is_not_yours_and_409_for_one_that_cannot_be_cancelled(monkeypatch):
    monkeypatch.setenv(portal_booking_sql.SETTING, "on")
    for raised, status in ((portal_booking_sql.Refused, 404), (portal_booking_sql.NotCancellable, 409)):

        async def boom(*a, _r=raised, **k):
            raise _r

        monkeypatch.setattr(portal_booking_sql, "cancel", boom)
        with pytest.raises(HTTPException) as e:
            await app_module.cancel_my_booking(uuid.uuid4(), "k", _Conn(), CLAIMS)
        assert e.value.status_code == status
