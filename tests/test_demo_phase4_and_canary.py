"""The Phase 4 demo pack (config/demo-phase4.yaml) and canary injection into the Front Door, offline. The live side (docs.register_canary_document, migration 0042) is in tests/test_canary_document_db_live.py."""

import asyncio
import copy
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from careflow import canary_injection, demo_front_door, demo_phase4
from careflow.canary import MARKER, rate_from
from careflow.demo_phase3 import DEMO_LABEL, DemoConfigRefused
from careflow.review_console import DeadlinePolicy, ReasonCodes
from careflow.reviewer_stats import RubberStampPolicy
from careflow.watchdog import limits_from

DEMO_ENV = {"CAREFLOW_DATA_POLICY": "synthetic_only", "CAREFLOW_DEMO_PHASE4": "1"}


def test_every_phase4_required_setting_has_a_labelled_placeholder_and_each_parses_with_the_real_parser():
    env = demo_phase4.effective_environ(DEMO_ENV, "demo")
    assert ReasonCodes.from_setting(env["CAREFLOW_REVIEW_REASON_CODES"]) is not None
    assert DeadlinePolicy.from_setting(env["CAREFLOW_REVIEW_DEADLINE_MINUTES"]) is not None
    assert limits_from(env["CAREFLOW_WATCHDOG_LIMITS"]).red_flag_minutes
    assert RubberStampPolicy.from_setting(env["CAREFLOW_RUBBER_STAMP_POLICY"]) is not None
    assert 0 < (rate_from(env) or 0) <= 0.05
    for variable in demo_phase4.VARIABLES.values():
        assert variable in env


def test_the_placeholders_load_only_under_demo_and_synthetic_only_and_the_switch():
    assert demo_phase4.effective_environ({}, "demo") == {}  # switch off: the environment itself
    assert demo_phase4.effective_environ({"CAREFLOW_DATA_POLICY": "synthetic_only"}, "clinical") == {"CAREFLOW_DATA_POLICY": "synthetic_only"}
    for profile, env in (("clinical", DEMO_ENV), ("demo", {"CAREFLOW_DEMO_PHASE4": "1"}), ("demo", {**DEMO_ENV, "CAREFLOW_DATA_POLICY": "real"})):
        with pytest.raises(DemoConfigRefused):
            demo_phase4.effective_environ(env, profile)  # refuses to start rather than quietly use a demo value


def test_an_explicit_environment_value_wins_and_an_empty_one_does_not_hide_a_placeholder():
    env = demo_phase4.effective_environ({**DEMO_ENV, "CAREFLOW_CANARY_RATE": "0.01", "CAREFLOW_WATCHDOG_LIMITS": "  "}, "demo")
    assert env["CAREFLOW_CANARY_RATE"] == "0.01"
    assert env["CAREFLOW_WATCHDOG_LIMITS"] != "  "


def test_no_phase4_value_is_a_default_in_code_or_in_the_env_example():
    root = Path(__file__).resolve().parent.parent
    example = (root / ".env.example").read_text(encoding="utf-8")
    placeholder = demo_phase4._read(demo_phase4.DEMO_FILE)
    for variable, value in placeholder.items():
        assert f"{variable}={value}" not in example


def _doc(tmp_path, mutate):
    data = yaml.safe_load(demo_phase4.DEMO_FILE.read_text(encoding="utf-8"))
    mutate(data)
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(label="x"),
        lambda d: d.update(loads_only_when={"profile": "demo"}),
        lambda d: d["settings"].pop("canary_rate"),
        lambda d: d["settings"].update(extra={"value": "1", "label": DEMO_LABEL}),
        lambda d: d["settings"]["canary_rate"].update(label="mine"),
        lambda d: d["settings"]["canary_rate"].update(value=0.02),  # not text
    ],
)
def test_a_demo_file_that_is_unlabelled_incomplete_or_extended_is_refused(tmp_path, mutate):
    with pytest.raises(DemoConfigRefused):
        demo_phase4.effective_environ(DEMO_ENV, "demo", path=_doc(tmp_path, copy.deepcopy(mutate)))


def test_every_value_in_the_file_is_labelled():
    data = yaml.safe_load(demo_phase4.DEMO_FILE.read_text(encoding="utf-8"))
    assert all(entry["label"] == DEMO_LABEL for entry in data["settings"].values())


# ---- the canary through the demo Front Door -------------------------------------------------------------------------------------------------------------------


def test_the_demo_text_source_makes_the_canary_letter_only_for_origin_canary():
    doc = uuid.uuid4()
    texts = demo_front_door.DemoTexts()
    assert MARKER in asyncio.run(texts.text_for_origin(doc, "canary"))
    assert MARKER not in asyncio.run(texts.text_for_origin(doc, "live"))
    assert asyncio.run(texts.text_for_origin(doc, "live")) == asyncio.run(texts.text_for(doc))


