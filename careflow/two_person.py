"""The two-person rules in one place (BUILD-SPEC.md 12.5), as a pure check the routes and tests share. The database enforces the ones that already live there (merge: two different front-desk people, 0029;
erasure: requester and approver differ, 0032); this module is the rule table and the check for the ones that do not have a table yet (publishing or retiring a protocol card or red-flag rule; switching
a clinic to `autonomous`), and a place where a test pins that all four exist.

A rule names the roles the two signers must hold and whether they must be different accounts. Nothing here records anything.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Rule:
    name: str
    first_roles: tuple[str, ...]  # the proposer / author
    second_roles: tuple[str, ...]  # the approver
    must_differ: bool = True


RULES: Final[Mapping[str, Rule]] = {
    "patient_merge": Rule("patient_merge", ("front_desk",), ("front_desk",)),  # enforced in the database (migration 0029)
    "erasure": Rule("erasure", ("front_desk",), ("front_desk",)),  # enforced in the database (migration 0032)
    "publish_or_retire_clinical_content": Rule("publish_or_retire_clinical_content", ("doctor", "nurse"), ("doctor",)),  # the author cannot be the approver; WHO may author is the clinic's decision
    "autonomous_mode": Rule("autonomous_mode", ("admin",), ("doctor",)),  # an admin AND a doctor
}


class TwoPersonRefused(Exception):
    """Fixed words only."""


def check(rule_name: str, first: tuple[str, Sequence[str]] | None, second: tuple[str, Sequence[str]] | None) -> None:
    """Each signer is (account id, roles they hold in the clinic). Raises if a signer is missing, holds the wrong role, or both are the same account where the rule says they must differ."""
    rule = RULES.get(rule_name)
    if rule is None:
        raise TwoPersonRefused("unknown rule")
    if first is None or second is None:
        raise TwoPersonRefused("two signers are required")
    if not set(first[1]) & set(rule.first_roles):
        raise TwoPersonRefused("the first signer does not hold a role this rule allows")
    if not set(second[1]) & set(rule.second_roles):
        raise TwoPersonRefused("the second signer does not hold a role this rule allows")
    if rule.must_differ and first[0] == second[0]:
        raise TwoPersonRefused("the two signers must be different people")
