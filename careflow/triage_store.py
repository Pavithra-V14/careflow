"""The SQL writers behind the Front Door's two seams (migration 0018; BUILD-SPEC.md §12; CLAUDE.md rules 5, 6, 8, 9). Package P2-9.

`SqlTriageProposer` implements `TriageProposer`: it writes the review task and the proposal (ids only: no card content, no patient text, NO triage priority) in
the caller's transaction, idempotently. `SqlReviewRecorder` implements `ReviewRecorder`: it records a HUMAN decision and resolves the review task.

Both run as `careflow_app` with the verified claims set; row-level security is the enforcement (the proposal: queue worker, nurse or doctor; the decision: a nurse or
doctor of the clinic, as themselves). The Python checks here only give clean errors; they are not the control.

Nothing is approved by these writers on their own: the proposer writes a proposal and an OPEN task, and the recorder needs a reviewer id. The task's own `priority`
column ('standard') is the urgency class of the TASK, not a clinical triage priority (that belongs to a card or a clinician and is not stored here).
"""

import json
import uuid
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.front_door import TriageProposal
from careflow.triage import ApprovalMode

_NAMESPACE: Final = uuid.UUID("0b6c2f9e-3a47-4c5d-8d41-7a3c2f1e9b55")
_INSUFFICIENT_PRIVILEGE: Final = "42501"
_UNIQUE_VIOLATION: Final = "23505"
ASSIGNABLE_ROLES: Final = ("nurse", "doctor")  # review_tasks.assigned_role also allows front_desk; a triage review is clinical


class TriageStoreError(Exception):
    """Fixed words only."""


class NotPermitted(TriageStoreError):
    """The caller's role may not do this in this clinic."""


class DecisionConflict(TriageStoreError):
    """This proposal already has a different decision, or this key was used for a different one."""


def proposal_id_for(clinic_id: uuid.UUID, idempotency_key: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{clinic_id}|{idempotency_key}")


class SqlTriageProposer:
    def __init__(self, *, mode: ApprovalMode, assigned_role: str) -> None:
        # No default for the role: who reviews a triage proposal is a clinical-governance decision for the owner and clinicians.
        if assigned_role not in ASSIGNABLE_ROLES:
            raise ValueError(f"assigned_role must be one of {ASSIGNABLE_ROLES}")
        self._mode, self._role = ApprovalMode(mode), assigned_role

    async def propose(
        self, conn: AsyncConnection, *, document_id: uuid.UUID, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID | None,
        candidates: list[dict[str, Any]], abstained: bool, idempotency_key: str,
    ) -> TriageProposal:
        if patient_id is None:
            raise TriageStoreError("a triage proposal needs a resolved patient")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise TriageStoreError("an idempotency key is required on every write")
        ids = [{"card_id": str(c["card_id"]), "version": int(c["version"])} for c in candidates]  # ids only, whatever else was in the dict
        proposal_id = proposal_id_for(clinic_id, idempotency_key)
        task_key = f"triage-task:{document_id}"
        try:
            async with conn.begin_nested():
                task = (
                    await conn.execute(
                        text(
                            "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
                            "values (:c, :o, 'triage_proposal', 'standard', :r, :s, cast(:p as jsonb), :k) on conflict (idempotency_key) do nothing returning id"
                        ),
                        {"c": clinic_id, "o": origin, "r": self._role, "s": f"document:{document_id}", "p": json.dumps({"proposal_id": str(proposal_id)}), "k": task_key},
                    )
                ).first()
                if task is None:  # a replay: the task exists
                    task = (await conn.execute(text("select id from agents.review_tasks where idempotency_key = :k"), {"k": task_key})).first()
                if task is None:
                    raise TriageStoreError("this task key is used by another request")
                await conn.execute(
                    text(
                        "insert into agents.triage_proposals (id, clinic_id, origin, document_id, patient_id, candidates, abstained, mode, review_task_id, idempotency_key) "
                        "values (:i, :c, :o, :d, :p, cast(:cand as jsonb), :a, :m, :t, :k) on conflict (clinic_id, idempotency_key) do nothing"
                    ),
                    {"i": proposal_id, "c": clinic_id, "o": origin, "d": document_id, "p": patient_id, "cand": json.dumps(ids), "a": abstained, "m": self._mode.value,
                     "t": task.id, "k": idempotency_key},
                )
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state == _INSUFFICIENT_PRIVILEGE:
                raise NotPermitted("this role may not write a triage proposal here") from None
            if state == _UNIQUE_VIOLATION:
                raise TriageStoreError("a different proposal already exists for this document") from None
            raise
        stored = (
            await conn.execute(text("select id, review_task_id from agents.triage_proposals where clinic_id = :c and idempotency_key = :k"), {"c": clinic_id, "k": idempotency_key})
        ).first()
        if stored is None or stored.id != proposal_id:
            raise TriageStoreError("this proposal key is used by another request")
        return TriageProposal(stored.id, stored.review_task_id)


class SqlReviewRecorder:
    async def record(
        self, conn: AsyncConnection, *, proposal_id: uuid.UUID, clinic_id: uuid.UUID, decision: str, reviewer_id: uuid.UUID, idempotency_key: str
    ) -> None:
        if decision not in ("approve", "edit", "reject"):
            raise TriageStoreError("unknown decision")
        origin_row = (
            await conn.execute(text("select origin, review_task_id from agents.triage_proposals where id = :p and clinic_id = :c"), {"p": proposal_id, "c": clinic_id})
        ).first()
        if origin_row is None:
            raise TriageStoreError("no such proposal")  # missing and invisible are one answer
        try:
            async with conn.begin_nested():
                inserted = (
                    await conn.execute(
                        text(
                            "insert into agents.review_decisions (clinic_id, origin, proposal_id, decision, decided_by, idempotency_key) "
                            "values (:c, :o, :p, :d, :u, :k) on conflict (clinic_id, idempotency_key) do nothing returning id"
                        ),
                        {"c": clinic_id, "o": origin_row.origin, "p": proposal_id, "d": decision, "u": reviewer_id, "k": idempotency_key},
                    )
                ).first()
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state == _INSUFFICIENT_PRIVILEGE:
                raise NotPermitted("only a nurse or doctor of the clinic may record a review decision, as themselves") from None
            if state == _UNIQUE_VIOLATION:
                raise DecisionConflict("this proposal already has a decision") from None
            raise
        if inserted is None:  # a replay of the same key: it must be the same decision
            same = (
                await conn.execute(
                    text("select 1 from agents.review_decisions where clinic_id = :c and idempotency_key = :k and proposal_id = :p and decision = :d and decided_by = :u"),
                    {"c": clinic_id, "k": idempotency_key, "p": proposal_id, "d": decision, "u": reviewer_id},
                )
            ).first()
            if same is None:
                raise DecisionConflict("this key was used for a different decision")
            return
        await conn.execute(
            text(
                "update agents.review_tasks set status = 'resolved', acknowledged_by = coalesce(acknowledged_by, :u), acknowledged_at = coalesce(acknowledged_at, now()), "
                "resolved_at = now() where id = :t and clinic_id = :c and status <> 'resolved'"
            ),
            {"u": reviewer_id, "t": origin_row.review_task_id, "c": clinic_id},
        )