def test_the_canary_letter_is_read_by_the_demo_extractor_and_gets_the_planted_card():
    doc = uuid.uuid4()
    letter = demo_front_door.canary_letter(doc)
    claim = asyncio.run(demo_front_door.DemoExtractor().extract(letter, None, clinic_id=uuid.uuid4()))
    assert claim.name and claim.name.startswith("Canary Test") and claim.dob == "1990-01-01"
    found = asyncio.run(demo_front_door.DemoRetriever().retrieve(letter, scope={}))
    assert [(c.card_id, c.version) for c in found.candidates] == [demo_front_door.CANARY_CARD]
    ordinary = asyncio.run(demo_front_door.DemoRetriever().retrieve(demo_front_door.demo_letter(doc), scope={}))
    assert demo_front_door.CANARY_CARD[0] not in [c.card_id for c in ordinary.candidates]


# ---- the sweep ----------------------------------------------------------------------------------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows=(), scalar=0):
        self._rows, self._scalar = list(rows), scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, clinics, live_tasks):
        self.clinics, self.live_tasks, self.calls = clinics, live_tasks, []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "from iam.clinics" in sql:
            return _Rows([(c,) for c in self.clinics])
        if "from agents.review_tasks" in sql:
            return _Rows(scalar=self.live_tasks)
        return _Rows()

    def begin_nested(self):
        return _Nested()


class _Queue:
    enqueued: ClassVar[list] = []

    async def enqueue(self, conn, clinic_id, origin, task_type, payload, key):
        _Queue.enqueued.append((clinic_id, origin, task_type, payload, key))


@pytest.fixture(autouse=True)
def _queue(monkeypatch):
    _Queue.enqueued = []
    monkeypatch.setattr(canary_injection, "PostgresQueue", _Queue)


NOW = lambda: datetime(2026, 11, 6, 10, 30, tzinfo=UTC)


def test_no_rate_injects_nothing_and_reads_nothing():
    conn = _Conn([uuid.uuid4()], 5)
    assert asyncio.run(canary_injection.canary_sweep(conn, None, 20)) == 0
    assert conn.calls == [] and _Queue.enqueued == []


def test_with_no_live_tasks_there_is_nothing_to_draw_against():
    conn = _Conn([uuid.uuid4()], 0)
    assert asyncio.run(canary_injection.canary_sweep(conn, 0.02, 20, draw=lambda: 0.0, now=NOW)) == 0
    assert _Queue.enqueued == []


def test_a_winning_draw_registers_one_canary_document_and_enqueues_a_canary_origin_run():
    clinic = uuid.uuid4()
    conn = _Conn([clinic], 3)
    assert asyncio.run(canary_injection.canary_sweep(conn, 0.02, 20, draw=lambda: 0.0, now=NOW)) == 1  # three winning draws still make ONE canary per clinic per tick
    (c, origin, _task, payload, key), = _Queue.enqueued
    assert (c, origin) == (clinic, "canary") and payload["document_id"] in key and key.startswith("canary-run:")
    register = [p for sql, p in conn.calls if "register_canary_document" in sql]
    assert len(register) == 1 and register[0]["k"] == f"canary:{payload['document_id']}" and register[0]["m"] == 20


def test_a_losing_draw_injects_nothing_and_the_same_minute_gives_the_same_ids():
    conn = _Conn([uuid.uuid4()], 4)
    assert asyncio.run(canary_injection.canary_sweep(conn, 0.02, 20, draw=lambda: 0.99, now=NOW)) == 0
    clinic = uuid.uuid4()
    for _ in range(2):
        asyncio.run(canary_injection.canary_sweep(_Conn([clinic], 1), 0.5, 20, draw=lambda: 0.0, now=NOW))
    assert _Queue.enqueued[0][3:] == _Queue.enqueued[1][3:]  # a retried tick names the same document and key: nothing doubles


def test_one_clinic_failing_does_not_stop_the_others():
    good, bad = uuid.uuid4(), uuid.uuid4()

    class _Failing(_Queue):
        async def enqueue(self, conn, clinic_id, origin, task_type, payload, key):
            if clinic_id == bad:
                raise RuntimeError("boom")
            await super().enqueue(conn, clinic_id, origin, task_type, payload, key)

    canary_injection.PostgresQueue = _Failing
    assert asyncio.run(canary_injection.canary_sweep(_Conn([bad, good], 1), 1.0, 20, draw=lambda: 0.0, now=NOW)) == 1
    assert [e[0] for e in _Queue.enqueued] == [good]


def test_the_sweep_is_wired_only_when_the_demo_gate_holds_and_a_rate_is_set():
    source = (Path(__file__).resolve().parent.parent / "careflow" / "app.py").read_text(encoding="utf-8")
    wired = source[source.index("canary_rate = canary_rate_from"): source.index("worker: asyncio.Task | None = None")]
    assert "demo_allowed" in wired and "switched_on" in wired and "canary_rate is not None" in wired


def test_no_daily_cap_injects_nothing_and_reads_nothing():
    for cap in (None, 0, -1):
        conn = _Conn([uuid.uuid4()], 5)
        assert asyncio.run(canary_injection.canary_sweep(conn, 0.5, cap, draw=lambda: 0.0, now=NOW)) == 0
        assert conn.calls == [] and _Queue.enqueued == []


def test_the_sweep_is_wired_only_with_a_daily_cap_too():
    source = (Path(__file__).resolve().parent.parent / "careflow" / "app.py").read_text(encoding="utf-8")
    wired = source[source.index("canary_rate = canary_rate_from"): source.index("worker: asyncio.Task | None = None")]
    assert "canary_max_per_day is not None" in wired
