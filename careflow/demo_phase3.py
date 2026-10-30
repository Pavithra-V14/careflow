"""Demo-only placeholders for the Phase 3 operating settings (owner decision 2026-10-29). Values live in ONE file, `config/demo-phase3.yaml`, every one labelled
"DEMO ONLY - NOT A CLINICAL DECISION".

THE GATE. The placeholders load only when ALL hold: `CAREFLOW_PROFILE=demo`, `CAREFLOW_DATA_POLICY=synthetic_only` (a deployment-level declaration that nothing here may see real patient
data; an unset or any other value is NOT synthetic_only), and `CAREFLOW_DEMO_PHASE3=1`. Anywhere else the application REFUSES TO START (`refuse_demo_outside_demo`, called while the app is being
built) if the switch is set or a scheduling setting carries the demo label: a demo value must never be mistaken for a clinician's decision.

NOT HERE, even for demo: red-flag rules, thresholds, emergency wording, protocol cards, unit synonyms, identity rules, and the accept-confidence threshold (it stays unset). The file is read
with `yaml.safe_load`; unknown keys and unlabelled values are refused.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from careflow.scheduling_agent import SchedulingSettings

DEMO_LABEL: Final = "DEMO ONLY - NOT A CLINICAL DECISION"
DEMO_FILE: Final = Path(__file__).resolve().parent.parent / "config" / "demo-phase3.yaml"
SWITCH: Final = "CAREFLOW_DEMO_PHASE3"
DATA_POLICY_VARIABLE: Final = "CAREFLOW_DATA_POLICY"
SETTING_KEYS: Final = frozenset(
    {"offer_expiry_seconds", "hold_lifetime_seconds", "backfill_candidates", "priority_names", "offer_message_text", "scheduling_task_priority", "scheduling_task_role"}
)
_TRUE: Final = {"1", "true", "yes", "on"}


class DemoConfigRefused(Exception):
    """The demo placeholders were asked for (or found) where they must not be. Fixed words."""


def data_policy(environ: Mapping[str, str]) -> str | None:
    return (environ.get(DATA_POLICY_VARIABLE) or "").strip().lower() or None


def demo_allowed(profile: str, policy: str | None) -> bool:
    return profile == "demo" and policy == "synthetic_only"


def _switched_on(environ: Mapping[str, str]) -> bool:
    return (environ.get(SWITCH) or "").strip().lower() in _TRUE


def _carries_label(environ: Mapping[str, str]) -> bool:
    return any(DEMO_LABEL in value for name, value in environ.items() if name.upper().startswith("CAREFLOW_SCHED_"))


def refuse_demo_outside_demo(environ: Mapping[str, str], profile: str) -> None:
    """Called while the app is built. Raises if the demo switch is set, or a scheduling setting carries the demo label, unless the profile is demo AND the data policy is synthetic_only."""
    if (_switched_on(environ) or _carries_label(environ)) and not demo_allowed(profile, data_policy(environ)):
        raise DemoConfigRefused(
            "the demo Phase 3 placeholders are present or referenced, but this deployment is not profile=demo with data_policy=synthetic_only: refusing to start"
        )


@dataclass(frozen=True)
class DemoPhase3:
    offer_expiry_seconds: int
    hold_lifetime_seconds: int
    backfill_candidates: int
    priority_names: tuple[str, ...]
    offer_message_text: Mapping[str, str]
    scheduling_task_priority: str
    scheduling_task_role: str

    def scheduling_settings(self) -> SchedulingSettings:
        """The Scheduling agent's settings with the demo values filled in. The accept-confidence threshold stays unset: no demo value exists for it."""
        return SchedulingSettings(
            _env_file=None,  # type: ignore[call-arg]
            offer_expiry_seconds=self.offer_expiry_seconds,
            hold_lifetime_seconds=self.hold_lifetime_seconds,
            backfill_candidates=self.backfill_candidates,
            offer_message_template=self.offer_message_text["en"],
        )


def _read(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("label") != DEMO_LABEL:
        raise DemoConfigRefused("the demo file does not carry the demo label")
    if data.get("loads_only_when") != {"profile": "demo", "data_policy": "synthetic_only"}:
        raise DemoConfigRefused("the demo file does not state its loading condition")
    settings = data.get("settings")
    if not isinstance(settings, dict) or set(settings) != SETTING_KEYS:
        raise DemoConfigRefused("the demo file holds unexpected or missing settings")
    values: dict[str, Any] = {}
    for key, entry in settings.items():
        if not isinstance(entry, dict) or set(entry) != {"value", "label"} or entry["label"] != DEMO_LABEL:
            raise DemoConfigRefused(f"the demo setting {key!r} is not labelled")
        values[key] = entry["value"]
    return values


def load_demo_phase3(profile: str, policy: str | None, *, path: Path = DEMO_FILE) -> DemoPhase3:
    """The demo placeholders, or DemoConfigRefused. Allowed ONLY for profile demo AND data_policy synthetic_only."""
    if not demo_allowed(profile, policy):
        raise DemoConfigRefused("the demo Phase 3 placeholders load only when profile=demo and data_policy=synthetic_only")
    v = _read(path)
    text = v["offer_message_text"]
    if not isinstance(text, dict) or "en" not in text:
        raise DemoConfigRefused("the demo message text needs an English entry")
    return DemoPhase3(
        offer_expiry_seconds=int(v["offer_expiry_seconds"]), hold_lifetime_seconds=int(v["hold_lifetime_seconds"]), backfill_candidates=int(v["backfill_candidates"]),
        priority_names=tuple(str(n) for n in v["priority_names"]), offer_message_text=dict(text), scheduling_task_priority=str(v["scheduling_task_priority"]),
        scheduling_task_role=str(v["scheduling_task_role"]),
    )


def scheduling_settings_for(environ: Mapping[str, str] | None = None, profile: str = "clinical") -> SchedulingSettings:
    """The Scheduling agent's settings for this deployment: from the environment, with the demo placeholders filled in ONLY if the switch is on and the gate allows it."""
    env = os.environ if environ is None else environ
    refuse_demo_outside_demo(env, profile)
    if _switched_on(env):
        return load_demo_phase3(profile, data_policy(env)).scheduling_settings()
    return SchedulingSettings()  # type: ignore[call-arg]  # the clinician-owned values from the environment; unset means unset
