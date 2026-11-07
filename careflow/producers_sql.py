"""The SQL halves of migration 0048, on the CALLER's own transaction under row-level security: the autonomy history, the care-gap producers (episode, rule, last-done), the review-sample writer and recheck, and
the worker's overdue-rule read. All inserts are PLAIN inserts in a savepoint with the unique violation caught (never ON CONFLICT: PostgreSQL also checks the SELECT policy for that, see careflow/phase4_sql.py).
The application role cannot call auth.uid() by name, so every caller id comes from the verified claims; the policies still check it equals auth.uid().
"""

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.autonomy import Mode, effective_mode
from careflow.care_gap_rules import RuleDef
from careflow.care_gap_sql import sqlstate


class Refused(Exception):
    """A foreign key or a guard refused the write (no such patient or rule in this clinic, a date that cannot move that way). Fixed words."""


async def _insert(conn: AsyncConnection, sql: str, params: dict, existing_sql: str, existing_params: dict) -> tuple[uuid.UUID, bool]:
    """(id, created). A repeat of the key (23505) returns the first row; a foreign-key or guard refusal raises Refused."""
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text(sql + " returning id"), params)).one()
        return row.id, True
    except IntegrityError as exc:
        state = sqlstate(exc)
        if state in ("23503", "23001"):
            raise Refused from None
        if state != "23505":
            raise
    existing = (await conn.execute(text(existing_sql), existing_params)).first()
    if existing is None:
        raise Refused  # the key or code is held by a row this caller cannot see
    return existing.id, False


# ---- autonomy history ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


async def read_mode(conn: AsyncConnection, clinic_id: uuid.UUID) -> tuple[Mode, bool]:
    """(mode, configured). No row means `shadow`, not configured: unknown is the most human mode."""
    row = (await conn.execute(text("select mode from ops.autonomy_changes where clinic_id = :c order by changed_at desc, id desc limit 1"), {"c": clinic_id})).first()
    return (effective_mode(row[0]), True) if row is not None else ("shadow", False)


