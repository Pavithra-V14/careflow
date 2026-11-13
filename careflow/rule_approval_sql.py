"""The two-person approval of red-flag rule versions (migration 0072; BUILD-SPEC.md 12.5, 20.2; CLAUDE.md rules 1, 2, 3, 5).

# CLINICAL-PLACEHOLDER: needs clinician sign-off

MECHANISM ONLY. Nothing here contains, proposes or edits rule content. A rule version arrives as a draft (from the migrator, today); two different doctors of its clinic, each with a verified second
factor, approve it through the database function `kb.decide_red_flag_rule`; only then does it count in the `clinical` profile. The function does the writing, the audit rows and every check; this module
only calls it and reads. The application role has no write grant on either table.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

RESULTS: Final = (
    "recorded", "approved", "rejected", "already_decided", "not_draft", "origin_mismatch", "author_cannot_decide", "empty_rule", "rejected_earlier", "platform_rule",
)


class Refused(Exception):
    """The database refused the caller (not a doctor of the clinic, no second factor, or no such version)."""


@dataclass(frozen=True)
class RuleVersion:
    rule_id: uuid.UUID
    rule_code: str
    version_id: int
    status: str
    origin: str
    phrases: list[str]
    patterns: list[str]
    has_vitals_criteria: bool
    approved_at: Any
    decisions: list[dict[str, Any]]


async def decide(conn: AsyncConnection, *, rule_id: uuid.UUID, decision: str, idempotency_key: str) -> str:
    """One of RESULTS. Raises Refused when the database refuses the caller. A replay of the same decision returns the same word."""
    try:
        async with conn.begin_nested():
            return str((await conn.execute(text("select kb.decide_red_flag_rule(:r, :d, :k)"), {"r": rule_id, "d": decision, "k": idempotency_key})).scalar())
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42501":
            raise Refused from None
        raise


async def list_versions(conn: AsyncConnection, clinic_id: uuid.UUID, *, limit: int) -> list[RuleVersion]:
    """The clinic's own rule versions, newest first, with the decisions made on each. Row-level security shows doctors and admins of the clinic only."""
    rules = (
        await conn.execute(
            text(
                "select id, rule_code, version_id, status, origin, phrases, patterns, vitals_criteria <> '{}'::jsonb as has_vitals, approved_at from kb.red_flag_rules "
                "where clinic_id = :c order by rule_code, version_id desc limit :n"
            ),
            {"c": clinic_id, "n": limit},
        )
    ).all()
    if not rules:
        return []
    decided = (
        await conn.execute(
            text("select rule_id, decision, decided_by, created_at from kb.red_flag_rule_decisions where rule_id = any(cast(:ids as uuid[])) order by created_at, id"),
            {"ids": [r.id for r in rules]},
        )
    ).all()
    by_rule: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for d in decided:
        by_rule.setdefault(d.rule_id, []).append({"decision": d.decision, "decided_by": d.decided_by, "decided_at": d.created_at})
    return [
        RuleVersion(r.id, r.rule_code, r.version_id, r.status, r.origin, list(r.phrases), list(r.patterns), bool(r.has_vitals), r.approved_at, by_rule.get(r.id, []))
        for r in rules
    ]


async def rule_clinic(conn: AsyncConnection, rule_id: uuid.UUID) -> tuple[bool, uuid.UUID | None]:
    """(visible, clinic_id) for one version; a version the caller cannot see is `(False, None)`; a platform-wide one has clinic_id None."""
    row = (await conn.execute(text("select clinic_id from kb.red_flag_rules where id = :r"), {"r": rule_id})).first()
    return (False, None) if row is None else (True, row.clinic_id)
