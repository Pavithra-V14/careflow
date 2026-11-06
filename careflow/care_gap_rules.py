"""The recurrence rules and episode templates as a REQUIRED SETTING (BUILD-SPEC.md 9.3, 20.2; owner decision of 2026-11-06 item C): clinician-authored data that this module only VALIDATES and never supplies.

`CAREFLOW_CARE_GAP_RULES` is a JSON object:

    {"rules":    {"<rule_code>": {"interval_days": 30, "overdue_after_days": 5}  or  {"annual_month": 3, "overdue_after_days": 14}},
     "episodes": {"<episode_code>": ["<rule_code>", ...]}}

Unset, empty or malformed means NO episode can be created (the route answers 503) and nothing is guessed: there is no default rule and no built-in schedule. The numbers are a clinician's; the demo pack carries
labelled DEMO placeholders only. A rule is validated for shape (a closed set of keys, whole numbers in sane ranges, code words in the same shape the database checks), not for clinical sense.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

CODE: Final = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
RULE_KEYS_INTERVAL: Final = {"interval_days", "overdue_after_days"}
RULE_KEYS_ANNUAL: Final = {"annual_month", "overdue_after_days"}
MAX_RULES: Final = 200
MAX_RULES_PER_EPISODE: Final = 20


class CareGapRulesError(Exception):
    """The setting is present but not valid. Fixed words; the value is never echoed."""


@dataclass(frozen=True)
class RuleDef:
    code: str
    interval_days: int | None
    annual_month: int | None
    overdue_after_days: int


@dataclass(frozen=True)
class CareGapRules:
    rules: Mapping[str, RuleDef]
    episodes: Mapping[str, tuple[str, ...]]

    def rules_for(self, episode_code: str) -> list[RuleDef] | None:
        """The rules governing this kind of episode, or None when the episode code is not in the setting (unknown is not an empty schedule)."""
        names = self.episodes.get(episode_code)
        return None if names is None else [self.rules[n] for n in names]


def _whole(value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise CareGapRulesError("a rule number is not a whole number in range")
    return value


def parse(raw: str | None) -> CareGapRules | None:
    """None when the setting is unset or blank. CareGapRulesError when it is present but invalid (the app logs that and treats the feature as unconfigured)."""
    if raw is None or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        raise CareGapRulesError("CAREFLOW_CARE_GAP_RULES is not valid JSON") from None
    if not isinstance(data, dict) or set(data) != {"rules", "episodes"} or not isinstance(data["rules"], dict) or not isinstance(data["episodes"], dict):
        raise CareGapRulesError("CAREFLOW_CARE_GAP_RULES must be an object with exactly 'rules' and 'episodes'")
    if not data["rules"] or len(data["rules"]) > MAX_RULES:
        raise CareGapRulesError("CAREFLOW_CARE_GAP_RULES needs between 1 and 200 rules")
    rules: dict[str, RuleDef] = {}
    for code, body in data["rules"].items():
        if not isinstance(code, str) or not CODE.match(code) or not isinstance(body, dict):
            raise CareGapRulesError("a rule code or body is malformed")
        keys = set(body)
        if keys == RULE_KEYS_INTERVAL:
            rules[code] = RuleDef(code, _whole(body["interval_days"], 1, 3650), None, _whole(body["overdue_after_days"], 0, 3650))
        elif keys == RULE_KEYS_ANNUAL:
            rules[code] = RuleDef(code, None, _whole(body["annual_month"], 1, 12), _whole(body["overdue_after_days"], 0, 3650))
        else:
            raise CareGapRulesError("a rule needs exactly one cycle (interval_days or annual_month) and overdue_after_days")
    episodes: dict[str, tuple[str, ...]] = {}
    for code, names in data["episodes"].items():
        if not isinstance(code, str) or not CODE.match(code) or not isinstance(names, list) or not names or len(names) > MAX_RULES_PER_EPISODE:
            raise CareGapRulesError("an episode code or its rule list is malformed")
        if len(set(names)) != len(names) or any(not isinstance(n, str) or n not in rules for n in names):
            raise CareGapRulesError("an episode names a rule twice or one that does not exist")
        episodes[code] = tuple(names)
    return CareGapRules(rules, episodes)
