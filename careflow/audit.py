"""Audit rows for human decisions (BUILD-SPEC.md 17.1 `audit`, 18.4; pre-launch item 5). Writes `audit.log` from the CALLING person's own session, in the SAME transaction as the decision.

* No migration and no new grant: `careflow_app` already holds INSERT on `audit.log`, and the policy `log_insert_own_clinic` (migration 0030, replacing 0001's `log_insert_as_self`) requires `actor_id` to be the signed-in account holding a front-desk, nurse, doctor or admin role at the row's clinic, so nobody
  can write a row as someone else. Reading audit rows stays with the auditor role.
* Same transaction: the decision and its audit row commit together or not at all. If the audit insert fails the exception propagates and the request rolls back (a decision with no audit row must not
  commit).
* Idempotent: `audit.log.idempotency_key` is unique. The insert is PLAIN (no `ON CONFLICT`: Postgres checks the new row against the SELECT policy for an insert with `ON CONFLICT`, and `careflow_app`
  may select audit rows only as an auditor, so that form is refused by row-level security for everyone else). The duplicate-key error (23505) is caught inside a SAVEPOINT, so a replayed request writes
  no second row and the surrounding decision is untouched. Any OTHER error propagates and the request rolls back (fail closed).
* IDS AND CLOSED WORDS ONLY (`audit.log` says "never patient text in details"): every detail value must be a uuid, an integer, a boolean, or a short token of lower-case letters, digits and `_ : . -`.
  Free text, names, dates and anything with spaces are refused with `AuditDetailRefused` before the query is built. `clinic_id` comes from the row the decision is about, never from the request body.
* The set of actions is closed (`ACTIONS`).
"""

import json
import re
import uuid
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

ACTIONS: Final = frozenset(
    {
        "referral.review_decision",  # a nurse or doctor recorded a decision on a paused referral run
        "merge.propose",
        "merge.approve",
        "merge.unmerge",
        "patient.confirm_identity",
        "patient.revoke_identity",
        "review_task.acknowledge",
        "review_task.resolve",
        "document.register",  # a front-desk person or nurse registered an uploaded document (careflow/documents.py)
        "patient.view",  # a front-desk person, nurse or doctor OPENED one record in full: who, role, patient id; never the contents
        "patient.view_labels",  # list rows decrypted for a page: the COUNT only
        "patient.search",  # a front-desk person searched for a patient: the KIND of search and the result count only, never the query or the results
        "setup.create_location", "setup.create_provider", "setup.create_visit_type", "setup.create_slot",  # a front-desk person or admin set the clinic up (careflow/app.py set-up routes): ids only
        "patient.erase_request", "patient.erase", "patient.erase_cancel",  # the two-person erasure of a patient's identity (written by the mpi.*_patient_erasure functions in the same transaction): reason code and role scope only
        "staff.invite", "staff.grant_role", "staff.revoke_role", "staff.suspend", "staff.reinstate", "staff.activate",  # staff account changes, written by the iam.* functions (migration 0034): role word and scope only
        "backfill.start", "backfill.decide", "backfill.reply", "backfill.task_raised", "canary.injected", "alert.acknowledge", "care_gap.barrier_record", "care_gap.barrier_resolve", "voice.transcript_read", "voice.transcript_purge", "retention.report", "red_flag_rule.approve", "red_flag_rule.approve_recorded", "red_flag_rule.reject", "red_flag_rule.draft_create", "red_flag_rule.draft_withdraw", "red_flag_rule.retire", "red_flag_rule.retire_recorded", "medication.record", "autonomy.set", "care_record.erased", "break_glass.open", "break_glass.read", "break_glass.refused", "break_glass.review", "autonomy.history_read", "note.write", "grievance.file", "grievance.acknowledged", "grievance.resolved", "portal.export", "staff.recovery_request", "staff.recovery_approve", "staff.recovery_complete", "staff.recovery_cancel", "autonomy.propose", "autonomy.proposal_approve", "autonomy.proposal_reject", "autonomy.proposal_expire", "readiness.start", "readiness.reply", "portal.claim_approve", "portal.claim_reject", "consent.notice_publish", "care_plan.create", "care_plan.confirm", "result.record", "patient.register", "patient.activate", "patient.claim", "care_gap.episode_create", "care_gap.rule_done", "care_gap.sample_recheck",  # a front-desk person started a backfill run for a freed slot, or answered one of its pauses: the closed action or outcome word and the run status only
        "offer.mark_delivered",  # a front-desk person delivered an offer by hand (channel manual): hold id and the closed channel word only
        "hold.release",  # a front-desk person gave a held slot back before its time ran out
        "offer.send", "offer.interpret_reply", "offer.reply_record",  # a front-desk person sent an offer: the slot hold and the offer message (careflow/offers.py)
        "clinical.tool_call",  # a call on the clinical read-only MCP server (careflow/mcp_kit.py `audited`): tool name and outcome only
    }
)
ORIGINS: Final = ("live", "test", "eval", "canary")
_TOKEN = re.compile(r"^[a-z0-9_:.\-]{1,64}$")


class AuditError(Exception):
    """Fixed words only."""


class AuditDetailRefused(AuditError):
    """A detail value that is not an id, a number, a boolean or a short closed token."""


def _check_detail(key: str, value: Any) -> Any:
    if not _TOKEN.match(key):
        raise AuditDetailRefused("a detail key must be a short lower-case token")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bool | int):
        return value
    if isinstance(value, str) and _TOKEN.match(value):
        return value
    raise AuditDetailRefused(f"the detail {key!r} is not an id, a number, a boolean or a closed token")


async def write_audit(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, actor_id: uuid.UUID, actor_role: str, action: str, resource_type: str, resource_id: uuid.UUID,
    details: dict[str, Any], idempotency_key: str,
) -> bool:
    """Insert one audit row; True if a row was written, False if the key already existed (a replay)."""
    if action not in ACTIONS:
        raise AuditError("unknown audit action")
    if origin not in ORIGINS:
        raise AuditError("unknown origin")
    if not idempotency_key or not idempotency_key.strip():
        raise AuditError("an idempotency key is required on every write")
    if not _TOKEN.match(resource_type) or not _TOKEN.match(actor_role):
        raise AuditError("resource type and actor role must be closed tokens")
    clean = {k: _check_detail(k, v) for k, v in details.items()}
    try:
        async with conn.begin_nested():
            result = await conn.execute(
                text(
                    "insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key) "
                    # details.role_scope: 'active' when the request carried a verified X-CareFlow-Active-Role (narrowed), else 'all_grants' (docs/design/active-role.md section 6). Set in SQL from the same claims the policies read.
                    "values (:o, :c, :a, :r, :act, :rt, :ri, cast(:d as jsonb) || jsonb_build_object('role_scope', iam.role_scope()), :k)"
                ),
                {"o": origin, "c": clinic_id, "a": actor_id, "r": actor_role, "act": action, "rt": resource_type, "ri": str(resource_id), "d": json.dumps(clean), "k": idempotency_key},
            )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":  # the key already exists: a replay
            return False
        raise
    return result.rowcount == 1
