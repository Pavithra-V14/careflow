"""Why GET /v1/staff/visits/{id}/readiness answered 503 in the demo (live finding 2026-11-21), pinned so it cannot come back:

  1. the run needs the FEATURE_VISIT_READINESS switch (a real switch in .env, never supplied by the demo pack) AND the checklist content AND its task priority (both supplied by the demo pack) AND the keys;
  2. the demo pack's checklist is keyed by visit-type NAME (exact match), so it must name the visit types the demo seed actually creates, or the answer is a 409 "no readiness content" even with everything on;
  3. the smoke check warns, in fixed words, when the switch is off;
  4. no setting the demo pack supplies is read from the raw process environment by a route (the class of bug of the public limit and the message limit).
Offline: the lifespan runs on a stand-in engine; nothing connects to a database."""

import ast
import base64
import json
import os
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from careflow.app import Settings, create_app
from careflow.auth import JWKSClient
from careflow.crypto import DemographicVault
from careflow.visit_readiness_content import parse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import demo_smoke as smoke  # noqa: E402
import seed_dev_demo_data as seed  # noqa: E402

PROJECT = "https://example-project.supabase.co"
VAULT = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})


def pack_content() -> str:
    return yaml.safe_load((ROOT / "config" / "demo-phase4.yaml").read_text(encoding="utf-8"))["settings"]["visit_readiness_content"]["value"]


def test_the_demo_pack_has_checklist_content_for_every_visit_type_the_demo_seed_creates():
    content = parse(pack_content())
    assert content is not None
    seeded = [name for name, _minutes in seed.VISIT_TYPES]
    assert len(seeded) >= 2
    missing = [name for name in seeded if content.for_visit_type(name) is None]
    assert not missing, f"the demo seed creates visit types the demo pack has no readiness content for (a 409 'no readiness content'): {missing}"
    json.loads(pack_content())  # still valid, still labelled
    assert all("DEMO ONLY" in item["text"] for t in json.loads(pack_content())["visit_types"].values() for item in t["checklist"])


def readiness_components(monkeypatch, **env):
    for name in ("FEATURE_VISIT_READINESS", "CAREFLOW_DEMO_PHASE4", "CAREFLOW_VISIT_READINESS_CONTENT", "CAREFLOW_VISIT_READINESS_TASK_PRIORITY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CAREFLOW_PROFILE", "demo")
    monkeypatch.setenv("CAREFLOW_DATA_POLICY", "synthetic_only")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app(Settings(_env_file=None, supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=JWKSClient(PROJECT + "/auth/v1/.well-known/jwks.json"), engine=object(), vault=VAULT)  # type: ignore[arg-type]
    with TestClient(app):
        return app.state.visit_readiness


def test_readiness_is_built_only_with_the_switch_the_pack_content_and_the_pack_priority(monkeypatch):
    assert readiness_components(monkeypatch) is None  # nothing on
    assert readiness_components(monkeypatch, CAREFLOW_DEMO_PHASE4="1") is None  # the pack alone: the SWITCH is missing (the live 503)
    assert readiness_components(monkeypatch, FEATURE_VISIT_READINESS="on") is None  # the switch alone: no content, no priority
    built = readiness_components(monkeypatch, FEATURE_VISIT_READINESS="on", CAREFLOW_DEMO_PHASE4="1")
    assert built is not None and built.content.for_visit_type(seed.VISIT_TYPES[0][0]) is not None


def test_the_smoke_check_warns_in_fixed_words_while_the_readiness_switch_is_off():
    name, status, detail = smoke.judge_visit_readiness({"CAREFLOW_DEMO_PHASE4": "1"})
    assert status == "warn" and "FEATURE_VISIT_READINESS" in detail
    name, status, detail = smoke.judge_visit_readiness({"FEATURE_VISIT_READINESS": "on"})
    assert status == "warn" and "CAREFLOW_DEMO_PHASE4" in detail  # no content and no priority without the pack or its own values
    assert smoke.judge_visit_readiness({"FEATURE_VISIT_READINESS": "on", "CAREFLOW_DEMO_PHASE4": "1"})[1] is True
    assert smoke.judge_visit_readiness({"FEATURE_VISIT_READINESS": "on", "CAREFLOW_VISIT_READINESS_CONTENT": "{}", "CAREFLOW_VISIT_READINESS_TASK_PRIORITY": "standard"})[1] is True
    assert name in smoke.FIXES


# Helpers that read a setting from the PROCESS environment when called with no argument. The demo pack supplies several of these settings only through the effective environment (`app.state.p4env`), so a call from app.py
# with no argument silently ignores the pack: the public limit, the message limit and the sex words were each that bug.
PACK_AWARE_HELPERS = {"sex_words", "public_ip_limit", "public_read_limit", "settings_from", "cost_settings_from", "sample_rate_from", "canary_rate_from", "thresholds_from", "budget_from", "key_from"}


def test_no_route_reads_a_pack_supplied_setting_from_the_raw_process_environment():
    tree = ast.parse((ROOT / "careflow" / "app.py").read_text(encoding="utf-8"))
    bare = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in PACK_AWARE_HELPERS and not node.args and not node.keywords:
                bare.append((name, node.lineno))
    assert not bare, f"called with no environment (the demo pack is not seen): {bare}"
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    for setting in ("CAREFLOW_MESSAGE_MAX_PER_MINUTE", "CAREFLOW_PUBLIC_READ_IP_PER_MINUTE", "CAREFLOW_PUBLIC_GRIEVANCE_IP_PER_HOUR", "CAREFLOW_SEX_WORDS"):
        assert f'os.environ.get("{setting}")' not in source, setting  # never read directly in the application


def test_the_only_switches_read_from_the_raw_environment_are_real_owner_switches_not_pack_values():
    """FEATURE_* flags and the demo switches themselves are owner choices in .env; the pack never supplies them. This pins that list so a pack value is never added to it by accident."""
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    assert "care_journey_enabled(dict(os.environ))" in source and "visit_readiness_enabled(dict(os.environ))" in source
    for pack_value in ("CAREFLOW_VISIT_READINESS_CONTENT", "CAREFLOW_VISIT_READINESS_TASK_PRIORITY"):
        assert f'p4env.get("{pack_value}")' in source  # the content and the priority DO come from the pack-aware environment
    assert pytest
