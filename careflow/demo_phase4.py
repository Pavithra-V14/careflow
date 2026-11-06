"""Demo-only placeholders for the Phase 4 required settings (owner decision 2026-11-06 item 1). The values live in ONE file, `config/demo-phase4.yaml`, every one labelled
"DEMO ONLY - NOT A CLINICAL DECISION". Same gate as the Phase 3 pack (careflow/demo_phase3.py): they load only when `CAREFLOW_PROFILE=demo`, `CAREFLOW_DATA_POLICY=synthetic_only` and
`CAREFLOW_DEMO_PHASE4=1`; anywhere else the application REFUSES TO START if the switch is set. An explicit value in the environment always wins. Nothing here is a default in `.env` or in code, and
the file is read with `yaml.safe_load`: unknown keys and unlabelled values are refused.

The result is an ENVIRONMENT-SHAPED mapping (the real variable names), so the existing parsers (review_console, watchdog, canary, reviewer_stats, cost_canvas, ...) are used unchanged.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import yaml

from careflow.demo_phase3 import (
    DEMO_LABEL,
    DemoConfigRefused,
    data_policy,
    demo_allowed,
)

DEMO_FILE: Final = Path(__file__).resolve().parent.parent / "config" / "demo-phase4.yaml"
SWITCH: Final = "CAREFLOW_DEMO_PHASE4"
_TRUE: Final = {"1", "true", "yes", "on"}
VARIABLES: Final = {
    "review_reason_codes": "CAREFLOW_REVIEW_REASON_CODES",
    "review_deadline_minutes": "CAREFLOW_REVIEW_DEADLINE_MINUTES",
    "watchdog_limits": "CAREFLOW_WATCHDOG_LIMITS",
    "canary_rate": "CAREFLOW_CANARY_RATE",
    "rubber_stamp_policy": "CAREFLOW_RUBBER_STAMP_POLICY",
    "auto_review_sample_rate": "CAREFLOW_AUTO_REVIEW_SAMPLE_RATE",
    "label_pseudonym_key": "CAREFLOW_LABEL_PSEUDONYM_KEY",
    "trace_sample_rate": "CAREFLOW_TRACE_SAMPLE_RATE",
    "cost_platform_floor": "CAREFLOW_COST_PLATFORM_FLOOR",
    "cost_reviewer_rates": "CAREFLOW_COST_REVIEWER_RATES",
    "sign_in_url": "CAREFLOW_SIGN_IN_URL",
    "care_gap_alert_days": "CAREFLOW_CARE_GAP_ALERT_DAYS",
    "voice_transcript_retention_days": "CAREFLOW_VOICE_TRANSCRIPT_RETENTION_DAYS",
}


def switched_on(environ: Mapping[str, str]) -> bool:
    return (environ.get(SWITCH) or "").strip().lower() in _TRUE


def refuse_demo_outside_demo(environ: Mapping[str, str], profile: str) -> None:
    """Called while the app is built. Raises if the demo switch is set unless the profile is demo AND the data policy is synthetic_only."""
    if switched_on(environ) and not demo_allowed(profile, data_policy(environ)):
        raise DemoConfigRefused("the demo Phase 4 placeholders are referenced, but this deployment is not profile=demo with data_policy=synthetic_only: refusing to start")


def _read(path: Path) -> dict[str, str]:
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("label") != DEMO_LABEL:
        raise DemoConfigRefused("the demo file does not carry the demo label")
    if data.get("loads_only_when") != {"profile": "demo", "data_policy": "synthetic_only"}:
        raise DemoConfigRefused("the demo file does not state its loading condition")
    settings = data.get("settings")
    if not isinstance(settings, dict) or set(settings) != set(VARIABLES):
        raise DemoConfigRefused("the demo file holds unexpected or missing settings")
    out: dict[str, str] = {}
    for key, entry in settings.items():
        if not isinstance(entry, dict) or set(entry) != {"value", "label"} or entry["label"] != DEMO_LABEL or not isinstance(entry["value"], str):
            raise DemoConfigRefused(f"the demo setting {key!r} is not labelled or is not text")
        out[VARIABLES[key]] = entry["value"]
    return out


def effective_environ(environ: Mapping[str, str] | None = None, profile: str = "clinical", *, path: Path = DEMO_FILE) -> Mapping[str, str]:
    """The environment with the demo placeholders filled in for every Phase 4 variable the environment leaves unset, ONLY if the switch is on and the gate allows it. Otherwise the environment itself."""
    env = os.environ if environ is None else environ
    refuse_demo_outside_demo(env, profile)
    if not switched_on(env):
        return env
    placeholders = _read(path)
    return {**placeholders, **{k: v for k, v in env.items() if not (k in placeholders and not v.strip())}}
