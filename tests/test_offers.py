"""Phase 3 slice 2, mechanism only (careflow/offers.py), offline: a fake connection, a fake SlotManager and a fake vault. Proves: nothing happens without the clinician-owned settings, the client's
choice of candidate and slot is re-checked, the order and content of the writes, idempotency, and that nothing outside the front desk's existing grants is touched."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from careflow import offers
from careflow.scheduling_agent import SchedulingSettings

pytestmark = pytest.mark.asyncio
CLINIC, VT, SLOT, ACTOR = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
THREAD = uuid.uuid4()
NOW = datetime(2030, 1, 1, tzinfo=UTC)
TEMPLATE = "TEMPLATE-FROM-A-CLINICIAN"


def settings(**over):
    # test inputs only: every number and word here is a stand-in, not a recommendation
    values = {"offer_expiry_seconds": 11, "hold_lifetime_seconds": 900, "backfill_candidates": 2, "accept_confidence_threshold": 0.5, "offer_message_template": TEMPLATE}
    return SchedulingSettings(_env_file=None, **{**values, **over})  # type: ignore[call-arg]


def cand(n):
    return SimpleNamespace(waitlist_id=uuid.uuid4(), patient_id=uuid.uuid4(), priority_code="p", rank=n, joined_at=NOW)


class Result:
    def __init__(self, rows=(), rowcount=1):
        self._rows, self.rowcount = list(rows), rowcount

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSlots:
    def __init__(self, ranked, replayed=False):
        self.ranked, self.replayed, self.calls = ranked, replayed, []

    async def rank_waitlist(self, conn, clinic_id, visit_type_id):
        self.calls.append("rank")
        return self.ranked

    async def hold_slot(self, conn, slot_id, minutes, *, idempotency_key, patient_id=None):
        self.calls.append(("hold", minutes, idempotency_key, patient_id))
        return SimpleNamespace(hold_id=uuid.uuid4(), slot_id=slot_id, expires_at=NOW, replayed=self.replayed)


class FakeVault:
    def encrypt_message_body(self, plaintext, *, clinic_id, thread_id, message_id, key_version):
        self.last = (plaintext, clinic_id, thread_id, message_id, key_version)
        return b"\x00" * 12 + plaintext.encode()[::-1] + b"\x00" * 16


class Conn:
    def __init__(self, *, slot=(CLINIC, VT), thread=THREAD, message_error=None, thread_error=None):
        self.slot, self.thread, self.message_error, self.thread_error, self.statements, self.params = slot, thread, message_error, thread_error, [], []

    def begin_nested(self):
        class Savepoint:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return Savepoint()

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(params or {})
        if "from sched.slots" in sql:
            return Result([self.slot] if self.slot else [])
        if "select id from msg.threads" in sql:
            return Result([(self.thread,)] if self.thread else [])
        if "insert into msg.threads" in sql:
            if self.thread_error:
                raise self.thread_error
            self.thread = THREAD
            return Result()
        if "insert into msg.messages" in sql:
            if self.message_error:
                raise self.message_error
            return Result()
        raise AssertionError(sql)


def dup():
    return DBAPIError("insert", {}, SimpleNamespace(sqlstate="23505"))


async def send(conn, slots, cfg=None, key="k-1", **over):
    args = {"key_version": 1, "clinic_id": CLINIC, "origin": "test", "visit_type_id": VT, "slot_id": SLOT, "waitlist_id": slots.ranked[0].waitlist_id if slots.ranked else uuid.uuid4(),
            "actor_id": ACTOR, "idempotency_key": key, "slots": slots, **over}
    return await offers.send_offer(conn, FakeVault(), cfg or settings(), **args)  # type: ignore[arg-type]


async def test_nothing_runs_without_every_clinician_owned_setting_and_the_error_names_them():
    slots = FakeSlots([cand(1)])
    for missing in ("offer_expiry_seconds", "hold_lifetime_seconds", "backfill_candidates", "offer_message_template"):
        conn = Conn()
        with pytest.raises(offers.OfferNotConfigured) as raised:
            await send(conn, slots, settings(**{missing: None}))
        assert raised.value.names == [missing] and conn.statements == [] and slots.calls == []  # not even a read


async def test_a_blank_key_is_refused_before_anything_else():
    conn = Conn()
    with pytest.raises(offers.OfferError):
        await send(conn, FakeSlots([cand(1)]), key="  ")
    assert conn.statements == []


async def test_a_slot_of_another_clinic_or_visit_type_and_an_unranked_candidate_are_refused_before_any_write():
    ranked = [cand(1), cand(2), cand(3)]
    for conn, kw in ((Conn(slot=None), {}), (Conn(slot=(uuid.uuid4(), VT)), {}), (Conn(slot=(CLINIC, uuid.uuid4())), {}), (Conn(), {"waitlist_id": uuid.uuid4()}), (Conn(), {"waitlist_id": ranked[2].waitlist_id})):
        slots = FakeSlots(ranked)
        with pytest.raises(offers.NotACandidate):
            await send(conn, slots, **kw)  # the third ranked candidate is outside backfill_candidates = 2
        assert not any(c[0] == "hold" for c in slots.calls if isinstance(c, tuple)) and not any("insert" in s for s in conn.statements)


async def test_a_send_holds_for_the_ranked_candidate_then_records_the_template_verbatim_as_a_staff_message_by_the_caller():
    ranked = [cand(1), cand(2)]
    slots, conn, vault = FakeSlots(ranked), Conn(), FakeVault()
    sent = await offers.send_offer(conn, vault, settings(), key_version=3, clinic_id=CLINIC, origin="test", visit_type_id=VT, slot_id=SLOT, waitlist_id=ranked[1].waitlist_id, actor_id=ACTOR, idempotency_key="k-9", slots=slots)  # type: ignore[arg-type]
    assert sent.patient_id == ranked[1].patient_id and sent.thread_id == THREAD and sent.replayed is False
    assert slots.calls[1] == ("hold", 15, "offer-hold:k-9", ranked[1].patient_id)  # 900 s = 15 minutes, held for THAT patient
    assert vault.last[0] == TEMPLATE and vault.last[4] == 3 and vault.last[3] == sent.message_id  # the clinician's text, verbatim, nothing interpolated
    message = conn.params[-1]
    assert (message["u"], message["o"], message["k"]) == (ACTOR, "test", "offer-message:k-9") and "'staff'" in conn.statements[-1] and "'appointments'" in conn.statements[-1]
    written = [s.split("insert into ")[1].split(" ")[0] for s in conn.statements if "insert into" in s]
    assert written == ["msg.messages"]  # the thread existed; the only write here is the message (the hold is the SlotManager's)
    assert not any(w in " ".join(conn.statements).lower() for w in ("update ", "delete ", "sched.bookings", "agents."))


async def test_the_hold_length_rounds_up_to_whole_minutes():
    for seconds, minutes in ((1, 1), (60, 1), (61, 2), (900, 15)):
        slots = FakeSlots([cand(1)])
        await send(Conn(), slots, settings(hold_lifetime_seconds=seconds))
        assert slots.calls[1][1] == minutes


async def test_the_patients_appointments_thread_is_opened_by_the_caller_when_it_does_not_exist_and_a_lost_race_is_read_back():
    conn = Conn(thread=None)
    sent = await send(conn, FakeSlots([cand(1)]))
    opened = [i for i, s in enumerate(conn.statements) if "insert into msg.threads" in s]
    assert len(opened) == 1 and conn.params[opened[0]]["u"] == ACTOR and "'appointments'" in conn.statements[opened[0]] and sent.thread_id == THREAD
    raced = Conn(thread=None, thread_error=dup())
    raced.thread_error = dup()

    original = raced.execute

    async def execute(statement, params=None):
        if "insert into msg.threads" in str(statement):
            raced.thread = THREAD  # the other opener committed first
        return await original(statement, params)

    raced.execute = execute  # type: ignore[method-assign]
    assert (await send(raced, FakeSlots([cand(1)]))).thread_id == THREAD


async def test_a_replay_is_recognised_and_a_key_reused_for_a_different_offer_is_a_conflict():
    ranked = [cand(1)]
    replayed = await send(Conn(message_error=dup()), FakeSlots(ranked, replayed=True))
    assert replayed.replayed is True  # the same hold and the same message: nothing more is written
    with pytest.raises(offers.OfferConflict):
        await send(Conn(message_error=dup()), FakeSlots(ranked, replayed=False))  # a message under this key exists but the hold is new: a different offer
    other = DBAPIError("insert", {}, SimpleNamespace(sqlstate="42501"))
    with pytest.raises(DBAPIError):
        await send(Conn(message_error=other), FakeSlots(ranked))  # any other database error propagates, so the whole request rolls back


async def test_the_message_id_is_derived_from_the_key_so_a_replay_names_the_same_message():
    a = await send(Conn(), FakeSlots([cand(1)]), key="same")
    b = await send(Conn(), FakeSlots([cand(1)]), key="same")
    c = await send(Conn(), FakeSlots([cand(1)]), key="different")
    assert a.message_id == b.message_id != c.message_id
