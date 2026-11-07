"""Audit rows for human decisions (careflow/audit.py). Offline: a fake connection. The live checks (the rows really commit with the decision, as the signed-in person, ids only) are in the
role-matrix module and tests/test_api_referrals_live.py."""

import inspect
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from careflow import app as app_module
from careflow.audit import ACTIONS, AuditDetailRefused, AuditError, write_audit

pytestmark = pytest.mark.asyncio
CLINIC, ACTOR, RES = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class Savepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False  # never swallows: the code under test decides what a duplicate means


def duplicate_key_error(sqlstate="23505"):
    return DBAPIError("insert", {}, SimpleNamespace(sqlstate=sqlstate))


class Conn:
    def __init__(self, rowcount=1, raises=None):
        self.calls, self.rowcount, self.raises = [], rowcount, raises

    def begin_nested(self):
        return Savepoint()

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        if self.raises is not None:
            raise self.raises
        return Result(self.rowcount)


KW = {
    "clinic_id": CLINIC, "origin": "test", "actor_id": ACTOR, "actor_role": "front_desk", "action": "merge.propose", "resource_type": "merge", "resource_id": RES,
    "idempotency_key": "audit:k-1",
}


async def test_one_row_is_written_idempotently_as_the_actor_with_ids_and_closed_words_only():
    conn = Conn()
    assert await write_audit(conn, details={"master_patient_id": uuid.uuid4(), "decision": "approve", "replayed": False, "n": 2}, **KW) is True  # type: ignore[arg-type]
    ((sql, p),) = conn.calls
    assert "insert into audit.log" in sql and "on conflict" not in sql.lower() and "returning" not in sql  # both would need the SELECT policy, which only auditors pass
    assert p["a"] == ACTOR and p["c"] == CLINIC and p["k"] == "audit:k-1" and p["ri"] == str(RES) and p["r"] == "front_desk"


async def test_a_replay_writes_no_second_row():
    assert await write_audit(Conn(raises=duplicate_key_error()), details={}, **KW) is False  # type: ignore[arg-type]


async def test_only_a_duplicate_key_is_a_replay_any_other_database_error_propagates():
    for state in ("42501", "23514", "40001", None):
        with pytest.raises(DBAPIError):
            await write_audit(Conn(raises=duplicate_key_error(state)), details={}, **KW)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["Asha Rao", "5 March 1980", "98765 43210", "a free sentence", "x" * 65, "UPPER", "", None, 1.5, ["a"], {"a": 1}])
async def test_free_text_and_anything_that_is_not_an_id_a_number_a_boolean_or_a_token_is_refused_before_a_query(bad):
    conn = Conn()
    with pytest.raises(AuditDetailRefused):
        await write_audit(conn, details={"note": bad}, **KW)  # type: ignore[arg-type]
    assert conn.calls == []


async def test_the_action_origin_key_and_tokens_are_closed():
    for override in ({"action": "patient.delete"}, {"origin": "production"}, {"idempotency_key": "  "}, {"resource_type": "A merge"}, {"actor_role": "Front Desk"}):
        conn = Conn()
        with pytest.raises(AuditError):
            await write_audit(conn, details={}, **{**KW, **override})  # type: ignore[arg-type]
        assert conn.calls == []
    with pytest.raises(AuditDetailRefused):
        await write_audit(Conn(), details={"Bad Key": "x"}, **KW)  # type: ignore[arg-type]


async def test_a_failing_insert_propagates_so_the_decision_does_not_commit():
    class Broken(Conn):
        async def execute(self, statement, params=None):
            raise RuntimeError("audit unavailable")

    with pytest.raises(RuntimeError):
        await write_audit(Broken(), details={}, **KW)  # type: ignore[arg-type]


# ---- the gate: a human-decision route cannot lose its audit call ----------------------------------------------------------------------------------------

