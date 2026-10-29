"""Phase 3, slice 2, MECHANISM ONLY: the front desk's "draft offer" read and "send offer" action (docs/design/phase3-scheduling-agent.md sections 1, 1a, 3, 4).

* The draft is COMPUTED on demand and stored nowhere: it is the read-only `propose_offer` tool of the in-process `scheduling` MCP server, called under the front-desk person's own session.
* SEND is one transaction under that same person's own session and role: hold the slot for the chosen candidate (`SlotManager.hold_slot`), find or open the patient's `appointments` thread, and
  record the offer as a staff message whose body is the clinician-approved template VERBATIM (nothing is interpolated: no name, no date, no patient text). Every write is done by a
  grant and a policy that already exist for front desk; there is no worker identity and no new grant.
* The candidate must be one of the top `backfill_candidates` of the waitlist ranking recomputed here (the client's choice is never trusted), and the slot must be open and of the same clinic and
  visit type.
* Settings unset means UNAVAILABLE: every clinician-owned value is required and undefaulted (`SchedulingSettings`); the profile-gated demo placeholders (careflow/demo_phase3.py) fill them only in
  `demo` with `data_policy=synthetic_only`.
* NOT built, by design: delivery to the patient (no channel is configured; nothing leaves the system), reply interpretation (blocked on the durable spend ledger), expiry of the offer after
  `offer_expiry_seconds` (needs a job that does not exist yet; the hold itself expires after `hold_lifetime_seconds`), and any check of the patient's open red-flag tasks before sending (what such a
  check must do is a clinical decision, clinician-sheet item 22).
"""

import math
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.crypto import DemographicVault
from careflow.messages import encrypt_body
from careflow.scheduling import SlotManager
from careflow.scheduling_agent import SchedulingNotConfigured, SchedulingSettings

REQUIRED = ("backfill_candidates", "offer_expiry_seconds", "hold_lifetime_seconds", "offer_message_template")


class OfferError(Exception):
    """Fixed words only."""


class OfferNotConfigured(OfferError):
    def __init__(self, names: list[str]) -> None:
        super().__init__("offers are unavailable: the clinician-owned settings are not configured")
        self.names = names


class NotACandidate(OfferError):
    """The patient is not among the top ranked candidates for this visit type, or the slot is not this clinic's / this visit type's."""


class OfferConflict(OfferError):
    """The idempotency key was used for a different offer."""


@dataclass(frozen=True)
class OfferSent:
    hold_id: uuid.UUID
    slot_id: uuid.UUID
    patient_id: uuid.UUID
    thread_id: uuid.UUID
    message_id: uuid.UUID
    hold_expires_at: datetime
    replayed: bool


def _require(settings: SchedulingSettings) -> None:
    try:
        settings.require(*REQUIRED)
    except SchedulingNotConfigured as exc:
        raise OfferNotConfigured(list(exc.names)) from None


async def _thread_for(conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID, created_by: uuid.UUID, key: str) -> uuid.UUID:
    """The patient's `appointments` thread (one per patient and topic): found, or opened by this person. Front desk may open this topic only (policy threads_insert)."""
    found = (await conn.execute(text("select id from msg.threads where patient_id = :p and topic = 'appointments' and clinic_id = :c"), {"p": patient_id, "c": clinic_id})).first()
    if found is not None:
        return found[0]
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) "
                    "values (:c, :p, 'appointments', :u, :o, :k)"
                ),
                {"c": clinic_id, "p": patient_id, "u": created_by, "o": origin, "k": key},
            )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":  # lost a race with another opener of the same thread: read it below
            raise
    row = (await conn.execute(text("select id from msg.threads where patient_id = :p and topic = 'appointments' and clinic_id = :c"), {"p": patient_id, "c": clinic_id})).first()
    if row is None:
        raise NotACandidate("no such patient in this clinic")
    return row[0]


async def send_offer(
    conn: AsyncConnection, vault: DemographicVault, settings: SchedulingSettings, *, key_version: int, clinic_id: uuid.UUID, origin: str, visit_type_id: uuid.UUID, slot_id: uuid.UUID,
    waitlist_id: uuid.UUID, actor_id: uuid.UUID, idempotency_key: str, slots: SlotManager | None = None,
) -> OfferSent:
    """Hold the slot for the candidate and record the offer message, in the caller's transaction. Idempotent on `idempotency_key`; a replay writes nothing more."""
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise OfferError("an idempotency key is required on every write")
    _require(settings)
    manager = slots or SlotManager()
    slot = (await conn.execute(text("select clinic_id, visit_type_id from sched.slots where id = :s"), {"s": slot_id})).first()
    if slot is None or slot[0] != clinic_id or slot[1] != visit_type_id:
        raise NotACandidate("the slot does not belong to this clinic and visit type")
    ranked = (await manager.rank_waitlist(conn, clinic_id, visit_type_id))[: settings.backfill_candidates]
    chosen = next((c for c in ranked if c.waitlist_id == waitlist_id), None)
    if chosen is None:
        raise NotACandidate("this patient is not among the ranked candidates")
    minutes = max(1, math.ceil(settings.hold_lifetime_seconds / 60))  # type: ignore[operator]  # required above
    hold = await manager.hold_slot(conn, slot_id, minutes, idempotency_key=f"offer-hold:{idempotency_key}", patient_id=chosen.patient_id)
    thread_id = await _thread_for(conn, clinic_id=clinic_id, origin=origin, patient_id=chosen.patient_id, created_by=actor_id, key=f"offer-thread:{chosen.patient_id}")
    message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"careflow-offer-message:{idempotency_key}")  # the same key always names the same message, so a replay recognises it
    body = encrypt_body(vault, settings.offer_message_template or "", clinic_id=clinic_id, thread_id=thread_id, key_version=key_version, message_id=message_id)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
                    "values (:i, :c, :o, 'appointments', :t, 'staff', :u, :b, :v, :k)"
                ),
                {"i": body.message_id, "c": clinic_id, "o": origin, "t": thread_id, "u": actor_id, "b": body.body_ct, "v": body.body_key_version, "k": f"offer-message:{idempotency_key}"},
            )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        if not hold.replayed:
            raise OfferConflict("this idempotency key was used for a different offer") from exc
    return OfferSent(hold.hold_id, slot_id, chosen.patient_id, thread_id, body.message_id, hold.expires_at, hold.replayed)
