"""Audit rows for human decisions (BUILD-SPEC.md 17.1 `audit`, 18.4; pre-launch item 5). Writes `audit.log` from the CALLING person's own session, in the SAME transaction as the decision.

* No migration and no new grant: `careflow_app` already holds INSERT on `audit.log`, and the policy `log_insert_as_self` (migration 0001) requires `actor_id` to be the signed-in account, so nobody
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
                    "values (:o, :c, :a, :r, :act, :rt, :ri, cast(:d as jsonb), :k)"
                ),
                {"o": origin, "c": clinic_id, "a": actor_id, "r": actor_role, "act": action, "rt": resource_type, "ri": str(resource_id), "d": json.dumps(clean), "k": idempotency_key},
            )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":  # the key already exists: a replay
            return False
        raise
    return result.rowcount == 1
