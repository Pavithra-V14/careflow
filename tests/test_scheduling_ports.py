"""The real ports of the Scheduling graph (careflow/scheduling_ports.py) and the DEMO wiring (careflow/demo_front_door.py, demo_decision.py), offline: the underlying modules are replaced by fakes, so this proves the
MAPPING (which suggestion is an accept, what a rules outcome becomes, where the actor comes from) and the GATE (the demo components exist only under the demo gate; the default autonomy mode is the most human)."""

import base64
import os
import uuid
from types import SimpleNamespace

import pytest

from careflow import demo_front_door, scheduling_ports
from careflow.crypto import DemographicVault
from careflow.demo_decision import DemoReplyEngine
from careflow.ports import DataClass, DecisionRequest
from careflow.scheduling_ports import (
    BookerPort,
    HoldReleaserPort,
    ReplyInterpreterPort,
    ReplyScreenerPort,
    SlotRanker,
    StateOnlyTasks,
    signed_in_account,
)

pytestmark = pytest.mark.asyncio
ACCOUNT, CLINIC, SLOT, PATIENT, WAIT, MESSAGE = (uuid.uuid4() for _ in range(6))


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class Conn:
    def __init__(self, sub=str(ACCOUNT)):
        self.sub = sub

    async def execute(self, statement, params=None):
        assert "request.jwt.claims" in str(statement)
        return _Scalar(self.sub)


def vault():
    key = lambda: base64.b64encode(os.urandom(32)).decode()
    return DemographicVault.from_env({"CAREFLOW_ENC_SECRET_V1": key(), "CAREFLOW_HMAC_SECRET_V1": key()})


async def test_the_actor_is_the_verified_account_on_the_connection_and_no_claims_is_a_hard_error():
    assert await signed_in_account(Conn()) == ACCOUNT  # type: ignore[arg-type]
    for none in (None, ""):
        with pytest.raises(PermissionError):
            await signed_in_account(Conn(sub=none))  # type: ignore[arg-type]


async def test_ranker_booker_and_releaser_pass_straight_through_to_the_slot_manager():
    calls = []

    class Slots:
        async def rank_waitlist(self, conn, clinic_id, visit_type_id):
            return [SimpleNamespace(waitlist_id=WAIT, patient_id=PATIENT, rank=1)]

        async def book_appointment(self, conn, slot_id, patient_id, key):
            calls.append(("book", slot_id, patient_id, key))

        async def release_hold(self, conn, hold_id):
            calls.append(("release", hold_id))

    assert await SlotRanker(Slots()).rank(None, CLINIC, SLOT) == [{"waitlist_id": WAIT, "patient_id": PATIENT, "rank": 1}]  # type: ignore[arg-type]
    await BookerPort(Slots()).book(None, slot_id=SLOT, patient_id=PATIENT, idempotency_key="k")  # type: ignore[arg-type]
    await HoldReleaserPort(Slots()).release(None, hold_id=WAIT)  # type: ignore[arg-type]
    assert calls == [("book", SLOT, PATIENT, "k"), ("release", WAIT)]


@pytest.mark.parametrize(("outcome", "expected"), [("clear", "clear"), ("red_flag", "red_flag_escalated"), ("unassessable", "held_unassessable"), ("anything_else", "held_unassessable")])
async def test_a_rules_outcome_becomes_one_of_the_graphs_three_words_and_unknown_is_never_clear(monkeypatch, outcome, expected):
    async def fake(conn, vault_, engine, message_id, *, idempotency_key, profile=None):
        assert idempotency_key == f"sched-screen:{message_id}"
        return SimpleNamespace(outcome=outcome)

    monkeypatch.setattr(scheduling_ports, "screen_stored_message", fake)
    assert await ReplyScreenerPort(vault(), object()).screen(Conn(), message_id=MESSAGE) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("suggestion", "reason", "answer", "why"),
    [("accept", None, "accept", "none"), ("decline", None, "decline", "none"), ("other_time", None, "unclear", "no_suggestion"), ("question", None, "unclear", "no_suggestion"), (None, "spend_ceiling", "unclear", "spend_ceiling"), (None, "no_route", "unclear", "no_route")],
)
async def test_only_accept_and_decline_are_actionable_everything_else_is_unclear_for_a_person(monkeypatch, suggestion, reason, answer, why):
    async def fake(conn, vault_, rules, service, message_id, *, actor_id, idempotency_key):
        assert actor_id == ACCOUNT and idempotency_key == f"sched-interpret:{message_id}"
        return SimpleNamespace(suggestion=suggestion, reason=reason)

    monkeypatch.setattr(scheduling_ports.reply_interpretation, "interpret_reply", fake)
    result = await ReplyInterpreterPort(vault(), object(), object()).interpret(Conn(), message_id=MESSAGE, clinic_id=CLINIC)  # type: ignore[arg-type]
    assert result == {"answer": answer, "reason": why}


