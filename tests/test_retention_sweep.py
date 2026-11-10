"""The retention report sweep, offline: it does nothing with no period set, passes a cutoff only for a period that is set, and reports a clinic once a day. The database function is in tests/test_retention_report_db_live.py."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from careflow import producer_sweeps
from careflow.producer_sweeps import retention_report_sweep

pytestmark = pytest.mark.asyncio(loop_scope="module")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.calls = []

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params):
        self.calls.append(params)
        return SimpleNamespace(one=lambda: SimpleNamespace(created=True))


@pytest.fixture
def one_clinic(monkeypatch):
    clinic = uuid.uuid4()

    async def clinics(conn, origin):
        return [clinic]

    monkeypatch.setattr(producer_sweeps, "_clinics", clinics)
    monkeypatch.setattr(producer_sweeps, "_reported_on", {})
    return clinic


async def test_no_period_set_does_nothing(one_clinic):
    conn = _Conn()
    assert await retention_report_sweep(conn, None, None, now=NOW) == 0 and await retention_report_sweep(conn, 0, -5, now=NOW) == 0
    assert conn.calls == []


async def test_only_a_period_that_is_set_gets_a_cutoff(one_clinic):
    conn = _Conn()
    assert await retention_report_sweep(conn, 90, None, now=NOW) == 1
    assert conn.calls == [{"c": one_clinic, "a": NOW - timedelta(days=90), "b": None}]


async def test_a_clinic_is_reported_once_a_day_by_this_process(one_clinic):
    conn = _Conn()
    assert await retention_report_sweep(conn, 90, 3000, now=NOW) == 1
    assert await retention_report_sweep(conn, 90, 3000, now=NOW + timedelta(hours=3)) == 0 and len(conn.calls) == 1
    assert await retention_report_sweep(conn, 90, 3000, now=NOW + timedelta(days=1)) == 1 and len(conn.calls) == 2