async def set_mode(conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, mode: str, changed_by: uuid.UUID, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    return await _insert(
        conn,
        "insert into ops.autonomy_changes (clinic_id, origin, mode, changed_by, idempotency_key) values (:c, :o, :m, :b, :k)",
        {"c": clinic_id, "o": origin, "m": mode, "b": changed_by, "k": idempotency_key},
        "select id from ops.autonomy_changes where clinic_id = :c and idempotency_key = :k",
        {"c": clinic_id, "k": idempotency_key},
    )


# ---- care-gap producers -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


async def ensure_rule(conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, rule: RuleDef) -> uuid.UUID:
    """The rule row for a setting-defined rule: inserted the first time, found afterwards. A row whose definition no longer matches the setting is REFUSED, never silently used (the setting and the data
    would disagree about when a patient is overdue)."""
    rule_id, _ = await _insert(
        conn,
        "insert into ops.care_recurrence_rules (clinic_id, origin, rule_code, interval_days, annual_month, overdue_after_days, idempotency_key) values (:c, :o, :code, :i, :m, :d, :k)",
        {"c": clinic_id, "o": origin, "code": rule.code, "i": rule.interval_days, "m": rule.annual_month, "d": rule.overdue_after_days, "k": f"care-rule:{rule.code}"},
        "select id from ops.care_recurrence_rules where clinic_id = :c and rule_code = :code",
        {"c": clinic_id, "code": rule.code},
    )
    row = (await conn.execute(text("select interval_days, annual_month, overdue_after_days from ops.care_recurrence_rules where id = :i"), {"i": rule_id})).one()
    if (row.interval_days, row.annual_month, row.overdue_after_days) != (rule.interval_days, rule.annual_month, rule.overdue_after_days):
        raise Refused
    return rule_id


async def create_episode(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID, episode_code: str, started_on: date, rules: Sequence[RuleDef], idempotency_key: str
) -> tuple[uuid.UUID, bool, list[uuid.UUID]]:
    """(episode id, created, the episode-rule ids). The rules are ensured and linked; on a replay the same ids come back and nothing more is written."""
    episode_id, created = await _insert(
        conn,
        "insert into ops.care_episodes (clinic_id, origin, patient_id, episode_code, started_on, idempotency_key) values (:c, :o, :p, :e, :s, :k)",
        {"c": clinic_id, "o": origin, "p": patient_id, "e": episode_code, "s": started_on, "k": idempotency_key},
        "select id from ops.care_episodes where clinic_id = :c and idempotency_key = :k and patient_id = :p",
        {"c": clinic_id, "k": idempotency_key, "p": patient_id},
    )
    links: list[uuid.UUID] = []
    for rule in rules:
        rule_id = await ensure_rule(conn, clinic_id=clinic_id, origin=origin, rule=rule)
        link_id, _ = await _insert(
            conn,
            "insert into ops.care_episode_rules (clinic_id, origin, episode_id, rule_id, idempotency_key) values (:c, :o, :e, :r, :k)",
            {"c": clinic_id, "o": origin, "e": episode_id, "r": rule_id, "k": f"{idempotency_key}:{rule.code}"},
            "select id from ops.care_episode_rules where clinic_id = :c and episode_id = :e and rule_id = :r",
            {"c": clinic_id, "e": episode_id, "r": rule_id},
        )
        links.append(link_id)
    return episode_id, created, links


async def record_done(conn: AsyncConnection, *, episode_rule_id: uuid.UUID, done_on: date) -> str:
    """'recorded', 'not_found' (invisible, or not yours to change) or 'refused' (backwards or in the future: the database guard)."""
    try:
        async with conn.begin_nested():
            n = (await conn.execute(text("update ops.care_episode_rules set last_done_on = :d where id = :i"), {"d": done_on, "i": episode_rule_id})).rowcount
    except IntegrityError as exc:
        if sqlstate(exc) == "23001":
            return "refused"
        raise
    return "recorded" if n == 1 else "not_found"


async def recheck_sample(conn: AsyncConnection, *, sample_id: uuid.UUID, by: uuid.UUID) -> str:
    """'rechecked', 'same_person' (you made the decision) or 'not_found' (invisible, already re-checked, or not yours to change)."""
    try:
        async with conn.begin_nested():
            n = (await conn.execute(text("update ops.care_review_samples set rechecked_by = :b where id = :i"), {"b": by, "i": sample_id})).rowcount
    except IntegrityError as exc:
        if sqlstate(exc) == "23001":
            return "same_person"  # the database refuses a re-check by the person who made the decision
        raise
    return "rechecked" if n == 1 else "not_found"


# ---- the worker side ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


async def worker_overdue_rows(conn: AsyncConnection, clinic_id: uuid.UUID) -> list:
    """The narrow read: only what the overdue check needs, no patient and no code word."""
    return list((await conn.execute(text("select * from ops.care_gap_rules_for_worker(:c)"), {"c": clinic_id})).all())


async def record_sample(conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, decision_id: uuid.UUID) -> bool:
    """True when a new sample was written; False when this decision was already sampled."""
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("insert into ops.care_review_samples (clinic_id, origin, decision_id, idempotency_key) values (:c, :o, :d, :k)"),
                {"c": clinic_id, "o": origin, "d": decision_id, "k": f"sample:{decision_id}"},
            )
        return True
    except IntegrityError as exc:
        if sqlstate(exc) == "23505":
            return False
        raise



# ---- the two-person switch to autonomous (migration 0053) ------------------------------------------------------------------------------------------------------------------------------------------------------------


async def propose_autonomous(conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, proposed_by: uuid.UUID, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    return await _insert(
        conn,
        "insert into ops.autonomy_proposals (clinic_id, origin, proposed_by, idempotency_key) values (:c, :o, :p, :k)",
        {"c": clinic_id, "o": origin, "p": proposed_by, "k": idempotency_key},
        "select id from ops.autonomy_proposals where clinic_id = :c and idempotency_key = :k",
        {"c": clinic_id, "k": idempotency_key},
    )


async def pending_proposals(conn: AsyncConnection, clinic_id: uuid.UUID) -> list:
    return list((await conn.execute(text("select id, proposed_by, created_at from ops.autonomy_proposals where clinic_id = :c and status = 'pending' order by created_at"), {"c": clinic_id})).all())


async def resolve_proposal(conn: AsyncConnection, *, proposal_id: uuid.UUID, decision: str) -> str:
    """'approved', 'rejected', 'same_person', 'expired' or 'not_pending'. Raises Refused when the database refuses the caller (not an admin of the clinic, no second factor)."""
    try:
        async with conn.begin_nested():
            return str((await conn.execute(text("select ops.resolve_autonomy_proposal(:i, :d)"), {"i": proposal_id, "d": decision})).scalar())
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused from None
        raise