ROUTES = {
    "review_referral": "referral.review_decision",
    "propose_merge": "merge.propose",
    "approve_merge": "merge.approve",
    "unmerge": "merge.unmerge",
    "confirm_patient_identity": "patient.confirm_identity",
    "revoke_patient_identity": "patient.revoke_identity",
    "acknowledge_review_task": "review_task.acknowledge",
    "resolve_review_task": "review_task.resolve",
    "send_offer_route": "offer.send",
    "interpret_reply_route": "offer.interpret_reply",
    "release_hold_route": "hold.release",
    "mark_offer_delivered": "offer.mark_delivered",
    "search_patients": "patient.search",
    "open_patient_record": "patient.view",
    "patient_labels_route": "patient.view_labels",
    "register_document": "document.register",
}


SHARED_TRANSITION = {"acknowledge_review_task": "acknowledge", "resolve_review_task": "resolve"}


@pytest.mark.parametrize(("function", "action"), sorted(ROUTES.items()))
async def test_each_human_decision_route_writes_its_audit_row_in_the_same_transaction(function, action):
    source = inspect.getsource(getattr(app_module, function))
    if function in SHARED_TRANSITION:  # these two routes share one function that decides and then audits; the route must call it with its own verb
        verb = SHARED_TRANSITION[function]
        assert f'_transition("{verb}"' in source and action == f"review_task.{verb}"
        source = inspect.getsource(app_module._transition)
        assert 'action=f"review_task.{action}"' in source
    else:
        assert f'action="{action}"' in source
    assert "await write_audit(" in source
    assert "idempotency_key=f\"audit:" in source  # keyed on the request's idempotency key, so a replay writes nothing more
    assert source.index("await write_audit(") > source.index("await ")  # after the decision itself, on the same connection `conn` (the request's transaction)
    assert re.search(r"write_audit\(\s*conn,", source)


SETUP_ROUTES = {
    "create_location": "setup.create_location",
    "create_provider": "setup.create_provider",
    "create_visit_type": "setup.create_visit_type",
    "create_slot": "setup.create_slot",
}


@pytest.mark.parametrize(("function", "action"), sorted(SETUP_ROUTES.items()))
async def test_each_set_up_route_audits_through_the_one_helper_in_the_same_transaction(function, action):
    source = inspect.getsource(getattr(app_module, function))
    assert f'action="{action}"' in source and "await _setup_audit(" in source and source.index("await _setup_audit(") > source.index("await _setup_insert(")
    helper = inspect.getsource(app_module._setup_audit)
    assert "await write_audit(" in helper and 'idempotency_key=f"audit:' in helper and re.search(r"write_audit\(\s*conn,", helper)


BACKFILL_ROUTES = {"start_backfill": "backfill.start", "decide_backfill": "backfill.decide", "reply_backfill": "backfill.reply"}


@pytest.mark.parametrize(("function", "action"), sorted(BACKFILL_ROUTES.items()))
async def test_each_backfill_route_audits_through_the_one_helper_after_the_step_in_the_same_transaction(function, action):
    source = inspect.getsource(getattr(app_module, function))
    assert f'action="{action}"' in source and "await _backfill_audit(" in source and source.index("await _backfill_audit(") > source.index("await runner.")
    helper = inspect.getsource(app_module._backfill_audit)
    assert "await write_audit(" in helper and 'idempotency_key=f"audit:' in helper and re.search(r"write_audit\(\s*conn,", helper)


CONSOLE_ROUTES = {"acknowledge_alert": "alert.acknowledge"}
PRODUCER_ROUTES = {
    "set_autonomy": ("autonomy.set", "set_mode"), "create_care_plan": ("care_plan.create", "create_plan"), "start_visit_readiness": ("readiness.start", "start"), "propose_autonomous_mode": ("autonomy.propose", "propose_autonomous"), "reply_visit_readiness": ("readiness.reply", "reply"), "record_result": ("result.record", "record_result"), "confirm_care_plan": ("care_plan.confirm", "confirm"), "create_care_episode": ("care_gap.episode_create", "create_episode"), "record_rule_done": ("care_gap.rule_done", "record_done"),
    "recheck_care_sample": ("care_gap.sample_recheck", "recheck_sample"),
}  # (migration 0048)
CARE_GAP_ROUTES = {"record_barrier": ("care_gap.barrier_record", "record_barrier"), "resolve_barrier": ("care_gap.barrier_resolve", "resolve_barrier")}


