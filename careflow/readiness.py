"""Readiness: the `clinical` profile refuses to report ready if a required role has no `phi_approved` candidate (BUILD-SPEC.md §2).

"Every external call and every model role declares a `data_policy`. The container refuses to report ready in `clinical` profile if a required
role has no `phi_approved` candidate. This is not a formality: it is the mechanism that stops free-tier infrastructure from quietly
handling real patient data."

This module is that rule and nothing else, as a pure function over the registry rows, so it can be tested exhaustively and so a deployment
can ask the question without a model call:

  * `demo` is ready: deterministic features need no model, and the demo profile is synthetic data by definition.
  * `clinical` is ready only if (a) the required roles are CONFIGURED, (b) the registry could be READ, and (c) every required role has at
    least one ENABLED `phi_approved` row (and, when the caller says which adapters are actually wired, one that has an adapter). An unknown
    profile name is treated as `clinical`; an unreadable registry, an unset required-roles list, an unknown role name: all are NOT ready.
    Unknown is not a value (CLAUDE.md rule 3): there is no path where missing information counts as ready.

Which roles are "required" is a deployment decision recorded in configuration (`CAREFLOW_REQUIRED_ROLES`), not a default in code: BUILD-SPEC §2
lists capabilities that DEGRADE visibly without an approved route (extraction, vision, translation...), and which of them a clinic treats as
launch-blocking is the owner's call. Until it is set, a `clinical` deployment reports not ready.

The result carries role NAMES and short reasons only (never a row, a price or an approver). The HTTP endpoint exposes only the boolean.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final

from careflow.gateway import ModelRow
from careflow.ports import ROLES

PROFILES: Final = ("demo", "clinical")


@dataclass(frozen=True)
class Readiness:
    ready: bool
    profile: str
    missing_roles: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def parse_required_roles(value: str | None) -> tuple[str, ...] | None:
    """`CAREFLOW_REQUIRED_ROLES` as a comma list. None (unset) stays None, which means "not configured", never "none required".
    An empty or blank value is also "not configured": an empty list would silently make the check vacuous."""
    if value is None or not value.strip():
        return None
    roles = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    return roles or None


def check_readiness(
    profile: str,
    registry: Sequence[ModelRow] | None,
    required_roles: Collection[str] | None,
    *,
    wired: Collection[tuple[str, str]] | None = None,
    malware_scanner_configured: bool = False,
) -> Readiness:
    """`registry` is None when it could not be read. `wired` (optional) is the set of (provider, model_id) that have an adapter.
    `malware_scanner_configured` is False unless a scanner is really wired (BUILD-SPEC.md 11.5): `clinical` is NOT ready without one, whatever the
    registry says. Uploads are separately refused outside `demo` by careflow.documents; this makes the same fact visible to the platform probe."""
    if profile == "demo":
        return Readiness(True, "demo")
    extra = () if profile == "clinical" else ("unrecognised profile treated as clinical",)
    result = _clinical(registry, required_roles, wired, extra=extra)
    if malware_scanner_configured:
        return result
    return Readiness(False, "clinical", result.missing_roles, (*result.reasons, "no malware scanner is configured for uploads"))


def _clinical(registry, required_roles, wired, extra: tuple[str, ...] = ()) -> Readiness:
    reasons = list(extra)
    if required_roles is None or len(required_roles) == 0:  # an empty list would make the check vacuous: it is NOT configured
        return Readiness(False, "clinical", (), (*reasons, "the required roles are not configured"))
    unknown = tuple(sorted(r for r in required_roles if r not in ROLES))
    if unknown:
        return Readiness(False, "clinical", unknown, (*reasons, "a required role is not a known role"))
    if registry is None:
        return Readiness(False, "clinical", tuple(required_roles), (*reasons, "the model registry could not be read"))
    missing = []
    for role in required_roles:
        ok = any(
            r.role == role and r.enabled and r.data_policy == "phi_approved" and (wired is None or r.key in wired)
            for r in registry
        )
        if not ok:
            missing.append(role)
    if missing:
        return Readiness(False, "clinical", tuple(missing), (*reasons, "a required role has no enabled phi_approved route"))
    return Readiness(True, "clinical", (), tuple(reasons))
