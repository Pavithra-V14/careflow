"""The review-task transitions (careflow/review_tasks.py), offline: a scripted connection. Proves the state rules, that only the listed kinds close, the fail-closed mapping of a policy refusal, and
that the module writes nothing beyond the one update. The live checks (what row-level security shows and permits per role, over HTTP) are in the role matrix."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from careflow import review_tasks as rt

pytestmark = pytest.mark.asyncio
TASK, CLINIC, ME, OTHER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NOW = datetime(2030, 1, 1, tzinfo=UTC)


def detail(*, kind="referral_hold", status="open", by=None):
    # id, clinic_id, origin, kind, priority, assigned_role, status, created_at, acknowledged_by, acknowledged_at, resolved_at, source_message_id, provenance
    return (TASK, CLINIC, "test", kind, "standard", "front_desk", status, NOW, by, NOW if by else None, NOW if status == "resolved" else None, "src-1", {"document_id": "d"})


class Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows, self.rowcount = list(rows), rowcount

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class Conn:
    """`reads` is the sequence of task rows successive selects return (None = invisible); `changed` the rowcount of the update; `error` an sqlstate the update raises."""

    def __init__(self, reads, changed=1, error=None):
        self.reads, self.changed, self.error, self.statements = list(reads), changed, error, []

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
        if sql.lstrip().startswith("select"):
            row = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
            return Result([] if row is None else [row])
        if self.error:
            raise DBAPIError("update", {}, SimpleNamespace(sqlstate=self.error))
        return Result(rowcount=self.changed)

    def updates(self):
        return [s for s in self.statements if s.lstrip().startswith("update")]


async def test_an_invisible_task_is_not_found():
    for fn in (rt.acknowledge, rt.resolve):
        with pytest.raises(rt.TaskNotFound):
            await fn(Conn([None]), TASK, ME)  # type: ignore[arg-type]


async def test_acknowledge_takes_an_open_task_replays_for_the_holder_and_conflicts_for_anyone_else():
    done = await rt.acknowledge(Conn([detail()]), TASK, ME)  # type: ignore[arg-type]
    assert (done.status, done.replayed, done.kind, done.origin) == ("acknowledged", False, "referral_hold", "test")
    again = Conn([detail(status="acknowledged", by=ME)])
    assert (await rt.acknowledge(again, TASK, ME)).replayed is True  # type: ignore[arg-type]
    assert again.updates() == []  # a replay writes nothing
    for held in (detail(status="acknowledged", by=OTHER), detail(status="resolved", by=OTHER)):
        with pytest.raises(rt.TaskConflict):
            await rt.acknowledge(Conn([held]), TASK, ME)  # type: ignore[arg-type]


async def test_zero_rows_updated_is_a_role_refusal_unless_somebody_else_took_it_meanwhile():
    with pytest.raises(rt.NotPermitted):  # still open afterwards: the policy hid the row from the update
        await rt.acknowledge(Conn([detail(), detail()], changed=0), TASK, ME)  # type: ignore[arg-type]
    with pytest.raises(rt.TaskConflict):
        await rt.acknowledge(Conn([detail(), detail(status="acknowledged", by=OTHER)], changed=0), TASK, ME)  # type: ignore[arg-type]
    assert (await rt.acknowledge(Conn([detail(), detail(status="acknowledged", by=ME)], changed=0), TASK, ME)).replayed is True  # type: ignore[arg-type]
    with pytest.raises(rt.NotPermitted):
        await rt.acknowledge(Conn([detail()], error="42501"), TASK, ME)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", sorted(rt.RESOLVABLE_KINDS))
async def test_the_listed_kinds_resolve_open_or_own_acknowledged_tasks_and_a_resolved_one_is_a_replay(kind):
    for row in (detail(kind=kind), detail(kind=kind, status="acknowledged", by=ME)):
        done = await rt.resolve(Conn([row]), TASK, ME)  # type: ignore[arg-type]
        assert (done.status, done.replayed) == ("resolved", False)
    conn = Conn([detail(kind=kind, status="resolved", by=OTHER)])
    assert (await rt.resolve(conn, TASK, ME)).replayed is True  # type: ignore[arg-type]
    assert conn.updates() == []


@pytest.mark.parametrize("kind", ["red_flag", "triage_proposal", "message_screening", "anything_else"])
async def test_a_kind_that_is_not_listed_is_never_closed_here(kind):
    conn = Conn([detail(kind=kind)])
    with pytest.raises(rt.NotResolvableHere):
        await rt.resolve(conn, TASK, ME)  # type: ignore[arg-type]
    assert conn.updates() == []  # nothing is written, and an unlisted kind stays refused whatever its status


async def test_a_task_held_by_another_person_is_a_conflict_both_before_and_through_the_policy():
    with pytest.raises(rt.TaskConflict):
        await rt.resolve(Conn([detail(status="acknowledged", by=OTHER)]), TASK, ME)  # type: ignore[arg-type]
    with pytest.raises(rt.TaskConflict):  # the WITH CHECK (acknowledged_by is null or the caller) refused a race
        await rt.resolve(Conn([detail()], error="42501"), TASK, ME)  # type: ignore[arg-type]
    with pytest.raises(rt.NotPermitted):  # visible, open, not held: the USING clause hid it from this role
        await rt.resolve(Conn([detail(), detail()], changed=0), TASK, ME)  # type: ignore[arg-type]


async def test_the_only_write_is_one_update_of_the_four_granted_columns():
    conn = Conn([detail(status="acknowledged", by=ME)])
    await rt.resolve(conn, TASK, ME)  # type: ignore[arg-type]
    (sql,) = conn.updates()
    assert "set status = 'resolved'" in sql and "acknowledged_by = coalesce(acknowledged_by" in sql and "resolved_at = now()" in sql
    assert "delete" not in " ".join(conn.statements).lower() and "insert" not in " ".join(conn.statements).lower()
