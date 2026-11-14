"""`voice_session` as a consent notice purpose (migration 0081; frontend request 31). Offline: the four places that list the purposes agree, the schema types the purpose as a closed enum, the demo pack supplies a LABELLED
placeholder notice and label for it, nothing outside the demo can load it, and the owner-run seed plan handles it. The database check is in tests/test_portal_extras_db_live.py. Counsel owns the real text."""

import json
import re
from pathlib import Path

import yaml

from careflow import consent_settings, demo_phase4, portal_sql

ROOT = Path(__file__).resolve().parent.parent


def test_every_list_of_purposes_agrees_and_includes_voice_session():
    migration = (ROOT / "db" / "migrations" / "0081_consent_purpose_voice_session.sql").read_text(encoding="utf-8")
    in_sql = set(re.findall(r"'([a-z_]+)'", re.search(r"check \(purpose in \(([^)]*)\)\)", migration).group(1)))  # type: ignore[union-attr]
    assert in_sql == set(consent_settings.PURPOSES) == set(portal_sql.PURPOSES) and "voice_session" in in_sql


def test_the_schema_types_the_purpose_as_a_closed_enum_in_both_places():
    spec = json.loads((ROOT / "openapi.snapshot.json").read_text(encoding="utf-8"))
    for schema in ("NoticeOut", "ConsentIn"):
        purpose = spec["components"]["schemas"][schema]["properties"]["purpose"]
        values = purpose.get("enum") or spec["components"]["schemas"][purpose["$ref"].split("/")[-1]]["enum"]
        assert set(values) == {"care_delivery", "ai_processing", "service_improvement", "voice_session"}, schema


def test_the_demo_pack_carries_a_labelled_placeholder_notice_and_label_for_the_voice_session():
    pack = yaml.safe_load((ROOT / "config" / "demo-phase4.yaml").read_text(encoding="utf-8"))
    notices = json.loads(pack["settings"]["consent_notices"]["value"])
    assert notices["voice_session"]["version"] == 1 and notices["voice_session"]["body"].startswith("DEMO ONLY - NOT A LEGAL NOTICE")
    labels = json.loads(pack["settings"]["consent_labels"]["value"])
    assert labels["voice_session"].startswith("DEMO ONLY")
    assert pack["settings"]["consent_notices"]["label"] == "DEMO ONLY - NOT A CLINICAL DECISION"


def test_the_settings_parser_accepts_the_new_purpose_and_still_refuses_an_unknown_one():
    ok = consent_settings.parse(json.dumps({"voice_session": {"version": 1, "body": "TEST"}}))
    assert ok is not None and ok["voice_session"].version == 1
    import pytest

    with pytest.raises(consent_settings.ConsentSettingsError):
        consent_settings.parse(json.dumps({"voice": {"version": 1, "body": "TEST"}}))
    assert consent_settings.parse_labels(json.dumps({"voice_session": "TEST"})) == {"voice_session": "TEST"}


def test_the_seed_plan_publishes_a_voice_notice_once_and_leaves_an_identical_one_alone():
    wanted = consent_settings.parse(json.dumps({"voice_session": {"version": 1, "body": "TEST"}}))
    assert consent_settings.plan_publication({}, wanted) == [("publish", "voice_session", 1)]  # type: ignore[arg-type]
    assert consent_settings.plan_publication({"voice_session": [(1, "TEST")]}, wanted) == [("exists", "voice_session", 1)]  # type: ignore[arg-type]
    assert consent_settings.plan_publication({"voice_session": [(1, "OTHER")]}, wanted) == [("conflict", "voice_session", 1)]  # type: ignore[arg-type]


def test_the_notice_comes_through_the_existing_consents_route_and_no_second_route_exists():
    spec = json.loads((ROOT / "openapi.snapshot.json").read_text(encoding="utf-8"))
    assert "/v1/portal/consents" in spec["paths"] and not [p for p in spec["paths"] if "consent-notice" in p]


def test_the_demo_placeholders_still_load_only_in_the_demo_profile():
    assert demo_phase4.VARIABLES["consent_notices"] == "CAREFLOW_CONSENT_NOTICES" and "voice_consent_notice" not in demo_phase4.VARIABLES