async def test_the_durable_task_writer_calls_only_the_narrow_function_and_a_missing_answer_is_an_error():
    seen = []

    class C:
        def __init__(self, value):
            self.value = value

        async def execute(self, statement, params=None):
            seen.append((str(statement), params))
            return _Scalar(self.value)

    args = {"clinic_id": CLINIC, "origin": "test", "slot_id": SLOT, "status": "needs_human", "reasons": ["x"], "priority": "standard", "assigned_role": "front_desk", "idempotency_key": f"sched-hold:{SLOT}:needs_human"}
    task = uuid.uuid4()
    assert await scheduling_ports.DurableTasks().raise_task(C(task), **args) == task  # type: ignore[arg-type]
    assert "agents.raise_backfill_task" in seen[0][0] and "insert" not in seen[0][0].lower() and "reasons" not in seen[0][1]  # no free text goes to the database
    with pytest.raises(PermissionError):
        await scheduling_ports.DurableTasks().raise_task(C(None), **args)  # type: ignore[arg-type]


def test_a_durable_run_is_scoped_to_its_clinic_and_a_durable_runner_without_one_is_refused():
    from careflow.scheduling_graph import run_config
    from careflow.scheduling_runner import SchedulingComponents

    plain = run_config(SLOT)["configurable"]
    scoped = run_config(SLOT, CLINIC, "test")["configurable"]
    assert plain == {"thread_id": f"scheduling:{SLOT}"} and scoped["clinic_id"] == str(CLINIC) and scoped["origin"] == "test" and scoped["thread_id"].startswith("scheduling:")
    built = demo_front_door.scheduling_components_from_environment(DEMO, "demo", vault=vault(), key_version=1)
    assert isinstance(built, SchedulingComponents)
    with pytest.raises(ValueError):
        built.runner(lambda: None)
    assert built.runner(lambda: None, clinic_id=CLINIC, origin="test") is not None


async def test_the_state_only_task_writer_writes_nothing_and_returns_a_stable_id():
    writer = StateOnlyTasks()
    args = {"clinic_id": CLINIC, "origin": "test", "slot_id": SLOT, "status": "needs_human", "reasons": [], "priority": "p", "assigned_role": "r", "idempotency_key": "sched-hold:x:needs_human"}
    assert await writer.raise_task(None, **args) == await writer.raise_task(None, **args)  # type: ignore[arg-type]


# ---- the demo gate ----------------------------------------------------------------------------------------------------------------------------------


DEMO = {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only", "CAREFLOW_DEMO_PHASE3": "1"}


@pytest.mark.parametrize(
    "environ",
    [{}, {"CAREFLOW_DEMO_PHASE3": "1"}, {**DEMO, "CAREFLOW_DATA_POLICY": "phi_approved"}, {"CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only"}],
    ids=["nothing", "switch_only", "wrong_policy", "no_switch"],
)
def test_outside_the_demo_gate_there_are_no_scheduling_components(environ):
    assert demo_front_door.scheduling_components_from_environment(environ, "demo", vault=vault(), key_version=1) is None


def test_clinical_profile_and_missing_keys_give_none_even_with_the_switch_on():
    assert demo_front_door.scheduling_components_from_environment(DEMO, "clinical", vault=vault(), key_version=1) is None
    assert demo_front_door.scheduling_components_from_environment(DEMO, "demo", vault=None, key_version=1) is None
    assert demo_front_door.scheduling_components_from_environment(DEMO, "demo", vault=vault(), key_version=None) is None


def test_inside_the_gate_the_components_exist_with_real_ports_a_fake_engine_and_the_most_human_default_mode():
    built = demo_front_door.scheduling_components_from_environment(DEMO, "demo", vault=vault(), key_version=1)
    assert built is not None and built.policy.mode == "shadow" and not built.policy.may_run_without_a_human_first("booking")
    assert type(built.screener).__name__ == "ReplyScreenerPort" and type(built.tasks).__name__ == "DurableTasks" and built.durable  # durable by default (migrations 0037, 0038)
    memory = demo_front_door.scheduling_components_from_environment({**DEMO, "CAREFLOW_DURABLE_RUNS": "memory"}, "demo", vault=vault(), key_version=1)
    assert memory is not None and not memory.durable and type(memory.tasks).__name__ == "StateOnlyTasks"
    assert built.task_priority and built.task_role and built.max_candidates >= 1  # the labelled DEMO ONLY pack values
    assisted = demo_front_door.scheduling_components_from_environment({**DEMO, "CAREFLOW_AUTONOMY_MODE": "assisted"}, "demo", vault=vault(), key_version=1)
    assert assisted is not None and assisted.policy.may_run_without_a_human_first("booking")


async def test_the_demo_engine_understands_two_words_and_its_confidence_is_never_calibrated():
    engine = DemoReplyEngine()
    opts = ("accept", "decline", "other_time", "question", "unclear")
    for text, expected in (("Yes please", "accept"), ("no thanks", "decline"), ("what time is it", "unclear"), ("", "unclear")):
        result = (await engine(DecisionRequest("offer_reply", text, opts, data_class=DataClass.PHI))).value
        assert result.choice == expected and result.calibrated is False and abs(sum(result.probabilities.values()) - 1) < 1e-9
