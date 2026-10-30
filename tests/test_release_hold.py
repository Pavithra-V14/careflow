"""SlotManager.release_hold, offline with a scripted connection: an active hold is released and its slot reopened; a released one is a replay in effect that writes nothing; a booked or expired one
is a conflict (never silently reopened); a hold the caller cannot see is not found; an update that row-level security hides is NotPermitted."""

import uuid

import pytest

from careflow.scheduling import (
    HoldNotFound,
    InvalidTransition,
    NotPermitted,
    SlotManager,
)

pytestmark = pytest.mark.asyncio
HOLD, SLOT = uuid.uuid4(), uuid.uuid4()


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Result:
    def __init__(self, rows=(), rowcount=1):
        self._rows, self.rowcount = list(rows), rowcount

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0][0]


class Conn:
    def __init__(self, status="active", exists=True, changed=1):
        self.status, self.exists, self.changed, self.statements = status, exists, changed, []

    def begin_nested(self):
        class Savepoint:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return Savepoint()

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if sql.startswith("select slot_id from sched.holds"):
            return Result([Row(slot_id=SLOT)] if self.exists else [])
        if "for update" in sql:
            return Result([Row(id=SLOT, clinic_id=uuid.uuid4(), status="held", visit_type_id=uuid.uuid4())])
        if sql.startswith("select status from sched.holds"):
            return Result([Row(status=self.status)] if self.exists else [])
        if sql.startswith("select id, patient_id from sched.holds"):
            return Result([(HOLD, None)] if self.status == "active" else [])
        if sql.startswith("select status from sched.slots"):
            return Result([("held",) if self.status == "active" else ("open",)])  # a slot is held only while a live hold exists
        if sql.startswith("update sched.holds set status = 'released'"):
            return Result(rowcount=self.changed)
        return Result()

    def writes(self):
        return [s for s in self.statements if s.startswith("update") and "set status = 'expired'" not in s]


async def test_an_active_hold_is_released_and_its_slot_reopens_only_if_no_other_live_hold_remains():
    conn = Conn("active")
    done = await SlotManager().release_hold(conn, HOLD)  # type: ignore[arg-type]
    assert (done.hold_id, done.slot_id, done.replayed) == (HOLD, SLOT, False)
    released, reopened = conn.writes()
    assert "where id = :h and status = 'active'" in released
    assert "set status = 'open'" in reopened and "not exists (select 1 from sched.holds h where h.slot_id = s.id and h.status = 'active')" in reopened


async def test_a_released_hold_is_a_replay_in_effect_and_writes_nothing():
    conn = Conn("released")
    assert (await SlotManager().release_hold(conn, HOLD)).replayed is True  # type: ignore[arg-type]
    assert not [s for s in conn.writes() if "set status = 'released'" in s or "set status = 'open'" in s]


@pytest.mark.parametrize("status", ["converted", "expired"])
async def test_a_booked_or_expired_hold_is_a_conflict_and_nothing_is_reopened(status):
    conn = Conn(status)
    with pytest.raises(InvalidTransition, match=status):
        await SlotManager().release_hold(conn, HOLD)  # type: ignore[arg-type]
    assert not [s for s in conn.writes() if "set status = 'open'" in s]


async def test_a_hold_the_caller_cannot_see_is_not_found_and_a_hidden_update_is_not_permitted():
    with pytest.raises(HoldNotFound):
        await SlotManager().release_hold(Conn(exists=False), HOLD)  # type: ignore[arg-type]
    with pytest.raises(NotPermitted):
        await SlotManager().release_hold(Conn("active", changed=0), HOLD)  # type: ignore[arg-type]
