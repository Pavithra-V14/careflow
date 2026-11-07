"""The approval matrix (BUILD-SPEC.md 12.1, 12.5): per autonomy mode and per action class, who decides. CONFIG AS DATA, not branches in code, and the LOCKED rows cannot be loosened by any setting.

AUTONOMY MODE is a clinic-level setting with three values, `shadow` (a human decides everything), `assisted` (the spec's default) and `autonomous`. It is a GOVERNANCE choice, so it is a REQUIRED setting with no
default in code: when it is unset or unrecognised the effective mode is `shadow`, the most human one (unknown is not a value, and more human review is always the safe direction). Switching a clinic to
`autonomous` is a two-person act (an admin AND a doctor, 12.5): that act is `AutonomyChange` below, a pure check; the route that records it is not part of this module.

THE MATRIX below transcribes the spec's table. It carries NO clinical content: the cells are who decides (`human`, `automatic`, `automatic_ack` = automatic AND a nurse must acknowledge, `human_auto_escalate`).
Two kinds of LOCKED row exist and are different things (spec 12.1):
  * locked toward a human: patient merge, a non-template patient message, an out-of-range readiness answer. No mode and no clinic option makes these automatic.
  * locked toward automatic: a red flag on any channel (including voice) is ALWAYS handled at once with a nurse acknowledgement, never waiting for a human first.
Clinic opt-ins ("unless clinic opts in") are explicit booleans that default to False; an opt-in applies only to `autonomous` mode and only to rows the spec allows it for.
"""

from dataclasses import dataclass
from typing import Final, Literal

Mode = Literal["shadow", "assisted", "autonomous"]
Decider = Literal["human", "automatic", "automatic_ack", "human_auto_escalate", "human_confirms"]
MODES: Final = ("shadow", "assisted", "autonomous")

# action class -> (shadow, assisted, autonomous)
MATRIX: Final[dict[str, tuple[Decider, Decider, Decider]]] = {
    "booking": ("human", "automatic", "automatic"),  # bookings, holds, cancellations
    "records_release": ("human", "human", "human"),  # records request / referral leaving the clinic (autonomous only with the clinic opt-in)
    "triage_approval": ("human", "human", "human"),  # triage approval, care-plan tasks, conditional referrals (assisted auto-approve of ONE confident low-risk card is a separate, explicit rule below)
    "patient_merge": ("human", "human", "human"),
    "uncertain_identity_registration": ("human", "human", "human"),
    "free_text_patient_message": ("human", "human", "human"),
    "emergency_template": ("automatic_ack", "automatic_ack", "automatic_ack"),
    "readiness_out_of_range": ("human", "human_auto_escalate", "human_auto_escalate"),
    "barrier_resolvable_by_rebooking": ("human_confirms", "automatic", "automatic"),
    "barrier_social_resource": ("human", "human", "human"),
    "voice_red_flag": ("automatic_ack", "automatic_ack", "automatic_ack"),
}
LOCKED_TO_HUMAN: Final = frozenset({"patient_merge", "uncertain_identity_registration", "free_text_patient_message", "readiness_out_of_range"})
LOCKED_TO_AUTOMATIC: Final = frozenset({"emergency_template", "voice_red_flag"})
OPT_IN_ALLOWED: Final = frozenset({"records_release", "triage_approval"})  # rows the spec lets a clinic open up in autonomous mode


def effective_mode(value: object) -> Mode:
    """The configured mode, or `shadow` for anything unset or unrecognised."""
    return value if isinstance(value, str) and value in MODES else "shadow"  # type: ignore[return-value]


@dataclass(frozen=True)
class Policy:
    mode: Mode = "shadow"
    opt_in_records_release: bool = False  # autonomous mode only
    opt_in_triage_approval: bool = False  # autonomous mode only
    auto_approve_one_confident_low_risk_card: bool = False  # assisted mode's single exception for triage approval; off unless a clinician enables it

    def decider(self, action_class: str) -> Decider:
        """Who decides this class of action under this policy. An unknown class is `human`: unknown is not automatic."""
        row = MATRIX.get(action_class)
        if row is None:
            return "human"
        decider = row[MODES.index(self.mode)]
        if action_class in LOCKED_TO_HUMAN and decider == "automatic":
            return "human"  # defence in depth: the table can never say automatic for a locked row
        if self.mode == "autonomous" and action_class in OPT_IN_ALLOWED:
            opted = {"records_release": self.opt_in_records_release, "triage_approval": self.opt_in_triage_approval}[action_class]
            return "automatic" if opted else "human"
        if self.mode == "assisted" and action_class == "triage_approval" and self.auto_approve_one_confident_low_risk_card:
            return "automatic"
        return decider

    def may_run_without_a_human_first(self, action_class: str) -> bool:
        return self.decider(action_class) in {"automatic", "automatic_ack"}


@dataclass(frozen=True)
class AutonomyChange:
    """Switching a clinic to `autonomous` needs an admin AND a doctor, two different people (12.5). Pure: the caller supplies who signed."""

    admin_account: object | None
    doctor_account: object | None

    def allowed(self) -> bool:
        return self.admin_account is not None and self.doctor_account is not None and self.admin_account != self.doctor_account


def policy_from(value: object, **opt_ins: bool) -> Policy:
    """A Policy from the raw setting; unknown opt-in names are refused (a typo must not silently leave an opt-in off or on)."""
    known = {"opt_in_records_release", "opt_in_triage_approval", "auto_approve_one_confident_low_risk_card"}
    if set(opt_ins) - known:
        raise ValueError("unknown policy option")
    return Policy(effective_mode(value), **opt_ins)


def tightened(configured: Policy, stored_mode: object | None) -> Policy:
    """The policy to run with when a clinic's STORED autonomy mode (ops.autonomy_changes) exists: it can only make behaviour MORE human than the configured policy, never less. When they disagree the more
    human one wins (shadow < assisted < autonomous); a stored mode that is equal or less human changes nothing, and no stored row (None) changes nothing. The opt-ins of the configured policy are kept: they
    apply only in `autonomous`, which a lowered mode never is."""
    if stored_mode is None:
        return configured
    stored = effective_mode(stored_mode)
    if MODES.index(stored) < MODES.index(configured.mode):
        return Policy(stored, configured.opt_in_records_release, configured.opt_in_triage_approval, configured.auto_approve_one_confident_low_risk_card)
    return configured
