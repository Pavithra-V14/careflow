"""Patient merge and unmerge: logical links, never destructive (BUILD-SPEC.md §12.1, §12.5, §17.1, §17.2).

`IdentityManager` links a subordinate patient record to a surviving master through `mpi.merges`. Nothing here
deletes a patient row or touches a ciphertext or token; a merge is a ROW, and routing is derived from it
(`resolve`), so there is no second copy of "who points where" that can drift.

Why a merge takes two calls
  §12.5 makes a patient-record merge a TWO-PERSON action and §12.1 locks it to a human, so no code path may
  complete one alone:
    1. `execute_merge`  - a front-desk person proposes the link and signs as `approved_by`. Status becomes
                          `awaiting_second_approval`. No routing changes yet; the review row stays pending.
    2. `approve_merge`  - a DIFFERENT front-desk person confirms. Status becomes `active`, the review-ledger
                          row (`mpi.match_candidates`) becomes `resolved`, and from then on `resolve()` routes
                          the subordinate to the master.
  Each signature must be the signed-in caller's own (row-level security checks `approved_by = auth.uid()` and
  the same for the second approver and for unmerge), so a person cannot name someone else as the approver. The
  database refuses an active merge with fewer than two distinct named people.

Unmerge is ONE person, deliberately: undoing a possibly wrong merge must be quick. It marks the link
`unmerged` (the row stays), so `resolve()` returns each record as itself again, and it raises a review task
for a nurse listing every decision recorded inside the link window (bookings, holds and waitlist entries of
either record, and review rows) so a human can re-check them (§17.2). It also cancels a proposal that was
never confirmed (no decisions were made in that window, so no task is raised).

Idempotency (rule 5): each step carries its own key. Replaying a key with the same parameters returns the
original result with `replayed=True`; the same key with different parameters raises IdempotencyConflict.

Window limit, stated rather than hidden: a decision belongs to the link window by its row's `created_at`, which
the tables stamp with the TRANSACTION's start time. A decision whose transaction began before the link was
activated but committed after is not listed; the nurse review is a safety net over the usual case, not a proof of
completeness, and a human should treat the list as a floor.

Routing note: `resolve` reads `mpi.merges` under row-level security, so only roles that can read merges
(front desk, nurse, doctor) see links. A caller who cannot read them gets each record back as itself; do not use
it from a role outside those three.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

ORIGINS: Final = ("live", "test", "eval", "canary")
Origin = Literal["live", "test", "eval", "canary"]
MAX_LISTED_DECISIONS: Final = 500


class IdentityError(Exception):
    """Base class: the request was refused and nothing was changed."""


class PatientNotFound(IdentityError):
    """A patient does not exist in this clinic, or the caller's role cannot see it."""


class MergeNotFound(IdentityError):
    """No such merge, or the caller's role cannot see it."""


class MergeNotAllowed(IdentityError):
    """The merge breaks a rule (self-link, chain, already linked, erased, wrong origin, no review row...)."""


class InvalidTransition(IdentityError):
    """The requested state change does not apply to the merge's current state."""


class IdempotencyConflict(IdentityError):
    """This idempotency key is already used by a request with different parameters."""


class NotPermitted(IdentityError):
    """Row-level security refused the write: only front desk may do this, and only as themselves."""


@dataclass(frozen=True)
class MergeResult:
    merge_id: uuid.UUID
    status: str
    master_id: uuid.UUID
    subordinate_id: uuid.UUID
    replayed: bool = False


@dataclass(frozen=True)
class UnmergeResult:
    merge_id: uuid.UUID
    review_task_id: uuid.UUID | None  # None when a never-confirmed proposal was cancelled
    decisions_flagged: int
    replayed: bool = False


def _key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityError("an idempotency key is required on every write")
    return value


def _check_origin(origin: object) -> None:
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")


