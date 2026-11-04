"""The ACTIVE ROLE: one optional request header that can only NARROW what a caller may do (docs/design/active-role.md; owner decision 2026-10-31).

    X-CareFlow-Active-Role: <clinic_id>:<role>

PURE code, no database and no network. The request flow (careflow/app.py `db_conn`):
  1. the token is verified (identity only; a token carries no role and no clinic);
  2. the header is parsed STRICTLY here, before any database work and before the step-up check;
  3. in the request's transaction ONE statement sets the claims (identity + the server's own `careflow_active`) and asks `iam.active_grant_ok` whether the caller holds that grant LIVE: any "no" is the one fixed
     403 below, before the route body runs, so nothing was written;
  4. the two RLS helpers (`iam.has_clinic_role`, `iam.has_any_role`, migration 0028) then count only that grant. No header = exactly today's behaviour (every live grant counts).

Everything that goes wrong collapses to ONE response with no reason and no echo of the header. Any `careflow_active` found in a token is DROPPED and replaced by the server's own: it could only narrow anyway.
`GET /v1/iam/me` ignores the header on purpose (the console needs every grant to offer its picker).
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

HEADER: Final = "X-CareFlow-Active-Role"
CLAIM: Final = "careflow_active"
REFUSAL_STATUS: Final = 403
REFUSAL_BODY: Final = {"detail": "active role not available"}
# The roles a grant can carry (the grants table role check, migrations 0001 and 0006). Anything else is refused before the database is asked.
ROLES: Final = frozenset({"front_desk", "nurse", "doctor", "admin", "business_owner", "auditor", "queue_worker"})
MAX_HEADER_CHARS: Final = 64  # a canonical uuid (36) + ":" + the longest role (14) is 51
_SHAPE = re.compile(r"\A([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([a-z_]+)\Z")


class ActiveRoleRefused(Exception):
    """The header is malformed, repeated, or names a role this system does not have. The caller turns it into the one fixed 403. Carries a reason CODE for the log, never the header value."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ActiveRole:
    clinic_id: uuid.UUID
    role: str


def parse(values: Sequence[str]) -> ActiveRole | None:
    """`values` is every occurrence of the header. None when the header is absent; ActiveRole when it is well formed; otherwise ActiveRoleRefused (a repeated header is refused, an empty one is refused)."""
    if not values:
        return None
    if len(values) != 1:
        raise ActiveRoleRefused("duplicate_header")
    raw = values[0]
    if not raw or len(raw) > MAX_HEADER_CHARS:
        raise ActiveRoleRefused("empty_or_too_long")
    match = _SHAPE.match(raw)
    if match is None:
        raise ActiveRoleRefused("malformed")
    try:
        clinic = uuid.UUID(match.group(1))
    except ValueError:
        raise ActiveRoleRefused("malformed") from None
    if str(clinic) != match.group(1):  # canonical lower-case form only
        raise ActiveRoleRefused("malformed")
    role = match.group(2)
    if role not in ROLES:
        raise ActiveRoleRefused("unknown_role")
    return ActiveRole(clinic, role)


def claims_with_active(claims: Mapping[str, Any], active: ActiveRole | None) -> dict[str, Any]:
    """The claims JSON for `set_config`: the verified identity claims with ANY incoming `careflow_active` removed, plus the server's own when a header was accepted for checking."""
    out = {k: v for k, v in claims.items() if k != CLAIM}
    if active is not None:
        out[CLAIM] = {"clinic_id": str(active.clinic_id), "role": active.role}
    return out
