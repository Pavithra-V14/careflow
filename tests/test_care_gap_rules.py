"""careflow/care_gap_rules.py (the required setting, validated and never supplied) and the sampler sweep with a stand-in candidate source (careflow/producer_sweeps.py)."""

import json
import uuid

import pytest

from careflow import producer_sweeps, producers_sql
from careflow.care_gap_rules import CareGapRulesError, parse

GOOD = {"rules": {"r_interval": {"interval_days": 30, "overdue_after_days": 5}, "r_annual": {"annual_month": 3, "overdue_after_days": 14}}, "episodes": {"e_one": ["r_interval", "r_annual"]}}


def test_unset_or_blank_is_none_and_nothing_is_defaulted():
    for raw in (None, "", "   "):
        assert parse(raw) is None


def test_a_valid_setting_gives_the_rules_per_episode_and_unknown_episodes_are_none_not_empty():
    rules = parse(json.dumps(GOOD))
    assert rules is not None
    got = rules.rules_for("e_one")
    assert got is not None and [(r.code, r.interval_days, r.annual_month, r.overdue_after_days) for r in got] == [("r_interval", 30, None, 5), ("r_annual", None, 3, 14)]
    assert rules.rules_for("other") is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("episodes"),
        lambda d: d.update(extra={}),
        lambda d: d.update(rules={}),
        lambda d: d["rules"].update(Bad_Code={"interval_days": 1, "overdue_after_days": 0}),
        lambda d: d["rules"]["r_interval"].update(annual_month=3),  # both cycles
        lambda d: d["rules"]["r_interval"].pop("interval_days"),
        lambda d: d["rules"]["r_interval"].update(interval_days=0),
        lambda d: d["rules"]["r_interval"].update(interval_days=True),
        lambda d: d["rules"]["r_interval"].update(interval_days=1.5),
        lambda d: d["rules"]["r_annual"].update(annual_month=13),
        lambda d: d["rules"]["r_annual"].update(overdue_after_days=-1),
        lambda d: d["episodes"].update(e_one=[]),
        lambda d: d["episodes"].update(e_one=["r_interval", "r_interval"]),
        lambda d: d["episodes"].update(e_one=["missing"]),
        lambda d: d["episodes"].update(Bad=["r_interval"]),
    ],
)
def test_malformed_settings_are_refused(mutate):
    data = json.loads(json.dumps(GOOD))
    mutate(data)
    with pytest.raises(CareGapRulesError):
        parse(json.dumps(data))


def test_not_json_or_not_an_object_is_refused():
    for raw in ("{", "[]", "3", '"x"'):
        with pytest.raises(CareGapRulesError):
            parse(raw)


@pytest.mark.asyncio
async def test_the_sampler_draws_about_the_rate_from_the_candidates_and_a_repeat_adds_nothing(monkeypatch):
    clinic = uuid.uuid4()
    candidates = [uuid.uuid4() for _ in range(4)]
    written: list[uuid.UUID] = []

    async def fake_clinics(conn, origin):
        return [clinic]

    async def fake_record(conn, *, clinic_id, origin, decision_id):
        if decision_id in written:
            return False
        written.append(decision_id)
        return True

    async def source(conn, clinic_id, origin):
        return candidates

    class _Nested:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Conn:
        def begin_nested(self):
            return _Nested()

    monkeypatch.setattr(producer_sweeps, "_clinics", fake_clinics)
    monkeypatch.setattr(producers_sql, "record_sample", fake_record)
    draws = iter([0.01, 0.9, 0.04, 0.5])
    assert await producer_sweeps.sampler_sweep(_Conn(), 0.05, source=source, draw=lambda: next(draws)) == 2
    assert written == [candidates[0], candidates[2]]
    again = iter([0.01, 0.9, 0.04, 0.5])
    assert await producer_sweeps.sampler_sweep(_Conn(), 0.05, source=source, draw=lambda: next(again)) == 0  # the same decisions are already sampled


@pytest.mark.asyncio
async def test_no_rate_and_no_auto_approval_path_write_nothing(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("must not be reached")

    monkeypatch.setattr(producer_sweeps, "_clinics", boom)
    assert await producer_sweeps.sampler_sweep(object(), None) == 0  # (type: ignore) no rate: nothing is even read
    assert await producer_sweeps.no_auto_approved_decisions(object(), uuid.uuid4(), "live") == []  # nothing approves on its own yet, so there is nothing to sample