def _raise_for_db(exc: DBAPIError, *, context: str) -> None:
    state = getattr(exc.orig, "sqlstate", None)
    detail = str(exc.orig)
    if state == "42501":
        raise NotPermitted("only front desk of this clinic may do this, and only as themselves") from exc
    if "merges_two_distinct_people" in detail:
        raise MergeNotAllowed("a merge needs two different people to approve it") from exc
    if "merges_one_live_link" in detail:
        raise MergeNotAllowed("that record is already part of a live merge link") from exc
    if "merges_not_self" in detail:
        raise MergeNotAllowed("a record cannot be merged into itself") from exc
    if state == "23514":  # a CHECK or the link-rules trigger
        raise MergeNotAllowed(f"{context} rejected by a database rule: {exc.orig}") from exc
    if "idempotency_key" in detail:
        raise IdempotencyConflict("this idempotency key is already used by another request") from exc


class IdentityManager:
    async def resolve(self, conn: AsyncConnection, patient_id: uuid.UUID) -> uuid.UUID:
        """The record this patient currently routes to: its master if it is the subordinate of an ACTIVE merge,
        otherwise itself. A merely proposed link does not route anything."""
        row = (
            await conn.execute(
                text("select master_patient_id from mpi.merges where subordinate_patient_id = :p and status = 'active'"),
                {"p": patient_id},
            )
        ).first()
        return row[0] if row else patient_id

    # ---- step 1: propose ---------------------------------------------------------------------------

    async def execute_merge(
        self,
        conn: AsyncConnection,
        clinic_id: uuid.UUID,
        origin: Origin,
        master_id: uuid.UUID,
        subordinate_id: uuid.UUID,
        approved_by_user_id: uuid.UUID,
        idempotency_key: str,
    ) -> MergeResult:
        """Propose linking `subordinate_id` to `master_id`, signed by the caller as `approved_by_user_id`. The link
        is NOT effective until a different front-desk person calls `approve_merge`. Nothing is deleted or edited."""
        key = _key(idempotency_key)
        _check_origin(origin)
        if master_id == subordinate_id:
            raise MergeNotAllowed("a record cannot be merged into itself")

        await self._lock_patients(conn, clinic_id, origin, master_id, subordinate_id)
        prior = (
            await conn.execute(
                text("select id, status, master_patient_id, subordinate_patient_id, approved_by, clinic_id, origin "
                     "from mpi.merges where idempotency_key = :k"),
                {"k": key},
            )
        ).first()
        if prior is not None:
            same = (prior.master_patient_id, prior.subordinate_patient_id, prior.approved_by, prior.clinic_id, prior.origin) == (
                master_id, subordinate_id, approved_by_user_id, clinic_id, origin)
            if not same:
                raise IdempotencyConflict("this idempotency key was used for a different merge")
            return MergeResult(prior.id, prior.status, master_id, subordinate_id, replayed=True)

        candidate = (
            await conn.execute(
                text(
                    """select id from mpi.match_candidates
                       where clinic_id = :c and status = 'pending'
                         and ((patient_id = :m and candidate_patient_id = :s) or (patient_id = :s and candidate_patient_id = :m))
                       order by created_at limit 1"""
                ),
                {"c": clinic_id, "m": master_id, "s": subordinate_id},
            )
        ).first()
        if candidate is None:
            raise MergeNotAllowed("these records are not a pending possible-duplicate; a merge starts from a review row")

        live = (
            await conn.execute(
                text(
                    """select 1 from mpi.merges
                       where status in ('awaiting_second_approval', 'active')
                         and (subordinate_patient_id in (:m, :s) or master_patient_id in (:m, :s)) limit 1"""
                ),
                {"m": master_id, "s": subordinate_id},
            )
        ).first()
        if live is not None:
            raise MergeNotAllowed("one of these records is already part of a live merge link (links stay flat)")
        try:
            async with conn.begin_nested():
                row = (
                    await conn.execute(
                        text(
                            """insert into mpi.merges (clinic_id, master_patient_id, subordinate_patient_id, candidate_id,
                                                       approved_by, origin, idempotency_key)
                               values (:c, :m, :s, :cand, :ap, :o, :k) returning id, status"""
                        ),
                        {"c": clinic_id, "m": master_id, "s": subordinate_id, "cand": candidate[0], "ap": approved_by_user_id, "o": origin, "k": key},
                    )
                ).one()
        except (IntegrityError, DBAPIError) as exc:
            _raise_for_db(exc, context="merge proposal")
            raise
        return MergeResult(row.id, row.status, master_id, subordinate_id)

    # ---- step 2: confirm ---------------------------------------------------------------------------

    async def approve_merge(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, merge_id: uuid.UUID,
        second_approver_user_id: uuid.UUID, idempotency_key: str,
    ) -> MergeResult:
        """Confirm a proposed merge as a SECOND, different person. The link becomes active, the review row is
        resolved, and `resolve()` routes the subordinate to the master from now on."""
        key = _key(idempotency_key)
        _check_origin(origin)
        merge = await self._get_merge(conn, merge_id, clinic_id, origin, lock=True)
        if merge.status == "active":
            if merge.approve_idempotency_key == key and merge.second_approved_by == second_approver_user_id:
                return MergeResult(merge.id, "active", merge.master_patient_id, merge.subordinate_patient_id, replayed=True)
            raise InvalidTransition("this merge is already active")
        if merge.status != "awaiting_second_approval":
            raise InvalidTransition(f"this merge is {merge.status}, not awaiting approval")
        if second_approver_user_id == merge.approved_by:
            raise MergeNotAllowed("a merge needs two different people to approve it")
        await self._lock_patients(conn, clinic_id, origin, merge.master_patient_id, merge.subordinate_patient_id)
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        """update mpi.merges set status = 'active', second_approved_by = :sec, activated_at = clock_timestamp(),
                                                 approve_idempotency_key = :k where id = :i"""
                    ),
                    {"sec": second_approver_user_id, "k": key, "i": merge_id},
                )
                resolved = (
                    await conn.execute(
                        text(
                            """update mpi.match_candidates set status = 'resolved', resolved_at = clock_timestamp(), resolved_by = :sec
                               where id = :cand and status = 'pending' returning id"""
                        ),
                        {"sec": second_approver_user_id, "cand": merge.candidate_id},
                    )
                ).first()
                if resolved is None:
                    raise MergeNotAllowed("the review row for this pair is no longer pending")
        except (IntegrityError, DBAPIError) as exc:
            _raise_for_db(exc, context="merge approval")
            raise
        return MergeResult(merge.id, "active", merge.master_patient_id, merge.subordinate_patient_id)

    # ---- unmerge -----------------------------------------------------------------------------------

    async def execute_unmerge(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, merge_id: uuid.UUID,
        requested_by_user_id: uuid.UUID, idempotency_key: str,
    ) -> UnmergeResult:
        """Break the link. Both records are independent again (`resolve` returns each as itself). For a link that
        had been active, raises a nurse review task listing every decision recorded inside the link window
        (§17.2); cancelling a never-confirmed proposal raises none. Nothing is deleted."""
        key = _key(idempotency_key)
        _check_origin(origin)
        merge = await self._get_merge(conn, merge_id, clinic_id, origin, lock=True)
        if merge.status == "unmerged":
            if merge.unmerge_idempotency_key == key and merge.unmerged_by == requested_by_user_id:
                task = (await conn.execute(text("select id from agents.review_tasks where idempotency_key = :k"), {"k": f"unmerge-review:{merge.id}"})).first()
                return UnmergeResult(merge.id, task[0] if task else None, 0, replayed=True)
            raise InvalidTransition("this merge was already unmerged")
        await self._lock_patients(conn, clinic_id, origin, merge.master_patient_id, merge.subordinate_patient_id)
        was_active = merge.status == "active"
        flagged: dict[str, list[str]] = {"bookings": [], "holds": [], "waitlist": [], "review_rows": []}
        if was_active:
            flagged = await self._decisions_in_window(conn, merge)
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        """update mpi.merges set status = 'unmerged', unmerged_at = clock_timestamp(), unmerged_by = :by,
                                                 unmerge_idempotency_key = :k where id = :i"""
                    ),
                    {"by": requested_by_user_id, "k": key, "i": merge.id},
                )
                task_id = None
                count = sum(len(v) for v in flagged.values())
                if was_active:
                    provenance = {
                        "merge_id": str(merge.id), "master_patient_id": str(merge.master_patient_id),
                        "subordinate_patient_id": str(merge.subordinate_patient_id), "link_activated_at": merge.activated_at.isoformat(),
                        "decisions": flagged, "decisions_listed": count, "truncated_to": MAX_LISTED_DECISIONS,
                    }
                    task_id = (
                        await conn.execute(
                            text(
                                """insert into agents.review_tasks
                                       (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key)
                                   values (:c, :o, 'unmerge_review', 'immediate', 'nurse', :src, cast(:p as jsonb), :k)
                                   on conflict (idempotency_key) do nothing returning id"""
                            ),
                            {"c": clinic_id, "o": origin, "src": f"merge:{merge.id}", "p": json.dumps(provenance, sort_keys=True), "k": f"unmerge-review:{merge.id}"},
                        )
                    ).scalar_one_or_none()
        except (IntegrityError, DBAPIError) as exc:
            _raise_for_db(exc, context="unmerge")
            raise
        return UnmergeResult(merge.id, task_id, count)

    # ---- helpers -----------------------------------------------------------------------------------

    async def _lock_patients(self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str, a: uuid.UUID, b: uuid.UUID) -> None:
        """Lock both records in id order (so two merges touching the same record serialise without deadlocking)
        and check they exist in this clinic and origin and are not erased."""
        rows = (
            await conn.execute(
                text("select id, clinic_id, origin, erased_at from mpi.patients where id in (:a, :b) order by id for update"),
                {"a": a, "b": b},
            )
        ).all()
        if len(rows) != 2 or any(r.clinic_id != clinic_id for r in rows):
            raise PatientNotFound("both patients must exist in this clinic")
        if any(r.origin != origin for r in rows):
            raise MergeNotAllowed("both patients must have the same origin as the request")
        if any(r.erased_at is not None for r in rows):
            raise MergeNotAllowed("an erased patient record cannot be merged")

    async def _get_merge(self, conn: AsyncConnection, merge_id: uuid.UUID, clinic_id: uuid.UUID, origin: str, *, lock: bool):
        row = (
            await conn.execute(
                text(
                    "select * from mpi.merges where id = :i" + (" for update" if lock else "")
                ),
                {"i": merge_id},
            )
        ).first()
        if row is None or row.clinic_id != clinic_id or row.origin != origin:
            raise MergeNotFound("merge not found")
        return row

    async def _decisions_in_window(self, conn: AsyncConnection, merge) -> dict[str, list[str]]:
        """Ids (never patient text) of what was recorded for either record while the link was active."""
        both = {"m": merge.master_patient_id, "s": merge.subordinate_patient_id, "since": merge.activated_at, "n": MAX_LISTED_DECISIONS, "cand": merge.candidate_id}

        async def ids(sql: str) -> list[str]:
            return [str(r[0]) for r in (await conn.execute(text(sql), both)).all()]

        return {
            "bookings": await ids("select id from sched.bookings where patient_id in (:m, :s) and created_at >= :since order by created_at limit :n"),
            "holds": await ids("select id from sched.holds where patient_id in (:m, :s) and created_at >= :since order by created_at limit :n"),
            "waitlist": await ids("select id from sched.waitlist where patient_id in (:m, :s) and created_at >= :since order by created_at limit :n"),
            "review_rows": await ids(
                "select id from mpi.match_candidates where (patient_id in (:m, :s) or candidate_patient_id in (:m, :s)) "
                "and created_at >= :since and id <> :cand order by created_at limit :n"
            ),
        }
