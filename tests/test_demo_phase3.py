"""The demo Phase 3 placeholders (careflow/demo_phase3.py, config/demo-phase3.yaml): they load ONLY for profile=demo AND data_policy=synthetic_only, every value is labelled, and any other
deployment refuses to start if they are present or referenced. Offline."""

import re
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI

from careflow.app import Settings, create_app
from careflow.demo_phase3 import (
    DATA_POLICY_VARIABLE,
    DEMO_FILE,
    DEMO_LABEL,
    SETTING_KEYS,
    SWITCH,
    DemoConfigRefused,
    load_demo_phase3,
    refuse_demo_outside_demo,
    scheduling_settings_for,
)

DEMO_ENV = {"CAREFLOW_PROFILE": "demo", DATA_POLICY_VARIABLE: "synthetic_only", SWITCH: "1"}


def settings():
    return Settings(supabase_url="https://x.supabase.test", database_url_app="postgresql://u:p@h/d")  # type: ignore[call-arg]


# ---- the file ------------------------------------------------------------------------------------------------------------------------------


def test_every_value_in_the_file_is_labelled_and_the_header_says_so():
    text = DEMO_FILE.read_text(encoding="utf-8")
    assert text.splitlines()[0] == f"# {DEMO_LABEL}."
    data = yaml.safe_load(text)
    assert data["label"] == DEMO_LABEL and data["loads_only_when"] == {"profile": "demo", "data_policy": "synthetic_only"}
    assert set(data["settings"]) == SETTING_KEYS
    for key, entry in data["settings"].items():
        assert set(entry) == {"value", "label"} and entry["label"] == DEMO_LABEL, key


def test_no_clinical_content_is_in_the_demo_file_not_even_for_demo():
    data = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))
    forbidden = re.compile(r"red[- ]?flag|threshold|emergency|protocol|synonym|identity|diagnos|dose|dosage|chest|breath|bleed|suicid|call 1|ambulance", re.IGNORECASE)
    assert not forbidden.search(str(data["settings"])), "the demo placeholders must not contain clinical content"
    assert "accept_confidence_threshold" not in data["settings"]  # the threshold stays unset, even for demo
    for text in data["settings"]["offer_message_text"]["value"].values():
        assert text.startswith(DEMO_LABEL)  # a demo message can never be mistaken for approved wording


def test_the_loader_refuses_a_file_that_is_unlabelled_or_holds_other_keys(tmp_path: Path):
    good = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))
    cases = {}
    missing = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))
    del missing["settings"]["backfill_candidates"]["label"]
    cases["an unlabelled value"] = missing
    extra = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))
    extra["settings"]["red_flag_phrases"] = {"value": ["x"], "label": DEMO_LABEL}
    cases["an extra key"] = extra
    unlabelled_file = dict(good, label="something else")
    cases["a wrong file label"] = unlabelled_file
    for what, content in cases.items():
        path = tmp_path / "demo.yaml"
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
        with pytest.raises(DemoConfigRefused):
            load_demo_phase3("demo", "synthetic_only", path=path)
        assert what


# ---- the gate: loads only in demo + synthetic_only -------------------------------------------------------------------------------------------


def test_the_placeholders_load_in_demo_with_a_synthetic_only_policy_and_fill_the_scheduling_settings():
    demo = load_demo_phase3("demo", "synthetic_only")
    assert demo.priority_names == ("DEMO-LEVEL-A", "DEMO-LEVEL-B", "DEMO-LEVEL-C") and set(demo.offer_message_text) == {"en", "ta"}
    s = scheduling_settings_for(DEMO_ENV, "demo")
    assert (s.offer_expiry_seconds, s.hold_lifetime_seconds, s.backfill_candidates) == (3600, 900, 3) and DEMO_LABEL in (s.offer_message_template or "")
    assert s.accept_confidence_threshold is None and s.missing() == ["accept_confidence_threshold"]  # the one value with no demo placeholder


@pytest.mark.parametrize(("profile", "policy"), [("clinical", "synthetic_only"), ("demo", "phi_approved"), ("demo", None), ("demo", ""), ("clinical", "phi_approved"), ("demo", "Synthetic")])
def test_every_other_combination_refuses_to_load(profile, policy):
    with pytest.raises(DemoConfigRefused):
        load_demo_phase3(profile, policy)


@pytest.mark.parametrize(
    "env",
    [
        {SWITCH: "1"},  # profile unset = clinical
        {SWITCH: "true", "CAREFLOW_PROFILE": "clinical", DATA_POLICY_VARIABLE: "synthetic_only"},
        {SWITCH: "1", "CAREFLOW_PROFILE": "demo", DATA_POLICY_VARIABLE: "phi_approved"},
        {SWITCH: "1", "CAREFLOW_PROFILE": "demo"},  # no data policy declared
        {"CAREFLOW_SCHED_OFFER_MESSAGE_TEMPLATE": f"{DEMO_LABEL}. text", "CAREFLOW_PROFILE": "clinical"},  # a demo value smuggled in by name
        {"CAREFLOW_SCHED_OFFER_MESSAGE_TEMPLATE": f"{DEMO_LABEL}. text", "CAREFLOW_PROFILE": "demo", DATA_POLICY_VARIABLE: "phi_approved"},
    ],
)
def test_outside_demo_with_synthetic_only_a_present_or_referenced_placeholder_refuses_to_start(env):
    profile = "demo" if env.get("CAREFLOW_PROFILE") == "demo" else "clinical"
    with pytest.raises(DemoConfigRefused):
        refuse_demo_outside_demo(env, profile)
    with pytest.raises(DemoConfigRefused):
        scheduling_settings_for(env, profile)


def test_nothing_is_refused_when_the_placeholders_are_absent_in_any_profile():
    for profile in ("demo", "clinical"):
        refuse_demo_outside_demo({}, profile)
        refuse_demo_outside_demo({"CAREFLOW_SCHED_BACKFILL_CANDIDATES": "2"}, profile)  # a real, clinician-supplied value is not a demo value
    assert scheduling_settings_for({}, "clinical").missing() == [
        "offer_expiry_seconds", "hold_lifetime_seconds", "backfill_candidates", "accept_confidence_threshold", "offer_message_template",
    ]  # without the switch nothing is filled in, in any profile


# ---- the application refuses to be built ----------------------------------------------------------------------------------------------------


def test_the_app_refuses_to_be_built_with_the_demo_switch_outside_demo_and_builds_inside_it(monkeypatch):
    monkeypatch.setenv(SWITCH, "1")
    monkeypatch.delenv("CAREFLOW_PROFILE", raising=False)  # unset = clinical
    monkeypatch.delenv(DATA_POLICY_VARIABLE, raising=False)
    with pytest.raises(DemoConfigRefused):
        create_app(settings())
    monkeypatch.setenv("CAREFLOW_PROFILE", "demo")
    with pytest.raises(DemoConfigRefused):  # demo, but no synthetic_only declaration
        create_app(settings())
    monkeypatch.setenv(DATA_POLICY_VARIABLE, "synthetic_only")
    assert isinstance(create_app(settings()), FastAPI)


def test_the_app_refuses_a_demo_labelled_scheduling_value_in_the_clinical_profile(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    monkeypatch.setenv("CAREFLOW_PROFILE", "clinical")
    monkeypatch.setenv("CAREFLOW_SCHED_OFFER_MESSAGE_TEMPLATE", f"{DEMO_LABEL}. smuggled")
    with pytest.raises(DemoConfigRefused):
        create_app(settings())
