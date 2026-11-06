"""Migrations 0039 (ops.alerts, review reason codes) and 0040 (agents.reviewer_stats) against the real database, as the restricted `careflow_app` role with verified claims, inside one never-committed
transaction (BUILD-SPEC.md 12.3, 12.4, 13.4; CLAUDE.md rules 5, 8). The routes over these are in the role matrix (tests/test_api_role_matrix.py)."""

import json

import asyncpg
import pytest

from tests.test_rls_triage_live import (  # noqa: F401  (the fixture and the world are reused so this module seeds the same kind of clinic)
    MIGRATOR,
    _u,
    db,
    seed_proposal,
    world,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
ALERT = (
    "insert into ops.alerts (clinic_id, origin, class, audience_role, alert_count, ref, idempotency_key) values "
    "('{clinic}', '{origin}', '{cls}', '{aud}', {n}, {ref}, '{key}') on conflict (clinic_id, idempotency_key) do nothing"
)


async def seq(conn, uid, *statements):
    """Run the statements in order as `careflow_app` with the account's claims, all in one savepoint that is rolled back. Each statement is (sql, fetch?). Returns the results, or the SQLSTATE of the first failure."""
    await conn.execute("savepoint seq")
    out = []
    try:
        await conn.execute("set local role careflow_app")
        await conn.execute("select set_config('request.jwt.claims', $1, true)", json.dumps({"sub": str(uid), "role": "authenticated"}))
        for sql, fetch in statements:
            out.append(await conn.fetch(sql) if fetch else await conn.execute(sql))
        return out
    except asyncpg.PostgresError as exc:
        return exc.sqlstate
    finally:
        await conn.execute("rollback to savepoint seq")


def alert(w, *, clinic=None, origin="test", cls="dead_letters", aud="admin", n=1, ref="null", key=None):
    return ALERT.format(clinic=clinic or w.a, origin=origin, cls=cls, aud=aud, n=n, ref=ref, key=key or f"k-{_u()}")


async def canary_decision(db, w, decided_by=None):  # noqa: F811
    """A canary-origin proposal (with its own canary patient and task, as the foreign keys pin the origin) approved by a nurse. Returns the decision id."""
    patient, task = _u(), _u()
    await db.execute(
        "insert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) values ($1, $2, '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'canary', $3)",
        patient, w.a, f"cp-{patient}",
    )
    await db.execute(
        "insert into agents.review_tasks (id, clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) values ($1, $2, 'canary', 'triage_proposal', 'standard', 'nurse', 'document:c', '{}'::jsonb, $3)",
        task, w.a, f"ct-{task}",
    )
    proposal = await seed_proposal(db, w, doc=w.doc_a2, patient=patient, task=task, origin="'canary'", key=f"cpk-{_u()}")
    decision = _u()
    await db.execute(
        "insert into agents.review_decisions (id, clinic_id, origin, proposal_id, decision, decided_by, idempotency_key) values ($1, $2, 'canary', $3, 'approve', $4, $5)",
        decision, w.a, proposal, decided_by or w.people["nurse_a"], f"cd-{decision}",
    )
    return decision


def canary_alert(w, decision, **kw):
    return alert(w, origin="canary", cls="canary_approved", aud="admin", ref=f"'{decision}'", key=f"canary-approved:{decision}", **kw)


async def test_a_reviewer_can_raise_only_the_silent_canary_alert_for_their_own_canary_approval_and_cannot_read_it_back(db):  # noqa: F811
    w = await world(db)
    decision = await canary_decision(db, w)
    raised = await seq(db, w.people["nurse_a"], (canary_alert(w, decision), False), ("select id from ops.alerts", True))
    assert isinstance(raised, list) and raised[1] == []  # accepted, and the reviewer sees no alert at all
    await db.execute(canary_alert(w, decision))  # (the owner writes the same row so the admin's view can be read next)
    seen = await seq(db, w.people["admin_a"], ("select class, audience_role, ref from ops.alerts", True))
    assert [tuple(r) for r in seen[0]] == [("canary_approved", "admin", str(decision))]
    for who in ("nurse_a", "doctor_a", "front_desk_a", "auditor_a", "owner_a", "worker_a", "nurse_b"):
        again = await seq(db, w.people[who], ("select id from ops.alerts", True))
        assert again == [[]], who


async def test_a_reviewer_cannot_raise_any_other_alert_nor_one_for_a_decision_that_is_not_theirs(db):  # noqa: F811
    w = await world(db)
    decision = await canary_decision(db, w)
    refused = [
        (w.people["doctor_a"], canary_alert(w, decision)),  # not the account that decided
        (w.people["nurse_b"], canary_alert(w, decision)),  # another clinic
        (w.people["nurse_a"], alert(w, cls="dead_letters")),  # any other class
        (w.people["nurse_a"], canary_alert(w, decision, n=5)),  # the count is 1
        (w.people["nurse_a"], alert(w, origin="canary", cls="canary_approved", ref=f"'{decision}'", key="some-other-key")),  # the key is bound to the decision
        (w.people["nurse_a"], alert(w, origin="canary", cls="canary_approved", ref=f"'{_u()}'", key="canary-approved:x")),  # no such decision
        (w.people["front_desk_a"], canary_alert(w, decision)),
        (w.people["admin_a"], canary_alert(w, decision)),
    ]
    for uid, sql in refused:
        assert await seq(db, uid, (sql, False)) == "42501", (uid, sql[:80])


async def test_the_worker_raises_alerts_each_role_reads_only_its_own_and_a_repeat_is_a_no_op(db):  # noqa: F811
    w = await world(db)
    key = f"k-{_u()}"
    assert isinstance(await seq(db, w.people["worker_a"], (alert(w, aud="admin", key=key), False), (alert(w, aud="admin", key=key), False)), list)
    await db.execute(alert(w, aud="admin", key=key))
    await db.execute(alert(w, aud="nurse", cls="red_flag_overdue", key=f"k-{_u()}"))
    count = lambda who: seq(db, w.people[who], (f"select count(*) from ops.alerts where clinic_id = '{w.a}'", True))
    assert (await count("admin_a"))[0][0][0] == 1 and (await count("nurse_a"))[0][0][0] == 1
    for who in ("doctor_a", "front_desk_a", "auditor_a", "owner_a", "worker_a", "nurse_b", "front_desk_b"):
        assert (await count(who))[0][0][0] == 0, who
    assert await seq(db, w.people["worker_b"], (alert(w, aud="admin"), False)) == "42501"  # a worker of another clinic
    assert await seq(db, w.people["nurse_a"], (alert(w, cls="red_flag_overdue", aud="nurse"), False)) == "42501"  # a reviewer is not the watchdog


async def test_an_alert_is_acknowledged_once_by_its_own_audience_with_the_database_clock_and_nothing_else_changes(db):  # noqa: F811
    w = await world(db)
    await db.execute(alert(w, aud="admin", key="k-ack"))
    ack = "update ops.alerts set acknowledged_by = auth.uid(), acknowledged_at = '2001-01-01' where idempotency_key = 'k-ack'"
    assert await seq(db, w.people["nurse_a"], (ack, False)) == ["UPDATE 0"]  # a role the alert is not aimed at cannot see the row, so it updates nothing
    done = await seq(db, w.people["admin_a"], (ack, False), ("select acknowledged_at > '2020-01-01' from ops.alerts where idempotency_key = 'k-ack'", True))
    assert isinstance(done, list) and done[0] == "UPDATE 1" and done[1][0][0] is True  # the caller's time is replaced by the database's
    await db.execute("update ops.alerts set acknowledged_by = $1, acknowledged_at = now() where idempotency_key = 'k-ack'", w.people["admin_a"])
    assert await seq(db, w.people["admin_a"], (ack, False)) == ["UPDATE 0"]  # acknowledged is final
    for change in ("class = 'queue_age'", "ref = 'x'", "audience_role = 'nurse'", "alert_count = 9", "idempotency_key = 'other'"):
        assert await seq(db, w.people["admin_a"], (f"update ops.alerts set {change} where idempotency_key = 'k-ack'", False)) == "42501", change
    assert await seq(db, w.people["admin_a"], ("delete from ops.alerts", False)) == "42501"
    assert await seq(db, w.people["admin_a"], ("truncate ops.alerts", False)) == "42501"


async def test_the_owner_cannot_delete_or_reopen_an_alert_either(db):  # noqa: F811
    w = await world(db)
    await db.execute(alert(w, aud="admin", key="k-final"))
    await db.execute("update ops.alerts set acknowledged_by = $1, acknowledged_at = now() where idempotency_key = 'k-final'", w.people["admin_a"])
    for sql in ("delete from ops.alerts where idempotency_key = 'k-final'", "update ops.alerts set acknowledged_at = null, acknowledged_by = null where idempotency_key = 'k-final'", "truncate ops.alerts"):
        await db.execute("savepoint s")
        with pytest.raises(asyncpg.PostgresError):
            await db.execute(sql)
        await db.execute("rollback to savepoint s")


async def test_the_canary_class_must_be_aimed_at_admin_and_carry_a_reference(db):  # noqa: F811
    w = await world(db)
    for bad in (alert(w, cls="canary_approved", aud="nurse", ref="'x'"), alert(w, cls="canary_approved", aud="admin", ref="null")):
        await db.execute("savepoint s")
        with pytest.raises(asyncpg.CheckViolationError):
            await db.execute(bad)
        await db.execute("rollback to savepoint s")


async def test_a_review_decision_may_carry_a_reason_code_of_the_right_shape_and_the_table_stays_append_only(db):  # noqa: F811
    w = await world(db)
    proposal = await seed_proposal(db, w, doc=w.doc_a3, key=f"rk-{_u()}")
    insert = (
        "insert into agents.review_decisions (clinic_id, origin, proposal_id, decision, decided_by, idempotency_key, reason_code) values "
        f"('{w.a}', 'test', '{proposal}', 'reject', '{w.people['nurse_a']}', 'k-{_u()}', {{code}})"
    )
    assert await seq(db, w.people["nurse_a"], (insert.format(code="'looks_wrong'"), False)) == ["INSERT 0 1"]
    for code in ("'Bad Code'", "'9starts_with_digit'", f"'{'x' * 61}'"):
        assert await seq(db, w.people["nurse_a"], (insert.format(code=code), False)) == "23514", code
    await db.execute(insert.format(code="'looks_wrong'"))
    assert await seq(db, w.people["nurse_a"], ("update agents.review_decisions set reason_code = 'changed'", False)) == "42501"


async def test_reviewer_statistics_are_for_the_admin_of_the_clinic_only_aggregated_and_origin_separated(db):  # noqa: F811
    w = await world(db)
    live = await seed_proposal(db, w, doc=w.doc_a3, key=f"sk-{_u()}")
    await db.execute(
        "insert into agents.review_decisions (clinic_id, origin, proposal_id, decision, decided_by, idempotency_key) values ($1, 'test', $2, 'approve', $3, $4)", w.a, live, w.people["nurse_a"], f"sd-{_u()}"
    )
    canary = await canary_decision(db, w)
    assert canary
    stats = "select * from agents.reviewer_stats('{c}', now() - interval '30 days')"
    # the 'test'-origin decision above is excluded by design; the canary one is counted in its own columns
    rows = await seq(db, w.people["admin_a"], (stats.format(c=w.a), True))
    assert isinstance(rows, list)
    only = rows[0]
    assert len(only) == 1 and only[0]["reviewer"] == w.people["nurse_a"] and only[0]["decisions"] == 0 and only[0]["canaries_seen"] == 1 and only[0]["canaries_approved"] == 1 and only[0]["median_seconds"] is None
    for who in ("nurse_a", "doctor_a", "front_desk_a", "owner_a", "auditor_a", "worker_a", "nurse_b"):
        assert await seq(db, w.people[who], (stats.format(c=w.a), True)) == "42501", who
    assert await seq(db, w.people["admin_a"], (stats.format(c=w.b), True)) == "42501"  # another clinic
    assert await seq(db, w.people["admin_a"], ("select * from agents.reviewer_stats(null, now())", True)) == "42501"
    assert await seq(db, w.people["admin_a"], (f"select * from agents.reviewer_stats('{w.a}', now() - interval '500 days')", True)) == "42501"  # the window is bounded