@pytest.mark.parametrize(("function", "action"), sorted(CONSOLE_ROUTES.items()))
async def test_acknowledging_an_alert_audits_in_the_same_transaction_after_the_update(function, action):
    source = inspect.getsource(getattr(app_module, function))
    assert f'action="{action}"' in source and "await write_audit(" in source and source.index("await write_audit(") > source.index("update ops.alerts")


@pytest.mark.parametrize(("function", "spec"), sorted(PRODUCER_ROUTES.items()))
async def test_the_producer_routes_audit_in_the_same_transaction_after_the_write(function, spec):
    action, helper = spec
    source = inspect.getsource(getattr(app_module, function))
    assert f'action="{action}"' in source and "await write_audit(" in source and source.index("await write_audit(") > source.index(f".{helper}(")


@pytest.mark.parametrize(("function", "spec"), sorted(CARE_GAP_ROUTES.items()))
async def test_the_care_gap_write_routes_audit_in_the_same_transaction_after_the_write(function, spec):
    action, helper = spec
    source = inspect.getsource(getattr(app_module, function))
    assert f'action="{action}"' in source and "await write_audit(" in source and source.index("await write_audit(") > source.index(f"care_gap_sql.{helper}(")


async def test_every_action_has_a_route_or_is_listed_as_pending():
    pending = {"clinical.tool_call"}  # written by careflow/mcp_kit.py `audited` (no clinical server is mounted yet)
    written_by_the_database = {"patient.erase_request", "patient.erase", "patient.erase_cancel", "staff.invite", "staff.grant_role", "staff.revoke_role", "staff.suspend", "staff.reinstate", "staff.activate", "backfill.task_raised", "canary.injected", "voice.transcript_read", "voice.transcript_purge", "patient.register", "patient.activate", "patient.claim", "portal.claim_approve", "portal.claim_reject", "consent.notice_publish", "autonomy.proposal_approve", "autonomy.proposal_reject", "autonomy.proposal_expire", "care_record.erased", "break_glass.open", "break_glass.read", "break_glass.refused", "break_glass.review", "autonomy.history_read", "note.write", "grievance.file", "grievance.acknowledged", "grievance.resolved", "portal.export", "staff.recovery_request", "staff.recovery_approve", "staff.recovery_complete", "staff.recovery_cancel"}  # (patient.*: iam.register_patient_account, iam.activate_patient_account and mpi.claim_patient_record, migration 0049) (voice.*: msg.read_voice_transcript and msg.purge_expired_voice_chunks, migration 0047) (canary.injected: by docs.register_canary_document, migration 0042) (backfill.task_raised: by agents.raise_backfill_task, migration 0038) written by the mpi.*_patient_erasure functions (migration 0032) in the same transaction; the live tests pin the rows
    assert set(ROUTES.values()) | set(SETUP_ROUTES.values()) | set(BACKFILL_ROUTES.values()) | set(CONSOLE_ROUTES.values()) | {a for a, _ in CARE_GAP_ROUTES.values()} | {a for a, _ in PRODUCER_ROUTES.values()} | pending | written_by_the_database == set(ACTIONS)


async def test_the_audit_module_adds_no_dependency_and_uses_only_the_existing_table():
    text = Path(app_module.__file__).with_name("audit.py").read_text(encoding="utf-8")
    assert "audit.log" in text and "create " not in text.lower().replace("create_", "") and "grant " not in text.lower()
