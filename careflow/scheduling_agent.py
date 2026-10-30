"""The Scheduling agent, MECHANISM ONLY (Phase 3; docs/design/phase3-scheduling-agent.md, owner-signed-off 2026-10-28).

WHAT EXISTS: an in-process MCP `scheduling` server with READ-ONLY tools that the front desk's screen calls under the requesting person's own session (`careflow.mcp_kit.session_context`):
`find_slots`, `rank_waitlist`, and `propose_offer` (ranked candidates plus a DRAFT, computed on demand and stored nowhere). It writes nothing: a person presses send, and sending (a hold plus
an `appointments` message, one transaction) is a later change.

WHAT DOES NOT EXIST, ON PURPOSE: every clinician-owned value. Offer expiry, hold lifetime, how many candidates to try, the confidence at which an accept may be SUGGESTED, and the message
template are REQUIRED settings with NO default and NO value in this repository (CLAUDE.md rule 1). Until they are set, `propose_offer` answers `unavailable` naming the missing settings; the
ranking itself (`rank_waitlist`) needs only the clinic's own priority levels, which a doctor authors (`sched.priority_levels`, empty by design) and answers `empty` without them: unknown is not
a value, and there is no default ranking. No model is called anywhere (the reply interpretation, which uses the `decision` tier, is a later change and its real engine is blocked on a durable spend
ledger). No grant, role or policy is added: the tools read through `SlotManager` under the caller's row-level security.
"""

import uuid
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from careflow.mcp_kit import Envelope, async_tool, current_session
from careflow.scheduling import SlotManager

MAX_LIMIT = 50  # a technical bound on one read
SETTING_NAMES = ("offer_expiry_seconds", "hold_lifetime_seconds", "backfill_candidates", "accept_confidence_threshold", "offer_message_template")


class SchedulingNotConfigured(Exception):
    """One or more clinician-owned settings are missing. Carries the SETTING NAMES only, never values."""

    def __init__(self, names: list[str]) -> None:
        super().__init__("scheduling settings are not configured: " + ", ".join(names))
        self.names = tuple(names)


def _alias(name: str) -> AliasChoices:
    return AliasChoices(f"CAREFLOW_SCHED_{name.upper()}", name)


class SchedulingSettings(BaseSettings):
    """Every field below is a CLINICIAN-OWNED value: `# CLINICAL-PLACEHOLDER: needs clinician sign-off`. None means NOT CONFIGURED (there is no default). They are listed for Priya in
    docs/design/clinician-sheet.md (items 17-20)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # CLINICAL-PLACEHOLDER: needs clinician sign-off  (item 18: how long an offer stays open)
    offer_expiry_seconds: int | None = Field(default=None, gt=0, validation_alias=_alias("offer_expiry_seconds"))
    # CLINICAL-PLACEHOLDER: needs clinician sign-off  (item 18: how long a slot is held for an offer)
    hold_lifetime_seconds: int | None = Field(default=None, gt=0, validation_alias=_alias("hold_lifetime_seconds"))
    # CLINICAL-PLACEHOLDER: needs clinician sign-off  (item 19: how many next-ranked patients are drafted for before a person is told "no one could be offered")
    backfill_candidates: int | None = Field(default=None, gt=0, le=MAX_LIMIT, validation_alias=_alias("backfill_candidates"))
    # CLINICAL-PLACEHOLDER: needs clinician sign-off  (item 17: the minimum confidence at which an accept may be SUGGESTED to the front desk)
    accept_confidence_threshold: float | None = Field(default=None, gt=0, le=1, validation_alias=_alias("accept_confidence_threshold"))
    # CLINICAL-PLACEHOLDER: needs clinician sign-off  (item 20: the offer wording; a template is clinician-approved text, never written by an engineer)
    offer_message_template: str | None = Field(default=None, min_length=1, max_length=2000, validation_alias=_alias("offer_message_template"))

    def missing(self, *names: str) -> list[str]:
        wanted = names or SETTING_NAMES
        return [n for n in wanted if getattr(self, n) is None]

    def require(self, *names: str) -> None:
        missing = self.missing(*names)
        if missing:
            raise SchedulingNotConfigured(missing)


def _slot_row(r: Any) -> dict[str, Any]:
    return {
        "slot_id": str(r.id), "provider_id": str(r.provider_id), "location_id": str(r.location_id), "visit_type_id": str(r.visit_type_id),
        "starts_at": r.starts_at.isoformat() if isinstance(r.starts_at, datetime) else str(r.starts_at), "status": r.status,
    }


def _candidate(c: Any) -> dict[str, Any]:
    return {"waitlist_id": str(c.waitlist_id), "patient_id": str(c.patient_id), "priority_code": c.priority_code, "rank": c.rank, "joined_at": c.joined_at.isoformat()}


def build_scheduling_server(settings: SchedulingSettings, slots: SlotManager | None = None) -> MCPServer:
    """The in-process `scheduling` server. Every tool is `async def` (checked at registration), reads the caller's session from `current_session()` and returns an `Envelope`."""
    manager = slots or SlotManager()
    server = MCPServer("scheduling")

    @async_tool(server)
    async def find_slots(clinic_id: uuid.UUID, limit: int = 20) -> Envelope:
        """READ-ONLY. Open and held slots of one clinic, soonest first (row-level security decides what the caller may see)."""
        session = current_session()
        rows = await manager.list_slots(session.conn, [clinic_id], limit=max(1, min(limit, MAX_LIMIT)))
        return Envelope.ok([_slot_row(r) for r in rows]) if rows else Envelope.empty("no_open_slots")

    @async_tool(server)
    async def rank_waitlist(clinic_id: uuid.UUID, visit_type_id: uuid.UUID) -> Envelope:
        """READ-ONLY. The waiting patients for a visit type in clinical-priority order (the clinic's own priority levels, then wait time). Empty if nobody waits or the clinic has defined no
        priority levels: there is no default ranking."""
        session = current_session()
        ranked = await manager.rank_waitlist(session.conn, clinic_id, visit_type_id)
        return Envelope.ok([_candidate(c) for c in ranked]) if ranked else Envelope.empty("no_ranked_candidates")

    @async_tool(server)
    async def propose_offer(clinic_id: uuid.UUID, visit_type_id: uuid.UUID) -> Envelope:
        """READ-ONLY. The top ranked candidates for a freed slot and a DRAFT offer for the first, computed now and stored nowhere. Nothing is held, booked or sent; a person presses send.
        Unavailable until the clinician-owned settings exist."""
        try:
            settings.require("backfill_candidates", "offer_expiry_seconds", "hold_lifetime_seconds", "offer_message_template")
        except SchedulingNotConfigured as exc:
            return Envelope.unavailable("not_configured:" + ",".join(exc.names))
        session = current_session()
        ranked = await manager.rank_waitlist(session.conn, clinic_id, visit_type_id)
        if not ranked:
            return Envelope.empty("no_ranked_candidates")
        top = ranked[: settings.backfill_candidates]
        draft = {
            "candidate_patient_id": str(top[0].patient_id), "waitlist_id": str(top[0].waitlist_id), "template": settings.offer_message_template,  # clinician-approved text, verbatim
            "offer_expiry_seconds": settings.offer_expiry_seconds, "hold_lifetime_seconds": settings.hold_lifetime_seconds,
            "rule": "rank_then_joined_at",  # why this patient: the only ordering used
        }
        return Envelope.ok({"candidates": [_candidate(c) for c in top], "draft": draft})

    return server
